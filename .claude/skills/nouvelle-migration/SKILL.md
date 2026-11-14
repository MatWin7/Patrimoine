---
name: nouvelle-migration
description: Étapes pour changer le schéma SQLite de Patrimoine (nouvelle colonne, table, contrainte) sans en oublier une. À suivre dès qu'une tâche touche app/schema.sql.
---

# Nouvelle migration de schéma

Une migration fait passer une base existante (celle de l'utilisateur) d'une version de schéma à la
suivante sans perdre de données. Le code refuse de démarrer si la version de la base diffère de
`SCHEMA_VERSION` : un oubli bloque l'app de l'utilisateur.

0. **Demander d'abord** à l'utilisateur (règle du CLAUDE.md) en disant ce qui change et pourquoi.
1. `app/schema.sql` : le schéma de référence d'une base neuve, à jour, et `PRAGMA user_version = N`.
2. `app/migrations/NNN_nom.sql` (NNN = N sur 3 chiffres) : en tête, un commentaire
   `-- Version N (D-0xx) : ...` ; finir par `PRAGMA user_version = N;`.
   - Ajouter une colonne : `ALTER TABLE ... ADD COLUMN` (elle va en fin de table).
   - Changer une contrainte `CHECK` : recréer la table (`CREATE TABLE x_new`, copie, `DROP`,
     `ALTER TABLE x_new RENAME TO x`) ; `app/db.py` coupe déjà les clés étrangères pendant ce temps.
   - Montants, prix, quantités, taux : `TEXT` (Decimal), jamais `REAL`.
3. `app/db.py` : `SCHEMA_VERSION = N`.
4. `tests/test_migrations.py` : un test `test_migration_vN_...` qui part d'une base de la version
   précédente avec des données FICTIVES et vérifie qu'elles sont gardées. Le test « base v1 vers
   la version actuelle » doit toujours passer.
5. `app/demo.py` si les nouvelles colonnes doivent apparaître dans la démo.
6. Documentation : décision D-0xx dans `docs/decisions.md` (finir par « Base existante : mise
   à jour au prochain lancement, copie `avant-migration` d'abord (D-120) »), version du schéma
   dans `CLAUDE.md` si elle y est citée, et dans la description de la PR : « Après fusion :
   relancer Patrimoine (ou `uv run python -m app.migrate`) ».
7. `uv run pytest tests/test_migrations.py tests/test_db.py` puis toute la suite.

Ne jamais lancer `app.migrate` ni l'app sur les vraies bases (dossier des données, D-119) : seul
l'utilisateur le fait, après fusion.
