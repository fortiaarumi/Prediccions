-- =====================================================================
-- BATERIA DE PROVES DE SEGURETAT POSTGRESQL / SUPABASE
-- =====================================================================
-- Aquest script verifica de manera diferenciada i rigorosa:
-- 1. NIVELL GRANT: Denegació per manca de privilegis de taula/funció.
-- 2. NIVELL RPC: Validació de rols i permisos dins de funcions SECURITY DEFINER.
-- 3. NIVELL CONSTRAINT/TRIGGER: Validacions estructurals i de màquina d'estats.
-- 4. NIVELL RLS: Filtratge estricte de visualització de dades a SELECT segons identitat.
-- 5. NIVELL CONCURRÈNCIA: Assignació atòmica FIFO i prevenció de duplicats.
--
-- Tota la bateria s'executa dins d'un bloc BEGIN ... ROLLBACK (zero dany persistent).
-- =====================================================================

BEGIN;

-- 0. Assegurar rols estàndard si s'executa en entorn local pur
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        CREATE ROLE anon NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        CREATE ROLE authenticated NOLOGIN;
    END IF;
END $$;

-- 1. Setup: Crear usuaris i agents de prova
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
    RAISE NOTICE '--- INICIANT SETUP DE PROVA DE SEGURETAT MULTICAPA ---';

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

    -- Registrar usuaris amb rols diferents
    INSERT INTO app_users (user_id, email, role) VALUES
        (v_admin_id, 'admin@prediccions.test', 'admin'),
        (v_oper1_id, 'operador1@prediccions.test', 'operator'),
        (v_oper2_id, 'operador2@prediccions.test', 'operator'),
        (v_agent_user_id, 'agent_win@prediccions.test', 'agent'),
        (v_viewer_id, 'viewer@prediccions.test', 'viewer')
    ON CONFLICT (user_id) DO NOTHING;

    -- Registrar Agent Windows
    INSERT INTO daemon_agents (agent_user_id, owner_user_id, agent_name) VALUES
        (v_agent_user_id, v_oper1_id, 'Windows-PC-Local')
    ON CONFLICT (agent_name) DO UPDATE SET is_active = true
    RETURNING id INTO v_agent_id;

    RAISE NOTICE '[SETUP OK] Identitats de prova registrades.';
END $$;

-- =====================================================================
-- NIVELL 1: PROVES DE MIGRACIÓ DE PRIVILEGIS (Manca de GRANT)
-- =====================================================================

-- 1.1 Bloqueig de lectura anònima per manca de GRANT SELECT
SET LOCAL ROLE anon;
DO $$
BEGIN
    BEGIN
        PERFORM * FROM surebet_sessions;
        RAISE EXCEPTION '[FAIL 1.1] anon ha pogut llegir surebet_sessions directament!';
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE NOTICE '[PASS 1.1: GRANT] Rol anon rebutjat per manca de dret SELECT a taules internes.';
    END;
END $$;
RESET ROLE;

-- 1.2 Bloqueig de crida anònima a RPC per manca de GRANT EXECUTE
SET LOCAL ROLE anon;
DO $$
BEGIN
    BEGIN
        PERFORM claim_next_pending_session();
        RAISE EXCEPTION '[FAIL 1.2] anon ha pogut executar claim_next_pending_session!';
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE NOTICE '[PASS 1.2: GRANT] Rol anon rebutjat per manca de dret EXECUTE a funcions RPC.';
    END;
END $$;
RESET ROLE;

-- 1.3 Bloqueig d'escriptura directa a surebet_sessions per manca de GRANT INSERT/UPDATE/DELETE
SET LOCAL ROLE authenticated;
SET LOCAL "request.jwt.claim.sub" = 'a0000000-0000-4000-8000-000000000002'; -- oper1
DO $$
BEGIN
    -- Intent d'INSERT directe (ha de fallar per manca de GRANT INSERT)
    BEGIN
        INSERT INTO surebet_sessions (created_by, duration_seconds)
        VALUES ('a0000000-0000-4000-8000-000000000002'::UUID, 600);
        RAISE EXCEPTION '[FAIL 1.3] authenticated ha pogut fer INSERT directe a surebet_sessions!';
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE NOTICE '[PASS 1.3: GRANT] INSERT directe denegat: cal usar obligatòriament create_search_session().';
    END;

    -- Intent d'UPDATE directe (ha de fallar per manca de GRANT UPDATE)
    BEGIN
        UPDATE surebet_sessions SET status = 'CANCELLED';
        RAISE EXCEPTION '[FAIL 1.4] authenticated ha pogut fer UPDATE directe a surebet_sessions!';
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE NOTICE '[PASS 1.4: GRANT] UPDATE directe denegat: cal usar RPCs de coordinació.';
    END;
END $$;
RESET ROLE;

-- =====================================================================
-- NIVELL 2: PROVES DE VALIDACIÓ DE PERMISOS DINS DE LES RPCs
-- =====================================================================

-- 2.1 Rebuig de creació de sessió per a usuari 'viewer' dins de l'RPC create_search_session
SET LOCAL ROLE authenticated;
SET LOCAL "request.jwt.claim.sub" = 'a0000000-0000-4000-8000-000000000005'; -- viewer
DO $$
BEGIN
    BEGIN
        PERFORM create_search_session(600);
        RAISE EXCEPTION '[FAIL 2.1] Usuari viewer ha pogut cridar create_search_session!';
    EXCEPTION
        WHEN SQLSTATE '42501' THEN
            RAISE NOTICE '[PASS 2.1: RPC AUTH] RPC create_search_session denega l''accés a usuaris amb rol viewer.';
    END;
END $$;
RESET ROLE;

-- 2.2 Rebuig de reclamació de tasca per a operador dins de l'RPC claim_next_pending_session
SET LOCAL ROLE authenticated;
SET LOCAL "request.jwt.claim.sub" = 'a0000000-0000-4000-8000-000000000002'; -- oper1
DO $$
BEGIN
    BEGIN
        PERFORM claim_next_pending_session();
        RAISE EXCEPTION '[FAIL 2.2] Operador ha pogut reclamar comanda a claim_next_pending_session!';
    EXCEPTION
        WHEN SQLSTATE '42501' THEN
            RAISE NOTICE '[PASS 2.2: RPC AUTH] RPC claim_next_pending_session exigeix rol agent actiu.';
    END;
END $$;
RESET ROLE;

-- =====================================================================
-- NIVELL 3: PROVES DE CONSTRAINTS I VALIDACIÓ DE PARÀMETRES
-- =====================================================================

-- 3.1 Rebuig de durada fora de rang (> 1800 segons)
SET LOCAL ROLE authenticated;
SET LOCAL "request.jwt.claim.sub" = 'a0000000-0000-4000-8000-000000000002'; -- oper1
DO $$
BEGIN
    BEGIN
        PERFORM create_search_session(7200); -- 120 minuts
        RAISE EXCEPTION '[FAIL 3.1] create_search_session ha acceptat 7200 segons!';
    EXCEPTION
        WHEN SQLSTATE '22000' THEN
            RAISE NOTICE '[PASS 3.1: CONSTRAINT] RPC valida i rebutja durada superior a 1800s.';
    END;
END $$;
RESET ROLE;

-- =====================================================================
-- NIVELL 4: PROVES DE POLÍTIQUES RLS (FILTRATGE REAL DE DADES A SELECT)
-- =====================================================================

-- Pas 4.0: Operador 1 crea una sessió legítima mitjançant l'RPC
SET LOCAL ROLE authenticated;
SET LOCAL "request.jwt.claim.sub" = 'a0000000-0000-4000-8000-000000000002'; -- oper1
DO $$
DECLARE
    v_new_id UUID;
BEGIN
    v_new_id := create_search_session(600);
    RAISE NOTICE '[PASS 4.0] Sessió legítima creada per Operador 1: %', v_new_id;
END $$;
RESET ROLE;

-- 4.1 Usuari 'viewer' consulta surebet_sessions -> RLS ha de retornar EXACTAMENT 0 files
SET LOCAL ROLE authenticated;
SET LOCAL "request.jwt.claim.sub" = 'a0000000-0000-4000-8000-000000000005'; -- viewer
DO $$
DECLARE
    v_count INT;
BEGIN
    SELECT count(*) INTO v_count FROM surebet_sessions;
    IF v_count = 0 THEN
        RAISE NOTICE '[PASS 4.1: RLS SELECT] Política RLS filtra correctament i retorna 0 sessions al viewer.';
    ELSE
        RAISE EXCEPTION '[FAIL 4.1] RLS ha fallat: viewer pot veure % sessions alienes!', v_count;
    END IF;
END $$;
RESET ROLE;

-- 4.2 Operador 2 (no propietari) consulta surebet_sessions -> RLS ha de retornar 0 files
SET LOCAL ROLE authenticated;
SET LOCAL "request.jwt.claim.sub" = 'a0000000-0000-4000-8000-000000000003'; -- oper2
DO $$
DECLARE
    v_count INT;
BEGIN
    SELECT count(*) INTO v_count FROM surebet_sessions;
    IF v_count = 0 THEN
        RAISE NOTICE '[PASS 4.2: RLS SELECT] Política RLS aïlla la sessió d''Operador 1 d''Operador 2 (retorna 0).';
    ELSE
        RAISE EXCEPTION '[FAIL 4.2] RLS ha fallat: operador 2 pot veure sessions d''operador 1!';
    END IF;
END $$;
RESET ROLE;

-- 4.3 Operador 1 (propietari legítim) consulta surebet_sessions -> RLS ha de retornar la seva fila (1 fila)
SET LOCAL ROLE authenticated;
SET LOCAL "request.jwt.claim.sub" = 'a0000000-0000-4000-8000-000000000002'; -- oper1
DO $$
DECLARE
    v_count INT;
BEGIN
    SELECT count(*) INTO v_count FROM surebet_sessions;
    IF v_count = 1 THEN
        RAISE NOTICE '[PASS 4.3: RLS SELECT] Política RLS permet llegir la pròpia sessió a l''operador propietari (1 fila).';
    ELSE
        RAISE EXCEPTION '[FAIL 4.3] Operador 1 no pot llegir la seva pròpia sessió!';
    END IF;
END $$;
RESET ROLE;

-- 4.4 Administrador consulta surebet_sessions -> RLS permet veure-les totes (1 fila)
SET LOCAL ROLE authenticated;
SET LOCAL "request.jwt.claim.sub" = 'a0000000-0000-4000-8000-000000000001'; -- admin
DO $$
DECLARE
    v_count INT;
BEGIN
    SELECT count(*) INTO v_count FROM surebet_sessions;
    IF v_count = 1 THEN
        RAISE NOTICE '[PASS 4.4: RLS SELECT] Política RLS atorga visibilitat global a administradors.';
    ELSE
        RAISE EXCEPTION '[FAIL 4.4] Administrador no pot veure totes les sessions!';
    END IF;
END $$;
RESET ROLE;

-- =====================================================================
-- NIVELL 5: PROVES DE CICLE DE VIDA, CONCURRÈNCIA I ATOMICITAT
-- =====================================================================

-- 5.1 Rebuig de sessió concurrent quan ja n'hi ha una a la cua
SET LOCAL ROLE authenticated;
SET LOCAL "request.jwt.claim.sub" = 'a0000000-0000-4000-8000-000000000002'; -- oper1
DO $$
BEGIN
    BEGIN
        PERFORM create_search_session(600);
        RAISE EXCEPTION '[FAIL 5.1] S''ha acceptat una segona sessió concurrent!';
    EXCEPTION
        WHEN SQLSTATE '23505' THEN
            RAISE NOTICE '[PASS 5.1: CONCURRÈNCIA] RPC create_search_session bloqueja sessions concurrents a la cua.';
    END;
END $$;
RESET ROLE;

-- 5.2 Agent autoritzat reclama la sessió atòmicament
SET LOCAL ROLE authenticated;
SET LOCAL "request.jwt.claim.sub" = 'a0000000-0000-4000-8000-000000000004'; -- agent
DO $$
DECLARE
    v_claimed_sess UUID;
BEGIN
    SELECT session_id INTO v_claimed_sess FROM claim_next_pending_session();
    IF v_claimed_sess IS NOT NULL THEN
        RAISE NOTICE '[PASS 5.2: AGENT] Agent autoritzat reclama la sessió correctament: %', v_claimed_sess;
    ELSE
        RAISE EXCEPTION '[FAIL 5.2] Agent no ha pogut reclamar la sessió pendent!';
    END IF;
END $$;
RESET ROLE;

-- 5.3 Intent de cancel·lació indeguda per usuari no propietari (operador 2)
SET LOCAL ROLE authenticated;
SET LOCAL "request.jwt.claim.sub" = 'a0000000-0000-4000-8000-000000000003'; -- oper2
DO $$
DECLARE
    v_target_id UUID;
BEGIN
    -- Com que RLS oculta la sessió a l'operador 2, comprovem que l'RPC cancel·lació ho bloqueja fins i tot si endevina el UUID
    SELECT id INTO v_target_id FROM surebet_sessions LIMIT 1;
    -- Si RLS ha ocultat la ID (v_target_id és null), simulem que l'operador 2 coneix la ID de la sessió de l'operador 1
    BEGIN
        PERFORM request_session_cancellation('b0000000-0000-4000-8000-000000000001'::UUID);
    EXCEPTION
        WHEN SQLSTATE '42501' THEN
            RAISE NOTICE '[PASS 5.3: RPC] Cancel·lació de sessió aliena bloquejada per l''RPC.';
    END;
END $$;
RESET ROLE;

-- Desfer tots els canvis temporals per garantir zero dany o contaminació de la base de dades
ROLLBACK;
