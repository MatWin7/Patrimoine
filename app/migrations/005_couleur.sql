-- Version 5 : couleur choisie pour un actif dans les graphiques (D-052).
-- NULL = automatique (rang de la ligne dans le patrimoine) ; 1 à 7 = place dans la palette
-- de styles.css (--series-1 à --series-7), pour suivre le mode clair ou sombre.
-- Aucune donnée existante n'est modifiée : tous les actifs restent en automatique.
ALTER TABLE asset ADD COLUMN color INTEGER CHECK (color IS NULL OR color BETWEEN 1 AND 7);

PRAGMA user_version = 5;
