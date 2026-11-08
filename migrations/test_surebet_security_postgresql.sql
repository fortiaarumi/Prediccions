-- =====================================================================
-- BATERIA DE PROVES NEGATIVES I DE SEGURETAT PER A POSTGRESQL (Supabase)
-- =====================================================================
-- Aquest script està dissenyat per verificar les polítiques RLS, els triggers
-- d'integritat, els drets DML i les funcions RPC utilitzant ROLS EFECTIUS REALS
-- de PostgreSQL (SET LOCAL ROLE anon / authenticated) i identitats auth.uid().
-- S'executa dins d'una transacció i acaba amb ROLLBACK (zero dany persistent).
-- =====================================================================

BEGIN;

-- 0. Assegurar existència dels rols estàndard de Supabase si estem en Postgres local
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        CREATE ROLE anon NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        CREATE ROLE authenticated NOLOGIN;
    END IF;
END $$;

-- 1. Setup: Crear usuaris i agents de prova amb UUIDs v4 deterministes
DO $$
DECLARE
    v_admin_id UUID := 'a0000000-0000-4000-8000-000000000001'::UUID;
    v_oper1_id UUID := 'a0000000-0000-4000-8000-000000000002'::UUID;
    v_oper2_id UUID := 'a0000000-0000-4000-8000-000000000003'::UUID;
    v_agent_user_id UUID := 'a0000000-0000-4000-8000-000000000004'::UUID;
    v_viewer_id UUID := 'a0000000-0000-4000-8000-000000000005'::UUID;
    v_agent_id UUID;
    v_has_auth BOOLEAN;
BEGIN
    RAISE NOTICE '--- INICIANT SETUP DE PROVA AMB IDENTITATS REALS ---';

    -- Comprovar si auth.users existeix a l'esquema (entorn Supabase)
    SELECT EXISTS (
        SELECT 1 FROM information_schema.tables 
        WHERE table_schema = 'auth' AND table_name = 'users'
    ) INTO v_has_auth;

    IF v_has_auth THEN
        INSERT INTO auth.users (id, email) VALUES
            (v_admin_id, 'admin@prediccions.test'),
            (v_oper1_id, 'operador1@prediccions.test'),
            (v_oper2_id, 'operador2@prediccions.test'),
            (v_agent_user_id, 'agent_win@prediccions.test'),
            (v_viewer_id, 'viewer@prediccions.test')
        ON CONFLICT (id) DO NOTHING;
    END IF;

    -- Registrar usuaris RBAC a app_users
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

    RAISE NOTICE '[SETUP OK] Usuaris i Agent de prova registrats correctament.';
END $$;

-- ---------------------------------------------------------------------
-- TEST 0: Rebuig d'accés anònim no autenticat (SET LOCAL ROLE anon)
-- ---------------------------------------------------------------------
SET LOCAL ROLE anon;

DO $$
BEGIN
    -- Intent de lectura de sessions anònima
    BEGIN
        PERFORM * FROM surebet_sessions;
        RAISE EXCEPTION '[FAIL TEST 0.1] Rol anon ha pogut llegir surebet_sessions directament!';
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE NOTICE '[PASS TEST 0.1] Rol anon no té permís de lectura sobre taules internes.';
    END;

    -- Intent de cridar RPC anònimament
    BEGIN
        PERFORM claim_next_pending_session();
        RAISE EXCEPTION '[FAIL TEST 0.2] Rol anon ha pogut executar claim_next_pending_session!';
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE NOTICE '[PASS TEST 0.2] Rol anon no té permís d''execució sobre funcions RPC.';
    END;
END $$;

RESET ROLE;

-- ---------------------------------------------------------------------
-- TEST 1: Rebuig de creació de sessió per usuari 'viewer' (SET LOCAL ROLE authenticated)
-- ---------------------------------------------------------------------
SET LOCAL ROLE authenticated;
SET LOCAL "request.jwt.claim.sub" = 'a0000000-0000-4000-8000-000000000005'; -- viewer_id

DO $$
DECLARE
    v_viewer_id UUID := 'a0000000-0000-4000-8000-000000000005'::UUID;
BEGIN
    BEGIN
        INSERT INTO surebet_sessions (created_by, duration_seconds)
        VALUES (v_viewer_id, 600);
        
        RAISE EXCEPTION '[FAIL TEST 1] Un usuari viewer ha pogut crear una sessió!';
    EXCEPTION
        WHEN insufficient_privilege OR check_violation THEN
            RAISE NOTICE '[PASS TEST 1] RLS ha blocat correctament la creació de sessió a usuari viewer.';
    END;
END $$;

RESET ROLE;

-- ---------------------------------------------------------------------
-- TEST 2: Rebuig de durada invàlida (>1800s / 30 minuts) des d'operador
-- ---------------------------------------------------------------------
SET LOCAL ROLE authenticated;
SET LOCAL "request.jwt.claim.sub" = 'a0000000-0000-4000-8000-000000000002'; -- oper1_id

DO $$
DECLARE
    v_oper1_id UUID := 'a0000000-0000-4000-8000-000000000002'::UUID;
BEGIN
    BEGIN
        -- Intent d'abús: demanar 120 minuts (7200s)
        INSERT INTO surebet_sessions (created_by, duration_seconds)
        VALUES (v_oper1_id, 7200);

        RAISE EXCEPTION '[FAIL TEST 2] S''ha acceptat una sessió de 120 minuts superior al límit!';
    EXCEPTION
        WHEN check_violation THEN
            RAISE NOTICE '[PASS TEST 2] La base de dades ha rebutjat la durada no permesa (>1800s).';
    END;
END $$;

RESET ROLE;

-- ---------------------------------------------------------------------
-- TEST 3: Bloqueig de bypass d'escriptura directa (UPDATE denegat a authenticated)
-- ---------------------------------------------------------------------
SET LOCAL ROLE authenticated;
SET LOCAL "request.jwt.claim.sub" = 'a0000000-0000-4000-8000-000000000002'; -- oper1_id

DO $$
BEGIN
    BEGIN
        -- Cap usuari o client authenticated té dret d'UPDATE directe sobre surebet_sessions
        UPDATE surebet_sessions SET status = 'CANCELLED';
        RAISE EXCEPTION '[FAIL TEST 3] Rol authenticated ha pogut fer UPDATE directe a surebet_sessions!';
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE NOTICE '[PASS TEST 3] UPDATE directe revocat: es requereix obligatòriament la crida RPC.';
    END;
END $$;

RESET ROLE;

-- ---------------------------------------------------------------------
-- TEST 4: Bloqueig de bypass d'escriptura directa a snapshots (INSERT denegat a authenticated)
-- ---------------------------------------------------------------------
SET LOCAL ROLE authenticated;
SET LOCAL "request.jwt.claim.sub" = 'a0000000-0000-4000-8000-000000000004'; -- agent_user_id

DO $$
BEGIN
    BEGIN
        -- Ni tan sols l'agent pot fer INSERT directe sense passar per publish_session_snapshot
        INSERT INTO surebet_snapshots (
            session_id, agent_id, market_type, match_id, odds_age_seconds, payload
        ) VALUES (
            gen_random_uuid(), gen_random_uuid(), '1X2', 'm1', 30, '{}'::JSONB
        );
        RAISE EXCEPTION '[FAIL TEST 4] Rol authenticated ha pogut fer INSERT directe a surebet_snapshots!';
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE NOTICE '[PASS TEST 4] INSERT directe revocat a snapshots: es requereix publish_session_snapshot RPC.';
    END;
END $$;

RESET ROLE;

-- ---------------------------------------------------------------------
-- TEST 5: Cancel·lació indeguda d'una sessió aliena (operador 2 vs sessió operador 1)
-- ---------------------------------------------------------------------
-- Pas 5.1: Operador 1 crea la sessió legítima
SET LOCAL ROLE authenticated;
SET LOCAL "request.jwt.claim.sub" = 'a0000000-0000-4000-8000-000000000002'; -- oper1_id

DO $$
DECLARE
    v_oper1_id UUID := 'a0000000-0000-4000-8000-000000000002'::UUID;
    v_sess_id UUID;
BEGIN
    INSERT INTO surebet_sessions (id, created_by, duration_seconds)
    VALUES ('b0000000-0000-4000-8000-000000000001'::UUID, v_oper1_id, 600);
END $$;

RESET ROLE;

-- Pas 5.2: Operador 2 intenta cancel·lar la sessió de l'operador 1
SET LOCAL ROLE authenticated;
SET LOCAL "request.jwt.claim.sub" = 'a0000000-0000-4000-8000-000000000003'; -- oper2_id

DO $$
DECLARE
    v_target_sess UUID := 'b0000000-0000-4000-8000-000000000001'::UUID;
BEGIN
    BEGIN
        PERFORM request_session_cancellation(v_target_sess);
        RAISE EXCEPTION '[FAIL TEST 5] Operador 2 ha pogut cancel·lar la sessió d''Operador 1!';
    EXCEPTION
        WHEN SQLSTATE '42501' THEN
            RAISE NOTICE '[PASS TEST 5] L''RPC impedeix estrictament la cancel·lació de sessions alienes.';
    END;
END $$;

RESET ROLE;

-- ---------------------------------------------------------------------
-- TEST 6: Reclamació d'ordre exclusivament des de l'agent autoritzat
-- ---------------------------------------------------------------------
-- Pas 6.1: Operador intenta reclamar (ha de fallar perquè no és agent)
SET LOCAL ROLE authenticated;
SET LOCAL "request.jwt.claim.sub" = 'a0000000-0000-4000-8000-000000000002'; -- oper1_id

DO $$
BEGIN
    BEGIN
        PERFORM claim_next_pending_session();
        RAISE EXCEPTION '[FAIL TEST 6.1] Un operador ha pogut cridar claim_next_pending_session!';
    EXCEPTION
        WHEN SQLSTATE '42501' THEN
            RAISE NOTICE '[PASS TEST 6.1] Operador no pot reclamar tasques (només agent autoritzat).';
    END;
END $$;

RESET ROLE;

-- Pas 6.2: Agent autoritzat reclama la tasca legítimament
SET LOCAL ROLE authenticated;
SET LOCAL "request.jwt.claim.sub" = 'a0000000-0000-4000-8000-000000000004'; -- agent_user_id

DO $$
DECLARE
    v_claimed_sess UUID;
BEGIN
    SELECT session_id INTO v_claimed_sess FROM claim_next_pending_session();

    IF v_claimed_sess = 'b0000000-0000-4000-8000-000000000001'::UUID THEN
        RAISE NOTICE '[PASS TEST 6.2] L''agent autoritzat ha reclamat la sessió correctament: %', v_claimed_sess;
    ELSE
        RAISE EXCEPTION '[FAIL TEST 6.2] Reclamació fallida o sessió inesperada: %', v_claimed_sess;
    END IF;
END $$;

RESET ROLE;

-- ---------------------------------------------------------------------
-- TEST 7: Bloqueig de concurrència (Impedeix 2 sessions simultànies al mateix agent)
-- ---------------------------------------------------------------------
-- Pas 7.1: Operador crea segona sessió
SET LOCAL ROLE authenticated;
SET LOCAL "request.jwt.claim.sub" = 'a0000000-0000-4000-8000-000000000002'; -- oper1_id

DO $$
DECLARE
    v_oper1_id UUID := 'a0000000-0000-4000-8000-000000000002'::UUID;
BEGIN
    INSERT INTO surebet_sessions (id, created_by, duration_seconds)
    VALUES ('b0000000-0000-4000-8000-000000000002'::UUID, v_oper1_id, 600);
END $$;

RESET ROLE;

-- Pas 7.2: Agent intenta reclamar mentre ja en té una en curs (RUNNING)
SET LOCAL ROLE authenticated;
SET LOCAL "request.jwt.claim.sub" = 'a0000000-0000-4000-8000-000000000004'; -- agent_user_id

DO $$
DECLARE
    v_second_claimed UUID;
BEGIN
    SELECT session_id INTO v_second_claimed FROM claim_next_pending_session();

    IF v_second_claimed IS NULL THEN
        RAISE NOTICE '[PASS TEST 7] El coordinador ha impedit correctament una segona sessió concurrent.';
    ELSE
        RAISE EXCEPTION '[FAIL TEST 7] L''agent ha pogut reclamar dues sessions simultànies: %', v_second_claimed;
    END IF;
END $$;

RESET ROLE;

-- Desfer tots els canvis de prova per garantir zero contaminació de la base de dades
ROLLBACK;
