-- Version 11 : ordre choisi des comptes et des actifs (D-070), réglé par glisser-déposer
-- dans « Gérer ». Les lignes existantes gardent l'ordre alphabétique qu'elles avaient.
ALTER TABLE account ADD COLUMN position INTEGER NOT NULL DEFAULT 0;
ALTER TABLE asset ADD COLUMN position INTEGER NOT NULL DEFAULT 0;
UPDATE account SET position = 1 + (SELECT COUNT(*) FROM account a WHERE a.name < account.name);
UPDATE asset SET position = 1 + (SELECT COUNT(*) FROM asset a
    WHERE a.name < asset.name OR (a.name = asset.name AND a.id < asset.id));

PRAGMA user_version = 11;
