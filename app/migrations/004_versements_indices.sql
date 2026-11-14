-- Version 4 : versements et retraits, espèces calculées (D-041), liste d'indices (D-042).
-- SQLite ne sait pas modifier la contrainte CHECK d'une table : la table operation est recréée
-- (nouvelle table, copie des lignes avec leurs id, suppression de l'ancienne, renommage).
-- Aucun montant n'est modifié. cash_op_id = dernière opération existante : les espèces affichées
-- restent exactement celles d'avant la migration ; seules les opérations suivantes les modifient.
ALTER TABLE account ADD COLUMN cash_op_id INTEGER NOT NULL DEFAULT 0;
UPDATE account SET cash_op_id = (SELECT COALESCE(MAX(id), 0) FROM operation);

CREATE TABLE operation_v4 (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id  INTEGER NOT NULL REFERENCES account(id) ON DELETE RESTRICT,
    asset_id    INTEGER          REFERENCES asset(id)   ON DELETE RESTRICT,
    op_date     TEXT    NOT NULL,           -- date de l'opération (YYYY-MM-DD)
    op_type     TEXT    NOT NULL CHECK (op_type IN ('solde_initial', 'achat', 'vente', 'dividende', 'frais', 'versement', 'retrait')),
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
        OR (op_type IN ('versement', 'retrait')
            AND asset_id IS NULL AND amount IS NOT NULL AND quantity IS NULL)
    )
);

INSERT INTO operation_v4 (id, account_id, asset_id, op_date, op_type, quantity, unit_price, amount,
    fees, currency, note, source, import_key, created_at)
SELECT id, account_id, asset_id, op_date, op_type, quantity, unit_price, amount,
    fees, currency, note, source, import_key, created_at FROM operation;
DROP TABLE operation;
ALTER TABLE operation_v4 RENAME TO operation;
CREATE INDEX IF NOT EXISTS ix_operation_account_asset ON operation(account_id, asset_id, op_date);
CREATE UNIQUE INDEX IF NOT EXISTS ux_operation_import ON operation(source, import_key) WHERE import_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS benchmark (
    id          INTEGER PRIMARY KEY,
    kind        TEXT    NOT NULL CHECK (kind IN ('indice', 'livret')),
    label       TEXT    NOT NULL,
    grp         TEXT,                       -- groupe affiché dans la liste (« Monde », « Livrets »...)
    ticker      TEXT,                       -- indice : ticker Yahoo
    rate        TEXT,                       -- livret : taux annuel en % (Decimal)
    position    INTEGER NOT NULL DEFAULT 0, -- ordre dans la liste
    CHECK ((kind = 'indice' AND ticker IS NOT NULL) OR (kind = 'livret' AND rate IS NOT NULL))
);
INSERT INTO benchmark (kind, label, grp, ticker, position) VALUES
    ('indice', 'MSCI World (iShares, en EUR)', 'Monde', 'IWDA.AS', 1),
    ('indice', 'MSCI World (Amundi, en EUR)', 'Monde', 'CW8.PA', 2),
    ('indice', 'Marchés émergents (iShares)', 'Monde', 'EIMI.L', 3),
    ('indice', 'CAC 40', 'Europe', '^FCHI', 4),
    ('indice', 'Euro Stoxx 50', 'Europe', '^STOXX50E', 5),
    ('indice', 'S&P 500', 'États-Unis (en USD, converti)', '^GSPC', 6),
    ('indice', 'Nasdaq Composite', 'États-Unis (en USD, converti)', '^IXIC', 7);

PRAGMA user_version = 4;
