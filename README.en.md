# Prediccions Lliga: Probabilistic System and Computational Framework for Multi-League Soccer Modeling
### Applied Probability, Bivariate Poisson Processes, Scoring Rules and Quantitative Market Analysis

[![Python 3.14+](https://img.shields.io/badge/python-3.14+-blue.svg)](https://www.python.org/)
[![Test Suite](https://img.shields.io/badge/pytest-681%20passed-brightgreen.svg)](tests/)
[![Database](https://img.shields.io/badge/database-SQLite%20%7C%20Supabase%20PostgreSQL-darkblue.svg)](database/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Documentation](https://img.shields.io/badge/docs-Mathematical%20Monograph-orange.svg)](docs/MATHEMATICAL_MODEL.md)

🌐 **Language / Idioma:** [Català (README.md)](README.md) | [English (Current)](README.en.md) | [Full Mathematical Monograph](docs/MATHEMATICAL_MODEL.md)

---

## Executive Summary / Abstract

**Prediccions Lliga** is a scientific and computational platform designed for probabilistic modeling, statistical inference, and quantitative market analysis of association football matches across major European domestic leagues (**LaLiga EA Sports, Premier League, LaLiga Hypermotion, EFL Championship, Bundesliga, Ligue 1, and Serie A**).

Rejecting opaque black-box machine learning heuristics, the system is grounded in the **classical theory of discrete counting processes**. The forecasting core estimates exact match scorelines as a two-dimensional discrete random variable $(X, Y) \in \mathbb{N}_0 \times \mathbb{N}_0$ via **independent Poisson distributions modified by a bivariate Dixon-Coles adjustment matrix ($\rho = -0.10$)**. The resulting normalized joint probability mass distribution is deterministically projected to derive all primary match markets (1X2, Double Chance, Over/Under Goal totals, and Both Teams to Score).

Model predictive skill and calibration are evaluated strictly using **Proper Scoring Rules**—primarily the **Ranked Probability Score (RPS)**, the **Multi-class Brier Score**, and **Logarithmic Loss (Cross-Entropy)**—categorically avoiding deceptive metrics such as classification Accuracy. On the quantitative execution layer, the platform evaluates **Expected Value (+EV%) with rigorous push/void probability correction**, allocates capital defensively via the **Fractional Kelly Criterion ($\kappa = 0.25$)**, and identifies **cross-bookmaker arbitrage opportunities (surebets)** through exact discrete integer stake optimization and comprehensive exchange commission modeling.

---

## Table of Contents

1. [Mathematical Architecture of the Goal Model](#1-mathematical-architecture-of-the-goal-model)
   - [Counting Process Formulation](#11-counting-process-formulation)
   - [Expected Intensities: League Profiles, Dynamic Ranks, and Rolling xG](#12-expected-intensities-league-profiles-dynamic-ranks-and-rolling-xg)
   - [Bivariate Dependency Adjustment: Dixon-Coles Model](#13-bivariate-dependency-adjustment-dixon-coles-model)
   - [Matrix Truncation, Discretization, and Kolmogorov Normalization](#14-matrix-truncation-discretization-and-kolmogorov-normalization)
   - [Deterministic Market Projections (1X2, Over/Under, BTTS)](#15-deterministic-market-projections-1x2-overunder-btts)
2. [Dynamic Team Quality Rating Systems](#2-dynamic-team-quality-rating-systems)
   - [Logistic Elo with Logarithmic Goal Margin Multiplier](#21-logistic-elo-with-logarithmic-goal-margin-multiplier)
   - [Tri-Ranks Engine (65% xG + 35% Observed Goals Blending)](#22-tri-ranks-engine-65-xg--35-observed-goals-blending)
3. [Secondary Stochastic Processes: Corners and Discipline](#3-secondary-stochastic-processes-corners-and-discipline)
   - [Corner Kick Distribution](#31-corner-kick-distribution)
   - [Card Model: Referee Severity, Tactical Rivalry, and Table Tension](#32-card-model-referee-severity-tactical-rivalry-and-table-tension)
4. [Statistical Validation and Proper Scoring Rules](#4-statistical-validation-and-proper-scoring-rules)
   - [Ranked Probability Score (RPS) for Ordered Categories](#41-ranked-probability-score-rps-for-ordered-categories)
   - [Brier Score and Logarithmic Loss](#42-brier-score-and-logarithmic-loss)
   - [The Accuracy Fallacy and Walk-Forward Backtesting Protocol](#43-the-accuracy-fallacy-and-walk-forward-backtesting-protocol)
5. [Computational Architecture and Data Pipeline](#5-computational-architecture-and-data-pipeline)
   - [Autonomous Multi-League Orchestrator (`daily_runner.py`)](#51-autonomous-multi-league-orchestrator-daily_runnerpy)
   - [Real-Time Ingestion and Bookmaker Scrapers](#52-real-time-ingestion-and-bookmaker-scrapers)
   - [4-Tier Freshness and Time-Skew Policy](#53-4-tier-freshness-and-time-skew-policy)
   - [Hybrid Persistence: Local SQLite and Supabase PostgreSQL Cloud](#54-hybrid-persistence-local-sqlite-and-supabase-postgresql-cloud)
6. [Quantitative Financial and Market Applications](#6-quantitative-financial-and-market-applications)
   - [Expected Value (+EV%) with Push/Void Support](#61-expected-value-ev-with-pushvoid-support)
   - [Defensive Fractional Kelly Criterion](#62-defensive-fractional-kelly-criterion)
   - [Arbitrage Conditions and Exchange Commission Profiles](#63-arbitrage-conditions-and-exchange-commission-profiles)
   - [Exact Discrete Integer Staking Optimization](#64-exact-discrete-integer-staking-optimization)
7. [Comprehensive Verified Numerical Example](#7-comprehensive-verified-numerical-example)
8. [Verification, Test Suite, and Reproducibility](#8-verification-test-suite-and-reproducibility)
9. [Intellectual Transparency and Model Limitations](#9-intellectual-transparency-and-model-limitations)

---

## 1. Mathematical Architecture of the Goal Model

### 1.1 Counting Process Formulation
Let a football match between home team $H$ and away team $A$ be modeled as a continuous-time stochastic counting process evaluated over match duration $T = 90$ minutes. The number of goals scored by each team is represented by discrete random variables $X \in \mathbb{N}_0$ (home goals) and $Y \in \mathbb{N}_0$ (away goals).

Under the classical Poisson postulate, the probability that a team scores exactly $k$ goals given an expected intensity $\lambda$ is:
$$P(K = k; \lambda) = \frac{\lambda^k e^{-\lambda}}{k!}, \quad k \in \{0, 1, 2, \dots\}$$
with expectation $\mathbb{E}[K] = \lambda$ and variance $\text{Var}(K) = \lambda$.

### 1.2 Expected Intensities: League Profiles, Dynamic Ranks, and Rolling xG
Implemented in [`model/goal_model.py`](file:///c:/Fortia/Personal/PreddicionsLliga/model/goal_model.py), the expected scoring intensities $\lambda_H$ and $\lambda_A$ are derived through a calibrated integration of:
1. **Competition Baselines:** Historical baseline scoring rates $\mu_H, \mu_A$ per league.
2. **Dynamic Offensive and Defensive Ranks:** Position indices $R_{off}, R_{def} \in [1.0, N]$.
3. **Rolling Expected Goals (xG):** Moving averages of recent shot-generation and concession quality.

```
                              ┌─────────────────────────────────────────┐
                              │ League Baseline Profile (μ_H, μ_A, N)   │
                              └────────────────────┬────────────────────┘
                                                   │
                 ┌─────────────────────────────────┴─────────────────────────────────┐
                 ▼                                                                   ▼
┌─────────────────────────────────┐                                 ┌─────────────────────────────────┐
│     Theoretical Rank Model      │                                 │     Empirical Rolling xG Model  │
│  α_H = clip(1 + 0.35·ΔR_off)    │                                 │  λ_emp,H = (xG_for + xG_opp)/2  │
│  β_A = clip(1 + 0.30·ΔR_def)    │                                 │  λ_emp,A = (xG_for + xG_opp)/2  │
│  λ_rank,H = μ_H · α_H · β_A     │                                 └────────────────┬────────────────┘
└────────────────┬────────────────┘                                                  │
                 │                                                                   │
                 └───────────────────────────────┬───────────────────────────────────┘
                                                 ▼
                              ┌─────────────────────────────────────┐
                              │     Calibrated Blended Intensity:   │
                              │  λ = (0.60·λ_rank + 0.40·λ_emp)·Rest │
                              │  Clamping: λ_H ≥ 0.20, λ_A ≥ 0.15   │
                              └──────────────────┬──────────────────┘
                                                 ▼
                              ┌─────────────────────────────────────┐
                              │ Dixon-Coles 8x8 Matrix (ρ = -0.10)  │
                              │ Normalization: M_i,j / ∑∑ M_u,v     │
                              └─────────────────────────────────────┘
```

#### Rank Scaling Factors
Let $M = \frac{N}{2}$ denote the league midpoint for a division with $N$ clubs:
$$\alpha_H = \text{clip}\left(1.0 + 0.35 \cdot \frac{M - R_{off, H}}{M}, \; 0.60, \; 1.50\right)$$
$$\beta_A = \text{clip}\left(1.0 + 0.30 \cdot \frac{R_{def, A} - M}{M}, \; 0.60, \; 1.50\right)$$

$$\alpha_A = \text{clip}\left(1.0 + 0.35 \cdot \frac{M - R_{off, A}}{M}, \; 0.60, \; 1.50\right)$$
$$\beta_H = \text{clip}\left(1.0 + 0.30 \cdot \frac{R_{def, H} - M}{M}, \; 0.60, \; 1.50\right)$$

The theoretical rank-based intensities are:
$$\lambda_{rank, H} = \mu_H \cdot \alpha_H \cdot \beta_A, \quad \lambda_{rank, A} = \mu_A \cdot \alpha_A \cdot \beta_H$$

#### Blended Formulation with Rolling xG and Rest Factors
$$\lambda_H = \max\left(0.20, \; (0.60 \cdot \lambda_{rank, H} + 0.40 \cdot \lambda_{emp, H}) \cdot Rest_H + 0.5 \cdot HA\right)$$
$$\lambda_A = \max\left(0.15, \; (0.60 \cdot \lambda_{rank, A} + 0.40 \cdot \lambda_{emp, A}) \cdot Rest_A - 0.2 \cdot HA\right)$$
where $\lambda_{emp, H} = \frac{xG_{for, H} + xG_{against, A}}{2}$ and $\lambda_{emp, A} = \frac{xG_{for, A} + xG_{against, H}}{2}$.

### 1.3 Bivariate Dependency Adjustment: Dixon-Coles Model
Strict independence fails in low-scoring soccer scorelines due to tactical risk aversion when drawn. The system applies the multiplicative distortion developed by Dixon & Coles (1997):
$$P(X = x, Y = y) = \tau_{\lambda_H, \lambda_A}(x, y) \cdot \frac{\lambda_H^x e^{-\lambda_H}}{x!} \cdot \frac{\lambda_A^y e^{-\lambda_A}}{y!}$$

where the modification function is specified as:
$$\tau_{\lambda_H, \lambda_A}(x, y) = \begin{cases}
1 - \lambda_H \lambda_A \rho & \text{for } x = 0, y = 0 \\
1 + \lambda_H \rho & \text{for } x = 0, y = 1 \\
1 + \lambda_A \rho & \text{for } x = 1, y = 0 \\
1 - \rho & \text{for } x = 1, y = 1 \\
1 & \text{for all } x \ge 2 \text{ or } y \ge 2
\end{cases}$$

The parameter is empirically set to $\mathbf{\rho = -0.10}$. This increases probability mass for tactical low-scoring draws ($0-0$ and $1-1$) while slightly decreasing $1-0$ and $0-1$ occurrences.

### 1.4 Matrix Truncation, Discretization, and Kolmogorov Normalization
The engine constructs a discrete probability matrix $M$ over an $8 \times 8$ grid ($x, y \in \{0, 1, \dots, 7\}$). To compensate for the truncated tail and non-linear distortion $\tau(x, y)$, the matrix is strictly normalized:
$$M_{i,j} = \frac{M_{i,j}^{(0)}}{\sum_{u=0}^7 \sum_{v=0}^7 M_{u,v}^{(0)}} \implies \sum_{i=0}^7 \sum_{j=0}^7 M_{i,j} \equiv 1.0$$

### 1.5 Deterministic Market Projections (1X2, Over/Under, BTTS)
Derived betting markets represent deterministic summations of disjoint matrix regions:

- **1X2 (Match Outcome):**
  $$P(\text{Home Win}) = \sum_{i > j} M_{i,j}, \quad P(\text{Draw}) = \sum_{i = j} M_{i,j}, \quad P(\text{Away Win}) = \sum_{i < j} M_{i,j}$$
- **Total Goals (Over / Under $L$):**
  $$P(\text{Over } L) = \sum_{i + j > L} M_{i,j}, \quad P(\text{Under } L) = 1.0 - P(\text{Over } L)$$
- **Both Teams to Score (BTTS):**
  $$P(\text{BTTS}_{\text{Yes}}) = 1.0 - \sum_{j=0}^7 M_{0,j} - \sum_{i=0}^7 M_{i,0} + M_{0,0}$$
- **Double Chance:**
  $$P(1X) = P(1) + P(X), \quad P(X2) = P(X) + P(2), \quad P(12) = P(1) + P(2)$$

---

## 2. Dynamic Team Quality Rating Systems

Implemented in [`model/rank_engine.py`](file:///c:/Fortia/Personal/PreddicionsLliga/model/rank_engine.py), club trajectories are tracked dynamically through two interacting frameworks.

### 2.1 Logistic Elo with Logarithmic Goal Margin Multiplier
Expected match outcomes incorporate a $+50$ point home pitch advantage:
$$E_H = \frac{1}{1 + 10^{\frac{\text{Elo}_A - \text{Elo}_H - 50}{400}}}, \quad E_A = 1.0 - E_H$$

The update factor $K$ scales logarithmically with observed goal differential:
$$G = \ln(\max(1, |g_H - g_A|) + 1.0)$$
$$K_{eff} = 32.0 \cdot G$$
$$\Delta \text{Elo}_H = K_{eff} \cdot (S_H - E_H), \quad \Delta \text{Elo}_A = -\Delta \text{Elo}_H$$
where $S_H \in \{1.0, 0.5, 0.0\}$ reflects win, draw, or loss.

### 2.2 Tri-Ranks Engine (65% xG + 35% Observed Goals Blending)
Observed goals exhibit high variance due to low-frequency chance events. The model isolates underlying performance through a blended output metric:
$$\text{Output}_{eff, off} = 0.65 \cdot xG_{for} + 0.35 \cdot g_{scored}$$
$$\Delta_{off} = \text{Output}_{eff, off} - \lambda_{exp, model}$$
$$\text{Shift}_{off} = -0.30 \cdot \Delta_{off}$$
$$R_{off}^{(t+1)} = \text{clip}\left(R_{off}^{(t)} + \text{Shift}_{off}, \; 1.0, \; N_{teams}\right)$$

Similarly for defensive solidity:
$$\text{Output}_{eff, def} = 0.65 \cdot xG_{against} + 0.35 \cdot g_{conceded}$$
$$\Delta_{def} = \lambda_{exp, opp} - \text{Output}_{eff, def}$$
$$\text{Shift}_{def} = -0.30 \cdot \Delta_{def}$$
$$R_{def}^{(t+1)} = \text{clip}\left(R_{def}^{(t)} + \text{Shift}_{def}, \; 1.0, \; N_{teams}\right)$$

The unified general ranking is: $R_{gen} = \frac{R_{off} + R_{def}}{2.0}$.

---

## 3. Secondary Stochastic Processes: Corners and Discipline

### 3.1 Corner Kick Distribution
In [`model/corner_model.py`](file:///c:/Fortia/Personal/PreddicionsLliga/model/corner_model.py), corner kicks are evaluated via a 1D Poisson counting process across total match volume:
$$\lambda_{C, H} = \frac{C_{for5, H} + C_{against5, A}}{2} \cdot 1.05, \quad \lambda_{C, A} = \frac{C_{for5, A} + C_{against5, H}}{2} \cdot 0.95$$
$$\lambda_{C, total} = \lambda_{C, H} + \lambda_{C, A}$$
$$P(\text{Over } K) = \sum_{k = K+1}^{24} \frac{\lambda_{C, total}^k e^{-\lambda_{C, total}}}{k!}$$

### 3.2 Card Model: Referee Severity, Tactical Rivalry, and Table Tension
In [`model/card_model.py`](file:///c:/Fortia/Personal/PreddicionsLliga/model/card_model.py), booking expectations balance the assigned official's profile (55%) with team behavioral trends (45%):
$$\lambda_{cards} = 0.55 \cdot (Y_{ref} \cdot S_{ref}) + 0.45 \cdot (Cards_{5, H} + Cards_{5, A}) + \text{Tactical Boosts}$$

where tactical modifiers include:
- $\text{Boost}_{derby} = +0.90$ for designated historical rivalries (e.g. El Clásico, Derby Sevillano, North London Derby).
- $\text{Boost}_{ko} = +0.50$ for single-elimination knockout matches.
- $\text{Boost}_{tension} = \max(0.0, \; (10.0 - |R_{gen, H} - R_{gen, A}|) \cdot 0.04)$ for closely contested table positions.

Red card incidence follows a Poisson hazard rate:
$$P(\text{Red Card} \ge 1) = 1.0 - e^{-\lambda_{red}}, \quad \lambda_{red} = Red_{ref} \cdot (1.0 + 0.30 \cdot \mathbb{I}_{derby})$$

---

## 4. Statistical Validation and Proper Scoring Rules

Evaluating a probabilistic forecasting model with **Accuracy** is mathematically fallacious: assigning a 99% probability to an outcome with a true calibrated probability of 60% yields 100% accuracy whenever that event occurs, masking severe probability distortion.

Validation requires **Proper Scoring Rules**:

### 4.1 Ranked Probability Score (RPS) for Ordered Categories
In soccer match forecasting, the outcome space has a natural ordering:
$$\text{Home Win (1)} < \text{Draw (X)} < \text{Away Win (2)}$$

Epstein's Ranked Probability Score evaluates cumulative probability distances between forecast vector $\mathbf{p} = (p_1, p_x, p_2)$ and outcome indicator $\mathbf{y} = (y_1, y_x, y_2)$:

$$RPS = \frac{1}{2} \left[ (p_1 - y_1)^2 + ((p_1 + p_x) - (y_1 + y_x))^2 \right]$$

- $RPS \in [0, 1]$, where $0.0$ indicates perfect foresight.
- An uninformative baseline model assigning long-run historical frequencies ($p_1 = 0.45, p_x = 0.28, p_2 = 0.27$) achieves an average RPS of $\approx 0.198 - 0.205$.
- Scores systematically below $0.190$ demonstrate statistically significant informational gain over consensus.

### 4.2 Brier Score and Logarithmic Loss
For binary markets and general multi-class evaluation:
- **Multi-class Brier Score:** $BS = \frac{1}{N} \sum_{n=1}^N \sum_{k=1}^K (p_{n,k} - y_{n,k})^2$
- **Logarithmic Loss (Cross-Entropy):** $\text{LogLoss} = -\frac{1}{N} \sum_{n=1}^N \sum_{k=1}^K y_{n,k} \ln(p_{n,k})$

### 4.3 The Accuracy Fallacy and Walk-Forward Backtesting Protocol
To eliminate **lookahead bias**:
1. Prior to round $T$, all team parameters are frozen at timestamp $t_0$.
2. Forecasts are generated for all fixtures in round $T$.
3. Post-match, RPS, Brier Score, and realized PnL are recorded.
4. Dynamic parameters (Elo, Tri-Ranks) are updated strictly afterwards for round $T+1$.

---

## 5. Computational Architecture and Data Pipeline

The project operates as an autonomous, distributed data pipeline supporting 7 European competitions:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          DAILY ORCHESTRATION                                │
│                     daily_runner.py / GitHub Actions                        │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
            ┌──────────────────────────┴──────────────────────────┐
            ▼                                                     ▼
┌───────────────────────────────┐             ┌───────────────────────────────┐
│     Flashscore Live Crawler   │             │   Real-Time Bookmaker Scrapers│
│  - Official schedule          │             │  - Winamax (DOM crawler)      │
│  - Match results and xG       │             │  - Bet365 (Moviodds API)      │
│  - Corners, cards, referees   │             │  - Betway (HTML Parser)       │
└───────────────┬───────────────┘             │  - The Odds API (+ Cost Guard)│
                │                             └───────────────┬───────────────┘
                ▼                                             ▼
┌───────────────────────────────┐             ┌───────────────────────────────┐
│     SQLite Local Database     │             │    4-Tier Freshness Engine    │
│   (ACID Local - data/*.db)    │             │   VERIFIED_FRESH ≤ 60s, Δ≤30s │
└───────────────┬───────────────┘             └───────────────┬───────────────┘
                │                                             │
                └──────────────────────┬──────────────────────┘
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                        COMPUTATIONAL ENGINES                                │
│  - GoalModel (Poisson Dixon-Coles)    - RankEngine (Elo + Tri-Ranks)        │
│  - ValueBetEngine (+EV & Kelly)       - ArbitrageEngine (Discrete Grid)     │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
            ┌──────────────────────────┴──────────────────────────┐
            ▼                                                     ▼
┌───────────────────────────────┐             ┌───────────────────────────────┐
│    Executive PDF Reports      │             │    Supabase PostgreSQL       │
│    (PDFReportGenerator)       │             │    - Realtime Broadcast       │
│    Multi-League & Single-Round│             │    - Edge Functions           │
│    Publishing                 │             │    - Vercel Web Dashboard     │
└───────────────────────────────┘             └───────────────────────────────┘
```

### 5.1 Autonomous Multi-League Orchestrator (`daily_runner.py`)
Operating via [`daily_runner.py`](file:///c:/Fortia/Personal/PreddicionsLliga/daily_runner.py) across two operational cycles:
- **Post-Round Cycle:** Ingests official match results, xG metrics, cards, and corners; updates Elo ratings and settles portfolio tracking.
- **Pre-Round Cycle:** Triggers within the official referee appointment window (28 hours prior to kickoff), ensuring CTA and PGMOL referee assignments are officially resolved before generating predictions.

### 5.2 Real-Time Ingestion and Bookmaker Scrapers
- **Flashscore Crawler ([`scraper/live_crawler.py`](file:///c:/Fortia/Personal/PreddicionsLliga/scraper/live_crawler.py)):** Fixture schedules, official statistics, and referee lineups.
- **Winamax Scraper ([`scraper/winamax_scraper.py`](file:///c:/Fortia/Personal/PreddicionsLliga/scraper/winamax_scraper.py)):** Spanish regulated market (DGOJ) feed parsing.
- **Bet365 Scraper ([`scraper/bet365_scraper.py`](file:///c:/Fortia/Personal/PreddicionsLliga/scraper/bet365_scraper.py)):** Integration via the Moviodds provider feed.
- **Betway Scraper ([`scraper/betway_scraper.py`](file:///c:/Fortia/Personal/PreddicionsLliga/scraper/betway_scraper.py)):** 1X2 and Over/Under market coverage.
- **The Odds API Client ([`scraper/odds_api_client.py`](file:///c:/Fortia/Personal/PreddicionsLliga/scraper/odds_api_client.py)):** High-efficiency quota tracking with **Cost Guard**, budget reservation, and automated circuit breaking.

### 5.3 4-Tier Freshness and Time-Skew Policy
To guard against phantom arbitrage caused by market skew, all bookmaker quotes are classified into 4 strict tiers:

| Freshness Tier | Maximum Age | Cross-Bookmaker Skew ($\Delta t$) | Operational Action |
| :--- | :---: | :---: | :--- |
| **`VERIFIED_FRESH`** | $\le 60$ seconds | $\le 30$ seconds | Active automated execution and alerts |
| **`POTENTIALLY_STALE`** | $\le 300$ seconds | $\le 90$ seconds | Near-Arb monitoring only; no execution |
| **`RECENT_HISTORICAL`** | $\le 1800$ seconds (30m) | $> 90$ seconds | Trend and historical analytics |
| **`EXCLUDED`** | $> 1800$ seconds | Any | Immediate rejection and memory purge |

### 5.4 Hybrid Persistence: Local SQLite and Supabase PostgreSQL Cloud
- **Local SQLite ([`data/laliga.db`](file:///c:/Fortia/Personal/PreddicionsLliga/data/laliga.db)):** Zero-latency relational ACID storage powering daily pipeline calculations, xG updates, and PDF report generation.
- **Supabase Cloud (PostgreSQL 15):** Distributed persistence featuring Row Level Security (RLS), `SECURITY DEFINER` stored procedures, atomic budget reservation, and real-time state broadcast to the Vercel web frontend.

---

## 6. Quantitative Financial and Market Applications

### 6.1 Expected Value (+EV%) with Push/Void Support
In [`engine/value_bet_engine.py`](file:///c:/Fortia/Personal/PreddicionsLliga/engine/value_bet_engine.py), expected value explicitly accounts for stake refund probability ($p_v$):
$$EV\% = \left(p_w \cdot Q + p_v - 1.0\right) \times 100\%$$
$$Q_{fair} = \frac{1.0 - p_v}{p_w}, \quad P_{imp} = \frac{1.0 - p_v}{Q} \times 100\%$$

Value Classifications:
- $EV \ge +15.0\%$: 🔥 Excellent Value
- $+5.0\% \le EV < +15.0\%$: ⭐ Strong Value
- $0.0\% < EV < +5.0\%$: ✓ Marginal Value
- $EV \le 0.0\%$: ❌ Negative Expectation (Avoid)

### 6.2 Defensive Fractional Kelly Criterion
With net decimal return $b = Q - 1.0$:
$$f^* = \frac{b \cdot p_w - p_l}{b \cdot (p_w + p_l)}$$
To insulate the portfolio against model parameter uncertainty, the engine applies a conservative factor $\kappa = 0.25$ (Quarter-Kelly):
$$f_{executed} = \max\left(0, \; 0.25 \cdot f^* \cdot 100\%\right)$$

### 6.3 Arbitrage Conditions and Exchange Commission Profiles
In [`engine/arbitrage_engine.py`](file:///c:/Fortia/Personal/PreddicionsLliga/engine/arbitrage_engine.py), cross-bookmaker arbitrage requires:
$$S_{inv} = \sum_{k=1}^K \frac{1}{Q_k} < 1.0 \implies \text{Gross Margin} < 100\%$$
$$\text{Gross Profit}\% = \left(\frac{1}{S_{inv}} - 1.0\right) \times 100\%$$

Supported exchange commission models:
1. **`NET_MARKET_WINNINGS`:** Commission deducted exclusively from positive aggregated net market profit ($\text{Fee} = c \cdot \max(0, PnL)$). Applicable to Betfair (2% ES/DGOJ), Matchbook (4% ES), and Smarkets Standard (2%).
2. **`PER_BET_SETTLEMENT`:** Turnover fee charged on every settled position ($\text{Fee} = c \cdot \text{Stake}$). Applicable to Smarkets Pro (1%) and Smarkets Select (3%).

### 6.4 Exact Discrete Integer Staking Optimization
Continuous analytical staking formulas ($s_k = \frac{B / Q_k}{S_{inv}}$) produce irregular currency fractions that trigger risk controls. The engine executes an **exhaustive discrete integer grid search**:
- 2-way markets: 1D grid search over $s_1 \in \{1, \dots, B-1\}, s_2 = B - s_1$.
- 3-way markets: 2D grid search over $s_1, s_2 \ge 1, s_3 = B - s_1 - s_2 \ge 1$.
- Objective: $\max_{\mathbf{s}} \min_{scen} PnL_{net}(scen, \mathbf{s})$.

---

## 7. Comprehensive Verified Numerical Example

Fixture: **Athletic Club (Home)** vs **Real Sociedad (Away)** — **LaLiga EA Sports**.

### Input Parameters
- **Athletic Club:** $R_{off} = 3.0$, $R_{def} = 4.0$, $\text{Rolling } xG_{for} = 1.65$, $\text{Rolling } xG_{against} = 1.10$.
- **Real Sociedad:** $R_{off} = 6.0$, $R_{def} = 5.0$, $\text{Rolling } xG_{for} = 1.30$, $\text{Rolling } xG_{against} = 1.25$.
- Baseline Profile: $\mu_H = 1.42, \mu_A = 1.12, N = 20, M = 10.0$.

### Intensity Derivations
$$\alpha_H = 1.0 + 0.35 \cdot \frac{10 - 3}{10} = 1.245, \quad \beta_A = 1.0 + 0.30 \cdot \frac{5 - 10}{10} = 0.850$$
$$\lambda_{rank, H} = 1.42 \cdot 1.245 \cdot 0.850 = 1.5027, \quad \lambda_{emp, H} = \frac{1.65 + 1.25}{2} = 1.4500$$
$$\lambda_H = 0.60 \cdot 1.5027 + 0.40 \cdot 1.4500 = \mathbf{1.4816} \approx \mathbf{1.48}$$

$$\alpha_A = 1.0 + 0.35 \cdot \frac{10 - 6}{10} = 1.140, \quad \beta_H = 1.0 + 0.30 \cdot \frac{4 - 10}{10} = 0.820$$
$$\lambda_{rank, A} = 1.12 \cdot 1.140 \cdot 0.820 = 1.0470, \quad \lambda_{emp, A} = \frac{1.30 + 1.10}{2} = 1.2000$$
$$\lambda_A = 0.60 \cdot 1.0470 + 0.40 \cdot 1.2000 = \mathbf{1.1082} \approx \mathbf{1.11}$$

### Normalized Dixon-Coles Matrix Outputs ($8 \times 8, \rho = -0.10$)

```
                 Most Probable Scorelines:
                 1 - 1 : 13.55%
                 1 - 0 :  9.89%
                 2 - 1 :  9.13%
                 0 - 0 :  8.74%
                 2 - 0 :  8.24%
```

- **1X2 Probabilities:**
  - $P(\text{Home Win "1"}) = \mathbf{44.5\%}$
  - $P(\text{Draw "X"}) = \mathbf{28.4\%}$
  - $P(\text{Away Win "2"}) = \mathbf{27.1\%}$
- **Goal Totals (Over / Under):**
  - $P(\text{Over 1.5}) = \mathbf{74.3\%}$ / $P(\text{Under 1.5}) = \mathbf{25.7\%}$
  - $P(\text{Over 2.5}) = \mathbf{47.9\%}$ / $P(\text{Under 2.5}) = \mathbf{52.1\%}$
  - $P(\text{Over 3.5}) = \mathbf{26.2\%}$ / $P(\text{Under 3.5}) = \mathbf{73.8\%}$
- **Both Teams to Score (BTTS):**
  - $P(\text{BTTS Yes}) = \mathbf{53.0\%}$ / $P(\text{BTTS No}) = \mathbf{47.0\%}$

### Market Value Evaluation (+EV%)
Given an offered market odd $Q = 2.45$ for Home Win:
- Fair Odd: $Q_{fair} = \frac{1.0}{0.445} = \mathbf{2.25}$
- Market Implied Probability: $P_{imp} = \frac{1.0}{2.45} = \mathbf{40.8\%}$
- **Expected Value:** $EV\% = (0.445 \cdot 2.45 - 1.0) \times 100\% = \mathbf{+9.0\%}$ (⭐ Strong Value)
- **Defensive Kelly Stake (1/4):** $\mathbf{1.6\% \text{ of Bankroll}}$

---

## 8. Verification, Test Suite, and Reproducibility

Computational and mathematical integrity is strictly enforced across a 681-test automated suite executed via `pytest`:

```bash
# Execute the full automated test suite (681 unit & integration tests)
python -m pytest tests/

# Targeted mathematical model tests
python -m pytest tests/test_dixon_coles_unification.py tests/test_rank_update_and_model.py
```

Current test execution status:
```
======================= 681 passed in 80.62s =======================
```

The test harness guarantees:
- Bivariate Dixon-Coles normalization and probability mass convergence.
- Numerical stability of Elo rating updates with logarithmic goal multipliers.
- Exact push/void probability propagation in Kelly and EV engines.
- Atomic session concurrency and state isolation in Supabase.
- Strict enforcement of timestamp freshness and time-skew limits.

---

## 9. Intellectual Transparency and Model Limitations

To preserve academic honesty and scientific rigor, the empirical assumptions of the model are explicitly documented:

1. **Empirical Parameter Calibration vs Online MLE:**
   - The low-score correlation coefficient $\rho = -0.10$ is calibrated heuristically from European historical datasets. Real-time Maximum Likelihood Estimation (MLE) or Markov Chain Monte Carlo (MCMC) sampling is not run during live execution cycles.
   - The 60/40 (ranks vs rolling xG) and 65/35 (xG vs goals in Tri-Ranks) blendings are empirical variance-minimizing weights rather than Bayesian analytical posteriors.
2. **Absence of Black-Box Machine Learning:**
   - The platform deliberately avoids opaque neural networks or ensemble boosting models, prioritizing fully interpretable, mathematically auditable parametric counting processes.
3. **Absence of Monte Carlo Market Projections:**
   - Market probabilities are calculated via exact closed-form matrix summations over the discrete $8 \times 8$ grid, eliminating stochastic estimation noise.
4. **Pre-Match Static Assumptions:**
   - The model is calibrated for pre-kickoff evaluation. It does not solve stochastic differential jump-diffusion equations for in-play (live) market pricing.

---

## License and Citation

This software is released under the terms of the [MIT License](LICENSE).

```bibtex
@software{prediccions_lliga_2026,
  author = {Prediccions Lliga Development Team},
  title = {Prediccions Lliga: Mathematical Modeling, Applied Probability and Quantitative Football Forecasting Architecture},
  year = {2026},
  url = {https://github.com/fortiaarumi/Prediccions}
}
```
