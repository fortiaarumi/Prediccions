-- =====================================================================
-- PREDICCIONS LLIGA — SUITE DE VALIDACIÓ INTEGRAL POST-MIGRACIÓ (001-004)
-- =====================================================================
-- PROPÒSIT I SEGURETAT DE L'SCRIPT:
-- Aquest script és exclusivament una SUITE DE VALIDACIÓ POST-MIGRACIÓ.
-- NO executa el DDL de les migracions 001-004 (ja que cadascuna conté el seu
-- propi bloc transaccional BEGIN...COMMIT i han de ser aplicades prèviament).
--
-- GARANTIES DE SEGURETAT:
-- 1. Innocuïtat: Totes les dades de prova creades en aquest script (usuaris temporals,
--    sessions de test, etc.) s'encapsulen dins d'un bloc BEGIN...ROLLBACK.
--    Quan acaba l'script, es fa ROLLBACK i no queda cap dada residual a la base.
-- 2. Privilegis estàndard: No requereix privilegis de superusuari ni creació de rols.
--    Empra els rols natius de Supabase ('anon' i 'authenticated') ja existents.
-- 3. Transparència tècnica:
--    - El test de concurrència valida la restricció atòmica de la constraint
--      'uq_surebet_single_active_session' (nivell motor SQL), no un test de càrrega paral·lel.
--    - La prova de permisos simula la identitat d'usuari amb 'request.jwt.claim.sub'
--      i 'SET LOCAL ROLE authenticated' per comprovar les polítiques RLS i les RPCs
--      'SECURITY DEFINER', sense dependre de tokens externs de GoTrue.
-- =====================================================================

BEGIN;

-- ---------------------------------------------------------------------
-- 1. VERIFICACIÓ D'EXISTÈNCIA D'ESTRUCTURES I VISTES
-- ---------------------------------------------------------------------
DO $$
BEGIN
    -- Comprovar taules fonamentals
    IF NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'app_users') THEN
        RAISE EXCEPTION '[FAIL 1.1] La taula app_users no existeix (cal aplicar migració 001).';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'surebet_sessions') THEN
        RAISE EXCEPTION '[FAIL 1.2] La taula surebet_sessions no existeix (cal aplicar migració 001).';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'api_budget_ledger') THEN
        RAISE EXCEPTION '[FAIL 1.3] La taula api_budget_ledger no existeix (cal aplicar migració 003).';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'api_search_logs') THEN
        RAISE EXCEPTION '[FAIL 1.4] La taula api_search_logs no existeix (cal aplicar migració 003).';
    END IF;

    -- Comprovar vistes
    IF NOT EXISTS (SELECT 1 FROM pg_views WHERE viewname = 'v_active_surebets') THEN
        RAISE EXCEPTION '[FAIL 1.5] La vista v_active_surebets no existeix.';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_views WHERE viewname = 'v_active_surebets_v3') THEN
        RAISE EXCEPTION '[FAIL 1.6] La vista v_active_surebets_v3 no existeix (cal aplicar migració 003).';
    END IF;

    RAISE NOTICE '[PASS 1] Totes les taules i vistes de 001 a 004 estan presents.';
END $$;

-- ---------------------------------------------------------------------
-- 2. AUDITORIA DE SEGURETAT DE LES RPCs (SECURITY DEFINER + search_path)
-- ---------------------------------------------------------------------
DO $$
DECLARE
    v_func RECORD;
    v_missing_secdef TEXT[] := '{}';
    v_missing_searchpath TEXT[] := '{}';
    v_target_funcs TEXT[] := ARRAY[
        'reconcile_api_budget',
        'create_search_session_v2',
        'claim_next_pending_session',
        'create_search_session',
        'request_session_cancellation',
        'admin_recover_stale_session'
    ];
    f TEXT;
BEGIN
    FOREACH f IN ARRAY v_target_funcs LOOP
        SELECT p.proname, p.prosecdef, p.proconfig
        INTO v_func
        FROM pg_proc p
        JOIN pg_namespace n ON p.pronamespace = n.oid
        WHERE n.nspname = 'public' AND p.proname = f
        LIMIT 1;

        IF v_func.proname IS NULL THEN
            RAISE EXCEPTION '[FAIL 2.0] La funció % no està registrada a la base de dades!', f;
        END IF;

        IF NOT v_func.prosecdef THEN
            v_missing_secdef := array_append(v_missing_secdef, f);
        END IF;

        IF v_func.proconfig IS NULL OR NOT ('search_path=public, pg_temp' = ANY(v_func.proconfig)) THEN
            v_missing_searchpath := array_append(v_missing_searchpath, f);
        END IF;
    END LOOP;

    IF array_length(v_missing_secdef, 1) > 0 THEN
        RAISE EXCEPTION '[FAIL 2.1] Funcions sense SECURITY DEFINER: %', v_missing_secdef;
    END IF;
    IF array_length(v_missing_searchpath, 1) > 0 THEN
        RAISE EXCEPTION '[FAIL 2.2] Funcions sense search_path protegit: %', v_missing_searchpath;
    END IF;

    RAISE NOTICE '[PASS 2] Auditoria de funcions RPC superada: totes 6 són SECURITY DEFINER amb search_path protegit.';
END $$;

-- ---------------------------------------------------------------------
-- 3. SETUP TEMPORAL D'USUARIS DE TEST (ES REVERTEIX AL FINAL)
-- ---------------------------------------------------------------------
DO $$
DECLARE
    v_admin_id UUID := 'a0000000-0000-4000-8000-000000000001'::UUID;
    v_oper1_id UUID := 'a0000000-0000-4000-8000-000000000002'::UUID;
    v_agent_user_id UUID := 'a0000000-0000-4000-8000-000000000004'::UUID;
    v_viewer_id UUID := 'a0000000-0000-4000-8000-000000000005'::UUID;
    v_has_auth BOOLEAN;
BEGIN
    SELECT EXISTS (
        SELECT 1 FROM information_schema.tables 
        WHERE table_schema = 'auth' AND table_name = 'users'
    ) INTO v_has_auth;

    IF v_has_auth THEN
        INSERT INTO auth.users (id, email) VALUES
            (v_admin_id, 'test_admin@prediccions.test'),
            (v_oper1_id, 'test_operador@prediccions.test'),
            (v_agent_user_id, 'test_agent@prediccions.test'),
            (v_viewer_id, 'test_viewer@prediccions.test')
        ON CONFLICT (id) DO NOTHING;
    END IF;

    INSERT INTO app_users (user_id, email, role) VALUES
        (v_admin_id, 'test_admin@prediccions.test', 'admin'),
        (v_oper1_id, 'test_operador@prediccions.test', 'operator'),
        (v_agent_user_id, 'test_agent@prediccions.test', 'agent'),
        (v_viewer_id, 'test_viewer@prediccions.test', 'viewer')
    ON CONFLICT (user_id) DO UPDATE SET role = EXCLUDED.role;

    INSERT INTO daemon_agents (agent_user_id, owner_user_id, agent_name, last_heartbeat) VALUES
        (v_agent_user_id, v_oper1_id, 'Windows-PC-Test', clock_timestamp())
    ON CONFLICT (agent_name) DO UPDATE SET is_active = true, last_heartbeat = clock_timestamp();

    RAISE NOTICE '[PASS 3] Identitats temporals de prova registrades per al test.';
END $$;

-- ---------------------------------------------------------------------
-- 4. SEGURETAT D'ACCÉS: BLOQUEIG TOTAL D'USUARI VIEWER I ANON
-- ---------------------------------------------------------------------
-- 4.1 Anon té denegat el SELECT directe a taules internes
SET LOCAL ROLE anon;
DO $$
BEGIN
    BEGIN
        PERFORM * FROM surebet_sessions;
        RAISE EXCEPTION '[FAIL 4.1] Rol anon ha pogut llegir surebet_sessions directament!';
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE NOTICE '[PASS 4.1: GRANT] Rol anon rebutjat per manca de privilegis SELECT a taules internes.';
    END;
END $$;
RESET ROLE;

-- 4.2 Viewer té bloquejada la creació de sessions a create_search_session (42501)
SET LOCAL ROLE authenticated;
SET LOCAL "request.jwt.claim.sub" = 'a0000000-0000-4000-8000-000000000005'; -- viewer
DO $$
BEGIN
    BEGIN
        PERFORM create_search_session(600);
        RAISE EXCEPTION '[FAIL 4.2] Viewer ha pogut cridar create_search_session!';
    EXCEPTION
        WHEN SQLSTATE '42501' THEN
            RAISE NOTICE '[PASS 4.2: RPC] create_search_session denega l''accés a usuaris amb rol viewer (42501).';
    END;
END $$;
RESET ROLE;

-- 4.3 Viewer té bloquejada la creació de cerques de The Odds API a create_search_session_v2 (42501)
SET LOCAL ROLE authenticated;
SET LOCAL "request.jwt.claim.sub" = 'a0000000-0000-4000-8000-000000000005'; -- viewer
DO $$
BEGIN
    BEGIN
        PERFORM create_search_session_v2(600, 'THE_ODDS_API');
        RAISE EXCEPTION '[FAIL 4.3] Viewer ha pogut iniciar una cerca de pagament a create_search_session_v2!';
    EXCEPTION
        WHEN SQLSTATE '42501' THEN
            RAISE NOTICE '[PASS 4.3: RPC] create_search_session_v2 impedeix que viewer consumeixi pressupost d''API (42501).';
    END;
END $$;
RESET ROLE;

-- 4.4 Viewer té bloquejat el desbloqueig d'emergència admin_recover_stale_session (42501)
SET LOCAL ROLE authenticated;
SET LOCAL "request.jwt.claim.sub" = 'a0000000-0000-4000-8000-000000000005'; -- viewer
DO $$
BEGIN
    BEGIN
        PERFORM admin_recover_stale_session(gen_random_uuid());
        RAISE EXCEPTION '[FAIL 4.4] Viewer ha pogut cridar admin_recover_stale_session!';
    EXCEPTION
        WHEN SQLSTATE '42501' THEN
            RAISE NOTICE '[PASS 4.4: RPC] admin_recover_stale_session reservat estrictament a administradors i operadors (42501).';
    END;
END $$;
RESET ROLE;

-- 4.5 Viewer té bloquejat l'UPDATE directe a api_budget_ledger
SET LOCAL ROLE authenticated;
SET LOCAL "request.jwt.claim.sub" = 'a0000000-0000-4000-8000-000000000005'; -- viewer
DO $$
BEGIN
    BEGIN
        UPDATE api_budget_ledger SET credits_consumed = 0;
        RAISE EXCEPTION '[FAIL 4.5] Viewer ha pogut modificar api_budget_ledger!';
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE NOTICE '[PASS 4.5: GRANT] UPDATE directe denegat a api_budget_ledger per a usuaris autenticats.';
    END;
END $$;
RESET ROLE;

-- ---------------------------------------------------------------------
-- 5. CONCURRÈNCIA ATÒMICA A NIVELL DE MOTOR (uq_surebet_single_active_session)
-- ---------------------------------------------------------------------
SET LOCAL ROLE authenticated;
SET LOCAL "request.jwt.claim.sub" = 'a0000000-0000-4000-8000-000000000002'; -- oper1
DO $$
DECLARE
    v_sess1 UUID;
    v_sess2 UUID;
BEGIN
    -- 1. Operador legítim crea la sessió 1
    v_sess1 := create_search_session_v2(600, 'WINDOWS_AGENT');
    IF v_sess1 IS NULL THEN
        RAISE EXCEPTION '[FAIL 5.1] No s''ha pogut crear la sessió 1!';
    END IF;
    RAISE NOTICE '[PASS 5.1] Sessió inicial creada correctament: %', v_sess1;

    -- 2. Intent de crear una segona sessió concurrent mentre la primera està PENDING
    BEGIN
        v_sess2 := create_search_session_v2(600, 'WINDOWS_AGENT');
        RAISE EXCEPTION '[FAIL 5.2] S''ha permès crear dues sessions concurrents!';
    EXCEPTION
        WHEN SQLSTATE '23505' THEN
            RAISE NOTICE '[PASS 5.2: CONSTRAINT] Restricció de concurrència atòmica comprovada (error 23505 uq_surebet_single_active_session).';
    END;
END $$;
RESET ROLE;

-- ---------------------------------------------------------------------
-- 6. RECUPERACIÓ AUTOMÀTICA DE SESSIONS ZOMBIS I DESBLOQUEIG DE DEADLOCK
-- ---------------------------------------------------------------------
SET LOCAL ROLE authenticated;
SET LOCAL "request.jwt.claim.sub" = 'a0000000-0000-4000-8000-000000000002'; -- oper1
DO $$
DECLARE
    v_zombie_id UUID;
    v_new_sess UUID;
BEGIN
    -- Simular que la sessió anterior va quedar abandonada en RUNNING fa >90s (Ctrl+C / tall de connexió)
    SELECT id INTO v_zombie_id FROM surebet_sessions WHERE status = 'PENDING' LIMIT 1;
    UPDATE surebet_sessions
    SET status = 'RUNNING',
        claimed_at = clock_timestamp() - INTERVAL '5 minutes',
        heartbeat_at = clock_timestamp() - INTERVAL '5 minutes',
        expires_at = clock_timestamp() - INTERVAL '1 minute'
    WHERE id = v_zombie_id;

    -- Comprovar que la següent petició de cerca neteja automàticament la sessió caducada
    v_new_sess := create_search_session_v2(600, 'WINDOWS_AGENT');
    IF v_new_sess IS NULL THEN
        RAISE EXCEPTION '[FAIL 6.1] La sessió zombi no s''ha netejat i ha bloquejat la nova cerca!';
    END IF;

    -- Verificar que la sessió anterior ha passat a TIMEOUT
    IF NOT EXISTS (
        SELECT 1 FROM surebet_sessions 
        WHERE id = v_zombie_id AND status = 'TIMEOUT'
    ) THEN
        RAISE EXCEPTION '[FAIL 6.2] La sessió caducada no ha passat a TIMEOUT!';
    END IF;

    RAISE NOTICE '[PASS 6] Neteja automàtica de sessió zombi verificada: sessió anterior passada a TIMEOUT i nova sessió % activada.', v_new_sess;
END $$;
RESET ROLE;

-- ---------------------------------------------------------------------
-- 7. CONTROL DE PRESSUPOST DE THE ODDS API I RECONCILIACIÓ
-- ---------------------------------------------------------------------
DO $$
DECLARE
    v_res JSONB;
    v_curr_period VARCHAR(7) := to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM');
BEGIN
    -- Reconciliar 15 crèdits
    v_res := reconcile_api_budget('THE_ODDS_API', v_curr_period, 15, 435, 15, 1);
    IF (v_res->>'credits_consumed')::INT < 15 THEN
        RAISE EXCEPTION '[FAIL 7.1] Reconciliació no ha sumat el consum: %', v_res;
    END IF;

    IF (v_res->>'safety_reserve')::INT != 50 THEN
        RAISE EXCEPTION '[FAIL 7.2] Reserva de seguretat alterada: %', v_res;
    END IF;

    RAISE NOTICE '[PASS 7] Reconciliació de pressupost verificada: consum = % crèdits, reserva intocable de 50 crèdits protegida.', v_res->>'credits_consumed';
END $$;

-- ---------------------------------------------------------------------
-- 8. COMPROVACIÓ IDEMPOTENT DE LA SESSIÓ da6fa2de
-- ---------------------------------------------------------------------
DO $$
DECLARE
    v_sess_id UUID := 'da6fa2de-3742-4faf-b9aa-bff8fd2b1455'::UUID;
BEGIN
    -- Simular la sessió existent en estat TIMEOUT (com està a producció)
    INSERT INTO surebet_sessions (
        id, created_by, duration_seconds, status, created_at, updated_at
    ) VALUES (
        v_sess_id,
        'a0000000-0000-4000-8000-000000000002'::UUID,
        600,
        'TIMEOUT',
        clock_timestamp() - INTERVAL '1 day',
        clock_timestamp() - INTERVAL '1 day'
    )
    ON CONFLICT (id) DO UPDATE SET status = 'TIMEOUT';

    -- Executar la comanda exacta de la migració 004
    UPDATE surebet_sessions
    SET status = 'TIMEOUT',
        error_message = 'Sessió recuperada: abandonada per aturada de l''agent (Ctrl+C / Timeout)',
        updated_at = clock_timestamp()
    WHERE id = v_sess_id
      AND status IN ('RUNNING', 'CANCEL_REQUESTED', 'PENDING');

    -- Verificar que continua en TIMEOUT i que no s'ha sobreescrit erròniament
    IF NOT EXISTS (SELECT 1 FROM surebet_sessions WHERE id = v_sess_id AND status = 'TIMEOUT') THEN
        RAISE EXCEPTION '[FAIL 8] Estat de la sessió da6fa2de alterat incorrectament!';
    END IF;

    RAISE NOTICE '[PASS 8] Preservació idempotent de la sessió da6fa2de verificada (0 files afectades indegudament).';
END $$;

RAISE NOTICE '=====================================================================';
RAISE NOTICE '🏆 TOTS ELS TESTS DE VALIDACIÓ INTEGRAL HAN ESTAT SUPERATS AMB ÈXIT!';
RAISE NOTICE '=====================================================================';

-- Finalitzar amb ROLLBACK per assegurar que cap dada de prova persisteix a la base
ROLLBACK;
