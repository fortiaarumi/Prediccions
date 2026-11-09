-- =====================================================================
-- MIGRACIÓ 004: CORRECCIÓ DEL CICLE DE VIDA, NETEJA DE SESSIONS ZOMBIS I DESBLOQUEIG
-- =====================================================================
-- Objectius:
-- 1. Resoldre el deadlock en claim_next_pending_session: la neteja de sessions zombis
--    s'ha d'executar SEMPRE ABANS de comprovar si l'agent té feina assignada.
-- 2. Afegir neteja preventiva de sessions expirades a create_search_session i create_search_session_v2
--    per evitar que sessions caducades bloquegin noves cerques mitjançant uq_surebet_single_active_session.
-- 3. Millorar request_session_cancellation perquè si l'agent fa més de 90s que no respon,
--    la sessió passi directament a CANCELLED (alliberant el bloqueig a l'instant).
-- 4. Funció RPC administrativa d'emergència: recover_stale_session(p_session_id).
-- 5. Instrucció segura i idempotent per desbloquejar la sessió da6fa2de-3742-4faf-b9aa-bff8fd2b1455.
-- =====================================================================

BEGIN;

-- ---------------------------------------------------------------------
-- 0. DESBLOQUEIG ESPECÍFIC DE LA SESSIÓ AFECTADA (IDEMPOTENT I SEGUR)
-- ---------------------------------------------------------------------
UPDATE surebet_sessions
SET status = 'TIMEOUT',
    error_message = 'Sessió recuperada: abandonada per aturada de l''agent (Ctrl+C / Timeout)',
    updated_at = clock_timestamp()
WHERE id = 'da6fa2de-3742-4faf-b9aa-bff8fd2b1455'
  AND status IN ('RUNNING', 'CANCEL_REQUESTED', 'PENDING');

-- ---------------------------------------------------------------------
-- 1. CORRECCIÓ DE claim_next_pending_session (Neteja preventiva abans del check)
-- ---------------------------------------------------------------------
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
    v_user_role VARCHAR(20);
    v_agent_id UUID;
    v_session_id UUID;
    v_duration INT;
    v_now TIMESTAMPTZ := clock_timestamp();
BEGIN
    -- 1. Validació estricta d'identitat
    SELECT role INTO v_user_role
    FROM app_users
    WHERE user_id = auth.uid()
      AND is_active = true;

    IF v_user_role IS NULL OR v_user_role != 'agent' THEN
        RAISE EXCEPTION 'Accés denegat: la reclamació de tasques està reservada exclusivament a usuaris amb rol agent actiu'
            USING ERRCODE = '42501';
    END IF;

    -- 2. Derivació de l'agent autoritzat
    SELECT id INTO v_agent_id
    FROM daemon_agents
    WHERE agent_user_id = auth.uid()
      AND is_active = true;

    IF v_agent_id IS NULL THEN
        RAISE EXCEPTION 'Accés denegat: L''agent associat no existeix, està inactiu o ha estat revocat'
            USING ERRCODE = '42501';
    END IF;

    -- 3. Actualitzar el darrer batec de l'agent
    UPDATE daemon_agents
    SET last_heartbeat = v_now
    WHERE id = v_agent_id
      AND agent_user_id = auth.uid();

    -- 4. [CRÍTIC]: NETEJA AUTOMÀTICA DE SESSIONS ZOMBIS EXECUTADA PRIMER!
    -- Si l'agent s'havia apagat amb Ctrl+C o tall de llum, la seva sessió anterior
    -- expirada o sense batec (>90s) es tanca aquí mateix, alliberant el bloqueig.
    UPDATE surebet_sessions
    SET status = 'TIMEOUT',
        error_message = 'Sessió avortada automàticament: batec de cor perdut o temps expirat',
        updated_at = v_now
    WHERE status IN ('RUNNING', 'CANCEL_REQUESTED')
      AND (v_now > expires_at OR v_now - COALESCE(heartbeat_at, claimed_at) > INTERVAL '90 seconds');

    -- 5. Comprovar si l'agent encara té alguna sessió REALMENT activa (viva i amb batec recent)
    IF EXISTS (
        SELECT 1 FROM surebet_sessions
        WHERE claimed_by_agent_id = v_agent_id
          AND status IN ('RUNNING', 'CANCEL_REQUESTED')
    ) THEN
        RETURN; -- Té una feina legítimament viva en curs
    END IF;

    -- 6. Reclamació atòmica FIFO
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

-- ---------------------------------------------------------------------
-- 2. CORRECCIÓ DE create_search_session (Neteja preventiva abans d'inserir)
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION create_search_session(p_duration_seconds INT DEFAULT 600)
RETURNS UUID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_user_role VARCHAR(20);
    v_session_id UUID;
    v_active_agent_exists BOOLEAN;
    v_now TIMESTAMPTZ := clock_timestamp();
BEGIN
    SELECT role INTO v_user_role
    FROM app_users
    WHERE user_id = auth.uid()
      AND is_active = true;

    IF v_user_role IS NULL OR v_user_role NOT IN ('admin', 'operator') THEN
        RAISE EXCEPTION 'Accés denegat: la creació de sessions està reservada a operadors i administradors actius'
            USING ERRCODE = '42501';
    END IF;

    IF p_duration_seconds IS NULL OR p_duration_seconds < 60 OR p_duration_seconds > 1800 THEN
        RAISE EXCEPTION 'La durada de la sessió ha d''estar compresa entre 60 i 1800 segons'
            USING ERRCODE = '22000';
    END IF;

    -- [CRÍTIC]: Neteja preventiva de sessions zombis o expirades abans de comprovar o inserir
    UPDATE surebet_sessions
    SET status = 'TIMEOUT',
        error_message = 'Sessió tancada automàticament per timeout abans d''iniciar una nova cerca',
        updated_at = v_now
    WHERE status IN ('RUNNING', 'CANCEL_REQUESTED')
      AND (v_now > expires_at OR v_now - COALESCE(heartbeat_at, claimed_at) > INTERVAL '90 seconds');

    -- Neteja de sessions PENDING abandonades fa més de 10 minuts
    UPDATE surebet_sessions
    SET status = 'CANCELLED',
        error_message = 'Sessió en cua cancel·lada automàticament per manca d''agent disponible',
        updated_at = v_now
    WHERE status = 'PENDING'
      AND v_now - created_at > INTERVAL '600 seconds';

    -- Comprovació d'agent Windows connectat recentment
    SELECT EXISTS (
        SELECT 1 FROM daemon_agents da
        WHERE da.is_active = true
          AND da.last_heartbeat IS NOT NULL
          AND da.last_heartbeat >= (v_now - INTERVAL '120 seconds')
          AND (v_user_role = 'admin' OR da.owner_user_id = auth.uid())
    ) INTO v_active_agent_exists;

    IF NOT v_active_agent_exists THEN
        RAISE EXCEPTION 'No hi ha cap agent Windows actiu, autoritzat i connectat recentment (heartbeat <= 120s)'
            USING ERRCODE = 'P0002';
    END IF;

    -- Inserció atòmica
    BEGIN
        v_session_id := gen_random_uuid();
        INSERT INTO surebet_sessions (
            id,
            created_by,
            duration_seconds,
            status,
            created_at,
            updated_at
        ) VALUES (
            v_session_id,
            auth.uid(),
            p_duration_seconds,
            'PENDING',
            v_now,
            v_now
        );
    EXCEPTION
        WHEN unique_violation THEN
            RAISE EXCEPTION 'Ja existeix una sessió de cerca activa o en cua (concurrència bloquejada a nivell atòmic)'
                USING ERRCODE = '23505';
    END;

    RETURN v_session_id;
END;
$$;

-- ---------------------------------------------------------------------
-- 3. CORRECCIÓ DE request_session_cancellation (Cancel·lació directa si agent mort)
-- ---------------------------------------------------------------------
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
    v_claimed_at TIMESTAMPTZ;
    v_heartbeat_at TIMESTAMPTZ;
    v_now TIMESTAMPTZ := clock_timestamp();
BEGIN
    SELECT role INTO v_role
    FROM app_users
    WHERE user_id = auth.uid() AND is_active = true;

    SELECT created_by, status, claimed_at, heartbeat_at
    INTO v_created_by, v_status, v_claimed_at, v_heartbeat_at
    FROM surebet_sessions
    WHERE id = p_session_id;

    IF v_status IS NULL THEN
        RETURN false;
    END IF;

    IF v_role <> 'admin' AND v_created_by <> auth.uid() THEN
        RAISE EXCEPTION 'Accés denegat: no podeu cancel·lar sessions alienes' USING ERRCODE = '42501';
    END IF;

    IF v_status = 'PENDING' THEN
        UPDATE surebet_sessions
        SET status = 'CANCELLED', updated_at = v_now
        WHERE id = p_session_id;
        RETURN true;

    ELSIF v_status = 'RUNNING' THEN
        -- Si l'agent està desconnectat o no envia batec des de fa >90s, passar directament a CANCELLED
        -- Això evita que la sessió quedi encallada en CANCEL_REQUESTED si l'agent ha mort.
        IF v_now - COALESCE(v_heartbeat_at, v_claimed_at) > INTERVAL '90 seconds' THEN
            UPDATE surebet_sessions
            SET status = 'CANCELLED',
                error_message = 'Sessió cancel·lada immediatament pel servidor (l''agent Windows està desconnectat)',
                updated_at = v_now
            WHERE id = p_session_id;
            RETURN true;
        ELSE
            UPDATE surebet_sessions
            SET status = 'CANCEL_REQUESTED', updated_at = v_now
            WHERE id = p_session_id;
            RETURN true;
        END IF;

    ELSIF v_status = 'CANCEL_REQUESTED' THEN
        -- Si ja s'havia demanat cancel·lació i l'agent no respon en 60s, forçar CANCELLED
        UPDATE surebet_sessions
        SET status = 'CANCELLED',
            error_message = 'Cancel·lació forçada: l''agent no ha confirmat la parada cooperativa',
            updated_at = v_now
        WHERE id = p_session_id;
        RETURN true;

    ELSE
        -- Ja és terminal (COMPLETED, CANCELLED, FAILED, TIMEOUT)
        RETURN false;
    END IF;
END;
$$;

-- ---------------------------------------------------------------------
-- 4. RPC D'EMERGÈNCIA: DESBLOQUEIG ADMINISTRATIU DE SESSIONS ENCROSTADES
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION admin_recover_stale_session(p_session_id UUID)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_role VARCHAR(20);
    v_status VARCHAR(20);
BEGIN
    SELECT role INTO v_role
    FROM app_users
    WHERE user_id = auth.uid() AND is_active = true;

    IF v_role NOT IN ('admin', 'operator') THEN
        RAISE EXCEPTION 'Accés denegat: només operadors o administradors poden recuperar sessions encrostades'
            USING ERRCODE = '42501';
    END IF;

    SELECT status INTO v_status
    FROM surebet_sessions
    WHERE id = p_session_id;

    IF v_status IS NULL OR v_status IN ('COMPLETED', 'CANCELLED', 'FAILED', 'TIMEOUT') THEN
        RETURN false; -- Ja és terminal o no existeix
    END IF;

    UPDATE surebet_sessions
    SET status = 'TIMEOUT',
        error_message = 'Sessió recuperada manualment per l''operador (Timeout administratiu forçat)',
        updated_at = clock_timestamp()
    WHERE id = p_session_id;

    RETURN true;
END;
$$;

GRANT EXECUTE ON FUNCTION admin_recover_stale_session TO authenticated;

COMMIT;
