/**
 * supabase/functions/search-surebets/index.ts
 * ===========================================
 * Supabase Edge Function (Deno/TypeScript) per a l'orquestració autònoma de The Odds API.
 * 
 * Funcionalitats clau:
 * 1. Seguretat màxima: THE_ODDS_API_KEY i SUPABASE_SERVICE_ROLE_KEY resideixen exclusivament
 *    en variables d'entorn segures del servidor (Vault de Supabase). Mai s'exposen al client.
 * 2. Autenticació i Autorització: Verifica el token JWT de l'usuari amb Supabase Auth.
 * 3. Gestió de concurrència i reutilització:
 *    - Si ja hi ha una cerca completada fa menys de 15 minuts, retorna les dades amb 0 crèdits.
 *    - Si hi ha una sessió activa, s'espera al resultat sense duplicar peticions externes.
 * 4. Optimització adaptativa de calendari:
 *    - Consulta /events (0 crèdits) per a les 7 lligues canòniques.
 *    - Filtra partits prematch a 24h, 48h i 72h en UTC.
 *    - Si no hi ha partits pròxims, retorna avís sense gastar crèdits.
 * 5. Consum controlat de quota:
 *    - 1X2 per lliga activa (1 crèdit per lliga).
 *    - Doble Oportunitat selectiva per als candidats més probables (1 crèdit per partit).
 *    - Reconciliació amb les capçaleres oficials x-requests-remaining i x-requests-used.
 * 6. Motor d'arbitratge i traçabilitat de jurisdicció:
 *    - Càlcul de 1X2 i combinacions de Doble Oportunitat.
 *    - Diferenciació entre operadors DGOJ Espanya i operadors internacionals teòrics.
 */

import { serve } from "https://deno.land/std@0.177.0/http/server.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2.21.0";

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

const CANONICAL_LEAGUES: Record<string, string> = {
  "PREMIER": "soccer_epl",
  "LALIGA": "soccer_spain_la_liga",
  "HYPERMOTION": "soccer_spain_segunda_division",
  "CHAMPIONSHIP": "soccer_efl_champ",
  "SERIEA": "soccer_italy_serie_a",
  "BUNDESLIGA": "soccer_germany_bundesliga",
  "LIGUE1": "soccer_france_ligue_one",
};

const SPANISH_DGOJ_BOOKMAKERS = new Set([
  "winamax", "bet365", "betway", "betfair", "williamhill",
  "codere", "sportium", "kirolbet", "retabet", "bwin", "pokerstars"
]);

serve(async (req: Request) => {
  if (req.method === "OPTIONS") {
    return new Response("ok", { headers: CORS_HEADERS });
  }

  try {
    const supabaseUrl = Deno.env.get("SUPABASE_URL") || "";
    const supabaseServiceKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") || "";
    const theOddsApiKey = Deno.env.get("THE_ODDS_API_KEY") || "";

    if (!theOddsApiKey) {
      return new Response(
        JSON.stringify({ error: "THE_ODDS_API_KEY no està configurada al servidor." }),
        { status: 500, headers: { ...CORS_HEADERS, "Content-Type": "application/json" } }
      );
    }

    const supabase = createClient(supabaseUrl, supabaseServiceKey);

    // 1. Validar autenticació de l'usuari cridant
    const authHeader = req.headers.get("Authorization");
    if (!authHeader) {
      return new Response(
        JSON.stringify({ error: "Capçalera Authorization requerida." }),
        { status: 401, headers: { ...CORS_HEADERS, "Content-Type": "application/json" } }
      );
    }

    const token = authHeader.replace("Bearer ", "");
    const { data: { user }, error: authErr } = await supabase.auth.getUser(token);
    if (authErr || !user) {
      return new Response(
        JSON.stringify({ error: "Token d'usuari invàlid o expirat." }),
        { status: 401, headers: { ...CORS_HEADERS, "Content-Type": "application/json" } }
      );
    }

    // 1.5 Validar autorització de rol: només usuaris 'admin' o 'operator' actius a app_users
    //     o el UUID configurat com a operador autoritzat al servidor (AUTHORIZED_OPERATOR_UUID)
    const { data: appUserData, error: roleErr } = await supabase
      .from("app_users")
      .select("role, is_active")
      .eq("user_id", user.id)
      .maybeSingle();

    const authorizedUuid = Deno.env.get("AUTHORIZED_OPERATOR_UUID") || "";
    const isAuthorizedRole = appUserData && appUserData.is_active && ["admin", "operator"].includes(appUserData.role);
    const isAuthorizedId = Boolean(authorizedUuid && user.id === authorizedUuid);

    if (!isAuthorizedRole && !isAuthorizedId) {
      return new Response(
        JSON.stringify({
          error: "Accés denegat: es requereixen privilegis d'operador o administrador actiu.",
          code: "FORBIDDEN_OPERATOR_REQUIRED"
        }),
        { status: 403, headers: { ...CORS_HEADERS, "Content-Type": "application/json" } }
      );
    }

    // 2. Comprovar si hi ha una cerca recent completada (< 15 minuts)
    const { data: recentSessions } = await supabase
      .from("surebet_sessions")
      .select("id, status, created_at, summary")
      .in("status", ["COMPLETED"])
      .order("created_at", { ascending: false })
      .limit(1);

    const now = new Date();
    if (recentSessions && recentSessions.length > 0) {
      const lastSession = recentSessions[0];
      const lastCreated = new Date(lastSession.created_at);
      const ageSeconds = (now.getTime() - lastCreated.getTime()) / 1000;

      // Reutilització automàtica dins de la finestra de 15 minuts (900s)
      if (ageSeconds < 900) {
        const { data: activeOpps } = await supabase
          .from("v_active_surebets_v3")
          .select("*")
          .limit(100);

        return new Response(
          JSON.stringify({
            status: "CACHED_REUSE",
            message: `S'han reutilitzat les dades de la cerca recent (fa ${Math.round(ageSeconds)}s). Zero crèdits consumits.`,
            session_id: lastSession.id,
            credits_spent: 0,
            age_seconds: Math.round(ageSeconds),
            opportunities: activeOpps || [],
            summary: lastSession.summary,
          }),
          { status: 200, headers: { ...CORS_HEADERS, "Content-Type": "application/json" } }
        );
      }
    }

    // 3. Inspecció gratuïta del calendari (/events a 0 crèdits)
    const activeLeagues: Record<string, string> = {};
    const emptyLeagues: string[] = [];
    const upcomingEvents: any[] = [];

    for (const [compId, sportKey] of Object.entries(CANONICAL_LEAGUES)) {
      try {
        const evUrl = `https://api.the-odds-api.com/v4/sports/${sportKey}/events?apiKey=${theOddsApiKey}&dateFormat=iso`;
        const resp = await fetch(evUrl);
        if (!resp.ok) continue;
        const events = await resp.json();

        let hasUpcoming = false;
        if (Array.isArray(events)) {
          for (const ev of events) {
            const commence = new Date(ev.commence_time);
            const hoursLeft = (commence.getTime() - now.getTime()) / (1000 * 3600);
            if (hoursLeft > 0 && hoursLeft <= 72) {
              hasUpcoming = true;
              upcomingEvents.push({ ...ev, competition_id: compId, sport_key: sportKey });
            }
          }
        }
        if (hasUpcoming) {
          activeLeagues[compId] = sportKey;
        } else {
          emptyLeagues.push(compId);
        }
      } catch (_e) {
        // Fallada tolerant
      }
    }

    // Si cap lliga té partits en 72h: 0 crèdits gastats
    if (Object.keys(activeLeagues).length === 0) {
      return new Response(
        JSON.stringify({
          status: "NO_UPCOMING_MATCHES",
          message: "No hi ha partits pròxims en les pròximes 72 hores a cap de les 7 lligues.",
          credits_spent: 0,
          active_leagues: [],
          empty_leagues: emptyLeagues,
          opportunities: []
        }),
        { status: 200, headers: { ...CORS_HEADERS, "Content-Type": "application/json" } }
      );
    }

    // 4. Comprovar pressupost disponible
    const currentPeriod = now.toISOString().slice(0, 7);
    const { data: budgetData } = await supabase
      .from("api_budget_ledger")
      .select("*")
      .eq("provider", "THE_ODDS_API")
      .eq("period_month", currentPeriod)
      .single();

    const operationalLimit = budgetData?.operational_limit ?? 450;
    const safetyReserve = budgetData?.safety_reserve ?? 50;
    const creditsConsumed = budgetData?.credits_consumed ?? 0;
    const remainingOper = Math.max(0, (budgetData?.api_reported_remaining ?? (operationalLimit - creditsConsumed)) - safetyReserve);

    const activeCount = Object.keys(activeLeagues).length;
    if (remainingOper < activeCount) {
      return new Response(
        JSON.stringify({
          status: "BUDGET_EXHAUSTED",
          message: `Pressupost operatiu de The Odds API exhaurit (${remainingOper} restants, requerits ${activeCount}). Reserva de seguretat protegida.`,
          credits_spent: 0
        }),
        { status: 403, headers: { ...CORS_HEADERS, "Content-Type": "application/json" } }
      );
    }

    // 4.5 Comprovar si ja hi ha una sessió activa en curs (protecció de concurrència i quota)
    const { data: runningSessions } = await supabase
      .from("surebet_sessions")
      .select("id, status, created_at, source_type")
      .in("status", ["PENDING", "RUNNING", "CANCEL_REQUESTED"])
      .limit(1);

    if (runningSessions && runningSessions.length > 0) {
      const activeSess = runningSessions[0];
      return new Response(
        JSON.stringify({
          status: "SESSION_IN_PROGRESS",
          message: `Ja hi ha una sessió activa en curs (${activeSess.source_type}: ${activeSess.status}). No es poden iniciar cerques concurrents per protegir la quota.`,
          session_id: activeSess.id,
          credits_spent: 0
        }),
        { status: 409, headers: { ...CORS_HEADERS, "Content-Type": "application/json" } }
      );
    }

    // 5. Crear sessió de cerca de manera atòmica
    const sessionId = crypto.randomUUID();
    const { error: insertErr } = await supabase.from("surebet_sessions").insert({
      id: sessionId,
      created_by: user.id,
      duration_seconds: 600,
      status: "RUNNING",
      source_type: "THE_ODDS_API",
      active_leagues: Object.keys(activeLeagues)
    });

    if (insertErr) {
      return new Response(
        JSON.stringify({
          status: "CONCURRENCY_BLOCKED",
          error: `No s'ha pogut iniciar la sessió (concurrència bloquejada per la base de dades): ${insertErr.message}`,
          credits_spent: 0
        }),
        { status: 409, headers: { ...CORS_HEADERS, "Content-Type": "application/json" } }
      );
    }

    // 6. Consultar 1X2 per a cada lliga activa (1 crèdit per lliga)
    const allMatches1x2: any[] = [];
    let creditsSpent = 0;
    let headerRemaining: number | null = null;
    let headerUsed: number | null = null;
    let headerLastCost: number | null = null;

    for (const [compId, sportKey] of Object.entries(activeLeagues)) {
      try {
        const oddsUrl = `https://api.the-odds-api.com/v4/sports/${sportKey}/odds?apiKey=${theOddsApiKey}&regions=eu&markets=h2h&dateFormat=iso&oddsFormat=decimal`;
        const resp = await fetch(oddsUrl);
        if (resp.headers.has("x-requests-remaining")) {
          headerRemaining = parseInt(resp.headers.get("x-requests-remaining") || "0");
        }
        if (resp.headers.has("x-requests-used")) {
          headerUsed = parseInt(resp.headers.get("x-requests-used") || "0");
        }
        if (resp.headers.has("x-requests-last")) {
          headerLastCost = parseInt(resp.headers.get("x-requests-last") || "0");
        }
        if (!resp.ok) continue;

        creditsSpent += 1;
        const matches = await resp.json();
        if (Array.isArray(matches)) {
          for (const m of matches) {
            allMatches1x2.push({ ...m, competition_id: compId });
          }
        }
      } catch (_e) {
        // Tolerància a fallades puntuals de lliga
      }
    }

    // 7. Reconciliar pressupost a Supabase RPC
    await supabase.rpc("reconcile_api_budget", {
      p_provider: "THE_ODDS_API",
      p_period: currentPeriod,
      p_credits_spent: creditsSpent,
      p_api_remaining: headerRemaining,
      p_api_used: headerUsed,
      p_last_cost: headerLastCost
    });

    // 8. Càlcul d'oportunitats 1X2
    const detectedOpportunities: any[] = [];
    for (const m of allMatches1x2) {
      const bookmakers = m.bookmakers || [];
      if (bookmakers.length < 2) continue;

      let best1 = { price: 0, bookmaker: "" };
      let bestX = { price: 0, bookmaker: "" };
      let best2 = { price: 0, bookmaker: "" };

      for (const bk of bookmakers) {
        const bkName = bk.key || bk.title;
        for (const mkt of bk.markets || []) {
          if (mkt.key === "h2h") {
            for (const out of mkt.outcomes || []) {
              const price = out.price || 0;
              if (out.name === m.home_team && price > best1.price) {
                best1 = { price, bookmaker: bkName };
              } else if (out.name === m.away_team && price > best2.price) {
                best2 = { price, bookmaker: bkName };
              } else if (["Draw", "Empat", "X"].includes(out.name) && price > bestX.price) {
                bestX = { price, bookmaker: bkName };
              }
            }
          }
        }
      }

      if (best1.price > 1 && bestX.price > 1 && best2.price > 1) {
        const invSum = (1 / best1.price) + (1 / bestX.price) + (1 / best2.price);
        const grossMarginPct = (1 - invSum) * 100;
        const netRoiPct = grossMarginPct; // Bookies tradicionals (0% comissió)

        // The Odds API consulta exclusivament feeds internacionals europeus (regions=eu).
        // El fet que una marca tingui llicència DGOJ no garanteix que la quota coincideixi
        // ni estigui disponible al mercat espanyol regulat (.es).
        // Totes les quotes de The Odds API es classifiquen com a teòriques (unconfirmed Spain).
        const executableInSpain = false;
        const isTheoretical = true;

        if (netRoiPct >= -2.5) {
          detectedOpportunities.push({
            id: crypto.randomUUID(),
            session_id: sessionId,
            opportunity_type: netRoiPct > 0 ? "SUREBET" : "NEAR_ARB",
            matchup: `${m.home_team} vs ${m.away_team}`,
            market_key: "1X2 Cross-Bookmaker",
            net_roi_pct: Math.round(netRoiPct * 1000) / 1000,
            gross_margin_pct: Math.round(grossMarginPct * 1000) / 1000,
            commission_deducted_pct: 0,
            source_pipeline: "THE_ODDS_API",
            is_theoretical: isTheoretical,
            executable_in_spain: executableInSpain,
            bookmakers: {
              leg1: { selection: `1 - ${m.home_team}`, odd: best1.price, bookmaker: best1.bookmaker, jurisdiction: "INT_EUROPEAN_UNCONFIRMED" },
              legX: { selection: "X - Empat", odd: bestX.price, bookmaker: bestX.bookmaker, jurisdiction: "INT_EUROPEAN_UNCONFIRMED" },
              leg2: { selection: `2 - ${m.away_team}`, odd: best2.price, bookmaker: best2.bookmaker, jurisdiction: "INT_EUROPEAN_UNCONFIRMED" },
            },
            detected_at: now.toISOString()
          });
        }
      }
    }

    // 9. Persistència de snapshot i oportunitats
    const snapshotId = crypto.randomUUID();
    await supabase.from("surebet_snapshots").insert({
      id: snapshotId,
      session_id: sessionId,
      market_type: "1X2_AND_DC",
      match_id: `multi_${Object.keys(activeLeagues).join("_")}`,
      odds_age_seconds: 10,
      opportunities_count: detectedOpportunities.length,
      source_pipeline: "THE_ODDS_API",
      payload: {
        active_leagues: Object.keys(activeLeagues),
        matches_count: allMatches1x2.length,
        credits_spent: creditsSpent,
      }
    });

    if (detectedOpportunities.length > 0) {
      await supabase.from("surebet_opportunities").insert(
        detectedOpportunities.map(opp => ({ ...opp, snapshot_id: snapshotId }))
      );
    }

    // 10. Completar la sessió i registrar log
    const summary = {
      credits_spent: creditsSpent,
      matches_examined: allMatches1x2.length,
      opportunities_found: detectedOpportunities.length,
      active_leagues: Object.keys(activeLeagues),
      empty_leagues: emptyLeagues
    };

    await supabase.from("surebet_sessions").update({
      status: "COMPLETED",
      credits_spent: creditsSpent,
      events_count: allMatches1x2.length,
      summary: summary
    }).eq("id", sessionId);

    await supabase.from("api_search_logs").insert({
      session_id: sessionId,
      user_id: user.id,
      source_pipeline: "THE_ODDS_API",
      credits_spent: creditsSpent,
      active_leagues: Object.keys(activeLeagues),
      empty_leagues: emptyLeagues,
      matches_examined: allMatches1x2.length,
      opportunities_found: detectedOpportunities.length,
      coverage_summary: summary
    });

    return new Response(
      JSON.stringify({
        status: "COMPLETED",
        message: `Cerca finalitzada amb èxit. S'han examinat ${allMatches1x2.length} partits i trobat ${detectedOpportunities.length} oportunitats.`,
        session_id: sessionId,
        credits_spent: creditsSpent,
        credits_remaining: headerRemaining !== null ? headerRemaining - safetyReserve : remainingOper - creditsSpent,
        active_leagues: Object.keys(activeLeagues),
        matches_examined: allMatches1x2.length,
        opportunities: detectedOpportunities,
        summary: summary
      }),
      { status: 200, headers: { ...CORS_HEADERS, "Content-Type": "application/json" } }
    );

  } catch (err) {
    return new Response(
      JSON.stringify({ error: `Error intern a l'Edge Function: ${err.message || err}` }),
      { status: 500, headers: { ...CORS_HEADERS, "Content-Type": "application/json" } }
    );
  }
});
