-- =====================================================================
-- MIGRACIÓ 003: THE ODDS API MULTITENANT I CONTROL DE PRESSUPOST
-- =====================================================================
-- Aquest script estén l'esquema de Surebet Monitor (001 i 002) per afegir:
-- 1. Control transaccional centralitzat de pressupost de l'API (500 crèdits/mes, 50 reserva).
-- 2. Compatibilitat dual: suport complet per a crides al núvol ('THE_ODDS_API')
--    sense requerir l'Agent Windows actiu, mentre es preserva al 100% el canal
--    existent de l'Agent Windows ('WINDOWS_AGENT').
-- 3. Traçabilitat de procedència (jurisdicció espanyola DGOJ vs. internacional teòrica).
-- 4. Reconciliació automàtica amb les capçaleres del proveïdor (x-requests-remaining/used).
-- 5. Polítiques RLS estrictes: Mínim Privilegi per a usuaris autenticats.
-- =====================================================================

BEGIN;

-- 1. TAULA DE CONTROL DE PRESSUPOST (LEDGER) DE L'API EXTERNA
CREATE TABLE IF NOT EXISTS api_budget_ledger (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider VARCHAR(50) NOT NULL DEFAULT 'THE_ODDS_API',
    period_month VARCHAR(7) NOT NULL, -- Format 'YYYY-MM' (ex: '2026-11')
    monthly_allowance INT NOT NULL DEFAULT 500,
    safety_reserve INT NOT NULL DEFAULT 50, -- Reserva intocable de seguretat
    operational_limit INT NOT NULL DEFAULT 450,
    credits_consumed INT NOT NULL DEFAULT 0,
    api_reported_remaining INT, -- Sincronitzat des de x-requests-remaining
    api_reported_used INT,      -- Sincronitzat des de x-requests-used
    api_reported_last_cost INT, -- Sincronitzat des de x-requests-last
    last_synced_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_api_budget_period UNIQUE (provider, period_month)
);

-- Inserir registre inicial per al mes vigent si no existeix
INSERT INTO api_budget_ledger (
    provider,
    period_month,
    monthly_allowance,
    safety_reserve,
    operational_limit,
    credits_consumed
) VALUES (
    'THE_ODDS_API',
    to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM'),
    500,
    50,
    450,
    0
)
ON CONFLICT (provider, period_month) DO NOTHING;

-- 2. AMPLIACIÓ DE SESSIONS DE CERCA (surebet_sessions)
ALTER TABLE surebet_sessions 
ADD COLUMN IF NOT EXISTS source_type VARCHAR(30) NOT NULL DEFAULT 'WINDOWS_AGENT'
CHECK (source_type IN ('WINDOWS_AGENT', 'THE_ODDS_API', 'HYBRID'));

ALTER TABLE surebet_sessions 
ADD COLUMN IF NOT EXISTS credits_spent INT NOT NULL DEFAULT 0;

ALTER TABLE surebet_sessions 
ADD COLUMN IF NOT EXISTS events_count INT NOT NULL DEFAULT 0;

ALTER TABLE surebet_sessions 
ADD COLUMN IF NOT EXISTS active_leagues JSONB NOT NULL DEFAULT '[]'::jsonb;

-- 3. AMPLIACIÓ DE SNAPSHOTS I OPORTUNITATS
ALTER TABLE surebet_snapshots 
ADD COLUMN IF NOT EXISTS source_pipeline VARCHAR(30) NOT NULL DEFAULT 'WINDOWS_AGENT';

ALTER TABLE surebet_snapshots 
ADD COLUMN IF NOT EXISTS jurisdiction VARCHAR(20) NOT NULL DEFAULT 'ES';

ALTER TABLE surebet_opportunities 
ADD COLUMN IF NOT EXISTS source_pipeline VARCHAR(30) NOT NULL DEFAULT 'WINDOWS_AGENT';

ALTER TABLE surebet_opportunities 
ADD COLUMN IF NOT EXISTS is_theoretical BOOLEAN NOT NULL DEFAULT false;

ALTER TABLE surebet_opportunities 
ADD COLUMN IF NOT EXISTS executable_in_spain BOOLEAN NOT NULL DEFAULT true;

-- 4. TAULA DE REGISTRE DETALLAT DE CERQUES COMPARTIDES
CREATE TABLE IF NOT EXISTS api_search_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID REFERENCES surebet_sessions(id) ON DELETE CASCADE,
    user_id UUID REFERENCES app_users(user_id) ON DELETE SET NULL,
    source_pipeline VARCHAR(30) NOT NULL DEFAULT 'THE_ODDS_API',
    credits_spent INT NOT NULL DEFAULT 0,
    active_leagues JSONB NOT NULL DEFAULT '[]'::jsonb,
    empty_leagues JSONB NOT NULL DEFAULT '[]'::jsonb,
    matches_examined INT NOT NULL DEFAULT 0,
    opportunities_found INT NOT NULL DEFAULT 0,
    coverage_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_api_search_logs_created 
ON api_search_logs (created_at DESC);

-- 5. FUNCIÓ RPC: RECONCILIACIÓ DEL PRESSUPOST AMB CAPÇALERES REALS
CREATE OR REPLACE FUNCTION reconcile_api_budget(
    p_provider VARCHAR,
    p_period VARCHAR,
    p_credits_spent INT,
    p_api_remaining INT DEFAULT NULL,
    p_api_used INT DEFAULT NULL,
    p_last_cost INT DEFAULT NULL
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_record RECORD;
BEGIN
    INSERT INTO api_budget_ledger (
        provider, period_month, monthly_allowance, safety_reserve, operational_limit, credits_consumed
    ) VALUES (
        p_provider, p_period, 500, 50, 450, GREATEST(0, p_credits_spent)
    )
    ON CONFLICT (provider, period_month) DO UPDATE
    SET 
        credits_consumed = api_budget_ledger.credits_consumed + GREATEST(0, p_credits_spent),
        api_reported_remaining = COALESCE(p_api_remaining, api_budget_ledger.api_reported_remaining),
        api_reported_used = COALESCE(p_api_used, api_budget_ledger.api_reported_used),
        api_reported_last_cost = COALESCE(p_last_cost, api_budget_ledger.api_reported_last_cost),
        last_synced_at = clock_timestamp()
    RETURNING * INTO v_record;

    RETURN jsonb_build_object(
        'provider', v_record.provider,
        'period_month', v_record.period_month,
        'credits_consumed', v_record.credits_consumed,
        'api_reported_remaining', v_record.api_reported_remaining,
        'api_reported_used', v_record.api_reported_used,
        'operational_limit', v_record.operational_limit,
        'safety_reserve', v_record.safety_reserve,
        'remaining_operational', GREATEST(0, COALESCE(v_record.api_reported_remaining, v_record.operational_limit - v_record.credits_consumed) - v_record.safety_reserve)
    );
END;
$$;

-- 6. FUNCIÓ RPC FLEXIBLE PER CREAR SESSIONS (Suporta WINDOWS_AGENT i THE_ODDS_API)
CREATE OR REPLACE FUNCTION create_search_session_v2(
    p_duration_seconds INT DEFAULT 600,
    p_source_type VARCHAR DEFAULT 'WINDOWS_AGENT'
)
RETURNS UUID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_user_role VARCHAR(20);
    v_session_id UUID;
    v_active_agent_exists BOOLEAN;
    v_current_period VARCHAR(7);
    v_budget_record RECORD;
    v_avail_credits INT;
BEGIN
    -- Validació d'identitat
    SELECT role INTO v_user_role
    FROM app_users
    WHERE user_id = auth.uid()
      AND is_active = true;

    IF v_user_role IS NULL OR v_user_role NOT IN ('admin', 'operator') THEN
        RAISE EXCEPTION 'Accés denegat: la creació de sessions està reservada a operadors i administradors'
            USING ERRCODE = '42501';
    END IF;

    -- Validació de durada (1 a 30 minuts)
    IF p_duration_seconds IS NULL OR p_duration_seconds < 60 OR p_duration_seconds > 1800 THEN
        RAISE EXCEPTION 'La durada ha d''estar entre 60 i 1800 segons' USING ERRCODE = '22000';
    END IF;

    -- Neteja preventiva de sessions zombis o expirades abans de comprovar o inserir
    UPDATE surebet_sessions
    SET status = 'TIMEOUT',
        error_message = 'Sessió tancada automàticament per timeout abans d''iniciar una nova cerca',
        updated_at = clock_timestamp()
    WHERE status IN ('RUNNING', 'CANCEL_REQUESTED')
      AND (clock_timestamp() > expires_at OR clock_timestamp() - COALESCE(heartbeat_at, claimed_at) > INTERVAL '90 seconds');

    -- Neteja de sessions PENDING abandonades fa més de 10 minuts
    UPDATE surebet_sessions
    SET status = 'CANCELLED',
        error_message = 'Sessió en cua cancel·lada automàticament per manca d''agent disponible',
        updated_at = clock_timestamp()
    WHERE status = 'PENDING'
      AND clock_timestamp() - created_at > INTERVAL '600 seconds';

    -- Bifurcació segons origen
    IF p_source_type = 'WINDOWS_AGENT' THEN
        -- Comprovació de l'agent Windows viu (batec <= 120s)
        SELECT EXISTS (
            SELECT 1 FROM daemon_agents da
            WHERE da.is_active = true
              AND da.last_heartbeat IS NOT NULL
              AND da.last_heartbeat >= (clock_timestamp() - INTERVAL '120 seconds')
              AND (v_user_role = 'admin' OR da.owner_user_id = auth.uid())
        ) INTO v_active_agent_exists;

        IF NOT v_active_agent_exists THEN
            RAISE EXCEPTION 'No hi ha cap agent Windows actiu i connectat (heartbeat <= 120s)'
                USING ERRCODE = 'P0002';
        END IF;

    ELSIF p_source_type = 'THE_ODDS_API' THEN
        -- Comprovació del pressupost restant de l'API
        v_current_period := to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM');
        
        SELECT * INTO v_budget_record
        FROM api_budget_ledger
        WHERE provider = 'THE_ODDS_API' AND period_month = v_current_period;

        IF v_budget_record IS NOT NULL THEN
            v_avail_credits := GREATEST(0, COALESCE(v_budget_record.api_reported_remaining, v_budget_record.operational_limit - v_budget_record.credits_consumed) - v_budget_record.safety_reserve);
            IF v_avail_credits < 1 THEN
                RAISE EXCEPTION 'Pressupost de The Odds API exhaurit per al cicle actual. Reserva de 50 crèdits protegida.'
                    USING ERRCODE = 'P0003';
            END IF;
        END IF;
    ELSE
        RAISE EXCEPTION 'Origen de sessió invàlid: %', p_source_type USING ERRCODE = '22000';
    END IF;

    -- Creació atòmica de la sessió
    BEGIN
        v_session_id := gen_random_uuid();
        INSERT INTO surebet_sessions (
            id,
            created_by,
            duration_seconds,
            status,
            source_type,
            created_at,
            updated_at
        ) VALUES (
            v_session_id,
            auth.uid(),
            p_duration_seconds,
            'PENDING',
            p_source_type,
            clock_timestamp(),
            clock_timestamp()
        );
    EXCEPTION
        WHEN unique_violation THEN
            RAISE EXCEPTION 'Ja existeix una sessió activa en curs (concurrència bloquejada)'
                USING ERRCODE = '23505';
    END;

    RETURN v_session_id;
END;
$$;

-- 7. VISTA MILLORADA PER AL PANEL WEB AMB TRAÇABILITAT (v_active_surebets_v3)
-- Conserva compatibilitat amb v_active_surebets i n'afegeix de noves
CREATE OR REPLACE VIEW v_active_surebets_v3
WITH (security_invoker = on) AS
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
    EXTRACT(EPOCH FROM (now() - o.detected_at))::INT AS age_seconds,
    o.source_pipeline,
    o.is_theoretical,
    o.executable_in_spain
FROM surebet_opportunities o
WHERE o.detected_at >= (now() - INTERVAL '30 minutes')
  AND o.is_stale = false
  AND o.is_synthetic = false
ORDER BY o.net_roi_pct DESC;

-- 8. POLÍTIQUES RLS PER A LES NOVES TAULES
ALTER TABLE api_budget_ledger ENABLE ROW LEVEL SECURITY;
ALTER TABLE api_search_logs ENABLE ROW LEVEL SECURITY;

-- Usuaris autenticats poden llegir el pressupost restant per mostrar-lo al web
DROP POLICY IF EXISTS p_budget_read_auth ON api_budget_ledger;
CREATE POLICY p_budget_read_auth ON api_budget_ledger
FOR SELECT TO authenticated
USING (true);

-- Usuaris autenticats poden consultar l'historial global de cerques
DROP POLICY IF EXISTS p_search_logs_read_auth ON api_search_logs;
CREATE POLICY p_search_logs_read_auth ON api_search_logs
FOR SELECT TO authenticated
USING (true);

-- Concessions de permisos
GRANT SELECT ON api_budget_ledger TO authenticated;
GRANT SELECT ON api_search_logs TO authenticated;
GRANT EXECUTE ON FUNCTION reconcile_api_budget TO authenticated;
GRANT EXECUTE ON FUNCTION create_search_session_v2 TO authenticated;
GRANT SELECT ON v_active_surebets_v3 TO authenticated;

COMMIT;
