-- Version 15 (D-082) : nouveau type d'opération « transfert_sortant » (titres ou crypto envoyés
-- hors du compte, ni vente ni espèces). Changer la contrainte CHECK oblige à recréer la table :
-- copie à l'identique, mêmes id, et compteur AUTOINCREMENT gardé (un id n'est jamais réutilisé).
CREATE TABLE operation_new (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id  INTEGER NOT NULL REFERENCES account(id) ON DELETE RESTRICT,
    asset_id    INTEGER          REFERENCES asset(id)   ON DELETE RESTRICT,
    op_date     TEXT    NOT NULL,           -- date de l'opération (YYYY-MM-DD)
    op_type     TEXT    NOT NULL CHECK (op_type IN ('solde_initial', 'achat', 'vente', 'transfert_sortant', 'dividende', 'frais', 'versement', 'retrait')),
    quantity    TEXT,                       -- Decimal, > 0 ; le sens vient de op_type
    unit_price  TEXT,                       -- Decimal, dans la devise de l'opération
    amount      TEXT,                       -- Decimal, montant brut (dividende, frais)
    fees        TEXT    NOT NULL DEFAULT '0',
    currency    TEXT    NOT NULL CHECK (length(currency) = 3),
    note        TEXT,
    source      TEXT    NOT NULL DEFAULT 'manuel',  -- 'manuel' ou nom de l'importeur CSV
    import_key  TEXT,                       -- empreinte de la ligne importée, évite les doublons
    -- Vente d'un compte crypto (D-066) : échange contre une autre crypto (1 = non imposable) et
    -- valeur globale du portefeuille crypto ce jour-là en EUR (vide = estimée par l'app).
    crypto_swap     INTEGER NOT NULL DEFAULT 0 CHECK (crypto_swap IN (0, 1)),
    portfolio_value TEXT,
    -- Achat (D-076) : part payée par la plateforme (Saveback, Stockperk), ni versement ni argent
    -- investi. Dividende : impôt retenu à la source (le montant reste le net reçu).
    offered         TEXT,
    tax_withheld    TEXT,
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    CHECK (
        (op_type IN ('solde_initial', 'achat', 'vente', 'transfert_sortant')
            AND asset_id IS NOT NULL AND quantity IS NOT NULL AND unit_price IS NOT NULL)
        OR (op_type = 'dividende' AND asset_id IS NOT NULL AND amount IS NOT NULL AND quantity IS NULL)
        OR (op_type = 'frais' AND amount IS NOT NULL AND quantity IS NULL)
        OR (op_type IN ('versement', 'retrait')
            AND asset_id IS NULL AND amount IS NOT NULL AND quantity IS NULL)
    )
);

-- Colonnes nommées : dans une base migrée, les colonnes ajoutées depuis sont en fin de table.
INSERT INTO operation_new (
    id, account_id, asset_id, op_date, op_type, quantity, unit_price, amount, fees, currency, note,
    source, import_key, crypto_swap, portfolio_value, offered, tax_withheld, created_at)
SELECT
    id, account_id, asset_id, op_date, op_type, quantity, unit_price, amount, fees, currency, note,
    source, import_key, crypto_swap, portfolio_value, offered, tax_withheld, created_at
FROM operation;
UPDATE sqlite_sequence SET seq = (SELECT seq FROM sqlite_sequence WHERE name = 'operation')
    WHERE name = 'operation_new';
DROP TABLE operation;
ALTER TABLE operation_new RENAME TO operation;
CREATE INDEX IF NOT EXISTS ix_operation_account_asset ON operation(account_id, asset_id, op_date);
CREATE UNIQUE INDEX IF NOT EXISTS ux_operation_import ON operation(source, import_key) WHERE import_key IS NOT NULL;

PRAGMA user_version = 15;
