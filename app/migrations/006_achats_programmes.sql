-- Version 6 : achats programmés (investissement régulier, D-055).
-- Un modèle par achat récurrent ; chaque échéance passée est proposée à valider ou à ignorer, rien
-- n'est enregistré tout seul. Aucune donnée existante n'est modifiée.
CREATE TABLE IF NOT EXISTS recurring (
    id          INTEGER PRIMARY KEY,
    account_id  INTEGER NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    asset_id    INTEGER NOT NULL REFERENCES asset(id)   ON DELETE CASCADE,
    amount      TEXT    NOT NULL,           -- Decimal, montant visé par achat, devise de l'actif
    frequency   TEXT    NOT NULL DEFAULT 'mensuel'
                CHECK (frequency IN ('mensuel', 'trimestriel', 'hebdomadaire')),
    -- jour du mois (1 à 28) ; en hebdomadaire, jour de la semaine (1 = lundi, 7 = dimanche)
    day         INTEGER NOT NULL CHECK (day BETWEEN 1 AND 28),
    start_date  TEXT    NOT NULL,           -- premier jour possible (YYYY-MM-DD)
    last_due    TEXT,                       -- dernière échéance validée ou ignorée (YYYY-MM-DD)
    note        TEXT
);

PRAGMA user_version = 6;
