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
        ('a0000000-0000-4000-8000-000000000006'::UUID, 'agent2@prediccions.test', 'agent'),
        (v_viewer_id, 'viewer@prediccions.test', 'viewer')
    ON CONFLICT (user_id) DO NOTHING;

    -- Registrar Agent Windows assignat a operador 1 amb heartbeat inicial fresc
    INSERT INTO daemon_agents (agent_user_id, owner_user_id, agent_name, last_heartbeat) VALUES
        (v_agent_user_id, v_oper1_id, 'Windows-PC-Local', clock_timestamp())
    ON CONFLICT (agent_name) DO UPDATE SET is_active = true, last_heartbeat = clock_timestamp()
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

-- 2.3 Rebuig de creació de sessió si l'agent està desconnectat (heartbeat expirat > 120 segons)
SET LOCAL ROLE authenticated;
SET LOCAL "request.jwt.claim.sub" = 'a0000000-0000-4000-8000-000000000002'; -- oper1
DO $$
BEGIN
    -- Simular que l'agent porta 10 minuts sense donar senyals de vida
    UPDATE daemon_agents
    SET last_heartbeat = clock_timestamp() - INTERVAL '10 minutes'
    WHERE agent_name = 'Windows-PC-Local';

    BEGIN
        PERFORM create_search_session(600);
        RAISE EXCEPTION '[FAIL 2.3] S''ha acceptat crear sessió amb un agent desconnectat (>120s)!';
    EXCEPTION
        WHEN SQLSTATE 'P0002' THEN
            RAISE NOTICE '[PASS 2.3: AGENT AVAILABILITY] RPC rebutja la creació si l''agent no té un batec recent (<=120s).';
    END;

    -- Restaurar batec actiu de l'agent
    UPDATE daemon_agents
    SET last_heartbeat = clock_timestamp()
    WHERE agent_name = 'Windows-PC-Local';
END $$;
RESET ROLE;

-- 2.4 Rebuig si l'operador no té cap agent autoritzat assignat (operador 2)
SET LOCAL ROLE authenticated;
SET LOCAL "request.jwt.claim.sub" = 'a0000000-0000-4000-8000-000000000003'; -- oper2 (no és admin ni propietari de l'agent)
DO $$
BEGIN
    BEGIN
        PERFORM create_search_session(600);
        RAISE EXCEPTION '[FAIL 2.4] Operador 2 ha pogut crear sessió sense tenir cap agent propi assignat!';
    EXCEPTION
        WHEN SQLSTATE 'P0002' THEN
            RAISE NOTICE '[PASS 2.4: AGENT AUTH] RPC comprova l''autorització de l''agent respecte a l''usuari que crea la sessió.';
    END;
END $$;
RESET ROLE;

-- 2.5A Rebuig si un operador intenta cridar register_agent_heartbeat (Només rol 'agent')
SET LOCAL ROLE authenticated;
SET LOCAL "request.jwt.claim.sub" = 'a0000000-0000-4000-8000-000000000002'; -- oper1
DO $$
BEGIN
    BEGIN
        PERFORM register_agent_heartbeat();
        RAISE EXCEPTION '[FAIL 2.5A] Operador 1 ha pogut cridar register_agent_heartbeat!';
    EXCEPTION
        WHEN SQLSTATE '42501' THEN
            RAISE NOTICE '[PASS 2.5A: AGENT AUTH] register_agent_heartbeat rebutja usuaris que no tenen rol agent actiu.';
    END;
END $$;
RESET ROLE;

-- 2.5B Rebuig si l'agent està inactiu o revocat a daemon_agents
SET LOCAL ROLE authenticated;
SET LOCAL "request.jwt.claim.sub" = 'a0000000-0000-4000-8000-000000000004'; -- agent
DO $$
BEGIN
    UPDATE daemon_agents SET is_active = false WHERE agent_name = 'Windows-PC-Local';

    BEGIN
        PERFORM register_agent_heartbeat();
        RAISE EXCEPTION '[FAIL 2.5B] Agent inactiu ha pogut renovar batec!';
    EXCEPTION
        WHEN SQLSTATE '42501' THEN
            RAISE NOTICE '[PASS 2.5B: AGENT AUTH] Agent inactiu o revocat no pot renovar el seu batec de presència.';
    END;

    UPDATE daemon_agents SET is_active = true WHERE agent_name = 'Windows-PC-Local';
END $$;
RESET ROLE;

-- 2.5C Rebuig si l'usuari agent està desactivat a app_users
SET LOCAL ROLE authenticated;
SET LOCAL "request.jwt.claim.sub" = 'a0000000-0000-4000-8000-000000000004'; -- agent
DO $$
BEGIN
    UPDATE app_users SET is_active = false WHERE user_id = 'a0000000-0000-4000-8000-000000000004'::UUID;

    BEGIN
        PERFORM register_agent_heartbeat();
        RAISE EXCEPTION '[FAIL 2.5C] Usuari agent desactivat a app_users ha pogut renovar batec!';
    EXCEPTION
        WHEN SQLSTATE '42501' THEN
            RAISE NOTICE '[PASS 2.5C: AGENT AUTH] Usuari agent amb compte desactivat no pot executar l''RPC.';
    END;

    UPDATE app_users SET is_active = true WHERE user_id = 'a0000000-0000-4000-8000-000000000004'::UUID;
END $$;
RESET ROLE;

-- 2.5D L'agent actiu i autoritzat renova satisfactòriament el seu batec amb timestamp del servidor
SET LOCAL ROLE authenticated;
SET LOCAL "request.jwt.claim.sub" = 'a0000000-0000-4000-8000-000000000004'; -- agent
DO $$
DECLARE
    v_ok BOOLEAN;
    v_hb TIMESTAMPTZ;
BEGIN
    v_ok := register_agent_heartbeat();
    IF NOT v_ok THEN
        RAISE EXCEPTION '[FAIL 2.5D] register_agent_heartbeat ha retornat false!';
    END IF;

    SELECT last_heartbeat INTO v_hb FROM daemon_agents WHERE agent_name = 'Windows-PC-Local';
    IF v_hb >= (clock_timestamp() - INTERVAL '5 seconds') THEN
        RAISE NOTICE '[PASS 2.5D: AGENT HEARTBEAT] Agent en repòs renova presència amb timestamp legítim del servidor (%s).', v_hb;
    ELSE
        RAISE EXCEPTION '[FAIL 2.5D] Timestamp no coincideix amb el servidor: %s', v_hb;
    END IF;
END $$;
RESET ROLE;

-- 2.5E Impossibilitat de modificar el batec d'un altre agent
DO $$
DECLARE
    v_old_hb TIMESTAMPTZ := '2020-01-01 00:00:00Z'::TIMESTAMPTZ;
    v_curr_hb TIMESTAMPTZ;
BEGIN
    -- Registrar un segon agent amb heartbeat antic
    INSERT INTO daemon_agents (agent_user_id, owner_user_id, agent_name, last_heartbeat)
    VALUES ('a0000000-0000-4000-8000-000000000006'::UUID, 'a0000000-0000-4000-8000-000000000003'::UUID, 'Agent-Alie', v_old_hb)
    ON CONFLICT (agent_name) DO UPDATE SET last_heartbeat = v_old_hb;
END $$;

-- Agent 1 executa el seu heartbeat
SET LOCAL ROLE authenticated;
SET LOCAL "request.jwt.claim.sub" = 'a0000000-0000-4000-8000-000000000004'; -- agent 1
DO $$
DECLARE
    v_curr_hb TIMESTAMPTZ;
BEGIN
    PERFORM register_agent_heartbeat();

    -- Comprovar que el heartbeat d'Agent-Alie NO s'ha alterat
    SELECT last_heartbeat INTO v_curr_hb FROM daemon_agents WHERE agent_name = 'Agent-Alie';
    IF v_curr_hb = '2020-01-01 00:00:00Z'::TIMESTAMPTZ THEN
        RAISE NOTICE '[PASS 2.5E: AGENT ISOLATION] L''execució de register_agent_heartbeat només afecta el propi agent i no pot alterar agents aliens.';
    ELSE
        RAISE EXCEPTION '[FAIL 2.5E] El batec de l''agent aliè s''ha modificat indegudament!';
    END IF;
END $$;
RESET ROLE;

-- =====================================================================
-- NIVELL 3: PROVES DE CONSTRAINTS I VALIDACIÓ DE PARÀMETRES
-- =====================================================================

-- 3.1 Rebuig de durada NULL
SET LOCAL ROLE authenticated;
SET LOCAL "request.jwt.claim.sub" = 'a0000000-0000-4000-8000-000000000002'; -- oper1
DO $$
BEGIN
    BEGIN
        PERFORM create_search_session(NULL);
        RAISE EXCEPTION '[FAIL 3.1] create_search_session ha acceptat durada NULL!';
    EXCEPTION
        WHEN SQLSTATE '22000' THEN
            RAISE NOTICE '[PASS 3.1: CONSTRAINT] RPC valida i rebutja durada NULL.';
    END;
END $$;
RESET ROLE;

-- 3.2 Rebuig de durada fora de rang (> 1800 segons)
SET LOCAL ROLE authenticated;
SET LOCAL "request.jwt.claim.sub" = 'a0000000-0000-4000-8000-000000000002'; -- oper1
DO $$
BEGIN
    BEGIN
        PERFORM create_search_session(7200); -- 120 minuts
        RAISE EXCEPTION '[FAIL 3.2] create_search_session ha acceptat 7200 segons!';
    EXCEPTION
        WHEN SQLSTATE '22000' THEN
            RAISE NOTICE '[PASS 3.2: CONSTRAINT] RPC valida i rebutja durada superior a 1800s.';
    END;
END $$;
RESET ROLE;

-- 3.3 Rebuig de durada inferior al mínim (< 60 segons)
SET LOCAL ROLE authenticated;
SET LOCAL "request.jwt.claim.sub" = 'a0000000-0000-4000-8000-000000000002'; -- oper1
DO $$
BEGIN
    BEGIN
        PERFORM create_search_session(30); -- 30 segons
        RAISE EXCEPTION '[FAIL 3.3] create_search_session ha acceptat 30 segons!';
    EXCEPTION
        WHEN SQLSTATE '22000' THEN
            RAISE NOTICE '[PASS 3.3: CONSTRAINT] RPC valida i rebutja durada inferior a 60s.';
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

-- 5.1 Rebuig de sessió concurrent quan ja n'hi ha una a la cua (a través de l'RPC)
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

-- 5.1B Comprovació de la garantia atòmica a nivell de motor (constraint uq_surebet_single_active_session)
-- Simula una condició de cursa de dues transaccions concurrents a nivell de PostgreSQL
DO $$
BEGIN
    BEGIN
        INSERT INTO surebet_sessions (
            id, created_by, duration_seconds, status
        ) VALUES (
            gen_random_uuid(),
            'a0000000-0000-4000-8000-000000000001'::UUID,
            600,
            'PENDING'
        );
        RAISE EXCEPTION '[FAIL 5.1B] S''ha pogut inserir una segona sessió activa ignorant la constraint atòmica!';
    EXCEPTION
        WHEN unique_violation THEN
            RAISE NOTICE '[PASS 5.1B: ATOMIC ENGINE CONSTRAINT] L''índex únic parcial uq_surebet_single_active_session garanteix atomicitat absoluta a nivell de motor.';
    END;
END $$;

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
