-- Version 10 : fiscalité des cryptos (D-066). Deux colonnes sur les opérations, utiles aux
-- seules ventes d'un compte crypto ; aucune donnée existante n'est modifiée.
-- 1 = vente échangée contre une autre crypto : pas imposable (l'impôt attend la vente en euros).
ALTER TABLE operation ADD COLUMN crypto_swap INTEGER NOT NULL DEFAULT 0 CHECK (crypto_swap IN (0, 1));
-- Valeur globale du portefeuille crypto au moment de la vente, en EUR (Decimal), saisie depuis
-- le relevé ; vide = estimée par l'app.
ALTER TABLE operation ADD COLUMN portfolio_value TEXT;

PRAGMA user_version = 10;
