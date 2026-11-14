"""Mise à jour du schéma des bases : au lancement (D-120), ou `uv run python -m app.migrate`.

Pour chaque base (base unique, ou celle de chaque profil, D-098) en retard sur le code : copie
« avant-migration » dans sauvegardes/ du dossier des données (D-119), relue, puis scripts de
app/migrations/ manquants (D-009). Une base plus récente que le code arrête tout avant la moindre
écriture. Pour la base de démo : `PATRIMOINE_DB=data/demo.db uv run python -m app.migrate`.
"""

import sqlite3
from collections.abc import Iterable
from contextlib import closing
from pathlib import Path

from app import datadir, db, profiles
from app.backup import BackupError, backup, verify
from app.config import load_dotenv

LABEL = "avant-migration"  # <base>-avant-migration-<date>.db : jamais supprimée par la rotation


class UpgradeError(RuntimeError):
    """Mise à jour refusée ou ratée : message montré tel quel, le lancement s'arrête."""


def read_version(path: Path) -> int:
    """Version du schéma lue en lecture seule (aucun fichier créé ni modifié)."""
    with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as conn:
        return db.schema_version(conn)


def pending(paths: Iterable[Path]) -> list[tuple[Path, int]]:
    """Bases à mettre à jour et leur version. Vérifie toutes les bases avant d'en toucher une :
    une base illisible ou plus récente que le code lève UpgradeError, rien n'est modifié."""
    todo = []
    for path in paths:
        if not path.is_file():
            continue  # créée vide au lancement, directement à la bonne version
        try:
            version = read_version(path)
        except sqlite3.DatabaseError as err:
            raise UpgradeError(
                f"Base illisible : {path} ({err}). Rien n'a été modifié ; "
                "les sauvegardes sont dans le dossier sauvegardes/."
            ) from err
        if version > db.SCHEMA_VERSION:
            raise UpgradeError(
                f"La base {path} vient d'une version plus récente de Patrimoine (schéma "
                f"{version}, cette version connaît le {db.SCHEMA_VERSION}). Installer la dernière "
                "version de Patrimoine. Rien n'a été modifié."
            )
        if 0 < version < db.SCHEMA_VERSION:  # 0 : fichier vide, créé par init_db au lancement
            todo.append((path, version))
    return todo


def upgrade(paths: Iterable[Path]) -> list[str]:
    """Sauvegarde vérifiée puis mise à jour de chaque base en retard ; renvoie les messages.

    Une sauvegarde ratée laisse la base telle quelle. Une migration ratée est annulée script par
    script (D-009) : la base reste dans une version cohérente, la copie d'avant est nommée.
    """
    messages = []
    for path, version in pending(list(paths)):
        try:
            copy = backup(path, label=LABEL)
            verify(copy)
            if read_version(copy) != version:
                raise BackupError(f"Copie {copy.name} différente de la base")
        except (OSError, sqlite3.Error, BackupError) as err:
            raise UpgradeError(
                f"Sauvegarde impossible avant la mise à jour de {path} ({err}). "
                "La base n'a pas été modifiée (place sur le disque ?)."
            ) from err
        try:
            before, after = db.migrate(path)
        except (sqlite3.Error, RuntimeError) as err:
            raise UpgradeError(
                f"Mise à jour de {path} ratée ({err}). Copie d'avant la mise à jour : {copy}."
            ) from err
        messages.append(f"Base mise à jour : {path} (schéma {before} -> {after}), copie : {copy}")
    return messages


def main() -> None:
    """Chaque profil à son tour (D-098), sinon la base unique."""
    try:
        for message in datadir.prepare(profiles.DATA_DIR, profiles.DATA_ORIGIN):
            print(message)
    except datadir.DataDirError as err:
        raise SystemExit(str(err)) from err
    paths = profiles.databases()
    try:
        late = {path for path, _ in pending(paths)}  # refus avant tout affichage trompeur
        for path in paths:
            if not path.is_file():
                print(f"Base introuvable : {path} (elle sera créée vide au lancement).")
            elif path not in late:
                print(f"{path} est déjà en version {db.SCHEMA_VERSION} : rien à faire.")
        for message in upgrade(paths):
            print(message)
    except UpgradeError as err:
        raise SystemExit(str(err)) from err


if __name__ == "__main__":
    load_dotenv()
    main()
