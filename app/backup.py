"""Sauvegarde de la base : `uv run python -m app.backup`, et automatique au lancement.

Copie la base utilisée (PATRIMOINE_DB, sinon la base unique ou celle de chaque profil) dans
sauvegardes/ du dossier des données (D-119), avec la date et l'heure dans le nom. La copie
passe par l'API de sauvegarde de SQLite : elle reste cohérente même si l'app tourne et
écrit en même temps.

Sauvegarde du jour (D-032, D-114) : une copie automatique par base, relue avant d'être gardée,
plus profils.json ; puis, si BACKUP_COPY_DIR est réglé, les copies sont recopiées hors de WSL
(par exemple /mnt/d/Patrimoine-sauvegardes, sur un autre disque que C:). Rien ne quitte la machine.
"""

import argparse
import re
import shutil
import sqlite3
import threading
from collections.abc import Iterable
from contextlib import closing
from datetime import datetime
from pathlib import Path

from app import datadir, db, profiles
from app.config import APP_DIR, backup_copy_dir, backup_keep, load_dotenv

SUBDIR = "sauvegardes"  # dans le dossier des données (D-119)


def backup_dir() -> Path:
    """Dossier des sauvegardes, lu à chaque appel : suit le dossier des données (et celui des
    tests, qui le remplacent)."""
    return profiles.DATA_DIR / SUBDIR


AUTO = "auto"  # repère des copies automatiques : seules elles sont supprimées par la rotation
MONTHS_KEPT = 12  # en plus des copies récentes : la dernière de chacun des 12 derniers mois
REGISTRY_STEM = "profils"  # copie de data/profils.json : profils-auto-<date>.json
ENV_FILE = APP_DIR / ".env"
_copy_lock = threading.Lock()
ENV_COPY = "env-copie.txt"  # réglages (.env) recopiés hors de WSL, dernière version seulement
# Nom d'une copie automatique : <base>-auto-AAAA-MM-JJ-HHMMSS.db (ou .json pour profils.json).
# Tout autre fichier d'un dossier de sauvegarde (copie manuelle, avant-suppression, fichier
# posé à la main) n'est jamais supprimé.
AUTO_NAME = re.compile(
    r"^(?P<stem>.+)-auto-(?P<year>\d{4})-(?P<month>\d{2})-\d{2}-\d{6}\.(?P<ext>db|json)$"
)


class BackupError(RuntimeError):
    """Copie illisible ou dossier de copie introuvable : message montré tel quel."""


def backup(
    source: Path | None = None,
    target_dir: Path | None = None,
    label: str = "",
    now: datetime | None = None,
) -> Path:
    target_dir = backup_dir() if target_dir is None else target_dir
    source = source or db.db_path()
    if not source.is_file():
        raise FileNotFoundError(f"Base introuvable : {source}")
    target_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"{source.stem}-{label}-" if label else f"{source.stem}-"
    target = target_dir / f"{prefix}{now or datetime.now():%Y-%m-%d-%H%M%S}.db"
    src = sqlite3.connect(source)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return target


def verify(path: Path) -> None:
    """Relit une copie en lecture seule : BackupError si SQLite la trouve abîmée."""
    try:
        with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as conn:
            result = conn.execute("PRAGMA integrity_check").fetchone()[0]
    except sqlite3.DatabaseError as err:
        raise BackupError(f"Copie illisible : {path.name} ({err})") from err
    if result != "ok":
        raise BackupError(f"Copie abîmée : {path.name} ({result})")


def kept_names(names: Iterable[str], keep: int, months: int = MONTHS_KEPT) -> set[str]:
    """Rotation « grand-père, père, fils » (D-114) des copies automatiques d'une même base.

    Garde les `keep` plus récentes, la plus récente de chacun des `months` derniers mois qui
    en ont une, et la plus récente de chaque année, sans limite. Fonction pure.
    """
    # Le nom porte la date au format AAAA-MM-JJ-HHMMSS : l'ordre alphabétique est chronologique.
    newest_first = sorted(names, reverse=True)
    kept = set(newest_first[:keep])
    seen_months: set[str] = set()
    seen_years: set[str] = set()
    for name in newest_first:
        match = AUTO_NAME.match(name)
        if not match:
            continue
        year, month = match["year"], f"{match['year']}-{match['month']}"
        if month not in seen_months and len(seen_months) < months:
            seen_months.add(month)
            kept.add(name)
        if year not in seen_years:
            seen_years.add(year)
            kept.add(name)
    return kept


def rotate(folder: Path, keep: int) -> list[Path]:
    """Supprime les copies automatiques de `folder` que la rotation ne garde pas.

    Chaque base (et profils.json) a sa propre rotation. Renvoie les fichiers supprimés.
    """
    groups: dict[tuple[str, str], list[str]] = {}
    for path in folder.iterdir() if folder.is_dir() else []:
        if match := AUTO_NAME.match(path.name):
            groups.setdefault((match["stem"], match["ext"]), []).append(path.name)
    removed = []
    for names in groups.values():
        kept = kept_names(names, keep)
        for name in names:
            if name not in kept:
                (folder / name).unlink()
                removed.append(folder / name)
    return removed


def _has_copy_of_day(target_dir: Path, stem: str, ext: str, now: datetime) -> bool:
    return target_dir.is_dir() and any(target_dir.glob(f"{stem}-{AUTO}-{now:%Y-%m-%d}-*.{ext}"))


def auto_backup(
    source: Path | None = None,
    target_dir: Path | None = None,
    keep: int | None = None,
    now: datetime | None = None,
) -> Path | None:
    """Sauvegarde du jour d'une base, faite au lancement et toutes les heures (D-032, D-114).

    Au plus une copie automatique par jour et par base, relue avant d'être gardée (une copie
    abîmée est effacée et signalée). Rotation : `keep` dernières (BACKUP_KEEP, 14 par défaut),
    plus une par mois sur 12 mois et une par an. Les sauvegardes manuelles et
    « avant-suppression » ne sont jamais supprimées. Renvoie None si la base n'existe pas
    encore ou si la copie du jour existe.
    """
    target_dir = backup_dir() if target_dir is None else target_dir
    source = source or db.db_path()
    if not source.is_file():
        return None
    now = now or datetime.now()
    keep = backup_keep() if keep is None else keep
    if _has_copy_of_day(target_dir, source.stem, "db", now):
        return None
    target = backup(source, target_dir, AUTO, now)
    try:
        verify(target)
    except BackupError:
        target.unlink()
        raise
    rotate(target_dir, keep)
    return target


def registry_backup(
    target_dir: Path | None = None, keep: int | None = None, now: datetime | None = None
) -> Path | None:
    """Copie du jour de data/profils.json (noms, fichiers et empreintes des codes, D-098).

    Sans elle, les bases des profils survivraient à une perte de WSL mais l'app ne saurait
    plus les lister. Le fichier est écrit d'un coup par l'app (os.replace) : une simple copie
    est cohérente. None si les profils ne sont pas utilisés ou si la copie du jour existe.
    """
    target_dir = backup_dir() if target_dir is None else target_dir
    source = profiles.registry_path()
    if not profiles.enabled() or not source.is_file():
        return None
    now = now or datetime.now()
    if _has_copy_of_day(target_dir, REGISTRY_STEM, "json", now):
        return None
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{REGISTRY_STEM}-{AUTO}-{now:%Y-%m-%d-%H%M%S}.json"
    shutil.copy2(source, target)
    rotate(target_dir, backup_keep() if keep is None else keep)
    return target


def _copy_file(source: Path, target: Path) -> None:
    """Copie sous un nom provisoire puis renomme : jamais de copie à moitié écrite."""
    partial = target.with_name(target.name + ".partiel")
    shutil.copyfile(source, partial)
    partial.replace(target)


def copy_out(
    dest: Path,
    source_dir: Path | None = None,
    env_file: Path | None = None,
    keep: int | None = None,
) -> list[Path]:
    """Recopie hors de WSL les copies automatiques qui n'y sont pas encore (D-114).

    Seules des sauvegardes déjà faites et fermées sont copiées, jamais une base vivante. Un
    disque rebranché après quelques jours reçoit toutes les copies manquantes. Même rotation
    que data/sauvegardes/ ; .env est recopié en dernière version (env-copie.txt).
    Renvoie les fichiers copiés ; BackupError si le dossier est introuvable.
    """
    with _copy_lock:  # sauvegarde horaire et choix du dossier dans la carte Réglages (D-130)
        return _copy_out(dest, source_dir, env_file, keep)


def _copy_out(
    dest: Path, source_dir: Path | None, env_file: Path | None, keep: int | None
) -> list[Path]:
    source_dir = backup_dir() if source_dir is None else source_dir
    try:
        dest.mkdir(exist_ok=True)  # pas de parents : un disque débranché ne se « recrée » pas
    except FileNotFoundError as err:
        message = f"Dossier de copie introuvable : {dest.parent} (disque débranché ?)"
        raise BackupError(message) from err
    copied = []
    for path in sorted(source_dir.iterdir()) if source_dir.is_dir() else []:
        if not AUTO_NAME.match(path.name) or (dest / path.name).exists():
            continue
        if path.suffix == ".db":
            verify(path)  # une copie abîmée n'est jamais propagée
        _copy_file(path, dest / path.name)
        copied.append(dest / path.name)
    env_file = ENV_FILE if env_file is None else env_file
    if env_file.is_file():
        target = dest / ENV_COPY
        if not target.is_file() or target.read_bytes() != env_file.read_bytes():
            _copy_file(env_file, target)
    rotate(dest, backup_keep() if keep is None else keep)
    return copied


def daily(
    paths: Iterable[Path], target_dir: Path | None = None, now: datetime | None = None
) -> list[str]:
    """Sauvegarde du jour de chaque base, de profils.json, puis copie hors de WSL.

    Renvoie les messages à afficher. Une erreur est signalée sans arrêter le reste : une
    sauvegarde ratée ne doit pas empêcher de consulter son patrimoine.
    """
    target_dir = backup_dir() if target_dir is None else target_dir
    messages = []
    for path in paths:
        try:
            if saved := auto_backup(path, target_dir, now=now):
                messages.append(f"Sauvegarde du jour : {saved}")
        except (OSError, sqlite3.Error, RuntimeError) as err:
            messages.append(f"Attention, sauvegarde automatique impossible ({path.name}) : {err}")
    try:
        registry_backup(target_dir, now=now)
    except OSError as err:
        messages.append(f"Attention, copie de la liste des profils impossible : {err}")
    try:
        dest = backup_copy_dir()
        if dest is not None and (copied := copy_out(dest, target_dir)):
            messages.append(f"Copie des sauvegardes : {len(copied)} fichier(s) dans {dest}")
    except (OSError, sqlite3.Error, RuntimeError) as err:
        messages.append(f"Attention, copie des sauvegardes impossible : {err}")
    return messages


def describe_copy(path: Path) -> str:
    """Une ligne sur une copie : date, version du schéma, comptes et opérations (aucun montant)."""
    try:
        verify(path)
        with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as conn:
            version = db.schema_version(conn)
            accounts = conn.execute("SELECT count(*) FROM account").fetchone()[0]
            operations = conn.execute("SELECT count(*) FROM operation").fetchone()[0]
    except (BackupError, sqlite3.DatabaseError) as err:
        return f"ILLISIBLE {path} : {err}"
    return f"OK {path} : schéma v{version}, {accounts} compte(s), {operations} opération(s)"


def check(paths: Iterable[Path], folders: Iterable[Path]) -> list[str]:
    """Relit la dernière copie automatique de chaque base dans chaque dossier : test de
    restauration en une commande (`uv run python -m app.backup --verifier`)."""
    lines = []
    for folder in folders:
        lines.append(f"Dossier {folder}")
        for path in paths:
            copies = sorted(folder.glob(f"{path.stem}-{AUTO}-*.db")) if folder.is_dir() else []
            copies = [c for c in copies if AUTO_NAME.match(c.name)]
            lines.append(
                f"  {describe_copy(copies[-1])}" if copies else f"  AUCUNE copie de {path.name}"
            )
    return lines


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.backup", description=__doc__)
    parser.add_argument(
        "--verifier",
        action="store_true",
        help="relit la dernière copie de chaque base (sauvegardes/ et BACKUP_COPY_DIR)",
    )
    args = parser.parse_args(argv)
    load_dotenv()
    try:
        for message in datadir.prepare(profiles.DATA_DIR, profiles.DATA_ORIGIN):
            print(message)
    except datadir.DataDirError as err:
        raise SystemExit(str(err)) from err
    paths = profiles.databases()  # chaque profil (D-098), sinon la base unique
    if args.verifier:
        folders = [backup_dir()]
        try:
            if dest := backup_copy_dir():
                folders.append(dest)
        except RuntimeError as err:
            print(f"Attention : {err}")
        lines = check(paths, folders)
        print("\n".join(lines))
        if any(line.startswith("  ") and not line.startswith("  OK ") for line in lines):
            raise SystemExit(1)  # code de sortie non nul : utilisable dans un script
        return
    try:
        for path in paths:
            print(f"Sauvegarde créée : {backup(path)}")
    except FileNotFoundError as err:
        raise SystemExit(str(err)) from err


if __name__ == "__main__":
    main()
