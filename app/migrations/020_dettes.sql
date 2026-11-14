-- Version 20 (D-107) : dettes (crédit immobilier, prêt conso, prêt étudiant, autre) pour le
-- patrimoine net. Une dette = montant emprunté, taux annuel et durée facultatifs (tableau
-- d'amortissement à mensualités constantes) ; un capital restant dû saisi à une date recale le
-- calcul (debt_balance). Deux tables neuves, aucune donnée existante touchée.
CREATE TABLE IF NOT EXISTS debt (
    id          INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL,
    kind        TEXT    NOT NULL CHECK (kind IN ('immobilier', 'conso', 'etudiant', 'autre')),
    institution TEXT,
    currency    TEXT    NOT NULL DEFAULT 'EUR',
    principal   TEXT    NOT NULL,
    rate        TEXT,
    start_date  TEXT    NOT NULL,
    months      INTEGER CHECK (months IS NULL OR months > 0),
    position    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS debt_balance (
    debt_id      INTEGER NOT NULL REFERENCES debt(id) ON DELETE CASCADE,
    balance_date TEXT    NOT NULL,
    balance      TEXT    NOT NULL,
    PRIMARY KEY (debt_id, balance_date)
);

PRAGMA user_version = 20;
