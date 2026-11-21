-- Version 22 (D-142) : douze couleurs au choix pour un actif au lieu de sept. SQLite ne sait pas
-- modifier une contrainte CHECK : la table asset est recréée (nouvelle table, copie des lignes
-- avec leurs id, suppression de l'ancienne, renommage), clés étrangères coupées par
-- app.db.migrate. Aucune donnée n'est modifiée.

CREATE TABLE asset_v22 (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    -- fonds : OPCVM / unité de compte, coté ou non (D-072)
    asset_class   TEXT NOT NULL CHECK (asset_class IN ('action', 'etf', 'fonds', 'crypto',
                                       'obligation', 'fonds_euros', 'scpi', 'or', 'immobilier',
                                       'autre')),
    isin          TEXT UNIQUE CHECK (isin IS NULL OR length(isin) = 12),
    ticker        TEXT,                     -- ticker Yahoo, ex. "CW8.PA"
    coingecko_id  TEXT,                     -- ex. "bitcoin"
    currency      TEXT NOT NULL CHECK (length(currency) = 3),  -- devise de cotation
    -- Couleur dans les graphiques : NULL = automatique, 1 à 12 = --series-N de styles.css (D-052, D-142).
    color         INTEGER CHECK (color IS NULL OR color BETWEEN 1 AND 12),
    ter           TEXT,                     -- frais courants d'un fonds, en % par an (D-060)
    category      TEXT,                     -- catégorie personnelle ; vide = la classe (D-061)
    position      INTEGER NOT NULL DEFAULT 0, -- ordre choisi dans « Gérer » (D-070)
    -- Action, ETF, crypto : il faut au moins un identifiant de prix exploitable.
    CHECK (asset_class NOT IN ('action', 'etf', 'crypto')
           OR ticker IS NOT NULL OR coingecko_id IS NOT NULL),
    CHECK (asset_class <> 'crypto' OR coingecko_id IS NOT NULL)
);
INSERT INTO asset_v22 (id, name, asset_class, isin, ticker, coingecko_id, currency, color, ter,
                       category, position)
    SELECT id, name, asset_class, isin, ticker, coingecko_id, currency, color, ter, category,
           position FROM asset;
DROP TABLE asset;
ALTER TABLE asset_v22 RENAME TO asset;
CREATE UNIQUE INDEX IF NOT EXISTS ux_asset_ticker    ON asset(ticker)       WHERE ticker IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_asset_coingecko ON asset(coingecko_id) WHERE coingecko_id IS NOT NULL;

PRAGMA user_version = 22;
