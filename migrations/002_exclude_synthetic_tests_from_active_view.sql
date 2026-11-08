-- =====================================================================
-- Migració 002: Exclusió estricta de dades sintètiques de prova a v_active_surebets
-- =====================================================================
-- Descripció:
-- Garanteix que cap oportunitat sintètica o de simulació (amb patró 'TEST' o
-- 'SIMULAC' a matchup o provinent de sessions de prova) no aparegui a la vista
-- pública / operadora d'oportunitats actives v_active_surebets a nivell de PostgreSQL.
-- Manté inalterades les dades reals de partits i quotes històriques.
-- =====================================================================

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
    o.detected_at,
    EXTRACT(EPOCH FROM (now() - o.detected_at))::INT AS age_seconds
FROM surebet_opportunities o
WHERE o.detected_at >= (now() - INTERVAL '30 minutes')
  AND o.is_stale = false
  -- Filtratge estricte a nivell de PostgreSQL per excloure dades de prova sintètiques
  AND o.matchup NOT ILIKE '%TEST%'
  AND o.matchup NOT ILIKE '%SIMULAC%'
  AND o.market_key NOT ILIKE '%TEST%'
ORDER BY o.net_roi_pct DESC;

COMMENT ON VIEW v_active_surebets IS 'Oportunitats d''arbitratge reals i fresques (<30 min), excloent estrictament les simulacions sintètiques de prova.';
