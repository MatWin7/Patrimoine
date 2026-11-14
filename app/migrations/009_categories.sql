-- Version 9 : catégories personnelles et allocation cible par catégorie (D-061).
-- Une colonne (vide = la classe de l'actif sert de catégorie) et une table ; aucune donnée
-- existante n'est modifiée.
ALTER TABLE asset ADD COLUMN category TEXT;
-- Part visée de chaque catégorie, en % (Decimal). Indépendante des cibles par ligne (target).
CREATE TABLE IF NOT EXISTS category_target (
    category    TEXT    PRIMARY KEY,
    pct         TEXT    NOT NULL
);

PRAGMA user_version = 9;
