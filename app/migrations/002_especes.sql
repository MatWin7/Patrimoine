-- Version 2 : solde espèces par compte (liquidités non investies, saisies à la main).
ALTER TABLE account ADD COLUMN cash TEXT NOT NULL DEFAULT '0';  -- Decimal, >= 0
ALTER TABLE account ADD COLUMN cash_date TEXT;                  -- date de la dernière saisie
PRAGMA user_version = 2;
