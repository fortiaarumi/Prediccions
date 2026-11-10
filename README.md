# Prediccions Lliga: Sistema Probabilístic i Computacional per a la Modelització de Futbol Multi-Lliga
### Applied Probability, Bivariate Poisson Processes, Scoring Rules and Quantitative Market Analysis

[![Python 3.14+](https://img.shields.io/badge/python-3.14+-blue.svg)](https://www.python.org/)
[![Test Suite](https://img.shields.io/badge/pytest-681%20passed-brightgreen.svg)](tests/)
[![Database](https://img.shields.io/badge/database-SQLite%20%7C%20Supabase%20PostgreSQL-darkblue.svg)](database/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Documentation](https://img.shields.io/badge/docs-Mathematical%20Monograph-orange.svg)](docs/MATHEMATICAL_MODEL.md)

🌐 **Language / Idioma:** [Català (Actual)](README.md) | [English (README.en.md)](README.en.md) | [Monografia Matemàtica Completa](docs/MATHEMATICAL_MODEL.md)

---

## Resum Executiu / Abstract

**Prediccions Lliga** és una plataforma científica i computacional dissenyada per a la modelització probabilística, la inferència estadística i l'anàlisi quantitativa de partits de futbol associació a les principals lligues europees (**LaLiga EA Sports, Premier League, LaLiga Hypermotion, EFL Championship, Bundesliga, Ligue 1 i Serie A**).

El sistema rebutja les aproximacions opaques de tipus caixa negra (*black-box machine learning*) i es fonamenta en la **teoria clàssica de processos estocàstics de recompte**. El nucli del motor prediu marcadors exactes com una variable aleatòria bidimensional $(X, Y) \in \mathbb{N}_0 \times \mathbb{N}_0$ mitjançant **distribucions de Poisson independents corregides per la matriu bivariant de Dixon-Coles ($\rho = -0.10$)**. Aquesta distribució conjunta de probabilitats es projecta determinísticament per derivar els mercats clau (1X2, Doble Oportunitat, Més/Menys de gols i Ambdós Equips Marquen).

L'avaluació de la precisió del model s'estructura al voltant de **Regles de Puntuació Pròpies (*Proper Scoring Rules*)** com el **Ranked Probability Score (RPS)**, el **Brier Score Multiclasse** i la **Pèrdua Logarítmica (*Logarithmic Loss*)**, descartant mètriques enganyoses com l'Accuracy. Com a capa d'aplicació financera secundària, el sistema calcula el **Valor Esperat (+EV%) amb correcció per devolució (*push/void*)**, l'assignació òptima de capital mitjançant el **Criteri de Kelly Fraccional ($\kappa = 0.25$)** i detecta **oportunitats d'arbitratge esportiu (*surebets*)** amb optimització entera de posicions i modelització de comissions d'intercanvi (*exchange*).

---

## Taula de Continguts

1. [Arquitectura Matemàtica del Model de Gols](#1-arquitectura-matemàtica-del-model-de-gols)
   - [Formulació com a Procés de Recompte](#11-formulació-com-a-procés-de-recompte)
   - [Intensitats Esperades: Perfils de Lliga, Rànquings i Rolling xG](#12-intensitats-esperades-perfils-de-lliga-rànquings-i-rolling-xg)
   - [Correcció de Dependència Bivariant: Model de Dixon-Coles](#13-correcció-de-dependència-bivariant-model-de-dixon-coles)
   - [Truncament, Discretització i Normalització de la Matriu](#14-truncament-discretització-i-normalització-de-la-matriu)
   - [Projecció Determinista de Mercats (1X2, Over/Under, BTTS)](#15-projecció-determinista-de-mercats-1x2-overunder-btts)
2. [Sistemes Dinàmics de Qualitat d'Equip](#2-sistemes-dinàmics-de-qualitat-dequip)
   - [Elo Logístic amb Multiplicador Logarítmic de Gols](#21-elo-logístic-amb-multiplicador-logarítmic-de-gols)
   - [Motor de Tri-Ranks (Ponderació 65% xG + 35% Gols Reals)](#22-motor-de-tri-ranks-ponderació-65-xg--35-gols-reals)
3. [Models Estocàstics Secundaris: Córners i Disciplina](#3-models-estocàstics-secundaris-córners-i-disciplina)
   - [Distribució de Córners](#31-distribució-de-córners)
   - [Model de Targetes, Severitat Arbitral i Tensió](#32-model-de-targetes-severitat-arbitral-i-tensió)
4. [Validació Estadística i Proper Scoring Rules](#4-validació-estadística-i-proper-scoring-rules)
   - [Ranked Probability Score (RPS) per a Categories Ordenades](#41-ranked-probability-score-rps-per-a-categories-ordenades)
   - [Brier Score i Pèrdua Logarítmica](#42-brier-score-i-pèrdua-logarítmica)
   - [Biaix de l'Accuracy i Protocol Walk-Forward](#43-biaix-de-laccuracy-i-protocol-walk-forward)
5. [Arquitectura Computacional i Pipeline d'Ingestió](#5-arquitectura-computacional-i-pipeline-dingestió)
   - [Pipeline Autònom Multi-Lliga (`daily_runner.py`)](#51-pipeline-autònom-multi-lliga-daily_runnerpy)
   - [Xarxa d'Extracció i Scrapers en Temps Real](#52-xarxa-dextracció-i-scrapers-en-temps-real)
   - [Política de Frescor de 4 Nivells](#53-política-de-frescor-de-4-nivells)
   - [Persistència Híbrida: SQLite Local i Supabase Cloud](#54-persistència-híbrida-sqlite-local-i-supabase-cloud)
6. [Aplicacions Quantitatives de Mercat](#6-aplicacions-quantitatives-de-mercat)
   - [Valor Esperat (+EV%) amb Suport de Devolució (Push)](#61-valor-esperat-ev-amb-suport-de-devolució-push)
   - [Criteri de Kelly Fraccional Defensiu](#62-criteri-de-kelly-fraccional-defensiu)
   - [Condició d'Arbitratge Esportiu i Models d'Exchange](#63-condició-darbitratge-esportiu-i-models-dexchange)
   - [Optimització Exhaustiva de Stakes Enters](#64-optimització-exhaustiva-de-stakes-enters)
7. [Exemple Numèric Integral Verificat](#7-exemple-numèric-integral-verificat)
8. [Verificabilitat, Tests i Reproducibilitat](#8-verificabilitat-tests-i-reproducibilitat)
9. [Transparència Intel·lectual i Limitacions del Model](#9-transparència-intel·lectual-i-limitacions-del-model)

---

## 1. Arquitectura Matemàtica del Model de Gols

### 1.1 Formulació com a Procés de Recompte
Sigui un partit de futbol disputat entre l'equip local $H$ i l'equip visitant $A$. Considerem el nombre de gols anotats com variables aleatòries discretes $X \in \mathbb{N}_0$ (gols locals) i $Y \in \mathbb{N}_0$ (gols visitants).

Sota la hipòtesi fonamental de Poisson, la probabilitat que un equip anoti exactament $k$ gols en un interval $T = 90$ minuts amb taxa esperada $\lambda$ s'expressa com:
$$P(K = k; \lambda) = \frac{\lambda^k e^{-\lambda}}{k!}, \quad k \in \{0, 1, 2, \dots\}$$
amb moments $\mathbb{E}[K] = \lambda$ i $\text{Var}(K) = \lambda$.

### 1.2 Intensitats Esperades: Perfils de Lliga, Rànquings i Rolling xG
La determinació de les taxes esperades $\lambda_H$ i $\lambda_A$ no és estàtica; s'implementa al mòdul [`model/goal_model.py`](file:///c:/Fortia/Personal/PreddicionsLliga/model/goal_model.py) combinant:
1. **Mitjanes basals de la competició:** taxes històriques $\mu_H, \mu_A$ específiques per a cada lliga.
2. **Força relativa per rànquings ofensius i defensius:** índexs dinàmics $R_{off}, R_{def} \in [1.0, N]$.
3. **Mètriques empíriques de gols esperats (*rolling xG*):** mitjanes mòbils recents d'xG generat i concedit.

```
                              ┌─────────────────────────────────────────┐
                              │ Perfil de Lliga (μ_Home, μ_Away, N)     │
                              └────────────────────┬────────────────────┘
                                                   │
                 ┌─────────────────────────────────┴─────────────────────────────────┐
                 ▼                                                                   ▼
┌─────────────────────────────────┐                                 ┌─────────────────────────────────┐
│     Model Teòric de Ranks       │                                 │     Model Empíric Rolling xG    │
│  α_H = clip(1 + 0.35·ΔR_off)    │                                 │  λ_emp,H = (xG_for + xG_opp)/2  │
│  β_A = clip(1 + 0.30·ΔR_def)    │                                 │  λ_emp,A = (xG_for + xG_opp)/2  │
│  λ_rank,H = μ_H · α_H · β_A     │                                 └────────────────┬────────────────┘
└────────────────┬────────────────┘                                                  │
                 │                                                                   │
                 └───────────────────────────────┬───────────────────────────────────┘
                                                 ▼
                              ┌─────────────────────────────────────┐
                              │  Combinació Ponderada Calibrada:    │
                              │  λ = (0.60·λ_rank + 0.40·λ_emp)·Rest │
                              │  Clamping: λ_H ≥ 0.20, λ_A ≥ 0.15   │
                              └──────────────────┬──────────────────┘
                                                 ▼
                              ┌─────────────────────────────────────┐
                              │ Matriu Dixon-Coles 8x8 (ρ = -0.10)  │
                              │ Normalització: M_i,j / ∑∑ M_u,v      │
                              └─────────────────────────────────────┘
```

#### Coeficients de Modulació de Rànquing
Sigui $M = \frac{N}{2}$ la posició mitjana d'una lliga de $N$ equips:
$$\alpha_H = \text{clip}\left(1.0 + 0.35 \cdot \frac{M - R_{off, H}}{M}, \; 0.60, \; 1.50\right)$$
$$\beta_A = \text{clip}\left(1.0 + 0.30 \cdot \frac{R_{def, A} - M}{M}, \; 0.60, \; 1.50\right)$$

$$\alpha_A = \text{clip}\left(1.0 + 0.35 \cdot \frac{M - R_{off, A}}{M}, \; 0.60, \; 1.50\right)$$
$$\beta_H = \text{clip}\left(1.0 + 0.30 \cdot \frac{R_{def, H} - M}{M}, \; 0.60, \; 1.50\right)$$

Les taxes teòriques de rànquing són:
$$\lambda_{rank, H} = \mu_H \cdot \alpha_H \cdot \beta_A, \quad \lambda_{rank, A} = \mu_A \cdot \alpha_A \cdot \beta_H$$

#### Ponderació amb Rolling xG i Factors de Descans
$$\lambda_H = \max\left(0.20, \; (0.60 \cdot \lambda_{rank, H} + 0.40 \cdot \lambda_{emp, H}) \cdot Rest_H + 0.5 \cdot HA\right)$$
$$\lambda_A = \max\left(0.15, \; (0.60 \cdot \lambda_{rank, A} + 0.40 \cdot \lambda_{emp, A}) \cdot Rest_A - 0.2 \cdot HA\right)$$

on $\lambda_{emp, H} = \frac{xG_{for, H} + xG_{against, A}}{2}$ i $\lambda_{emp, A} = \frac{xG_{for, A} + xG_{against, H}}{2}$.

### 1.3 Correcció de Dependència Bivariant: Model de Dixon-Coles
La independència estricta falla en marcadors baixos degut a factors tàctics d'empat. S'aplica la modificació multiplicativa proposada per Dixon i Coles (1997):
$$P(X = x, Y = y) = \tau_{\lambda_H, \lambda_A}(x, y) \cdot \frac{\lambda_H^x e^{-\lambda_H}}{x!} \cdot \frac{\lambda_A^y e^{-\lambda_A}}{y!}$$

on la funció de correcció s'estableix com:
$$\tau_{\lambda_H, \lambda_A}(x, y) = \begin{cases}
1 - \lambda_H \lambda_A \rho & \text{si } x = 0, y = 0 \\
1 + \lambda_H \rho & \text{si } x = 0, y = 1 \\
1 + \lambda_A \rho & \text{si } x = 1, y = 0 \\
1 - \rho & \text{si } x = 1, y = 1 \\
1 & \text{per a tot } x \ge 2 \text{ o } y \ge 2
\end{cases}$$

El paràmetre $\rho$ està calibrat a $\mathbf{\rho = -0.10}$. Això eleva la probabilitat d'empats amb pocs gols ($0-0$ i $1-1$) i redueix lleugerament els marcadors $1-0$ i $0-1$.

### 1.4 Truncament, Discretització i Normalització de la Matriu
El model construeix una matriu discreta $M$ de mida $8 \times 8$ (per a $x, y \in \{0, 1, \dots, 7\}$). Per compensar el truncament del suport infinit i l'efecte de $\tau(x, y)$, la matriu es normalitza estrictament:
$$M_{i,j} = \frac{M_{i,j}^{(0)}}{\sum_{u=0}^7 \sum_{v=0}^7 M_{u,v}^{(0)}} \implies \sum_{i=0}^7 \sum_{j=0}^7 M_{i,j} \equiv 1.0$$

### 1.5 Projecció Determinista de Mercats (1X2, Over/Under, BTTS)
Tots els mercats s'obtenen sumant elements disjunts de la matriu $M$:

- **1X2 (Resultat Final):**
  $$P(\text{Home Win}) = \sum_{i > j} M_{i,j}, \quad P(\text{Draw}) = \sum_{i = j} M_{i,j}, \quad P(\text{Away Win}) = \sum_{i < j} M_{i,j}$$
- **Més / Menys de Gols (Over / Under $L$):**
  $$P(\text{Over } L) = \sum_{i + j > L} M_{i,j}, \quad P(\text{Under } L) = 1.0 - P(\text{Over } L)$$
- **Ambdós Equips Marquen (BTTS):**
  $$P(\text{BTTS}_{\text{Yes}}) = 1.0 - \sum_{j=0}^7 M_{0,j} - \sum_{i=0}^7 M_{i,0} + M_{0,0}$$
- **Doble Oportunitat:**
  $$P(1X) = P(1) + P(X), \quad P(X2) = P(X) + P(2), \quad P(12) = P(1) + P(2)$$

---

## 2. Sistemes Dinàmics de Qualitat d'Equip

El mòdul [`model/rank_engine.py`](file:///c:/Fortia/Personal/PreddicionsLliga/model/rank_engine.py) manté la trajectòria competitiva de cada club mitjançant dues capes complementàries.

### 2.1 Elo Logístic amb Multiplicador Logarítmic de Gols
L'expectativa de victòria incorpora $+50$ punts d'avantatge de camp per a l'equip local:
$$E_H = \frac{1}{1 + 10^{\frac{\text{Elo}_A - \text{Elo}_H - 50}{400}}}, \quad E_A = 1.0 - E_H$$

El factor de canvi $K$ s'adapta amb el logaritme del marge real de gols per premiar proporcionalment les golejades sòlides:
$$G = \ln(\max(1, |g_H - g_A|) + 1.0)$$
$$K_{eff} = 32.0 \cdot G$$
$$\Delta \text{Elo}_H = K_{eff} \cdot (S_H - E_H), \quad \Delta \text{Elo}_A = -\Delta \text{Elo}_H$$
on $S_H \in \{1.0, 0.5, 0.0\}$ segons si el partit finalitza en victòria local, empat o derrota.

### 2.2 Motor de Tri-Ranks (Ponderació 65% xG + 35% Gols Reals)
Els gols reals incorporen soroll aleatori (pals, rebots fortuïts, errors arbitrals). Per calcular el rendiment estructural d'un equip, el model crea una mètrica ponderada de sortida:
$$\text{Output}_{eff, off} = 0.65 \cdot xG_{for} + 0.35 \cdot g_{scored}$$
$$\Delta_{off} = \text{Output}_{eff, off} - \lambda_{exp, model}$$
$$\text{Shift}_{off} = -0.30 \cdot \Delta_{off}$$
$$R_{off}^{(t+1)} = \text{clip}\left(R_{off}^{(t)} + \text{Shift}_{off}, \; 1.0, \; N_{teams}\right)$$

Anàlogament, per a la solidesa defensiva:
$$\text{Output}_{eff, def} = 0.65 \cdot xG_{against} + 0.35 \cdot g_{conceded}$$
$$\Delta_{def} = \lambda_{exp, opp} - \text{Output}_{eff, def}$$
$$\text{Shift}_{def} = -0.30 \cdot \Delta_{def}$$
$$R_{def}^{(t+1)} = \text{clip}\left(R_{def}^{(t)} + \text{Shift}_{def}, \; 1.0, \; N_{teams}\right)$$

El rànquing general unificat és la mitjana aritmètica: $R_{gen} = \frac{R_{off} + R_{def}}{2.0}$.

---

## 3. Models Estocàstics Secundaris: Córners i Disciplina

### 3.1 Distribució de Córners
A [`model/corner_model.py`](file:///c:/Fortia/Personal/PreddicionsLliga/model/corner_model.py), els llançaments de cantonada es tracten com un procés de Poisson unidimensional sobre el volum total de partit:
$$\lambda_{C, H} = \frac{C_{for5, H} + C_{against5, A}}{2} \cdot 1.05, \quad \lambda_{C, A} = \frac{C_{for5, A} + C_{against5, H}}{2} \cdot 0.95$$
$$\lambda_{C, total} = \lambda_{C, H} + \lambda_{C, A}$$
$$P(\text{Over } K) = \sum_{k = K+1}^{24} \frac{\lambda_{C, total}^k e^{-\lambda_{C, total}}}{k!}$$

### 3.2 Model de Targetes, Severitat Arbitral i Tensió
A [`model/card_model.py`](file:///c:/Fortia/Personal/PreddicionsLliga/model/card_model.py), l'expectativa de targetes pondera l'estil de l'àrbitre designat oficialment pel CTA o PGMOL (55%) i la trajectòria recent dels clubs (45%):
$$\lambda_{cards} = 0.55 \cdot (Y_{ref} \cdot S_{ref}) + 0.45 \cdot (Cards_{5, H} + Cards_{5, A}) + \text{Boosts}$$

on els increments tàctics inclouen:
- $\text{Boost}_{derby} = +0.90$ en rivalitats històriques reconegudes (ex: El Clàssic, Gran Derbi Sevillà, North London Derby).
- $\text{Boost}_{ko} = +0.50$ en partits d'eliminació directa.
- $\text{Boost}_{tension} = \max(0.0, \; (10.0 - |R_{gen, H} - R_{gen, A}|) \cdot 0.04)$ en duels de màxima paritat a la classificació.

La probabilitat de targeta vermella al partit s'expressa mitjançant el risc instantani acumulat:
$$P(\text{Red Card} \ge 1) = 1.0 - e^{-\lambda_{red}}, \quad \lambda_{red} = Red_{ref} \cdot (1.0 + 0.30 \cdot \mathbb{I}_{derby})$$

---

## 4. Validació Estadística i Proper Scoring Rules

Avaluar un model probabilístic de futbol amb la mètrica d'**Accuracy** és metodològicament incorrecte: un pronòstic que assigni un 99% de probabilitat a una victòria local quan la probabilitat real calibrada és del 60% obtindria un 100% d'accuracy si l'equip guanya, ocultant un sobreajust catastròfic.

El nostre marc analític empra **Regles de Puntuació Pròpies (*Proper Scoring Rules*)**:

### 4.1 Ranked Probability Score (RPS) per a Categories Ordenades
En el futbol, l'ordre $1 < X < 2$ és rellevant (l'empat és l'estat intermedi entre les dues victòries). L'RPS d'Epstein mesura la distància quadràtica acumulada entre les probabilitats predites $\mathbf{p} = (p_1, p_x, p_2)$ i el resultat observat $\mathbf{y} = (y_1, y_x, y_2)$:

$$RPS = \frac{1}{2} \left[ (p_1 - y_1)^2 + ((p_1 + p_x) - (y_1 + y_x))^2 \right]$$

- $RPS \in [0, 1]$, on $0.0$ representa la perfecció absoluta.
- Un model no informatiu basat en mitjanes històriques universals obté un RPS típic de $\approx 0.198 - 0.205$.
- Valors inferiors a $0.190$ indiquen un poder predictiu superior al consens històric.

### 4.2 Brier Score i Pèrdua Logarítmica
Per a mercats binaris o categories no ordenades:
- **Brier Score Multiclasse:** $BS = \frac{1}{N} \sum_{n=1}^N \sum_{k=1}^K (p_{n,k} - y_{n,k})^2$
- **Pèrdua Logarítmica (Log Loss):** $\text{LogLoss} = -\frac{1}{N} \sum_{n=1}^N \sum_{k=1}^K y_{n,k} \ln(p_{n,k})$

### 4.3 Biaix de l'Accuracy i Protocol Walk-Forward
Per garantir la validesa out-of-sample sense **biaix de mirada al futur (*lookahead bias*)**:
1. Abans de cada jornada, les mètriques d'equip es congelen a data $t$.
2. Es generen les prediccions de tots els partits de la data $t+1$.
3. Un cop coneguts els resultats oficials, es computen els valors d'RPS, Brier Score i PnL financer.
4. Finalment, s'actualitzen els paràmetres dinàmics (Elo, Tri-Ranks) per a la jornada següent.

---

## 5. Arquitectura Computacional i Pipeline d'Ingestió

El projecte s'executa com una canonada de dades autònoma i distribuïda capaç de cobrir 7 competicions europees:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          ORQUESTRACIÓ DIÀRIA                                │
│                     daily_runner.py / GitHub Actions                        │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
            ┌──────────────────────────┴──────────────────────────┐
            ▼                                                     ▼
┌───────────────────────────────┐             ┌───────────────────────────────┐
│     Flashscore Live Crawler   │             │  Scrapers de Quotes en Temps  │
│  - Calendari oficial          │             │  Real:                        │
│  - Resultats i xG             │             │  - Winamax (DOM crawler)      │
│  - Córners, targetes, àrbitre │             │  - Bet365 (Moviodds API)      │
└───────────────┬───────────────┘             │  - Betway (HTML Parser)       │
                │                             │  - The Odds API (+ Cost Guard)│
                │                             └───────────────┬───────────────┘
                ▼                                             ▼
┌───────────────────────────────┐             ┌───────────────────────────────┐
│    Base de Dades SQLite       │             │   Filtre de Frescor 4 Tiers   │
│   (ACID Local - data/*.db)    │             │   VERIFIED_FRESH ≤ 60s, Δ≤30s │
└───────────────┬───────────────┘             └───────────────┬───────────────┘
                │                                             │
                └──────────────────────┬──────────────────────┘
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                       MOTORS DE CÀLCUL I PREDICCIÓ                          │
│  - GoalModel (Poisson Dixon-Coles)    - RankEngine (Elo + Tri-Ranks)        │
│  - ValueBetEngine (+EV & Kelly)       - ArbitrageEngine (Surebets Discret)  │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
            ┌──────────────────────────┴──────────────────────────┐
            ▼                                                     ▼
┌───────────────────────────────┐             ┌───────────────────────────────┐
│    Informes PDF Executius     │             │    Supabase PostgreSQL       │
│    (PDFReportGenerator)       │             │    - Realtime Broadcast       │
│    Mega-Informes Multi-Lliga  │             │    - Edge Functions           │
│    i Jornades Individuals     │             │    - Vercel Web Dashboard     │
└───────────────────────────────┘             └───────────────────────────────┘
```

### 5.1 Pipeline Autònom Multi-Lliga (`daily_runner.py`)
El script [`daily_runner.py`](file:///c:/Fortia/Personal/PreddicionsLliga/daily_runner.py) opera en dues fases complementàries:
- **Fase Post-Jornada:** Descarrega marcadors finals, xG oficial, córners i amonestacions; actualitza les puntuacions Elo i liquida les combinades del model financer.
- **Fase Pre-Jornada:** S'activa dins la finestra òptima de designació arbitral (28 hores abans del primer partit), assegurant que els àrbitres oficials assignats pel comitè tècnic ja estiguin resolts.

### 5.2 Xarxa d'Extracció i Scrapers en Temps Real
- **Flashscore Crawler ([`scraper/live_crawler.py`](file:///c:/Fortia/Personal/PreddicionsLliga/scraper/live_crawler.py)):** Extracció de calendari, alineacions, estadístiques de xoc i àrbitres.
- **Winamax Scraper ([`scraper/winamax_scraper.py`](file:///c:/Fortia/Personal/PreddicionsLliga/scraper/winamax_scraper.py)):** Extracció directa de quotes del mercat regulat espanyol (DGOJ).
- **Bet365 Scraper ([`scraper/bet365_scraper.py`](file:///c:/Fortia/Personal/PreddicionsLliga/scraper/bet365_scraper.py)):** Monitorització mitjançant el proveïdor de feed Moviodds.
- **Betway Scraper ([`scraper/betway_scraper.py`](file:///c:/Fortia/Personal/PreddicionsLliga/scraper/betway_scraper.py)):** Monitorització de mercats 1X2 i Over/Under.
- **The Odds API Integració ([`scraper/odds_api_client.py`](file:///c:/Fortia/Personal/PreddicionsLliga/scraper/odds_api_client.py)):** Client amb **Cost Guard**, comptabilitzador de crèdits consumits, circuit breaker i memòria cau d'alta disponibilitat.

### 5.3 Política de Frescor de 4 Nivells
Per evitar arbitratges fantasma deguts a quotes caducades o decalatges temporals, el sistema classifica cada parell de quotes:

| Tier | Condició Temporal | Decalatge entre quotes ($\Delta t$) | Estat Operatiu |
| :--- | :---: | :---: | :--- |
| **`VERIFIED_FRESH`** | Edat $\le 60$ segons | $\le 30$ segons | Apte per a execució automàtica i alertes |
| **`POTENTIALLY_STALE`** | Edat $\le 300$ segons | $\le 90$ segons | En observació (Near-Arb); no s'executa |
| **`RECENT_HISTORICAL`** | Edat $\le 1800$ segons (30 min) | $> 90$ segons | Indicador de tendència històrica |
| **`EXCLUDED`** | Edat $> 1800$ segons | Qualsevol | Rebuig immediat i purga de memòria |

### 5.4 Persistència Híbrida: SQLite Local i Supabase Cloud
- **SQLite Local ([`data/laliga.db`](file:///c:/Fortia/Personal/PreddicionsLliga/data/laliga.db)):** Motor relacional d'alta velocitat amb transaccions atòmiques per a les execucions diàries, anàlisi d'xG i càlcul d'informes PDF.
- **Supabase Cloud (PostgreSQL 15):** Emmagatzematge distribuït amb seguretat a nivell de fila (RLS), procediments emmagatzemats (*Stored Procedures / RPCs*) amb `SECURITY DEFINER`, coordinació de sessions concurrents i canal de telemetria en temps real cap al frontend públic de Vercel.

---

## 6. Aplicacions Quantitatives de Mercat

### 6.1 Valor Esperat (+EV%) amb Suport de Devolució (Push)
A [`engine/value_bet_engine.py`](file:///c:/Fortia/Personal/PreddicionsLliga/engine/value_bet_engine.py), el valor esperat contempla la probabilitat de devolució de l'aposta ($p_v$):
$$EV\% = \left(p_w \cdot Q + p_v - 1.0\right) \times 100\%$$
$$Q_{fair} = \frac{1.0 - p_v}{p_w}, \quad P_{imp} = \frac{1.0 - p_v}{Q} \times 100\%$$

Classificació:
- $EV \ge +15.0\%$: 🔥 Excel·lent Valor
- $+5.0\% \le EV < +15.0\%$: ⭐ Molt Bona Oportunitat
- $0.0\% < EV < +5.0\%$: ✓ Lleuger Valor
- $EV \le 0.0\%$: ❌ Sense Valor (Esperança negativa)

### 6.2 Criteri de Kelly Fraccional Defensiu
Amb retorn net $b = Q - 1.0$:
$$f^* = \frac{b \cdot p_w - p_l}{b \cdot (p_w + p_l)}$$
El motor aplica una fracció conservadora $\kappa = 0.25$ per protegir el bankroll davant l'error d'estimació del model:
$$f_{executat} = \max\left(0, \; 0.25 \cdot f^* \cdot 100\%\right)$$

### 6.3 Condició d'Arbitratge Esportiu i Models d'Exchange
A [`engine/arbitrage_engine.py`](file:///c:/Fortia/Personal/PreddicionsLliga/engine/arbitrage_engine.py), una oportunitat d'arbitratge pur (*Surebet*) requereix:
$$S_{inv} = \sum_{k=1}^K \frac{1}{Q_k} < 1.0 \implies \text{Margin} < 100\%$$
$$\text{Profit}_{brut}\% = \left(\frac{1}{S_{inv}} - 1.0\right) \times 100\%$$

El motor dóna suport a dos models de comissió d'intercanvi:
1. **`NET_MARKET_WINNINGS`:** Dedueix comissió exclusivament sobre el benefici net del mercat a l'exchange ($\text{Comissió} = c \cdot \max(0, PnL)$).
2. **`PER_BET_SETTLEMENT`:** Dedueix comissió per volum sobre cada posició tancada ($\text{Comissió} = c \cdot \text{Stake}$).

### 6.4 Optimització Exhaustiva de Stakes Enters
En comptes d'utilitzar fórmules contínues $s_k = \frac{B / Q_k}{S_{inv}}$ que donen fraccions irregulars i alerten els algoritmes de control dels operadors, el sistema realitza una **cerca en xarxa discreta exacta**:
- Per a mercats 2-way: cerca unidimensional sobre $s_1 \in \{1, \dots, B-1\}, s_2 = B - s_1$.
- Per a mercats 3-way: cerca bidimensional sobre $s_1, s_2 \ge 1, s_3 = B - s_1 - s_2 \ge 1$.
- Criteri d'optimització: $\max_{\mathbf{s}} \min_{scen} PnL_{net}(scen, \mathbf{s})$.

---

## 7. Exemple Numèric Integral Verificat

Enfrontament de **LaLiga EA Sports**: **Athletic Club (Local)** vs **Real Sociedad (Visitant)**.

### Dades de Partida
- **Athletic Club:** $R_{off} = 3.0$, $R_{def} = 4.0$, $\text{Rolling } xG_{for} = 1.65$, $\text{Rolling } xG_{against} = 1.10$.
- **Real Sociedad:** $R_{off} = 6.0$, $R_{def} = 5.0$, $\text{Rolling } xG_{for} = 1.30$, $\text{Rolling } xG_{against} = 1.25$.
- Perfil LaLiga: $\mu_H = 1.42, \mu_A = 1.12, N = 20, M = 10.0$.

### Resolució d'Intensitats
$$\alpha_H = 1.0 + 0.35 \cdot \frac{10 - 3}{10} = 1.245, \quad \beta_A = 1.0 + 0.30 \cdot \frac{5 - 10}{10} = 0.850$$
$$\lambda_{rank, H} = 1.42 \cdot 1.245 \cdot 0.850 = 1.5027, \quad \lambda_{emp, H} = \frac{1.65 + 1.25}{2} = 1.4500$$
$$\lambda_H = 0.60 \cdot 1.5027 + 0.40 \cdot 1.4500 = \mathbf{1.4816} \approx \mathbf{1.48}$$

$$\alpha_A = 1.0 + 0.35 \cdot \frac{10 - 6}{10} = 1.140, \quad \beta_H = 1.0 + 0.30 \cdot \frac{4 - 10}{10} = 0.820$$
$$\lambda_{rank, A} = 1.12 \cdot 1.140 \cdot 0.820 = 1.0470, \quad \lambda_{emp, A} = \frac{1.30 + 1.10}{2} = 1.2000$$
$$\lambda_A = 0.60 \cdot 1.0470 + 0.40 \cdot 1.2000 = \mathbf{1.1082} \approx \mathbf{1.11}$$

### Resultats de la Matriu Dixon-Coles ($8 \times 8, \rho = -0.10$) Normalitzada

```
                 Marcadors Més Probables:
                 1 - 1 : 13.55%
                 1 - 0 :  9.89%
                 2 - 1 :  9.13%
                 0 - 0 :  8.74%
                 2 - 0 :  8.24%
```

- **Probabilitats 1X2:**
  - $P(\text{Victòria Local "1"}) = \mathbf{44.5\%}$
  - $P(\text{Empat "X"}) = \mathbf{28.4\%}$
  - $P(\text{Victòria Visitant "2"}) = \mathbf{27.1\%}$
- **Gols Totals (Over / Under):**
  - $P(\text{Over 1.5}) = \mathbf{74.3\%}$ / $P(\text{Under 1.5}) = \mathbf{25.7\%}$
  - $P(\text{Over 2.5}) = \mathbf{47.9\%}$ / $P(\text{Under 2.5}) = \mathbf{52.1\%}$
  - $P(\text{Over 3.5}) = \mathbf{26.2\%}$ / $P(\text{Under 3.5}) = \mathbf{73.8\%}$
- **Ambdós Equips Marquen (BTTS):**
  - $P(\text{BTTS Sí}) = \mathbf{53.0\%}$ / $P(\text{BTTS No}) = \mathbf{47.0\%}$

### Avaluació de Mercat (+EV%)
Amb una quota de mercat oferta $Q = 2.45$ per a la Victòria Local:
- Quota justa teòrica: $Q_{fair} = \frac{1.0}{0.445} = \mathbf{2.25}$
- Probabilitat implícita a la quota: $P_{imp} = \frac{1.0}{2.45} = \mathbf{40.8\%}$
- **Valor Esperat:** $EV\% = (0.445 \cdot 2.45 - 1.0) \times 100\% = \mathbf{+9.0\%}$ (⭐ Molt Bona)
- **Aposta Recomanada (Kelly 1/4):** $\mathbf{1.6\% \text{ del bankroll}}$

---

## 8. Verificabilitat, Tests i Reproducibilitat

La integritat matemàtica i computacional del repositori està protegida per una suite exhaustiva de proves automatitzades amb `pytest`:

```bash
# Execució completa de la suite de tests (681 proves unitàries i d'integració)
python -m pytest tests/

# Execució específica dels mòduls matemàtics
python -m pytest tests/test_dixon_coles_unification.py tests/test_rank_update_and_model.py
```

Estat actual de la suite de proves:
```
======================= 681 passed in 80.62s =======================
```

La suite cobreix:
- Unificació i normalització de la matriu de Dixon-Coles en totes les combinacions de $\lambda$.
- Estabilitat del factor Elo amb multiplicador logarítmic davant golejades extremes.
- Suport complet de mercats amb devolució (*Push/Void*) a les fórmules de Kelly i EV.
- Concurrència atòmica de sessions i aïllament d'estat a Supabase.
- Compliment estricte de la política de frescor temporal de quotes.

---

## 9. Transparència Intel·lectual i Limitacions del Model

En estricte compliment de l'honestedat intel·lectual i el rigor científic, es documenten explícitament els supòsits empírics del sistema:

1. **Paràmetres Empírics Calibrats vs Estimació en Línia:**
   - El coeficient de dependència $\rho = -0.10$ està fixat heurísticament a partir d'estudis històrics en el futbol europeu. No s'executa un algorisme de Màxima Versemblança (*MLE*) o mostreig MCMC en temps real a cada iteració.
   - Les ponderacions 60% / 40% (rànquings vs rolling xG) i 65% / 35% (xG vs gols reals a tri-ranks) són pesos empírics optimitzats per reduir la variància, no derivats bayesians analítics.
2. **Absència de Caixes Negres (Black-Box ML):**
   - El sistema no utilitza xarxes neuronals profundes ni models de boosting opacs. S'opta deliberadament per models paramètrics de recompte perquè són auditables, matemàticament explicables i matemàticament verificables pas a pas.
3. **Absència de Simulacions de Monte Carlo en la Projecció:**
   - La integració de mercats no empra mostreig estocàstic de Monte Carlo. Els resultats deriven de sumacions discretes exactes sobre la matriu truncada $8 \times 8$.
4. **Model Estàtic Pre-Partit:**
   - El sistema està dissenyat per a l'avaluació prèvia a l'inici del partit (*pre-match*). No implementa processos estocàstics de difusió per a quotes en viu durant els 90 minuts de joc (*in-play*).

---

## Llicència i Citació

Aquest projecte està llicenciat sota els termes de la [Llicència MIT](LICENSE).

```bibtex
@software{prediccions_lliga_2026,
  author = {Equip de Desenvolupament de Prediccions Lliga},
  title = {Prediccions Lliga: Mathematical Modeling, Applied Probability and Quantitative Football Forecasting Architecture},
  year = {2026},
  url = {https://github.com/fortiaarumi/Prediccions}
}
```
