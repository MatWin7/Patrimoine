-- Version 8 : allocation cible, repères fiscaux, frais courants (D-057 à D-060).
-- Colonnes ajoutées (vides par défaut) et une table ; aucune donnée existante n'est modifiée.
-- Date d'ouverture d'un compte (5 ans d'un PEA, 8 ans d'une assurance-vie) et versements faits
-- avant le suivi (plafond de 150 000 € d'un PEA).
ALTER TABLE account ADD COLUMN opened_on TEXT;
ALTER TABLE account ADD COLUMN deposits_before TEXT NOT NULL DEFAULT '0';
-- Frais courants d'un fonds (TER), en % par an (Decimal).
ALTER TABLE asset ADD COLUMN ter TEXT;
-- Allocation cible : part visée de chaque ligne, en % (Decimal).
CREATE TABLE IF NOT EXISTS target (
    asset_id    INTEGER PRIMARY KEY REFERENCES asset(id) ON DELETE CASCADE,
    pct         TEXT    NOT NULL
);

PRAGMA user_version = 8;
