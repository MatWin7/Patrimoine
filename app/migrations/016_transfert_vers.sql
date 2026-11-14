-- Version 16 (D-085) : destination d'un transfert sortant ou d'un retrait ; aucune donnée
-- existante n'est modifiée.
-- Compte de l'app qui reçoit les titres (même prix de revient) ou l'argent (versement). Compte
-- supprimé : le transfert redevient « hors de l'app ».
ALTER TABLE operation ADD COLUMN to_account_id INTEGER REFERENCES account(id) ON DELETE SET NULL;
-- Destination hors de l'app, en texte libre (« Ledger », « cadeau »...). Vide = non précisée.
ALTER TABLE operation ADD COLUMN destination TEXT;

PRAGMA user_version = 16;
