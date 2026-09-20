-- Esquema relacional SQLite per al Sistema de Prediccions de La Lliga i Multicompetició

CREATE TABLE IF NOT EXISTS competitions (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL,          -- 'LEAGUE', 'CUP', 'EUROPE'
    weight REAL DEFAULT 1.0     -- Ponderació en l'impacte de rànquing/fatiga
);

CREATE TABLE IF NOT EXISTS teams (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    short_name TEXT,
    aliases TEXT,               -- Noms alternatius separats per coma (ex: 'Barcelona,FCB,Barça')
    stadium TEXT,
    city TEXT
);

CREATE TABLE IF NOT EXISTS referees (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    matches_count INTEGER DEFAULT 0,
    yellow_cards_avg REAL DEFAULT 4.5,
    red_cards_avg REAL DEFAULT 0.25,
    fouls_avg REAL DEFAULT 26.0,
    penalties_avg REAL DEFAULT 0.30,
    home_win_pct REAL DEFAULT 0.45,
    strictness_index REAL DEFAULT 1.0, -- Factor multiplicador de severitat (1.0 = mitjana)
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS matches (
    id TEXT PRIMARY KEY,
    competition_id TEXT NOT NULL,
    season TEXT NOT NULL,
    jornada INTEGER,
    date_time TEXT NOT NULL,
    home_team_id TEXT NOT NULL,
    away_team_id TEXT NOT NULL,
    referee_id TEXT,
    home_goals INTEGER,
    away_goals INTEGER,
    home_xg REAL,
    away_xg REAL,
    home_corners INTEGER,
    away_corners INTEGER,
    home_yellow_cards INTEGER,
    away_yellow_cards INTEGER,
    home_red_cards INTEGER,
    away_red_cards INTEGER,
    home_fouls INTEGER,
    away_fouls INTEGER,
    status TEXT NOT NULL DEFAULT 'SCHEDULED', -- 'SCHEDULED', 'FINISHED', 'POSTPONED'
    url TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (competition_id) REFERENCES competitions(id),
    FOREIGN KEY (home_team_id) REFERENCES teams(id),
    FOREIGN KEY (away_team_id) REFERENCES teams(id),
    FOREIGN KEY (referee_id) REFERENCES referees(id)
);

CREATE TABLE IF NOT EXISTS team_ratings (
    team_id TEXT PRIMARY KEY,
    general_rank REAL DEFAULT 50.0,
    off_rank REAL DEFAULT 50.0,
    def_rank REAL DEFAULT 50.0,
    elo_rating REAL DEFAULT 1500.0,
    last_match_date TEXT,
    rest_days REAL DEFAULT 7.0,
    avg_cards_for_5 REAL DEFAULT 2.2,
    avg_corners_for_5 REAL DEFAULT 5.0,
    avg_corners_against_5 REAL DEFAULT 4.5,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (team_id) REFERENCES teams(id)
);

-- Taules de Seguiment Històric ('Què hagués passat si...')
CREATE TABLE IF NOT EXISTS combo_recommendations (
    id TEXT PRIMARY KEY,
    competition_id TEXT NOT NULL,       -- 'LALIGA', 'PREMIER', 'HYPERMOTION', 'MULTI'
    season TEXT NOT NULL DEFAULT '2026-2027',
    jornada INTEGER NOT NULL,
    profile TEXT NOT NULL,              -- 'SAFE' (25€) o 'RISKY' (5€)
    stake REAL NOT NULL,                -- 25.0 o 5.0
    combined_odd REAL NOT NULL,
    combined_prob_pct REAL,
    fair_odd REAL,
    ev_pct REAL,
    status TEXT NOT NULL DEFAULT 'PENDING', -- 'PENDING', 'WON', 'LOST'
    payout REAL DEFAULT 0.0,
    profit REAL DEFAULT 0.0,
    winamax_url TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    evaluated_at TEXT
);

CREATE TABLE IF NOT EXISTS combo_legs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    combo_id TEXT NOT NULL,
    matchup TEXT NOT NULL,
    selection_name TEXT NOT NULL,
    category TEXT,
    bookie_odd REAL NOT NULL,
    model_prob REAL,
    match_date TEXT,
    url TEXT,
    home_team_id TEXT,
    away_team_id TEXT,
    status TEXT DEFAULT 'PENDING',      -- 'PENDING', 'WON', 'LOST'
    actual_result TEXT,
    evaluated_at TEXT,
    FOREIGN KEY (combo_id) REFERENCES combo_recommendations(id)
);

-- Índexs per a cerques ràpides
CREATE INDEX IF NOT EXISTS idx_matches_comp_jornada ON matches(competition_id, season, jornada);
CREATE INDEX IF NOT EXISTS idx_matches_date ON matches(date_time);
CREATE INDEX IF NOT EXISTS idx_matches_teams ON matches(home_team_id, away_team_id);
CREATE INDEX IF NOT EXISTS idx_combos_comp_jornada ON combo_recommendations(competition_id, season, jornada);
CREATE INDEX IF NOT EXISTS idx_combo_legs_combo_id ON combo_legs(combo_id);

