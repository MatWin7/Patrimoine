-- Version 18 (D-095) : dépôts DeFi suivis sur Aave (ligne reliée à un marché public), taux
-- publics du jour et soldes vérifiés. Tables nouvelles seulement : aucune donnée existante
-- n'est modifiée.
-- Dépôts DeFi suivis (D-095) : une ligne (compte, jeton) reliée à un marché Aave v3 public
-- (chaîne, adresse du pool, adresse du jeton : des contrats d'Aave, jamais un portefeuille).
-- Ses intérêts sont estimés chaque mois en achats offerts (source 'aave').
CREATE TABLE IF NOT EXISTS defi_line (
    id          INTEGER PRIMARY KEY,
    account_id  INTEGER NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    asset_id    INTEGER NOT NULL REFERENCES asset(id) ON DELETE CASCADE,
    protocol    TEXT    NOT NULL DEFAULT 'aave' CHECK (protocol IN ('aave')),
    chain_id    INTEGER NOT NULL,
    chain_name  TEXT    NOT NULL,
    market      TEXT    NOT NULL CHECK (length(market) = 42 AND market GLOB '0x*'),
    market_name TEXT    NOT NULL,
    token       TEXT    NOT NULL CHECK (length(token) = 42 AND token GLOB '0x*'),
    symbol      TEXT    NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (account_id, asset_id)
);
-- Taux publics d'un marché, un par jour (moyenne du jour, APY en Decimal) : dépôt (supply)
-- maintenant, emprunt (borrow) prévu pour les dettes.
CREATE TABLE IF NOT EXISTS defi_rate (
    chain_id    INTEGER NOT NULL,
    market      TEXT    NOT NULL,
    token       TEXT    NOT NULL,
    side        TEXT    NOT NULL CHECK (side IN ('supply', 'borrow')),
    rate_date   TEXT    NOT NULL,
    apy         TEXT    NOT NULL,
    PRIMARY KEY (chain_id, market, token, side, rate_date)
);
-- Soldes lus dans Aave et saisis par l'utilisateur (« Vérifier ») : ils recalent l'estimation.
CREATE TABLE IF NOT EXISTS defi_check (
    line_id     INTEGER NOT NULL REFERENCES defi_line(id) ON DELETE CASCADE,
    check_date  TEXT    NOT NULL,
    quantity    TEXT    NOT NULL,
    PRIMARY KEY (line_id, check_date)
);

PRAGMA user_version = 18;
