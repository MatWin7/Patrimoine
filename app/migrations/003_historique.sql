-- Version 3 : clôtures quotidiennes passées, pour la courbe d'évolution (jalon 6).
-- Ajoute seulement une table : aucune donnée existante n'est modifiée.
CREATE TABLE IF NOT EXISTS price_history (
    asset_id    INTEGER NOT NULL REFERENCES asset(id) ON DELETE CASCADE,
    price_date  TEXT    NOT NULL,           -- jour de cotation (YYYY-MM-DD), heure de la place
    close       TEXT    NOT NULL,           -- Decimal, cours de clôture
    currency    TEXT    NOT NULL CHECK (length(currency) = 3),
    source      TEXT    NOT NULL,           -- 'yahoo'
    PRIMARY KEY (asset_id, price_date)
);
PRAGMA user_version = 3;
