-- =====================================================================
-- Migració 002: Separació Estructurada de Simulacions i Exclusió a v_active_surebets
-- =====================================================================
-- Objectiu:
-- 1. Afegir la columna 'is_synthetic BOOLEAN' a surebet_opportunities i surebet_snapshots
--    per identificar de manera robusta dades de simulació/prova sense dependre de text.
-- 2. Marcar les dades de simulació existents com a is_synthetic = true.
-- 3. Actualitzar publish_session_snapshot per persistir 'is_synthetic' directament.
-- 4. Actualitzar la vista v_active_surebets filtrant per 'o.is_synthetic = false'
--    mantenint el filtre de text com a segona línia de defensa per compatibilitat.
-- =====================================================================

-- 1. Afegir columnes estructurades (idempotent)
ALTER TABLE surebet_opportunities 
ADD COLUMN IF NOT EXISTS is_synthetic BOOLEAN NOT NULL DEFAULT false;

ALTER TABLE surebet_snapshots 
ADD COLUMN IF NOT EXISTS is_synthetic BOOLEAN NOT NULL DEFAULT false;

-- 2. Indexació per accelerar consultes d'oportunitats reals no sintètiques
CREATE INDEX IF NOT EXISTS idx_surebet_opps_real_active 
ON surebet_opportunities (detected_at DESC, net_roi_pct DESC) 
WHERE is_stale = false AND is_synthetic = false;

-- 3. Actualitzar dades històriques de prova existents
UPDATE surebet_opportunities 
SET is_synthetic = true 
WHERE matchup ILIKE '%TEST%' 
   OR matchup ILIKE '%SIMULAC%' 
   OR market_key ILIKE '%TEST%';

UPDATE surebet_snapshots 
SET is_synthetic = true 
WHERE match_id ILIKE '%TEST%' 
   OR payload->>'is_synthetic_test' = 'true'
   OR payload->>'is_synthetic' = 'true';

-- 4. Actualització de la funció RPC publish_session_snapshot (signatura idèntica de 7 paràmetres)
CREATE OR REPLACE FUNCTION publish_session_snapshot(
    p_session_id UUID,
    p_odds_age_seconds INT,
    p_market_type VARCHAR,
    p_match_id VARCHAR,
    p_payload JSONB,
    p_opportunities JSONB,
    p_snapshot_id UUID DEFAULT NULL
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
    v_is_synthetic BOOLEAN;
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

    -- Determinar si el snapshot conté dades sintètiques de simulació
    v_is_synthetic := COALESCE(
        (p_payload->>'is_synthetic')::BOOLEAN,
        (p_payload->>'is_synthetic_test')::BOOLEAN,
        false
    );

    v_snapshot_id := COALESCE(p_snapshot_id, gen_random_uuid());

    -- Inserir snapshot amb el camp estructurat is_synthetic
    INSERT INTO surebet_snapshots (
        id, session_id, agent_id, market_type, match_id,
        odds_age_seconds, opportunities_count, payload, is_synthetic
    ) VALUES (
        v_snapshot_id, p_session_id, v_agent_id, p_market_type, p_match_id,
        p_odds_age_seconds, COALESCE(jsonb_array_length(p_opportunities), 0), p_payload, v_is_synthetic
    )
    ON CONFLICT (id) DO UPDATE
    SET odds_age_seconds = EXCLUDED.odds_age_seconds,
        payload = EXCLUDED.payload,
        is_synthetic = EXCLUDED.is_synthetic;

    -- Desglossar oportunitats accionables
    DELETE FROM surebet_opportunities WHERE snapshot_id = v_snapshot_id;

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
                bookmakers,
                is_synthetic
            ) VALUES (
                v_snapshot_id,
                p_session_id,
                v_item->>'opportunity_type',
                v_item->>'matchup',
                v_item->>'market_key',
                (v_item->>'net_roi_pct')::NUMERIC,
                (v_item->>'gross_margin_pct')::NUMERIC,
                COALESCE((v_item->>'commission_deducted_pct')::NUMERIC, 0.0),
                v_item->'bookmakers',
                COALESCE(
                    (v_item->>'is_synthetic')::BOOLEAN,
                    v_is_synthetic,
                    false
                )
            );
        END LOOP;
    END IF;

    RETURN v_snapshot_id;
END;
$$;

-- 5. Actualització de la vista v_active_surebets amb filtratge estructurat per is_synthetic
CREATE OR REPLACE VIEW v_active_surebets
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
    o.is_synthetic,
    o.detected_at,
    EXTRACT(EPOCH FROM (now() - o.detected_at))::INT AS age_seconds
FROM surebet_opportunities o
WHERE o.detected_at >= (now() - INTERVAL '30 minutes')
  AND o.is_stale = false
  -- Filtratge estructurat per camp booleà
  AND o.is_synthetic = false
  -- Doble protecció de seguretat per text
  AND o.matchup NOT ILIKE '%TEST%'
  AND o.matchup NOT ILIKE '%SIMULAC%'
  AND o.market_key NOT ILIKE '%TEST%'
ORDER BY o.net_roi_pct DESC;

COMMENT ON VIEW v_active_surebets IS 'Oportunitats d''arbitratge reals i fresques (<30 min), excloent estrictament les simulacions sintètiques de prova via camp booleà is_synthetic.';

GRANT SELECT ON v_active_surebets TO authenticated, anon;
