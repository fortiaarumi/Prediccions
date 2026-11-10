# Fonaments Matemàtics i Modelització Probabilística de Prediccions Lliga
## Mathematical Foundations and Applied Probability Framework

> **Autor:** Equip de Desenvolupament de Prediccions Lliga  
> **Versió:** 3.2-Engineering / Applied Probability Edition  
> **Estat:** Document Tècnic Oficial  
> **Data:** Novembre 2026  
> **Enllaços:** [README Principal (Català)](../README.md) | [README in English](../README.en.md)

---

## Índex / Table of Contents

1. [Introducció i Marc Probabilístic](#1-introducció-i-marc-probabilístic)
2. [Processos de Poisson Aplicats al Futbol](#2-processos-de-poisson-aplicats-al-futbol)
3. [Intensitats Esperades de Gol ($\lambda_H, \lambda_A$)](#3-intensitats-esperades-de-gol-lambda_h-lambda_a)
4. [Ajust Bivariant de Dependència: Model de Dixon-Coles](#4-ajust-bivariant-de-dependència-model-de-dixon-coles)
5. [Integritat de la Matriu Truncada i Normalització](#5-integritat-de-la-matriu-truncada-i-normalització)
6. [Càlcul de Probabilitats de Mercat (1X2, Over/Under, BTTS)](#6-càlcul-de-probabilitats-de-mercat-1x2-overunder-btts)
7. [Dinamisme d'Equip: Elo Logístic i Tri-Ranks](#7-dinamisme-dequip-elo-logístic-i-tri-ranks)
8. [Processos Estocàstics Secundaris: Córners i Disciplina](#8-processos-estocàstics-secundaris-córners-i-disciplina)
9. [Teoria Quantitativa de Mercat: Valor Esperat, Kelly i Arbitratge](#9-teoria-quantitativa-de-mercat-valor-esperat-kelly-i-arbitratge)
10. [Validació Estadística i Proper Scoring Rules](#10-validació-estadística-i-proper-scoring-rules)
11. [Exemple Numèric Complet i Verificat](#11-exemple-numèric-complet-i-verificat)
12. [Transparència Intel·lectual i Limitacions Empíriques](#12-transparència-intel·lectual-i-limitacions-empíriques)

---

## 1. Introducció i Marc Probabilístic

La modelització d'un partit de futbol des de la perspectiva de la probabilitat aplicada requereix tractar la generació de gols com la realització d'un **procés estocàstic de recompte en temps continu**. Cada equip competeix durant un interval tancat de durada $T = 90$ minuts (més temps afegit) per produir esdeveniments puntuals (gols) a través d'enfrontaments tàctics, possessió de pilota, generació d'arribades i rematades a porteria.

En aquest sistema, no es modela el resultat d'un partit com una variable aleatòria categòrica directa amb tres estats ($1, X, 2$), sinó com una **variable aleatòria bidimensional de recompte discret** $(X, Y) \in \mathbb{N}_0 \times \mathbb{N}_0$, on:
- $X$ representa el nombre total de gols anotats per l'equip local (*Home*).
- $Y$ representa el nombre total de gols anotats per l'equip visitant (*Away*).

Els mercats financers i de pronòstics (1X2, Doble Oportunitat, Més/Menys de 2.5 gols, Ambdós Equips Marquen) són projeccions deterministes de la distribució conjunta de probabilitat:
$$P(X = x, Y = y)$$

Aquest document estableix les derivacions matemàtiques formals, l'especificació paramètrica, els ajustos de dependència, les regles d'actualització dinàmica i les mètriques d'avaluació probabilística emprades al repositori `PreddicionsLliga`.

---

## 2. Processos de Poisson Aplicats al Futbol

### 2.1 Postulats del Procés de Poisson Homogeni
Sigui $\{N(t), t \ge 0\}$ un procés de recompte que mesura el nombre acumulat de gols fins al minut $t$. Considerem les hipòtesis clàssiques de Poisson:
1. **Increment independent:** El nombre de gols anotats en intervals de temps disjunts $(t_1, t_2]$ i $(t_3, t_4]$ són variables aleatòries independents.
2. **Homogeneïtat temporal:** La taxa instantània d'anotació és constant:
   $$\lim_{\Delta t \to 0} \frac{P(N(t + \Delta t) - N(t) = 1)}{\Delta t} = \lambda$$
3. **Ordinaritat (absència d'ocurrències simultànies):** La probabilitat de dos o més gols en un interval infinitesimal $\Delta t$ és d'ordre inferior:
   $$\lim_{\Delta t \to 0} \frac{P(N(t + \Delta t) - N(t) \ge 2)}{\Delta t} = 0$$

Sota aquests postulats, per a un partit complet de durada estàndard $T$, el nombre de gols $k$ segueix una distribució de Poisson de paràmetre $\lambda$:
$$P(k; \lambda) = \frac{\lambda^k e^{-\lambda}}{k!}, \quad k \in \{0, 1, 2, \dots\}$$
amb esperança i variància donades per $\mathbb{E}[k] = \lambda$ i $\text{Var}(k) = \lambda$.

### 2.2 Limitacions Reals al Futbol
En la realitat competitiva del futbol d'elit, el procés no és perfectament homogeni:
- **No-homogeneïtat temporal:** La intensitat d'anotació $\lambda(t)$ tendeix a créixer cap al final de cada part (minuts 40-45 i minuts 75-90) a causa del cansament físic, la desorganització tàctica i la urgència del resultat.
- **Estat-dependència:** Si un equip s'avança en el marcador, la dinàmica tàctica canvia (l'equip en avantatge pot replegar línies mentre el desavantatjat augmenta la pressió ofensiva).
- **Sobredispersió (Overdispersion):** La variància empírica dels gols pot superar lleugerament la mitjana ($\text{Var}(k) > \mathbb{E}[k]$).

Malgrat aquestes desviacions, la distribució de Poisson proporciona una base matemàtica tractable, analíticament neta i estadísticament robusta que, quan es corregeix mitjançant una **funció de copula/ajust bivariant**, ofereix un ajust predictiu d'alta fidelitat respecte a les distribucions observades.

---

## 3. Intensitats Esperades de Gol ($\lambda_H, \lambda_A$)

Per a cada partit entre l'equip local $i$ i el visitant $j$, el sistema calcula dues taxes d'anotació esperades:
$$\lambda_H = \mathbb{E}[X_{i,j}], \quad \lambda_A = \mathbb{E}[Y_{i,j}]$$

Aquestes intensitats s'obtenen mitjançant una combinació calibrada de dos components:
1. Un **model basat en rànquings ofensius i defensius dinàmics** de la competició.
2. Una **mitjana mòbil de gols esperats empírics (*rolling xG*)** procedent de mètriques de tirs i generació de perill.

### 3.1 Perfils de Lliga i Mitjanes Base
Cada competició presenta una taxa natural d'anotació reflectida al fitxer [`model/goal_model.py`](file:///c:/Fortia/Personal/PreddicionsLliga/model/goal_model.py):

| Competició (`competition_id`) | Equips ($N$) | Mitjana Local ($\mu_{H}$) | Mitjana Visitant ($\mu_{A}$) | Gols Totals Mitjans |
| :--- | :---: | :---: | :---: | :---: |
| **LaLiga EA Sports** (`LALIGA`) | 20 | 1.42 | 1.12 | 2.54 |
| **Premier League** (`PREMIER`) | 20 | 1.55 | 1.25 | 2.80 |
| **LaLiga Hypermotion** (`HYPERMOTION`) | 22 | 1.35 | 1.05 | 2.40 |
| **EFL Championship** (`CHAMPIONSHIP`) | 24 | 1.48 | 1.18 | 2.66 |
| **Bundesliga** (`BUNDESLIGA`) | 18 | 1.62 | 1.38 | 3.00 |
| **Ligue 1** (`LIGUE1`) | 18 | 1.42 | 1.18 | 2.60 |
| **Serie A** (`SERIEA`) | 20 | 1.44 | 1.16 | 2.60 |

### 3.2 Modulació per Rànquings Dinàmics
Sigui $N$ el nombre d'equips de la lliga i $M = \frac{N}{2}$ la posició mitjana de referència. Cada equip posseeix un rànquing ofensiu $R_{off} \in [1.0, N]$ i un rànquing defensiu $R_{def} \in [1.0, N]$, on una posició numèrica més baixa indica major qualitat (el líder és 1.0).

Es defineixen els factors de força d'atac $\alpha$ i de feblesa defensiva $\beta$:
$$\alpha_H = \text{clip}\left(1.0 + 0.35 \cdot \frac{M - R_{off, H}}{M}, 0.60, 1.50\right)$$
$$\beta_A = \text{clip}\left(1.0 + 0.30 \cdot \frac{R_{def, A} - M}{M}, 0.60, 1.50\right)$$

$$\alpha_A = \text{clip}\left(1.0 + 0.35 \cdot \frac{M - R_{off, A}}{M}, 0.60, 1.50\right)$$
$$\beta_H = \text{clip}\left(1.0 + 0.30 \cdot \frac{R_{def, H} - M}{M}, 0.60, 1.50\right)$$

on la funció de truncament s'estableix en $[0.60, 1.50]$ per evitar divergències asimptòtiques davant equips en extrems de rendiment.

Les taxes de gol teòriques derivades dels rànquings són:
$$\lambda_{rank, H} = \mu_H \cdot \alpha_H \cdot \beta_A$$
$$\lambda_{rank, A} = \mu_A \cdot \alpha_A \cdot \beta_H$$

### 3.3 Integració de Gols Esperats Empírics (*Rolling xG*)
Quan es disposa de dades recents de qualitat de xut (gols esperats $xG$ generats i concedits dels darrers partits), es defineixen les taxes empíriques creuades:
$$\lambda_{emp, H} = \frac{xG_{for, H} + xG_{against, A}}{2}$$
$$\lambda_{emp, A} = \frac{xG_{for, A} + xG_{against, H}}{2}$$

### 3.4 Combinació Ponderada Final
El model combina ambdues fonts mitjançant una ponderació empírica de 60% rànquing teòric i 40% mètrica $xG$ directa, modulant posteriorment pels factors de descans físic ($Rest_H, Rest_A \in [0.975, 1.025]$) i un coeficient addicional d'avantatge de camp ($HA = 0.0$ per defecte en perfils que ja incorporen $\mu_H > \mu_A$):

$$\lambda_H = \max\left(0.20, \; (0.60 \cdot \lambda_{rank, H} + 0.40 \cdot \lambda_{emp, H}) \cdot Rest_H + 0.5 \cdot HA\right)$$
$$\lambda_A = \max\left(0.15, \; (0.60 \cdot \lambda_{rank, A} + 0.40 \cdot \lambda_{emp, A}) \cdot Rest_A - 0.2 \cdot HA\right)$$

Els llindars mínims $\lambda_H \ge 0.20$ i $\lambda_A \ge 0.15$ garanteixen la no-degeneració de les distribucions en situacions de màxima desigualtat competitiva.

---

## 4. Ajust Bivariant de Dependència: Model de Dixon-Coles

### 4.1 La Fal·làcia de la Independència Estricta
Si $X$ i $Y$ fossin estricament independents, la probabilitat conjunta seria el producte directe de les probabilitats marginals:
$$P(X=x, Y=y) = P(X=x) \cdot P(Y=y) = \frac{\lambda_H^x e^{-\lambda_H}}{x!} \cdot \frac{\lambda_A^y e^{-\lambda_A}}{y!}$$

No obstant això, l'anàlisi empírica històrica (Dixon & Coles, 1997) demostra que el futbol professional pateix desviacions sistemàtiques en marcadors baixos:
- Els resultats **$0-0$** i **$1-1$** tenen una freqüència empírica superior a la predita per la independència.
- Els resultats **$1-0$** i **$0-1$** tenen una freqüència lleugerament inferior.

Això es deu a la naturalesa estratègica del joc: a mesura que avança el partit amb empat a 0 o 1, la utilitat marginal d'assumir riscos defensius per guanyar disminueix davant el cost de perdre el punt ja aconseguit.

### 4.2 La Funció Modificadora $\tau(x, y)$
Per capturar aquesta interdependència sense abandonar la flexibilitat analítica de Poisson, s'introdueix la correcció multiplicativa de Dixon-Coles:
$$P(X=x, Y=y) = \tau_{\lambda_H, \lambda_A}(x, y) \cdot \frac{\lambda_H^x e^{-\lambda_H}}{x!} \cdot \frac{\lambda_A^y e^{-\lambda_A}}{y!}$$

on la funció d'ajust $\tau(x, y)$ es defineix com:
$$\tau_{\lambda_H, \lambda_A}(x, y) = \begin{cases}
1 - \lambda_H \lambda_A \rho & \text{si } x = 0, y = 0 \\
1 + \lambda_H \rho & \text{si } x = 0, y = 1 \\
1 + \lambda_A \rho & \text{si } x = 1, y = 0 \\
1 - \rho & \text{si } x = 1, y = 1 \\
1 & \text{per a tot } x \ge 2 \text{ o } y \ge 2
\end{cases}$$

### 4.3 Significat del Paràmetre $\rho$
El coeficient $\rho$ mesura la correlació i la dependència en marcadors baixos:
- Quan $\rho < 0$, s'incrementa la massa de probabilitat de $(0,0)$ i $(1,1)$, alhora que es redueix la de $(1,0)$ i $(0,1)$.
- Al sistema `PreddicionsLliga`, el paràmetre està establert empíricament a:
  $$\rho = -0.10$$
  Aquest valor s'ajusta als patrons observats en les grans lligues europees modernes, on la taxa d'empats tàctics és consistent.
- Per mantenir la coherència probabilística, la funció està protegida amb una cota inferior:
  $$\tau(x, y) \leftarrow \max(0.0001, \; \tau(x, y))$$

---

## 5. Integritat de la Matriu Truncada i Normalització

A la pràctica computacional, una distribució de Poisson posseeix suport infinit sobre $\mathbb{N}_0$. Tanmateix, la probabilitat que un equip de futbol anoti 8 o més gols és de l'ordre de $10^{-6}$ a $10^{-9}$.

El model construeix una matriu discreta $M$ de dimensió $8 \times 8$ (des de 0 fins a 7 gols per equip, $N_{goals} = 8$):
$$M_{i,j} = \tilde{P}(X = i, Y = j), \quad i, j \in \{0, 1, \dots, 7\}$$

On cada element inicial es calcula com:
$$M_{i,j}^{(0)} = \tau_{\lambda_H, \lambda_A}(i, j) \cdot \left(\frac{\lambda_H^i e^{-\lambda_H}}{i!}\right) \cdot \left(\frac{\lambda_A^j e^{-\lambda_A}}{j!}\right)$$

Com que s'ha truncat el suport infinit i s'ha aplicat la pertorbació no-lineal $\tau$, la suma dels elements no és exactament unitària:
$$S_M = \sum_{i=0}^7 \sum_{j=0}^7 M_{i,j}^{(0)} \approx 1 - \epsilon$$

Per garantir que l'espai mostral compleixi estrictament el **Primer Axioma de Kolmogórov** ($\sum_{\omega \in \Omega} P(\omega) = 1$), la matriu final es normalitza:
$$M_{i,j} = \frac{M_{i,j}^{(0)}}{\sum_{u=0}^7 \sum_{v=0}^7 M_{u,v}^{(0)}}$$

Aquesta operació assegura que:
$$0 \le M_{i,j} \le 1 \quad \forall i, j \quad \text{i} \quad \sum_{i=0}^7 \sum_{j=0}^7 M_{i,j} \equiv 1.0$$

---

## 6. Càlcul de Probabilitats de Mercat (1X2, Over/Under, BTTS)

Un cop normalitzada la matriu de probabilitats bivariants $M$, tots els mercats derivats s'obtenen mitjançant agregacions deterministes d'elements de la matriu.

### 6.1 Mercat 1X2 (Resultat del Partit)
L'espai d'esdeveniments es particiona en tres conjunts disjunts:
- **Victòria Local ("1"):** $X > Y$ (elements sota la diagonal principal, matriu triangular inferior estricta):
  $$P(\text{Home Win}) = \sum_{i=1}^7 \sum_{j=0}^{i-1} M_{i,j} = \sum_{i > j} M_{i,j}$$
  A la implementació: `float(np.tril(matrix, -1).sum()) * 100`

- **Empat ("X"):** $X = Y$ (elements sobre la diagonal principal):
  $$P(\text{Draw}) = \sum_{i=0}^7 M_{i,i}$$
  A la implementació: `float(np.diag(matrix).sum()) * 100`

- **Victòria Visitant ("2"):** $X < Y$ (elements sobre la diagonal principal, matriu triangular superior estricta):
  $$P(\text{Away Win}) = \sum_{i=0}^6 \sum_{j=i+1}^7 M_{i,j} = \sum_{i < j} M_{i,j}$$
  A la implementació: `float(np.triu(matrix, 1).sum()) * 100`

Per construcció:
$$P(\text{Home Win}) + P(\text{Draw}) + P(\text{Away Win}) \equiv 100\%$$

### 6.2 Mercat de Gols Totals (Over / Under)
Sigui $Z = X + Y$ la variable aleatòria de gols totals del partit. La seva funció de massa de probabilitat s'obté mitjançant la convolució discreta projectada a la matriu:
$$P(Z = k) = \sum_{i + j = k} M_{i,j}, \quad k \in \{0, 1, \dots, 14\}$$

A partir d'aquesta distribució unidimensional, es calculen les probabilitats de superació de línia estàndard $L \in \{1.5, 2.5, 3.5\}$:
$$P(\text{Over } L) = \sum_{k = \lfloor L \rfloor + 1}^{14} P(Z = k) = \sum_{i + j > L} M_{i,j}$$
$$P(\text{Under } L) = 1.0 - P(\text{Over } L)$$

Exemples concrets:
- **Over 1.5:** $\sum_{k=2}^{14} P(Z = k)$
- **Over 2.5:** $\sum_{k=3}^{14} P(Z = k)$
- **Over 3.5:** $\sum_{k=4}^{14} P(Z = k)$

### 6.3 Ambdós Equips Marquen (BTTS - Both Teams to Score)
L'esdeveniment $\text{BTTS}_{\text{Yes}}$ equival a $\{X \ge 1\} \cap \{Y \ge 1\}$. Mitjançant el principi d'inclusió-exclusió sobre el complementari:
$$P(\text{BTTS}_{\text{Yes}}) = 1 - P(X = 0 \cup Y = 0) = 1 - \left[ P(X = 0) + P(Y = 0) - P(X = 0, Y = 0) \right]$$

En termes dels elements de la matriu $M$:
- $P(X = 0) = \sum_{j=0}^7 M_{0,j}$ (suma de la fila 0)
- $P(Y = 0) = \sum_{i=0}^7 M_{i,0}$ (suma de la columna 0)
- $P(X = 0, Y = 0) = M_{0,0}$

Per tant:
$$P(\text{BTTS}_{\text{Yes}}) = 1.0 - \sum_{j=0}^7 M_{0,j} - \sum_{i=0}^7 M_{i,0} + M_{0,0}$$
$$P(\text{BTTS}_{\text{No}}) = 1.0 - P(\text{BTTS}_{\text{Yes}})$$

### 6.4 Mercat de Doble Oportunitat
Pel principi d'additivitat sobre esdeveniments incompatibles:
$$P(1X) = P(\text{Home Win}) + P(\text{Draw})$$
$$P(X2) = P(\text{Draw}) + P(\text{Away Win})$$
$$P(12) = P(\text{Home Win}) + P(\text{Away Win}) = 1.0 - P(\text{Draw})$$

---

## 7. Dinamisme d'Equip: Elo Logístic i Tri-Ranks

Els equips no mantenen un nivell de qualitat estàtic durant tota la temporada. El rendiment evoluciona a cada jornada. El mòdul [`model/rank_engine.py`](file:///c:/Fortia/Personal/PreddicionsLliga/model/rank_engine.py) implementa un doble sistema d'actualització: la puntuació Elo i els rànquings ofensius/defensius (*Tri-Ranks*).

### 7.1 Puntuació Elo amb Multiplicador Logarítmic de Gols
El sistema adapta la formulació clàssica de la FIDE introduint dos ajustos específics per al futbol:
1. **Avantatge de camp:** Afegit de $+50$ punts a l'equip local a l'expectativa de victòria.
2. **Multiplicador de marge de victòria:** Modulació del factor $K$ en funció de la diferència de gols reals.

#### 7.1.1 Expectativa de Resultat
$$E_H = \frac{1}{1 + 10^{\frac{\text{Elo}_A - \text{Elo}_H - 50}{400}}}$$
$$E_A = 1.0 - E_H$$

#### 7.1.2 Resultat Observat ($S_H, S_A$)
$$S_H = \begin{cases} 1.0 & \text{si } g_H > g_A \\ 0.5 & \text{si } g_H = g_A \\ 0.0 & \text{si } g_H < g_A \end{cases}, \quad S_A = 1.0 - S_H$$

#### 7.1.3 Factor $K$ Efectiu amb Marge de Gols
Per evitar que una victòria per 1-0 tingui el mateix pes que una golejada per 5-0, es defineix:
$$G = \ln(\max(1, |g_H - g_A|) + 1.0)$$
$$K_{eff} = K_{base} \cdot G$$
on $K_{base} = 32.0$. Per exemple, si $|g_H - g_A| = 1$, $G = \ln(2) \approx 0.693$, donant $K_{eff} \approx 22.18$. Si $|g_H - g_A| = 3$, $G = \ln(4) \approx 1.386$, donant $K_{eff} \approx 44.36$.

#### 7.1.4 Actualització Elo
$$\Delta \text{Elo}_H = K_{eff} \cdot (S_H - E_H)$$
$$\Delta \text{Elo}_A = K_{eff} \cdot (S_A - E_A) = -\Delta \text{Elo}_H$$
$$\text{Elo}_H^{(t+1)} = \text{Elo}_H^{(t)} + \Delta \text{Elo}_H$$

### 7.2 Motor de Tri-Ranks (Rànquing General, Ofensiu i Defensiu)
El futbol presenta una elevada variància en els resultats reals degut a la influència d'accions aïllades o fortuitades (gols en pròpia porta, rebots, penals discutits). El sistema redueix aquest soroll ponderant la generació de perill real:
$$\text{Output}_{eff} = 0.65 \cdot xG + 0.35 \cdot \text{Gols Reals}$$

#### 7.2.1 Actualització del Rànquing Ofensiu ($R_{off}$)
Sigui $\lambda_{exp}$ l'expectativa de gols que el model havia previst per a l'equip:
$$\Delta_{off} = (0.65 \cdot xG_{for} + 0.35 \cdot g_{scored}) - \lambda_{exp}$$
$$\text{Shift}_{off} = -0.30 \cdot \Delta_{off}$$
$$R_{off}^{(t+1)} = \text{clip}\left(R_{off}^{(t)} + \text{Shift}_{off}, \; 1.0, \; N_{teams}\right)$$

*Nota:* Com que un índex menor indica millor rànquing (1.0 és el millor), si l'equip sobrepassa la seva expectativa ($\Delta_{off} > 0$), el desplaçament és negatiu, millorant la seva posició.

#### 7.2.2 Actualització del Rànquing Defensiu ($R_{def}$)
$$\Delta_{def} = \lambda_{exp, opp} - (0.65 \cdot xG_{against} + 0.35 \cdot g_{conceded})$$
$$\text{Shift}_{def} = -0.30 \cdot \Delta_{def}$$
$$R_{def}^{(t+1)} = \text{clip}\left(R_{def}^{(t)} + \text{Shift}_{def}, \; 1.0, \; N_{teams}\right)$$

#### 7.2.3 Rànquing General Unificat ($R_{gen}$)
$$R_{gen} = \frac{R_{off} + R_{def}}{2.0}$$

---

## 8. Processos Estocàstics Secundaris: Córners i Disciplina

A més del mercat principal de gols, el projecte modela de forma independent altres variables discretes mitjançant els mòduls [`model/corner_model.py`](file:///c:/Fortia/Personal/PreddicionsLliga/model/corner_model.py) i [`model/card_model.py`](file:///c:/Fortia/Personal/PreddicionsLliga/model/card_model.py).

### 8.1 Model de Córners
Els llançaments de cantonada depenen de la pressió lateral ofensiva i de les tendències defensives de rebuig:
$$\lambda_{C, H} = \frac{C_{for5, H} + C_{against5, A}}{2} \cdot 1.05$$
$$\lambda_{C, A} = \frac{C_{for5, A} + C_{against5, H}}{2} \cdot 0.95$$
$$\lambda_{C, total} = \lambda_{C, H} + \lambda_{C, A}$$

On $C_{for5}$ i $C_{against5}$ representen les mitjanes dels darrers 5 partits, i els factors $1.05$ i $0.95$ capturen el biaix empíric de jugar a casa.

La distribució dels córners totals s'avalua sobre un procés de Poisson unidimensional fins a $k = 24$:
$$P(\text{Corners} = k) = \frac{\lambda_{C, total}^k e^{-\lambda_{C, total}}}{k!}$$
$$P(\text{Over } K) = \sum_{k = K+1}^{24} P(\text{Corners} = k)$$

### 8.2 Model de Disciplina i Targetes
El nombre de targetes en un partit està fortament condicionat pel perfil de l'àrbitre designat, la intensitat del duel i la proximitat classificatòria.

#### 8.2.1 Component Arbitral i Tendència d'Equip
- **Àrbitre:** Mitjana històrica de targetes grogues $Y_{ref}$ multiplicada pel seu índex de severitat $S_{ref} \in [0.80, 1.30]$.
- **Equips:** Suma de la mitjana d'amonestacions recents $T_{comb} = Cards_{5, H} + Cards_{5, A}$.
- **Ponderació Base:** $0.55 \cdot (Y_{ref} \cdot S_{ref}) + 0.45 \cdot T_{comb}$.

#### 8.2.2 Factors Moduladors Tàctics
- **Factor Derbi / Rivalitat Històrica:**
  $$\text{Boost}_{derby} = \begin{cases} +0.90 & \text{si és duel de màxima rivalitat} \\ 0.0 & \text{altrament} \end{cases}$$
- **Factor Eliminatòria (*Knockout*):** $\text{Boost}_{ko} = +0.50$ si el partit és d'eliminació directa.
- **Tensió de Taula:** Si dos equips es troben molt propers a la taula classificatòria:
  $$\text{Boost}_{tension} = \max(0.0, \; (10.0 - |R_{gen, H} - R_{gen, A}|) \cdot 0.04)$$

#### 8.2.3 Intensitat Total de Targetes ($\lambda_{cards}$)
$$\lambda_{cards} = 0.55 (Y_{ref} \cdot S_{ref}) + 0.45 T_{comb} + \text{Boost}_{derby} + \text{Boost}_{ko} + \text{Boost}_{tension}$$

#### 8.2.4 Probabilitat de Targeta Vermella
Les expulsions segueixen un procés de Poisson amb taxa d'incidència $\lambda_{red} = Red_{ref} \cdot (1.0 + 0.30 \cdot \mathbb{I}_{derby})$. La probabilitat que hi hagi com a mínim una expulsió és:
$$P(\text{Red Card} \ge 1) = 1 - e^{-\lambda_{red}}$$

---

## 9. Teoria Quantitativa de Mercat: Valor Esperat, Kelly i Arbitratge

Un model probabilístic esdevé una eina financera quan es confronta amb les probabilitats implícites en les quotes ($Q$) establertes pels creadors de mercat (*bookmakers*) o creuades entre diferents operadors.

### 9.1 Valor Esperat (+EV%) amb Suport de Devolució (*Push/Void*)
A diferència de les formulacions trivials que només consideren victòria o pèrdua, el mòdul [`engine/value_bet_engine.py`](file:///c:/Fortia/Personal/PreddicionsLliga/engine/value_bet_engine.py) dóna suport a mercats amb possibilitat de devolució del capital (*push*), com els handicaps asiàtics o empats no apostables.

Sigui $p_w$ la probabilitat de guanyar, $p_v$ la probabilitat de devolució (*void/push*), i $p_l = 1 - p_w - p_v$ la probabilitat de pèrdua:
$$\mathbb{E}[\text{Retorn per unitat apostada}] = p_w \cdot Q + p_v \cdot 1.0 + p_l \cdot 0.0 = p_w \cdot Q + p_v$$
$$EV\% = \left(p_w \cdot Q + p_v - 1.0\right) \times 100\%$$

#### 9.1.1 Quota Justa (*Fair Odd*)
La quota justa teòrica satisfà $\mathbb{E}[\text{Retorn}] = 1.0$:
$$p_w \cdot Q_{fair} + p_v = 1.0 \implies Q_{fair} = \frac{1.0 - p_v}{p_w}$$

#### 9.1.2 Probabilitat Implícita de la Quota
$$P_{imp} = \frac{1.0 - p_v}{Q} \times 100\%$$

Una aposta presenta **valor esperat positiu (+EV)** si i només si:
$$Q > Q_{fair} \iff p_w > \frac{1.0 - p_v}{Q}$$

### 9.2 Criteri de Kelly Fraccional amb Push
El Criteri de Kelly maximitza el creixement logarítmic asimptòtic del capital:
$$\max_f \; \mathbb{E}\left[\ln\left(1 + f \cdot R\right)\right]$$

Per a un escenari amb retorn net $b = Q - 1.0$ en cas de victòria, $0$ en cas de push i $-1.0$ en cas de pèrdua:
$$f^* = \frac{b \cdot p_w - p_l}{b \cdot (p_w + p_l)}$$

A causa de la incertesa dels paràmetres estimats del model (*model risk*), aplicar el Kelly complet condueix a una excessiva volatilitat del capital. El motor aplica un **Kelly Fraccional defensiu** amb multiplicador $\kappa = 0.25$ (un quart de Kelly):
$$f_{executat} = \max\left(0, \; f^* \cdot \kappa\right)$$

### 9.3 Condició Matemàtica d'Arbitratge Esportiu (*Surebet*)
Un arbitratge es produeix quan es troben quotes disponibles en diferents plataformes per a un conjunt exhaustiu i mútuament excloent d'esdeveniments tals que la suma de les seves probabilitats implícites és estrictament inferior a la unitat.

Sigui un mercat de $K$ opcions disjuntes ($K=2$ per a Over/Under, BTTS o Doble Oportunitat vs Pura; $K=3$ per a 1X2 creuat). Siguin $Q_1, Q_2, \dots, Q_K$ les millors quotes trobades a diferents cases d'apostes:
$$S_{inv} = \sum_{k=1}^K \frac{1}{Q_k}$$

- **Condició de Surebet:** $S_{inv} < 1.0$ (Marge $< 100\%$).
- **Rendiment Brut Teòric Garantit:**
  $$\text{Profit}_{gross}\% = \left(\frac{1}{S_{inv}} - 1.0\right) \times 100\%$$

### 9.4 Models de Comissió d'Exchange
Quan una de les quotes procedeix d'un mercat d'intercanvi (*exchange* com Betfair, Matchbook o Smarkets), el càlcul ha d'incorporar el cost de liquidació. El motor [`engine/arbitrage_engine.py`](file:///c:/Fortia/Personal/PreddicionsLliga/engine/arbitrage_engine.py) implementa dos models:

1. **`NET_MARKET_WINNINGS` (Comissió sobre Guany Net de Mercat):**
   S'aplica exclusivament si el balanç net agregat de la posició en aquest operador és positiu:
   $$\text{Comissió} = c \cdot \max(0, \; PnL_{gross})$$
   utilitzat a Betfair (2% ES / DGOJ), Matchbook (4% ES) i Smarkets Standard (2%).

2. **`PER_BET_SETTLEMENT` (Taxa per Aposta Liquidada / Turnover):**
   S'aplica directament sobre cada aposta tancada, independentment de si guanya o perd:
   $$\text{Comissió} = c \cdot \text{Stake}$$
   utilitzat a Smarkets Pro (1%) i Smarkets Select (3%).

### 9.5 Optimització Discreta de Stakes Enters
A la pràctica, apostar fraccions de cèntim provoca alertes als sistemes de control de risc de les cases d'apostes. El motor d'arbitratge descarta les fórmules analítiques contínues arrodonides a posteriori i implementa una **optimització exhaustiva sobre una xarxa entera**:

Sigui $B \in \mathbb{N}$ el bankroll total enter (per defecte $100$ €).
- En mercats de 2 opcions: l'espai és unidimensional de dimensió $B-1$, amb $s_1 \in \{1, 2, \dots, B-1\}$ i $s_2 = B - s_1$.
- En mercats de 3 opcions: l'espai és bidimensional de mida $\frac{(B-1)(B-2)}{2}$, amb $s_1 \ge 1, s_2 \ge 1, s_3 = B - s_1 - s_2 \ge 1$.

L'algorisme troba el vector $\mathbf{s}^*$ que resol:
$$\mathbf{s}^* = \arg\max_{\mathbf{s} \in \mathcal{S}} \left( \min_{i \in \{1, \dots, K\}} PnL_{net}(i, \mathbf{s}) \right)$$
amb criteris de desempat que maximitzen el guany mitjà i minimitzen la variància entre escenaris. Això garanteix que cap resultat no pugui generar una pèrdua per culpa d'errors d'arrodoniment.

---

## 10. Validació Estadística i Proper Scoring Rules

Una de les majors equivocacions en la literatura d'aprenentatge automàtic aplicat a l'esport és utilitzar la precisió categòrica (**Accuracy**) per avaluar un model de pronòstics. Si un equip té un 70% de probabilitat de guanyar, predir simplement la seva victòria tindrà un 70% d'accuracy, però no mesura si la probabilitat calibrada era $0.70$ o $0.99$.

La validació d'un model probabilístic requereix **Regles de Puntuació Pròpies (*Proper Scoring Rules*)**, on la puntuació esperada es maximitza si i només si les probabilitats emeses coincideixen amb les veritables probabilitats subjacents del procés.

### 10.1 Ranked Probability Score (RPS)
El resultat d'un partit de futbol en el mercat 1X2 té una estructura ordenada natural:
$$\text{Victòria Local (1)} < \text{Empat (X)} < \text{Victòria Visitant (2)}$$

Un error predint victòria local quan es produeix un empat és menys greu que predir victòria local quan guanya el visitant. El **Ranked Probability Score (RPS)** d'Epstein (1969) avalua les funcions de distribució acumulades:

$$RPS = \frac{1}{K - 1} \sum_{i=1}^{K - 1} \left( \sum_{j=1}^i p_j - \sum_{j=1}^i y_j \right)^2$$

Per a $K = 3$ categories:
$$RPS = \frac{1}{2} \left[ (p_1 - y_1)^2 + ((p_1 + p_x) - (y_1 + y_x))^2 \right]$$

on:
- $\mathbf{p} = (p_1, p_x, p_2)$ és el vector de probabilitats predites ($p_1 + p_x + p_2 = 1$).
- $\mathbf{y} = (y_1, y_x, y_2)$ és el vector indicador del resultat real (un vector de base canònica: $(1, 0, 0)$ per a 1, $(0, 1, 0)$ per a X, $(0, 0, 1)$ per a 2).

Un $RPS = 0.0$ indica una predicció perfecta amb certesa absoluta. Un model de referència ingenu basat en freqüències històriques típiques ($p_1 = 0.45, p_x = 0.28, p_2 = 0.27$) obté un RPS mitjà d'aproximadament $\approx 0.190 - 0.205$. Qualsevol millora estructural per sota d'aquest llindar reflecteix un guany informatiu real.

### 10.2 Brier Score Multiclasse
El Brier Score mesura la distància euclidiana quadràtica mitjana entre el vector de probabilitats i el vector indicador:
$$BS = \frac{1}{N} \sum_{n=1}^N \sum_{k=1}^K (p_{n,k} - y_{n,k})^2$$

A diferència de l'RPS, no pren en consideració l'ordenació de les categories, però constitueix una mètrica de calibració directa per a mercats binaris (Over/Under, BTTS):
$$BS_{binari} = \frac{1}{N} \sum_{n=1}^N (p_n - y_n)^2 \in [0, 1]$$

### 10.3 Pèrdua Logarítmica (*Logarithmic Loss / Cross-Entropy*)
Derivada de la teoria de la informació i de la màxima versemblança:
$$\text{LogLoss} = -\frac{1}{N} \sum_{n=1}^N \sum_{k=1}^K y_{n,k} \ln(p_{n,k})$$

La pèrdua logarítmica penalitza de forma exponencial les prediccions que assignen una probabilitat molt baixa a un esdeveniment que finalment ocorre (sorpreses majúscules).

### 10.4 Protocol de Backtesting Walk-Forward sense Biaix de Futur
Per avaluar el sistema amb rigor científic, és imperatiu evitar el **biaix de mirada al futur (*lookahead bias*)**:
1. Per predir la jornada $T$, l'estat dels equips (Elo, rànquings ofensius, rànquings defensius i mitjanes mòbils d'xG) s'ha de congelar estrictament a la data i hora prèvia a l'inici del primer partit d'aquesta jornada.
2. Un cop finalitzats tots els partits de la jornada $T$, es registren les mètriques de scoring rule ($RPS_T, BS_T$) i es procedeix a l'actualització dels paràmetres d'equip.
3. El model mai no pot recalcular estadístiques d'una jornada passada amb informació generada a posteriori.

---

## 11. Exemple Numèric Complet i Verificat

Per il·lustrar de forma transparent i reproduïble tot el flux de càlcul matemàtic, s'executa un partit de **LaLiga EA Sports** ($N = 20$, $M = 10.0$, $\mu_H = 1.42$, $\mu_A = 1.12$):

### 11.1 Dades d'Entrada
- **Equip Local (Athletic Club):**
  - $R_{off, H} = 3.0$, $R_{def, H} = 4.0$
  - $\text{Rolling } xG_{for} = 1.65$, $\text{Rolling } xG_{against} = 1.10$
  - $\text{Elo}_H = 1620.0$
- **Equip Visitant (Real Sociedad):**
  - $R_{off, A} = 6.0$, $R_{def, A} = 5.0$
  - $\text{Rolling } xG_{for} = 1.30$, $\text{Rolling } xG_{against} = 1.25$
  - $\text{Elo}_A = 1580.0$

### 11.2 Càlcul de Forces i Intensitats
$$\alpha_H = 1.0 + 0.35 \cdot \frac{10.0 - 3.0}{10.0} = 1.0 + 0.245 = 1.245$$
$$\beta_A = 1.0 + 0.30 \cdot \frac{5.0 - 10.0}{10.0} = 1.0 - 0.150 = 0.850$$
$$\lambda_{rank, H} = 1.42 \cdot 1.245 \cdot 0.850 = 1.5027$$

$$\alpha_A = 1.0 + 0.35 \cdot \frac{10.0 - 6.0}{10.0} = 1.0 + 0.140 = 1.140$$
$$\beta_H = 1.0 + 0.30 \cdot \frac{4.0 - 10.0}{10.0} = 1.0 - 0.180 = 0.820$$
$$\lambda_{rank, A} = 1.12 \cdot 1.140 \cdot 0.820 = 1.0470$$

Intensitats empíriques creuades:
$$\lambda_{emp, H} = \frac{1.65 + 1.25}{2} = 1.4500$$
$$\lambda_{emp, A} = \frac{1.30 + 1.10}{2} = 1.2000$$

Intensitats finals combinades (60% rank + 40% empíric):
$$\lambda_H = 0.60 \cdot 1.5027 + 0.40 \cdot 1.4500 = 0.9016 + 0.5800 = \mathbf{1.4816} \approx 1.48$$
$$\lambda_A = 0.60 \cdot 1.0470 + 0.40 \cdot 1.2000 = 0.6282 + 0.4800 = \mathbf{1.1082} \approx 1.11$$

### 11.3 Distribució Conjunta Dixon-Coles i Normalització
Amb $\lambda_H = 1.4816$, $\lambda_A = 1.1082$ i $\rho = -0.10$:
- $\tau(0, 0) = 1.0 - (1.4816 \cdot 1.1082 \cdot -0.10) = 1.0 + 0.1642 = 1.1642$
- $\tau(0, 1) = 1.0 + (1.4816 \cdot -0.10) = 0.8518$
- $\tau(1, 0) = 1.0 + (1.1082 \cdot -0.10) = 0.8892$
- $\tau(1, 1) = 1.0 - (-0.10) = 1.1000$

Després de calcular els 64 elements de la matriu $8 \times 8$ i dividir per la suma total de la matriu ($S_M \approx 0.9996$):

| Marcador | Probabilitat Calculada |
| :---: | :---: |
| **1 - 1** | **13.55%** |
| **1 - 0** | **9.89%** |
| **2 - 1** | **9.13%** |
| **0 - 0** | **8.74%** |
| **2 - 0** | **8.24%** |

### 11.4 Mercats Agregats Obtinguts
- **1X2:**
  - $P(\text{Victòria Local "1"}) = \mathbf{44.5\%}$
  - $P(\text{Empat "X"}) = \mathbf{28.4\%}$
  - $P(\text{Victòria Visitant "2"}) = \mathbf{27.1\%}$
- **Gols Totals:**
  - $P(\text{Over 1.5}) = \mathbf{74.3\%} \quad (P(\text{Under 1.5}) = 25.7\%)$
  - $P(\text{Over 2.5}) = \mathbf{47.9\%} \quad (P(\text{Under 2.5}) = 52.1\%)$
  - $P(\text{Over 3.5}) = \mathbf{26.2\%} \quad (P(\text{Under 3.5}) = 73.8\%)$
- **Ambdós Equips Marquen (BTTS):**
  - $P(\text{BTTS}_{\text{Yes}}) = \mathbf{53.0\%} \quad (P(\text{BTTS}_{\text{No}}) = 47.0\%)$

### 11.5 Avaluació de Valor (+EV) i Criteri de Kelly
Suposem que una casa d'apostes ofereix una quota $Q = 2.45$ per a la Victòria Local:
- $p_w = 0.445, \quad p_v = 0.0$
- Quota justa del model:
  $$Q_{fair} = \frac{1.0}{0.445} = \mathbf{2.25}$$
- Probabilitat implícita en la quota del mercat:
  $$P_{imp} = \frac{1.0}{2.45} \times 100 = \mathbf{40.8\%}$$
- **Valor Esperat:**
  $$EV\% = (0.445 \cdot 2.45 - 1.0) \times 100\% = (1.09025 - 1.0) \times 100 = \mathbf{+9.0\%}$$
- **Càlcul de Kelly:**
  $$b = 2.45 - 1.0 = 1.45, \quad p_l = 0.555$$
  $$f^* = \frac{1.45 \cdot 0.445 - 0.555}{1.45 \cdot (0.445 + 0.555)} = \frac{0.64525 - 0.555}{1.45} = \frac{0.09025}{1.45} \approx 6.22\%$$
  $$f_{executat} = 6.22\% \times 0.25 = \mathbf{1.6\% \text{ del bankroll}}$$

---

## 12. Transparència Intel·lectual i Limitacions Empíriques

En concordança amb els principis d'integritat científica del projecte, cal detallar explícitament la frontera entre les deduccions teòriques i els paràmetres heurístics:

1. **Paràmetres Fixats Empíricament (No MLE en temps real):**
   - El coeficient de dependència $\rho = -0.10$ està calibrat de forma heurística per a les lligues europees. No s'executa una Màxima Versemblança (*Maximum Likelihood Estimation - MLE*) a cada minut d'execució.
   - Les ponderacions 60% / 40% (rànquings vs $xG$) i 65% / 35% (xG vs gols per a tri-ranks) són paràmetres empírics basats en la minimització de variància històrica, no en un mostreig bayesià MCMC.
2. **Absència de Xarxes Neuronals o Machine Learning Opac:**
   - El sistema prioritza deliberadament models estadístics interpretables de recompte (Poisson, Dixon-Coles, Elo logístic) per sobre de xarxes de caixa negra (Deep Learning, Random Forest). Això permet auditar cada decimal i derivar cotes d'error analítiques.
3. **Absència de Simulacions de Monte Carlo:**
   - La integració dels mercats s'efectua per convolució i sumació exacta sobre la matriu discreta $8 \times 8$, no per mostreig aleatori de Monte Carlo. Això elimina el soroll d'estimació estocàstic i produeix resultats 100% deterministes.
4. **Falta de Correlació Dinàmica Temporal In-Play:**
   - El model actual està dissenyat per a l'avaluació prèvia al xiulet inicial (*pre-match*). No resol equacions diferencials estocàstiques de salts per al mercat en viu (*in-play*).

---

> **Citació Recomanada:**  
> Preddicions Lliga Development Team (2026). *Mathematical Foundations and Applied Probability Framework for Association Football Prediction Models*. GitHub Repository Technical Monograph. https://github.com/fortiaarumi/Prediccions
