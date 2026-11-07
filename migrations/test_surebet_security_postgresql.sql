-- =====================================================================
-- BATERIA DE PROVES NEGATIVES I DE SEGURETAT PER A POSTGRESQL (Supabase)
-- =====================================================================
-- Aquest script està dissenyat per executar-se directament sobre una instància
-- real de PostgreSQL / Supabase per verificar les polítiques RLS, els triggers
-- d'integritat i les funcions RPC amb canvis d'identitat (SET LOCAL ROLE).
-- =====================================================================

BEGIN;

-- Setup: Crear usuaris i agents de prova si no existeixen
DO $$
DECLARE
    v_admin_id UUID := 'a0000000-0000-4000-8000-000000000001'::UUID;
    v_oper1_id UUID := 'a0000000-0000-4000-8000-000000000002'::UUID;
    v_oper2_id UUID := 'a0000000-0000-4000-8000-000000000003'::UUID;
    v_agent_user_id UUID := 'a0000000-0000-4000-8000-000000000004'::UUID;
    v_viewer_id UUID := 'a0000000-0000-4000-8000-000000000005'::UUID;
    v_agent_id UUID;
    v_sess_id UUID;
    v_has_auth BOOLEAN;
BEGIN
    RAISE NOTICE '--- INICIANT SETUP DE PROVA AMB UUIDs v4 ---';

    -- Comprovar si auth.users existeix a l'esquema (entorn Supabase)
    SELECT EXISTS (
        SELECT 1 FROM information_schema.tables 
        WHERE table_schema = 'auth' AND table_name = 'users'
    ) INTO v_has_auth;

    IF v_has_auth THEN
        -- Inserir usuaris temporals a auth.users per satisfer FK si cal
        INSERT INTO auth.users (id, email) VALUES
            (v_admin_id, 'admin@prediccions.test'),
            (v_oper1_id, 'operador1@prediccions.test'),
            (v_oper2_id, 'operador2@prediccions.test'),
            (v_agent_user_id, 'agent_win@prediccions.test'),
            (v_viewer_id, 'viewer@prediccions.test')
        ON CONFLICT (id) DO NOTHING;
    END IF;

    -- Registrar usuaris RBAC
    INSERT INTO app_users (user_id, email, role) VALUES
        (v_admin_id, 'admin@prediccions.test', 'admin'),
        (v_oper1_id, 'operador1@prediccions.test', 'operator'),
        (v_oper2_id, 'operador2@prediccions.test', 'operator'),
        (v_agent_user_id, 'agent_win@prediccions.test', 'agent'),
        (v_viewer_id, 'viewer@prediccions.test', 'viewer')
    ON CONFLICT (user_id) DO NOTHING;

    -- Registrar Agent Windows assignat a operador 1
    INSERT INTO daemon_agents (agent_user_id, owner_user_id, agent_name) VALUES
        (v_agent_user_id, v_oper1_id, 'Windows-PC-Local')
    ON CONFLICT (agent_name) DO UPDATE SET is_active = true
    RETURNING id INTO v_agent_id;

    RAISE NOTICE '[SETUP OK] Usuaris i Agent de prova creats amb èxit.';
END $$;

-- ---------------------------------------------------------------------
-- TEST 1: Rebuig de creació de sessió per usuari amb rol 'viewer'
-- ---------------------------------------------------------------------
DO $$
DECLARE
    v_viewer_id UUID := 'a0000000-0000-4000-8000-000000000005'::UUID;
BEGIN
    -- Simular context d'usuari viewer
    PERFORM set_config('request.jwt.claim.sub', v_viewer_id::text, true);

    BEGIN
        INSERT INTO surebet_sessions (created_by, duration_seconds)
        VALUES (v_viewer_id, 600);
        
        RAISE EXCEPTION '[FAIL TEST 1] Un usuari viewer ha pogut crear una sessió!';
    EXCEPTION
        WHEN insufficient_privilege OR check_violation THEN
            RAISE NOTICE '[PASS TEST 1] RLS ha bloquejat correctament la creació de sessió a un usuari viewer.';
    END;
END $$;

-- ---------------------------------------------------------------------
-- TEST 2: Rebuig de durada invàlida (> 1800 segons / 30 minuts)
-- ---------------------------------------------------------------------
DO $$
DECLARE
    v_oper1_id UUID := 'a0000000-0000-4000-8000-000000000002'::UUID;
BEGIN
    PERFORM set_config('request.jwt.claim.sub', v_oper1_id::text, true);

    BEGIN
        -- Intent d'abús: demanar 120 minuts (7200s)
        INSERT INTO surebet_sessions (created_by, duration_seconds)
        VALUES (v_oper1_id, 7200);

        RAISE EXCEPTION '[FAIL TEST 2] S''ha acceptat una sessió de 120 minuts superior al límit!';
    EXCEPTION
        WHEN check_violation THEN
            RAISE NOTICE '[PASS TEST 2] La base de dades ha rebutjat la durada manipulada (>1800s).';
    END;
END $$;

-- ---------------------------------------------------------------------
-- TEST 3: Transició il·legal d'estat bloquejada pel Trigger (PENDING -> COMPLETED)
-- ---------------------------------------------------------------------
DO $$
DECLARE
    v_oper1_id UUID := 'a0000000-0000-4000-8000-000000000002'::UUID;
    v_sess_id UUID;
BEGIN
    PERFORM set_config('request.jwt.claim.sub', v_oper1_id::text, true);

    INSERT INTO surebet_sessions (created_by, duration_seconds)
    VALUES (v_oper1_id, 600)
    RETURNING id INTO v_sess_id;

    BEGIN
        -- Intent de saltar-se el cicle RUNNING
        UPDATE surebet_sessions
        SET status = 'COMPLETED'
        WHERE id = v_sess_id;

        RAISE EXCEPTION '[FAIL TEST 3] S''ha permès un salt directe de PENDING a COMPLETED!';
    EXCEPTION
        WHEN SQLSTATE '22000' THEN
            RAISE NOTICE '[PASS TEST 3] El disparador ha bloquejat la transició il·legal d''estat.';
    END;
END $$;

-- ---------------------------------------------------------------------
-- TEST 4: Protecció d'immutabilitat del creador i timestamp
-- ---------------------------------------------------------------------
DO $$
DECLARE
    v_oper1_id UUID := 'a0000000-0000-4000-8000-000000000002'::UUID;
    v_oper2_id UUID := 'a0000000-0000-4000-8000-000000000003'::UUID;
    v_sess_id UUID;
BEGIN
    PERFORM set_config('request.jwt.claim.sub', v_oper1_id::text, true);

    INSERT INTO surebet_sessions (created_by, duration_seconds)
    VALUES (v_oper1_id, 600)
    RETURNING id INTO v_sess_id;

    BEGIN
        -- Intent de canviar el creador
        UPDATE surebet_sessions
        SET created_by = v_oper2_id
        WHERE id = v_sess_id;

        RAISE EXCEPTION '[FAIL TEST 4] S''ha permès alterar el camp immutable created_by!';
    EXCEPTION
        WHEN SQLSTATE '42501' THEN
            RAISE NOTICE '[PASS TEST 4] La base de dades ha impedit la manipulació de camps immutables.';
    END;
END $$;

-- ---------------------------------------------------------------------
-- TEST 5: Cancel·lació indeguda d'una sessió aliena
-- ---------------------------------------------------------------------
DO $$
DECLARE
    v_oper1_id UUID := 'a0000000-0000-4000-8000-000000000002'::UUID;
    v_oper2_id UUID := 'a0000000-0000-4000-8000-000000000003'::UUID;
    v_sess_id UUID;
BEGIN
    PERFORM set_config('request.jwt.claim.sub', v_oper1_id::text, true);

    INSERT INTO surebet_sessions (created_by, duration_seconds)
    VALUES (v_oper1_id, 600)
    RETURNING id INTO v_sess_id;

    -- L'operador 2 intenta cancel·lar la sessió de l'operador 1
    PERFORM set_config('request.jwt.claim.sub', v_oper2_id::text, true);

    BEGIN
        PERFORM request_session_cancellation(v_sess_id);
        RAISE EXCEPTION '[FAIL TEST 5] Un usuari ha pogut cancel·lar la sessió d''un altre!';
    EXCEPTION
        WHEN SQLSTATE '42501' THEN
            RAISE NOTICE '[PASS TEST 5] La funció RPC ha impedit cancel·lar una sessió aliena.';
    END;
END $$;

-- ---------------------------------------------------------------------
-- TEST 6: Reclamació d'ordre exclusivament des de l'agent autoritzat
-- ---------------------------------------------------------------------
DO $$
DECLARE
    v_oper1_id UUID := 'a0000000-0000-4000-8000-000000000002'::UUID;
    v_agent_user_id UUID := 'a0000000-0000-4000-8000-000000000004'::UUID;
    v_claimed_sess UUID;
BEGIN
    -- Crear ordre legítima
    PERFORM set_config('request.jwt.claim.sub', v_oper1_id::text, true);
    INSERT INTO surebet_sessions (created_by, duration_seconds)
    VALUES (v_oper1_id, 600);

    -- L'agent autoritzat reclama la tasca
    PERFORM set_config('request.jwt.claim.sub', v_agent_user_id::text, true);
    SELECT session_id INTO v_claimed_sess FROM claim_next_pending_session();

    IF v_claimed_sess IS NOT NULL THEN
        RAISE NOTICE '[PASS TEST 6] L''agent autoritzat ha reclamat la sessió correctament: %', v_claimed_sess;
    ELSE
        RAISE EXCEPTION '[FAIL TEST 6] L''agent autoritzat no ha pogut reclamar la sessió pendent!';
    END IF;
END $$;

-- ---------------------------------------------------------------------
-- TEST 7: Bloqueig de concurrència (Impedeix 2 sessions simultànies al mateix agent)
-- ---------------------------------------------------------------------
DO $$
DECLARE
    v_oper1_id UUID := 'a0000000-0000-4000-8000-000000000002'::UUID;
    v_agent_user_id UUID := 'a0000000-0000-4000-8000-000000000004'::UUID;
    v_claimed_sess_2 UUID;
BEGIN
    -- Crear segona ordre
    PERFORM set_config('request.jwt.claim.sub', v_oper1_id::text, true);
    INSERT INTO surebet_sessions (created_by, duration_seconds)
    VALUES (v_oper1_id, 600);

    -- L'agent ja té la sessió del Test 6 en estat RUNNING. Intenta reclamar-ne una altra:
    PERFORM set_config('request.jwt.claim.sub', v_agent_user_id::text, true);
    SELECT session_id INTO v_claimed_sess_2 FROM claim_next_pending_session();

    IF v_claimed_sess_2 IS NULL THEN
        RAISE NOTICE '[PASS TEST 7] El coordinador ha impedit correctament una segona sessió concurrent a l''agent actiu.';
    ELSE
        RAISE EXCEPTION '[FAIL TEST 7] L''agent ha pogut reclamar dues sessions simultànies!';
    END IF;
END $$;

-- Desfer canvis de prova (zero contaminació de la base de dades)
ROLLBACK;

