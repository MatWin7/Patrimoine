-- Version 12 : achats programmés « toutes les deux semaines » (D-071), fréquence proposée par
-- Trade Republic et détectée à l'import de son relevé.
-- SQLite ne sait pas modifier une contrainte CHECK : la table recurring est recréée (nouvelle
-- table, copie des lignes avec leurs id, suppression de l'ancienne, renommage). Aucune table ne
-- la référence. Aucune donnée n'est modifiée.

CREATE TABLE recurring_v12 (
    id          INTEGER PRIMARY KEY,
    account_id  INTEGER NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    asset_id    INTEGER NOT NULL REFERENCES asset(id)   ON DELETE CASCADE,
    amount      TEXT    NOT NULL,
    frequency   TEXT    NOT NULL DEFAULT 'mensuel'
                CHECK (frequency IN ('mensuel', 'trimestriel', 'hebdomadaire', 'bimensuel')),
    day         INTEGER NOT NULL CHECK (day BETWEEN 1 AND 28),
    start_date  TEXT    NOT NULL,
    last_due    TEXT,
    note        TEXT
);
INSERT INTO recurring_v12 (id, account_id, asset_id, amount, frequency, day, start_date, last_due, note)
    SELECT id, account_id, asset_id, amount, frequency, day, start_date, last_due, note FROM recurring;
DROP TABLE recurring;
ALTER TABLE recurring_v12 RENAME TO recurring;

PRAGMA user_version = 12;
