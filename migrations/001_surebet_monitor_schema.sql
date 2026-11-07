-- =====================================================================
-- MIGRACIÓ 001: ESQUEMA DE SEGURETAT DE SUREBET MONITOR (PostgreSQL / Supabase)
-- =====================================================================
-- Aquest script defineix l'arquitectura de seguretat, persistència i concurrència
-- per a la coordinació asíncrona entre el panell web (Vercel) i l'agent local Windows.
--
-- Principis de seguretat aplicats:
-- 1. Mínim Privilegi: Cap credencial de 'service_role' ni d'administrador al PC local.
-- 2. Identitat Verificada al Servidor: L'agent s'autentica mitjançant Supabase Auth
--    i la seva identitat es resol exclusivament mitjançant auth.uid().
-- 3. Concurrència Atòmica: Índex únic parcial a nivell de motor PostgreSQL per impedir
--    dues sessions actives simultànies per agent, amb bloqueig SKIP LOCKED a la cua.
-- 4. Transicions d'Estat Segures: Trigger a nivell de base de dades que impedeix salts
--    d'estat il·legals i protegeix la immutabilitat dels timestamps.
-- 5. Retenció de 30 Minuts: Filtres estrictes per antiguitat real de les quotes (<= 1800s).
-- =====================================================================

-- 1. TAULA D'USUARIS AUTORITZATS (RBAC)
CREATE TABLE IF NOT EXISTS app_users (
    user_id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    email VARCHAR(255) NOT NULL UNIQUE,
    role VARCHAR(20) NOT NULL CHECK (role IN ('admin', 'operator', 'viewer', 'agent')),
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 2. TAULA D'AGENTS WINDOWS REGISTRATS
CREATE TABLE IF NOT EXISTS daemon_agents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_user_id UUID NOT NULL REFERENCES app_users(user_id) ON DELETE RESTRICT,
    owner_user_id UUID NOT NULL REFERENCES app_users(user_id) ON DELETE RESTRICT,
    agent_name VARCHAR(100) NOT NULL UNIQUE,
    is_active BOOLEAN NOT NULL DEFAULT true,
    last_heartbeat TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_agent_user UNIQUE (agent_user_id)
);

-- 3. TAULA DE SESSIONS DE CERCA DE SUREBETS
CREATE TABLE IF NOT EXISTS surebet_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_by UUID NOT NULL REFERENCES app_users(user_id) ON DELETE RESTRICT,
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'RUNNING', 'CANCEL_REQUESTED', 'CANCELLED', 'COMPLETED', 'FAILED', 'TIMEOUT')),
    claimed_by_agent_id UUID REFERENCES daemon_agents(id) ON DELETE SET NULL,
    claimed_at TIMESTAMPTZ,
    heartbeat_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    duration_seconds INT NOT NULL DEFAULT 600
        CHECK (duration_seconds BETWEEN 60 AND 1800), -- Límit màxim estricte de 30 minuts (evita abús de 120 min)
    summary JSONB,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 4. RESTRICCIÓ DE CONCURRÈNCIA REAL (PostgreSQL Engine Level)
-- Impedeix que un mateix agent tingui dues sessions en curs (RUNNING o CANCEL_REQUESTED) simultàniament.
CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_single_active_session
ON surebet_sessions (claimed_by_agent_id)
WHERE status IN ('RUNNING', 'CANCEL_REQUESTED');

-- 5. ÍNDEX DE CUA D'ASSIGNACIÓ FIFO
CREATE INDEX IF NOT EXISTS idx_surebet_sessions_queue
ON surebet_sessions (created_at ASC)
WHERE status = 'PENDING';

-- 6. TAULA DE SNAPSHOTS DE QUOTES I MERCATS
CREATE TABLE IF NOT EXISTS surebet_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES surebet_sessions(id) ON DELETE CASCADE,
    agent_id UUID NOT NULL REFERENCES daemon_agents(id) ON DELETE RESTRICT,
    market_type VARCHAR(50) NOT NULL,
    match_id VARCHAR(100) NOT NULL,
    odds_age_seconds INT NOT NULL CHECK (odds_age_seconds >= 0 AND odds_age_seconds <= 1800),
    opportunities_count INT NOT NULL DEFAULT 0,
    captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    payload JSONB NOT NULL,
    CONSTRAINT chk_payload_size CHECK (octet_length(payload::text) <= 524288) -- Màxim 512 KB per snapshot
);

CREATE INDEX IF NOT EXISTS idx_surebet_snapshots_retention
ON surebet_snapshots (captured_at DESC);

-- 7. TAULA D'OPORTUNITATS ACCIONABLES (Surebets i Near-Arbs)
CREATE TABLE IF NOT EXISTS surebet_opportunities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    snapshot_id UUID NOT NULL REFERENCES surebet_snapshots(id) ON DELETE CASCADE,
    session_id UUID NOT NULL REFERENCES surebet_sessions(id) ON DELETE CASCADE,
    opportunity_type VARCHAR(20) NOT NULL CHECK (opportunity_type IN ('SUREBET', 'NEAR_ARB')),
    matchup VARCHAR(200) NOT NULL,
    market_key VARCHAR(100) NOT NULL,
    net_roi_pct NUMERIC(6, 3) NOT NULL,
    gross_margin_pct NUMERIC(6, 3) NOT NULL,
    commission_deducted_pct NUMERIC(6, 3) NOT NULL DEFAULT 0.0,
    bookmakers JSONB NOT NULL,
    detected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_stale BOOLEAN NOT NULL DEFAULT false
);

CREATE INDEX IF NOT EXISTS idx_surebet_opportunities_active
ON surebet_opportunities (detected_at DESC, net_roi_pct DESC)
WHERE is_stale = false;

-- 8. VISTA PÚBLICA / OPERADORA D'OPORTUNITATS ACTIVES (< 30 MINUTS)
CREATE OR REPLACE VIEW v_active_surebets AS
SELECT 
    o.id,
    o.session_id,
    o.opportunity_type,
    o.matchup,
    o.market_key,
    o.net_roi_pct,
    o.gross_margin_pct,
    o.commission_deducted_pct,
    o.bookmakers,
    o.detected_at,
    EXTRACT(EPOCH FROM (now() - o.detected_at))::INT AS age_seconds
FROM surebet_opportunities o
WHERE o.detected_at >= (now() - INTERVAL '30 minutes')
  AND o.is_stale = false
ORDER BY o.net_roi_pct DESC;

-- =====================================================================
-- TRIGGERS DE SEGURETAT I VALIDACIÓ DE TRANSICIONS D'ESTAT
-- =====================================================================

CREATE OR REPLACE FUNCTION trg_fn_validate_session_integrity()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
    -- 1. Protecció d'immutabilitat del creador i timestamps inicials
    IF TG_OP = 'UPDATE' THEN
        IF OLD.created_by <> NEW.created_by THEN
            RAISE EXCEPTION 'Camp immutable: no es permet modificar el creador de la sessió'
                USING ERRCODE = '42501';
        END IF;

        IF OLD.created_at <> NEW.created_at THEN
            RAISE EXCEPTION 'Camp immutable: no es permet alterar la data de creació'
                USING ERRCODE = '42501';
        END IF;

        -- 2. Validació estricta de la màquina d'estats
        IF OLD.status <> NEW.status THEN
            -- PENDING només pot passar a RUNNING o CANCELLED
            IF OLD.status = 'PENDING' AND NEW.status NOT IN ('RUNNING', 'CANCELLED') THEN
                RAISE EXCEPTION 'Transició d''estat invàlida des de PENDING a %', NEW.status
                    USING ERRCODE = '22000';
            END IF;

            -- RUNNING només pot passar a CANCEL_REQUESTED, COMPLETED, FAILED o TIMEOUT
            IF OLD.status = 'RUNNING' AND NEW.status NOT IN ('CANCEL_REQUESTED', 'COMPLETED', 'FAILED', 'TIMEOUT') THEN
                RAISE EXCEPTION 'Transició d''estat invàlida des de RUNNING a %', NEW.status
                    USING ERRCODE = '22000';
            END IF;

            -- CANCEL_REQUESTED només pot passar a CANCELLED, FAILED o TIMEOUT
            IF OLD.status = 'CANCEL_REQUESTED' AND NEW.status NOT IN ('CANCELLED', 'FAILED', 'TIMEOUT') THEN
                RAISE EXCEPTION 'Transició d''estat invàlida des de CANCEL_REQUESTED a %', NEW.status
                    USING ERRCODE = '22000';
            END IF;

            -- Els estats terminals (COMPLETED, CANCELLED, FAILED, TIMEOUT) no poden canviar mai més
            IF OLD.status IN ('COMPLETED', 'CANCELLED', 'FAILED', 'TIMEOUT') THEN
                RAISE EXCEPTION 'La sessió ja es troba en estat terminal (%) i no admet nous canvis', OLD.status
                    USING ERRCODE = '22000';
            END IF;
        ELSE
            -- Si l'estat és idèntic però la sessió ja és terminal, bloquejar qualsevol modificació
            IF OLD.status IN ('COMPLETED', 'CANCELLED', 'FAILED', 'TIMEOUT') THEN
                RAISE EXCEPTION 'No es permet actualitzar sessions en estat terminal (%)', OLD.status
                    USING ERRCODE = '42501';
            END IF;
        END IF;
    END IF;

    NEW.updated_at := clock_timestamp();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_surebet_session_integrity ON surebet_sessions;
CREATE TRIGGER trg_surebet_session_integrity
BEFORE UPDATE ON surebet_sessions
FOR EACH ROW
EXECUTE FUNCTION trg_fn_validate_session_integrity();

-- =====================================================================
-- FUNCIONS RPC PROTEGIDES (SECURITY DEFINER)
-- =====================================================================

-- 1. Reclamació atòmica de la següent tasca pendent
CREATE OR REPLACE FUNCTION claim_next_pending_session()
RETURNS TABLE (
    session_id UUID,
    duration_sec INT,
    assigned_agent_id UUID
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_agent_id UUID;
    v_session_id UUID;
    v_duration INT;
    v_now TIMESTAMPTZ := clock_timestamp();
BEGIN
    -- Identificar exclusivament l'agent a través de l'usuari autenticat al token
    SELECT id INTO v_agent_id
    FROM daemon_agents
    WHERE agent_user_id = auth.uid()
      AND is_active = true;

    IF v_agent_id IS NULL THEN
        RAISE EXCEPTION 'Accés denegat: El cridant no correspon a cap agent actiu autoritzat'
            USING ERRCODE = '42501';
    END IF;

    -- Comprovar que l'agent no té cap sessió activa (RUNNING o CANCEL_REQUESTED)
    IF EXISTS (
        SELECT 1 FROM surebet_sessions
        WHERE claimed_by_agent_id = v_agent_id
          AND status IN ('RUNNING', 'CANCEL_REQUESTED')
    ) THEN
        RETURN; -- Ja té feina activa assignada
    END IF;

    -- Neteja automàtica preventiva de sessions zombis (> 90 segons sense heartbeat o expirades)
    UPDATE surebet_sessions
    SET status = 'TIMEOUT',
        error_message = 'Sessió avortada: batec de cor (heartbeat) de l''agent perdut',
        updated_at = v_now
    WHERE status IN ('RUNNING', 'CANCEL_REQUESTED')
      AND (v_now > expires_at OR v_now - COALESCE(heartbeat_at, claimed_at) > INTERVAL '90 seconds');

    -- Reclamació atòmica FIFO amb bloqueig de fila sense espera
    SELECT id, duration_seconds INTO v_session_id, v_duration
    FROM surebet_sessions
    WHERE status = 'PENDING'
    ORDER BY created_at ASC
    LIMIT 1
    FOR UPDATE SKIP LOCKED;

    IF v_session_id IS NOT NULL THEN
        UPDATE surebet_sessions
        SET status = 'RUNNING',
            claimed_by_agent_id = v_agent_id,
            claimed_at = v_now,
            heartbeat_at = v_now,
            expires_at = v_now + (v_duration || ' seconds')::INTERVAL,
            updated_at = v_now
        WHERE id = v_session_id;

        RETURN QUERY SELECT v_session_id, v_duration, v_agent_id;
    END IF;
END;
$$;

-- 2. Emissió de batec de cor (Heartbeat) i comprovació de cancel·lació cooperativa
CREATE OR REPLACE FUNCTION send_session_heartbeat(p_session_id UUID)
RETURNS TABLE (
    alive BOOLEAN,
    cancel_requested BOOLEAN
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_agent_id UUID;
    v_status VARCHAR(20);
    v_now TIMESTAMPTZ := clock_timestamp();
BEGIN
    SELECT id INTO v_agent_id
    FROM daemon_agents
    WHERE agent_user_id = auth.uid() AND is_active = true;

    IF v_agent_id IS NULL THEN
        RAISE EXCEPTION 'Accés denegat: Crida no autoritzada' USING ERRCODE = '42501';
    END IF;

    SELECT status INTO v_status
    FROM surebet_sessions
    WHERE id = p_session_id
      AND claimed_by_agent_id = v_agent_id;

    IF v_status IS NULL THEN
        RETURN QUERY SELECT false, false;
        RETURN;
    END IF;

    IF v_status = 'CANCEL_REQUESTED' THEN
        -- Registrar batec mentre l'agent s'apaga cooperativament
        UPDATE surebet_sessions
        SET heartbeat_at = v_now, updated_at = v_now
        WHERE id = p_session_id;

        RETURN QUERY SELECT true, true;
        RETURN;
    ELSIF v_status = 'RUNNING' THEN
        UPDATE surebet_sessions
        SET heartbeat_at = v_now, updated_at = v_now
        WHERE id = p_session_id;

        -- Actualitzar també el darrer batec de l'agent
        UPDATE daemon_agents
        SET last_heartbeat = v_now
        WHERE id = v_agent_id;

        RETURN QUERY SELECT true, false;
        RETURN;
    ELSE
        -- La sessió ja ha expirat o s'ha marcat com a terminal
        RETURN QUERY SELECT false, false;
        RETURN;
    END IF;
END;
$$;

-- 3. Sol·licitud de cancel·lació de sessió per propietari o administrador
CREATE OR REPLACE FUNCTION request_session_cancellation(p_session_id UUID)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_role VARCHAR(20);
    v_created_by UUID;
    v_status VARCHAR(20);
BEGIN
    SELECT role INTO v_role
    FROM app_users
    WHERE user_id = auth.uid() AND is_active = true;

    SELECT created_by, status INTO v_created_by, v_status
    FROM surebet_sessions
    WHERE id = p_session_id;

    IF v_status IS NULL THEN
        RETURN false;
    END IF;

    -- Només l'administrador o el propi creador de la sessió poden sol·licitar cancel·lar-la
    IF v_role <> 'admin' AND v_created_by <> auth.uid() THEN
        RAISE EXCEPTION 'Accés denegat: no podeu cancel·lar sessions alienes' USING ERRCODE = '42501';
    END IF;

    IF v_status = 'PENDING' THEN
        UPDATE surebet_sessions
        SET status = 'CANCELLED', updated_at = clock_timestamp()
        WHERE id = p_session_id;
        RETURN true;
    ELSIF v_status = 'RUNNING' THEN
        UPDATE surebet_sessions
        SET status = 'CANCEL_REQUESTED', updated_at = clock_timestamp()
        WHERE id = p_session_id;
        RETURN true;
    ELSE
        -- Ja és en un estat terminal o en procés de cancel·lació
        RETURN false;
    END IF;
END;
$$;

-- 4. Finalització de sessió amb resum (Només l'agent assignat)
CREATE OR REPLACE FUNCTION complete_session(p_session_id UUID, p_summary JSONB)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_agent_id UUID;
    v_status VARCHAR(20);
    v_now TIMESTAMPTZ := clock_timestamp();
BEGIN
    SELECT id INTO v_agent_id
    FROM daemon_agents
    WHERE agent_user_id = auth.uid() AND is_active = true;

    SELECT status INTO v_status
    FROM surebet_sessions
    WHERE id = p_session_id AND claimed_by_agent_id = v_agent_id;

    IF v_status IS NULL THEN
        RAISE EXCEPTION 'Sessió no trobada o no assignada a aquest agent' USING ERRCODE = '42501';
    END IF;

    IF v_status = 'CANCEL_REQUESTED' THEN
        UPDATE surebet_sessions
        SET status = 'CANCELLED', summary = p_summary, updated_at = v_now
        WHERE id = p_session_id;
        RETURN true;
    ELSIF v_status = 'RUNNING' THEN
        UPDATE surebet_sessions
        SET status = 'COMPLETED', summary = p_summary, updated_at = v_now
        WHERE id = p_session_id;
        RETURN true;
    ELSE
        RETURN false;
    END IF;
END;
$$;

-- 5. Fallada de sessió amb missatge d'error (Només l'agent assignat)
CREATE OR REPLACE FUNCTION fail_session(p_session_id UUID, p_error_message TEXT)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_agent_id UUID;
BEGIN
    SELECT id INTO v_agent_id
    FROM daemon_agents
    WHERE agent_user_id = auth.uid() AND is_active = true;

    UPDATE surebet_sessions
    SET status = 'FAILED',
        error_message = p_error_message,
        updated_at = clock_timestamp()
    WHERE id = p_session_id
      AND claimed_by_agent_id = v_agent_id
      AND status IN ('RUNNING', 'CANCEL_REQUESTED');

    RETURN FOUND;
END;
$$;

-- 6. Publicació atòmica de Snapshot i Oportunitats des de l'agent
CREATE OR REPLACE FUNCTION publish_session_snapshot(
    p_session_id UUID,
    p_odds_age_seconds INT,
    p_market_type VARCHAR,
    p_match_id VARCHAR,
    p_payload JSONB,
    p_opportunities JSONB
)
RETURNS UUID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_agent_id UUID;
    v_snapshot_id UUID;
    v_item JSONB;
BEGIN
    SELECT id INTO v_agent_id
    FROM daemon_agents
    WHERE agent_user_id = auth.uid() AND is_active = true;

    IF v_agent_id IS NULL THEN
        RAISE EXCEPTION 'Accés denegat: Crida no autoritzada' USING ERRCODE = '42501';
    END IF;

    -- Validar que la sessió pertany a l'agent i està en curs
    IF NOT EXISTS (
        SELECT 1 FROM surebet_sessions
        WHERE id = p_session_id
          AND claimed_by_agent_id = v_agent_id
          AND status = 'RUNNING'
    ) THEN
        RAISE EXCEPTION 'No es poden publicar snapshots a una sessió no activa o aliena'
            USING ERRCODE = '42501';
    END IF;

    -- Inserir snapshot
    INSERT INTO surebet_snapshots (
        session_id, agent_id, market_type, match_id,
        odds_age_seconds, opportunities_count, payload
    ) VALUES (
        p_session_id, v_agent_id, p_market_type, p_match_id,
        p_odds_age_seconds, COALESCE(jsonb_array_length(p_opportunities), 0), p_payload
    ) RETURNING id INTO v_snapshot_id;

    -- Desglossar oportunitats accionables si n'hi ha
    IF p_opportunities IS NOT NULL AND jsonb_typeof(p_opportunities) = 'array' THEN
        FOR v_item IN SELECT * FROM jsonb_array_elements(p_opportunities)
        LOOP
            INSERT INTO surebet_opportunities (
                snapshot_id,
                session_id,
                opportunity_type,
                matchup,
                market_key,
                net_roi_pct,
                gross_margin_pct,
                commission_deducted_pct,
                bookmakers
            ) VALUES (
                v_snapshot_id,
                p_session_id,
                v_item->>'opportunity_type',
                v_item->>'matchup',
                v_item->>'market_key',
                (v_item->>'net_roi_pct')::NUMERIC,
                (v_item->>'gross_margin_pct')::NUMERIC,
                COALESCE((v_item->>'commission_deducted_pct')::NUMERIC, 0.0),
                v_item->'bookmakers'
            );
        END LOOP;
    END IF;

    RETURN v_snapshot_id;
END;
$$;

-- Permisos estrictes per a les funcions RPC
REVOKE ALL ON FUNCTION claim_next_pending_session() FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION send_session_heartbeat(UUID) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION request_session_cancellation(UUID) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION complete_session(UUID, JSONB) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION fail_session(UUID, TEXT) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION publish_session_snapshot(UUID, INT, VARCHAR, VARCHAR, JSONB, JSONB) FROM PUBLIC, anon;

GRANT EXECUTE ON FUNCTION claim_next_pending_session() TO authenticated;
GRANT EXECUTE ON FUNCTION send_session_heartbeat(UUID) TO authenticated;
GRANT EXECUTE ON FUNCTION request_session_cancellation(UUID) TO authenticated;
GRANT EXECUTE ON FUNCTION complete_session(UUID, JSONB) TO authenticated;
GRANT EXECUTE ON FUNCTION fail_session(UUID, TEXT) TO authenticated;
GRANT EXECUTE ON FUNCTION publish_session_snapshot(UUID, INT, VARCHAR, VARCHAR, JSONB, JSONB) TO authenticated;

-- =====================================================================
-- POLÍTIQUES RLS (ROW LEVEL SECURITY)
-- =====================================================================

ALTER TABLE app_users ENABLE ROW LEVEL SECURITY;
ALTER TABLE daemon_agents ENABLE ROW LEVEL SECURITY;
ALTER TABLE surebet_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE surebet_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE surebet_opportunities ENABLE ROW LEVEL SECURITY;

-- 1. app_users
CREATE POLICY "Usuari consulta el seu propi rol"
ON app_users FOR SELECT
TO authenticated
USING (user_id = auth.uid());

-- 2. daemon_agents
CREATE POLICY "Agent o propietari consulta el seu perfil"
ON daemon_agents FOR SELECT
TO authenticated
USING (agent_user_id = auth.uid() OR owner_user_id = auth.uid());

-- 3. surebet_sessions: SELECT
CREATE POLICY "Lectura de sessions per propietari, agent assignat o admin"
ON surebet_sessions FOR SELECT
TO authenticated
USING (
    created_by = auth.uid() OR
    EXISTS (SELECT 1 FROM daemon_agents WHERE agent_user_id = auth.uid() AND id = surebet_sessions.claimed_by_agent_id) OR
    EXISTS (SELECT 1 FROM app_users WHERE user_id = auth.uid() AND role = 'admin' AND is_active = true)
);

-- 4. surebet_sessions: INSERT (Només administradors o operadors autoritzats)
CREATE POLICY "Creació de sessions reservada a operadors i administradors"
ON surebet_sessions FOR INSERT
TO authenticated
WITH CHECK (
    created_by = auth.uid() AND
    EXISTS (SELECT 1 FROM app_users WHERE user_id = auth.uid() AND role IN ('admin', 'operator') AND is_active = true)
);

-- 5. surebet_snapshots: SELECT (Només creador de la sessió, agent assignat o admin)
CREATE POLICY "Lectura de snapshots per propietaris o admin"
ON surebet_snapshots FOR SELECT
TO authenticated
USING (
    EXISTS (
        SELECT 1 FROM surebet_sessions s
        WHERE s.id = surebet_snapshots.session_id
          AND (s.created_by = auth.uid() OR EXISTS (SELECT 1 FROM app_users WHERE user_id = auth.uid() AND role = 'admin'))
    ) OR
    EXISTS (SELECT 1 FROM daemon_agents WHERE agent_user_id = auth.uid() AND id = surebet_snapshots.agent_id)
);

-- 6. surebet_opportunities: SELECT
CREATE POLICY "Lectura d'oportunitats segons rols autoritzats"
ON surebet_opportunities FOR SELECT
TO authenticated
USING (
    EXISTS (
        SELECT 1 FROM app_users
        WHERE user_id = auth.uid()
          AND is_active = true
          AND role IN ('admin', 'operator', 'viewer')
    )
);
