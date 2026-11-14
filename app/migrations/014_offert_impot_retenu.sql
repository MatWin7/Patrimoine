-- Version 14 (D-076) : deux colonnes sur les opérations ; aucune donnée existante n'est modifiée.
-- Part d'un achat payée par la plateforme (Saveback, Stockperk de Trade Republic), dans la devise
-- de l'opération (Decimal) : ni versement ni argent investi. Vide = achat payé en entier.
ALTER TABLE operation ADD COLUMN offered TEXT;
-- Impôt retenu à la source sur un dividende (Decimal, devise de l'opération) ; le montant du
-- dividende reste le net reçu. Vide = inconnu ou aucun.
ALTER TABLE operation ADD COLUMN tax_withheld TEXT;

PRAGMA user_version = 14;
