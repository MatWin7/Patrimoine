-- Version 7 : nouvelles enveloppes et actifs à valeur saisie (D-056).
-- Comptes : + assurance_vie, per, livret, immobilier. Classes d'actif : + obligation,
-- fonds_euros, scpi, or, immobilier, autre ; ces classes peuvent n'avoir aucun identifiant de
-- prix (valeur saisie à la main, gardée dans price / price_history avec source = 'manuel').
-- SQLite ne sait pas modifier une contrainte CHECK : les tables account et asset sont recréées
-- (nouvelle table, copie des lignes avec leurs id, suppression de l'ancienne, renommage).
-- app/db.py coupe les clés étrangères pendant la migration (sinon supprimer l'ancienne table
-- effacerait en cascade les prix et achats programmés) et les vérifie avant de valider.
-- Aucune donnée n'est modifiée.

CREATE TABLE account_v7 (
    id          INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL UNIQUE,
    kind        TEXT    NOT NULL CHECK (kind IN ('pea', 'cto', 'crypto', 'assurance_vie', 'per',
                                                 'livret', 'immobilier', 'autre')),
    institution TEXT,
    currency    TEXT    NOT NULL DEFAULT 'EUR' CHECK (length(currency) = 3),
    cash        TEXT    NOT NULL DEFAULT '0',
    cash_date   TEXT,
    cash_op_id  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
INSERT INTO account_v7 (id, name, kind, institution, currency, cash, cash_date, cash_op_id, created_at)
    SELECT id, name, kind, institution, currency, cash, cash_date, cash_op_id, created_at FROM account;
DROP TABLE account;
ALTER TABLE account_v7 RENAME TO account;

CREATE TABLE asset_v7 (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    asset_class   TEXT NOT NULL CHECK (asset_class IN ('action', 'etf', 'crypto', 'obligation',
                                       'fonds_euros', 'scpi', 'or', 'immobilier', 'autre')),
    isin          TEXT UNIQUE CHECK (isin IS NULL OR length(isin) = 12),
    ticker        TEXT,
    coingecko_id  TEXT,
    currency      TEXT NOT NULL CHECK (length(currency) = 3),
    color         INTEGER CHECK (color IS NULL OR color BETWEEN 1 AND 7),
    CHECK (asset_class NOT IN ('action', 'etf', 'crypto')
           OR ticker IS NOT NULL OR coingecko_id IS NOT NULL),
    CHECK (asset_class <> 'crypto' OR coingecko_id IS NOT NULL)
);
INSERT INTO asset_v7 (id, name, asset_class, isin, ticker, coingecko_id, currency, color)
    SELECT id, name, asset_class, isin, ticker, coingecko_id, currency, color FROM asset;
DROP TABLE asset;
ALTER TABLE asset_v7 RENAME TO asset;
CREATE UNIQUE INDEX IF NOT EXISTS ux_asset_ticker    ON asset(ticker)       WHERE ticker IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_asset_coingecko ON asset(coingecko_id) WHERE coingecko_id IS NOT NULL;

PRAGMA user_version = 7;
