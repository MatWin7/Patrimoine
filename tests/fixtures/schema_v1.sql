-- Schéma SQLite de « Patrimoine » — version 1 (jalon 1).
-- Conventions (voir docs/decisions.md) :
--   * Montants, prix, quantités et taux : TEXT contenant un Decimal ("1234.5600").
--     Jamais REAL (float). Conversion en decimal.Decimal dans app/db.py.
--   * Dates : TEXT ISO 8601 ("2026-10-10"), horodatages en UTC ("2026-10-10T12:00:00Z").
--   * Devises : code ISO 4217 en majuscules ("EUR", "USD").
--   * Version du schéma : PRAGMA user_version, incrémentée à chaque migration.

PRAGMA foreign_keys = ON;

-- Un compte = un endroit où sont détenus des actifs (PEA, CTO, plateforme crypto...).
CREATE TABLE IF NOT EXISTS account (
    id          INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL UNIQUE,
    kind        TEXT    NOT NULL CHECK (kind IN ('pea', 'cto', 'crypto', 'autre')),
    institution TEXT,                       -- ex. "BoursoBank" ; libellé libre
    currency    TEXT    NOT NULL DEFAULT 'EUR' CHECK (length(currency) = 3),
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Un actif = ce qui a un prix public (action, ETF, crypto).
CREATE TABLE IF NOT EXISTS asset (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    asset_class   TEXT NOT NULL CHECK (asset_class IN ('action', 'etf', 'crypto')),
    isin          TEXT UNIQUE CHECK (isin IS NULL OR length(isin) = 12),
    ticker        TEXT,                     -- yfinance, ex. "CW8.PA"
    coingecko_id  TEXT,                     -- ex. "bitcoin"
    currency      TEXT NOT NULL CHECK (length(currency) = 3),  -- devise de cotation
    -- Il faut au moins un identifiant de prix exploitable.
    CHECK (ticker IS NOT NULL OR coingecko_id IS NOT NULL),
    CHECK (asset_class <> 'crypto' OR coingecko_id IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_asset_ticker    ON asset(ticker)       WHERE ticker IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_asset_coingecko ON asset(coingecko_id) WHERE coingecko_id IS NOT NULL;

-- Une opération sur un compte. « transaction » étant un mot réservé SQL, la table s'appelle operation.
--   solde_initial : instantané d'une position existante (quantité + PRU), évite de ressaisir l'historique.
--   achat / vente : quantity et unit_price obligatoires.
--   dividende / frais : amount obligatoire, quantity vide.
CREATE TABLE IF NOT EXISTS operation (
    id          INTEGER PRIMARY KEY,
    account_id  INTEGER NOT NULL REFERENCES account(id) ON DELETE RESTRICT,
    asset_id    INTEGER          REFERENCES asset(id)   ON DELETE RESTRICT,
    op_date     TEXT    NOT NULL,           -- date de l'opération (YYYY-MM-DD)
    op_type     TEXT    NOT NULL CHECK (op_type IN ('solde_initial', 'achat', 'vente', 'dividende', 'frais')),
    quantity    TEXT,                       -- Decimal, > 0 ; le sens vient de op_type
    unit_price  TEXT,                       -- Decimal, dans la devise de l'opération
    amount      TEXT,                       -- Decimal, montant brut (dividende, frais)
    fees        TEXT    NOT NULL DEFAULT '0',
    currency    TEXT    NOT NULL CHECK (length(currency) = 3),
    note        TEXT,
    source      TEXT    NOT NULL DEFAULT 'manuel',  -- 'manuel' ou nom de l'importeur CSV
    import_key  TEXT,                       -- empreinte de la ligne importée, évite les doublons
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    CHECK (
        (op_type IN ('solde_initial', 'achat', 'vente')
            AND asset_id IS NOT NULL AND quantity IS NOT NULL AND unit_price IS NOT NULL)
        OR (op_type = 'dividende' AND asset_id IS NOT NULL AND amount IS NOT NULL AND quantity IS NULL)
        OR (op_type = 'frais' AND amount IS NOT NULL AND quantity IS NULL)
    )
);
CREATE INDEX IF NOT EXISTS ix_operation_account_asset ON operation(account_id, asset_id, op_date);
CREATE UNIQUE INDEX IF NOT EXISTS ux_operation_import ON operation(source, import_key) WHERE import_key IS NOT NULL;

-- Cache et historique des prix publics.
CREATE TABLE IF NOT EXISTS price (
    asset_id    INTEGER NOT NULL REFERENCES asset(id) ON DELETE CASCADE,
    fetched_at  TEXT    NOT NULL,           -- horodatage UTC de la récupération
    price       TEXT    NOT NULL,           -- Decimal
    currency    TEXT    NOT NULL CHECK (length(currency) = 3),
    source      TEXT    NOT NULL,           -- 'yfinance', 'coingecko', 'manuel'
    PRIMARY KEY (asset_id, fetched_at, source)
);

-- Taux de change BCE : 1 EUR = rate unités de currency (convention Frankfurter).
CREATE TABLE IF NOT EXISTS fx_rate (
    rate_date   TEXT NOT NULL,              -- jour ouvré BCE (YYYY-MM-DD)
    currency    TEXT NOT NULL CHECK (length(currency) = 3 AND currency <> 'EUR'),
    rate        TEXT NOT NULL,              -- Decimal
    PRIMARY KEY (rate_date, currency)
);

PRAGMA user_version = 1;
