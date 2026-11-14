"""Dossier des données, hors du dossier du code (D-119) : `uv run python -m app.datadir`.

Bases, profils (D-098), sauvegardes (D-114) et corbeille vivent dans le dossier de données de
l'utilisateur, propre à chaque système, pour qu'une mise à jour ou une désinstallation de l'app
ne puisse pas les emporter :
- Windows : %LOCALAPPDATA%\\Patrimoine (jamais synchronisé par OneDrive, contrairement à
  Documents) ;
- macOS : ~/Library/Application Support/Patrimoine ;
- Linux et WSL : $XDG_DATA_HOME/patrimoine, sinon ~/.local/share/patrimoine.

Un autre dossier peut être choisi (« le fichier vous appartient ») : `--choisir DOSSIER` y copie
les données, vérifiées, puis l'app s'en sert ; PATRIMOINE_DATA_DIR l'impose aussi. Ce choix est
écrit dans le dossier par défaut (dossier-choisi.txt), pas dans le code : il survit à une mise
à jour.

Ancien emplacement (data/ dans le dossier du code) : au premier lancement, ses données sont
COPIÉES puis vérifiées, jamais déplacées ni effacées. Il reste intact tant que l'utilisateur ne
le supprime pas lui-même. Aucune requête réseau ici, aucun montant affiché.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shutil
import sqlite3
import sys
from collections.abc import Iterator, Mapping
from contextlib import closing
from pathlib import Path

from app.config import APP_DIR, ROOT, SYNCED_FOLDERS, dotenv_value

ENV_NAME = "PATRIMOINE_DATA_DIR"
LEGACY_DIR = ROOT / "data"  # emplacement d'avant D-119, dans le dossier du code
POINTER_NAME = "dossier-choisi.txt"  # dans le dossier par défaut : chemin du dossier choisi
STAGING_NAME = ".copie-en-cours"
# Écrit avant de ranger la copie à sa place, effacé à la fin : tant qu'il existe, le dossier
# n'a qu'une copie inachevée (jamais prise pour des données) et la liste de ce qui y a été rangé.
MARKER_NAME = ".copie-inachevee.json"
LIVE_BASE = "patrimoine.db"  # base unique d'avant les profils (profiles.LEGACY_FILE)
REGISTRY = "profils.json"
PROFILES_SUBDIR = "profils"
LOCK_NAME = "lancement.lock"
# Jamais copiés : bases de démo (fictives, recréées par app.demo), verrous du lanceur, fichiers
# provisoires de l'app, journaux SQLite (l'API de sauvegarde en reprend le contenu) et dossier
# de la fenêtre (cache du moteur web et réglages de la page, recréés au lancement, D-125), journal
# de Patrimoine.exe (propre au dossier par défaut, D-126).
SKIPPED_NAMES = {
    "demo.db",
    "demo-grand.db",
    POINTER_NAME,
    STAGING_NAME,
    MARKER_NAME,
    "fenetre",
    "journal.txt",
    "journal-precedent.txt",
}
SKIPPED_PREFIXES = (LOCK_NAME, ".restauration-", ".export-", ".demo-")
SKIPPED_SUFFIXES = (
    ".lancement",
    ".lancement.port",
    ".fenetre",
    ".partiel",
    ".tmp",
    "-journal",
    "-wal",
    "-shm",
)


class DataDirError(RuntimeError):
    """Dossier refusé, introuvable ou copie impossible : message montré tel quel."""


# --- Où sont les données ----------------------------------------------------------------------


def default_dir(
    platform: str | None = None, env: Mapping[str, str] | None = None, home: Path | None = None
) -> Path:
    """Dossier de données de l'utilisateur selon le système (sans le créer)."""
    platform = platform or sys.platform
    env = os.environ if env is None else env
    home = home or Path.home()
    if platform == "win32":
        base = env.get("LOCALAPPDATA")
        return (Path(base) if base else home / "AppData" / "Local") / "Patrimoine"
    if platform == "darwin":
        return home / "Library" / "Application Support" / "Patrimoine"
    xdg = env.get("XDG_DATA_HOME", "")
    base = Path(xdg) if xdg and Path(xdg).is_absolute() else home / ".local" / "share"
    return base / "patrimoine"


def check_choice(path: Path, *, what: str = "Dossier des données") -> Path:
    """Refuse un dossier relatif, dans le code de l'app ou synchronisé vers un cloud (règle 2)."""
    if not path.is_absolute():
        raise DataDirError(f"{what} : un chemin complet est attendu, pas {str(path)!r}.")
    texts = f"{path} {path.resolve()}".lower()  # un lien vers OneDrive est vu aussi
    if cloud := next((n for n in SYNCED_FOLDERS if n in texts), None):
        raise DataDirError(
            f"{what} : {path} semble synchronisé ({cloud}), vos données partiraient dans le "
            "cloud. Choisir un dossier non synchronisé."
        )
    if path.resolve().is_relative_to(APP_DIR.resolve()):  # avec Patrimoine.exe, son dossier
        raise DataDirError(
            f"{what} : {path} est dans le dossier de l'app, qu'une mise à jour peut remplacer. "
            "Choisir un dossier à part."
        )
    return path


def resolve() -> tuple[Path, str]:
    """(dossier, origine) : PATRIMOINE_DATA_DIR (environnement puis .env), sinon le dossier
    choisi par `--choisir`, sinon le dossier par défaut. Rien n'est créé ici."""
    value = (os.environ.get(ENV_NAME) or dotenv_value(ENV_NAME) or "").strip()
    if value:
        return check_choice(Path(value).expanduser(), what=ENV_NAME), "PATRIMOINE_DATA_DIR"
    default = default_dir()
    pointer = default / POINTER_NAME
    if pointer.is_file():
        chosen = Path(pointer.read_text(encoding="utf-8").strip())
        return check_choice(chosen, what=f"Dossier choisi ({pointer})"), "choisi"
    return default, "par défaut"


def ensure(path: Path, origin: str) -> None:
    """Crée le dossier par défaut au besoin. Un dossier choisi doit déjà exister : un disque
    débranché ne doit jamais faire naître une base vide ailleurs sans prévenir."""
    if origin == "par défaut":
        path.mkdir(parents=True, exist_ok=True)
        if sys.platform != "win32":
            path.chmod(0o700)  # lisible par le seul utilisateur qui lance l'app
    elif not path.is_dir():
        raise DataDirError(
            f"Dossier des données introuvable : {path} (disque débranché ?). Rien n'a été créé."
        )


def has_data(folder: Path) -> bool:
    """Le dossier contient-il une base ou une liste de profils ? (fichiers de démo exclus)
    Une copie inachevée n'en est pas : elle est refaite, jamais utilisée."""
    if (folder / MARKER_NAME).exists():
        return False
    if (folder / LIVE_BASE).is_file() or (folder / REGISTRY).is_file():
        return True
    profiles = folder / PROFILES_SUBDIR
    return profiles.is_dir() and any(profiles.glob("*.db"))


# --- Copie vérifiée ---------------------------------------------------------------------------


def _skipped(name: str) -> bool:
    return (
        name in SKIPPED_NAMES
        or name.startswith(SKIPPED_PREFIXES)
        or name.endswith(SKIPPED_SUFFIXES)
    )


def _files(source: Path) -> Iterator[Path]:
    """Fichiers à copier, en chemins relatifs, dans un ordre stable."""
    for path in sorted(source.rglob("*")):
        rel = path.relative_to(source)
        if path.is_file() and not any(_skipped(part) for part in rel.parts):
            yield rel


def _is_live_base(rel: Path) -> bool:
    """Base en service (base unique, base d'un profil) : copiée par l'API de sauvegarde de
    SQLite, cohérente même si un journal traîne à côté. Les sauvegardes et la corbeille sont
    des fichiers fermés : copiés à l'octet près."""
    return rel == Path(LIVE_BASE) or (rel.parent == Path(PROFILES_SUBDIR) and rel.suffix == ".db")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _content(path: Path) -> tuple[int, dict[str, int]]:
    """Version du schéma et nombre de lignes de chaque table (aucun montant lu)."""
    with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            )
        ]
        counts = {t: conn.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in tables}
    return version, counts


def _copy_base(source: Path, target: Path) -> None:
    src = sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    with closing(sqlite3.connect(f"{target.resolve().as_uri()}?mode=ro", uri=True)) as conn:
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise DataDirError(f"Copie abîmée : {target.name}")
    if _content(source) != _content(target):
        raise DataDirError(f"Copie incomplète : {target.name} n'a pas le même contenu.")


def _undo_partial(target: Path) -> None:
    """Retire une copie inachevée laissée par une coupure : seulement ce que la copie avait
    elle-même rangé (liste du marqueur), le dossier provisoire, puis le marqueur. Ces éléments
    sont des copies : l'original n'a jamais été touché."""
    marker = target / MARKER_NAME
    if marker.is_file():
        names = json.loads(marker.read_text(encoding="utf-8"))
        for name in names:
            if not isinstance(name, str) or Path(name).name != name or name in ("", ".", ".."):
                raise DataDirError(f"{marker} illisible : rien n'est retiré.")
            path = target / name
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            elif path.exists() or path.is_symlink():
                path.unlink()
    staging = target / STAGING_NAME
    if staging.exists():
        shutil.rmtree(staging)
    marker.unlink(missing_ok=True)


def copy_data(source: Path, target: Path) -> int:
    """Copie toutes les données de `source` dans `target`, vérifiées une à une, sans jamais
    rien modifier dans `source` ni écraser un fichier de `target`. Renvoie le nombre de
    fichiers copiés.

    Tout passe d'abord par un sous-dossier provisoire de `target` : une copie interrompue ne
    laisse jamais de données à moitié copiées que l'app prendrait pour les bonnes. Une fois
    tout vérifié, les éléments sont renommés à leur place (même disque : instantané), la liste
    des profils et la base unique en dernier. Le marqueur MARKER_NAME couvre ce rangement :
    coupé au milieu, il est défait au début de la tentative suivante."""
    if (target / MARKER_NAME).exists():
        _undo_partial(target)
    if has_data(target):
        raise DataDirError(f"{target} contient déjà des données : rien n'est écrasé.")
    files = list(_files(source))
    tops = sorted({rel.parts[0] for rel in files})
    if taken := [name for name in tops if (target / name).exists()]:
        raise DataDirError(f"{target} contient déjà {', '.join(taken)} : rien n'est écrasé.")
    staging = target / STAGING_NAME
    if staging.exists():  # reste d'une copie interrompue : jamais pris pour des données
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    marker = target / MARKER_NAME
    try:
        for rel in files:
            dest = staging / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if _is_live_base(rel):
                _copy_base(source / rel, dest)
            else:
                shutil.copy2(source / rel, dest)
                if _sha256(source / rel) != _sha256(dest):
                    raise DataDirError(f"Copie différente de l'original : {rel}")
        marker.write_text(json.dumps(tops), encoding="utf-8")
        last = (REGISTRY, LIVE_BASE)
        for name in sorted(tops, key=lambda n: last.index(n) + 1 if n in last else 0):
            os.replace(staging / name, target / name)
        staging.rmdir()
        marker.unlink()  # dernier geste : la copie est complète
    except (OSError, sqlite3.Error, DataDirError):
        try:
            _undo_partial(target)  # nos copies seulement, jamais l'original
        except (OSError, ValueError, DataDirError):
            pass  # marqueur laissé : défait au prochain essai, le dossier reste « sans données »
        raise
    return len(files)


def _legacy_running(legacy: Path) -> bool:
    """Une app d'avant D-119 tourne-t-elle encore sur l'ancien dossier ? (verrou D-118)"""
    from app.launcher import InstanceLock

    path = legacy / LOCK_NAME
    if not path.is_file():  # pas de verrou : rien à tester, et rien n'est créé là-bas
        return False
    lock = InstanceLock(path)
    if not lock.acquire():
        return True
    lock.release(forget_port=False)  # rien n'est retiré de l'ancien dossier
    return False


def adopt_legacy(target: Path, legacy: Path | None = None) -> list[str]:
    """Premier lancement après la mise à jour : copie data/ (dossier du code) vers `target`.

    Rien n'est fait si `target` a déjà des données : l'ancien dossier est alors seulement
    signalé tant qu'il en contient. L'ancien dossier n'est jamais modifié ni supprimé.
    DataDirError si la copie échoue : l'app ne doit pas s'ouvrir vide à côté des données."""
    legacy = LEGACY_DIR if legacy is None else legacy
    if (target / MARKER_NAME).exists() and not has_data(legacy):
        raise DataDirError(
            f"Copie inachevée dans {target} et plus rien à recopier : rien n'est ouvert."
        )
    if legacy.resolve() == target.resolve() or not has_data(legacy):
        return []
    if has_data(target):
        return [
            f"Ancien dossier {legacy} : il contient encore une copie de vos données, que l'app "
            "n'utilise plus. Vous pouvez le supprimer vous-même une fois vérifié."
        ]
    if _legacy_running(legacy):
        raise DataDirError(
            "Une ancienne version de Patrimoine tourne encore : la fermer, puis relancer."
        )
    count = copy_data(legacy, target)
    return [
        f"Données copiées et vérifiées : {count} fichier(s) de {legacy} vers {target}.",
        f"L'ancien dossier {legacy} n'a pas été touché : le supprimer vous-même une fois vérifié.",
    ]


def prepare(path: Path, origin: str) -> list[str]:
    """Avant une commande (migration, sauvegarde) : dossier prêt et ancien data/ recopié, pour
    ne jamais travailler sur un dossier neuf et vide alors que les données sont restées
    derrière. Sans effet si PATRIMOINE_DB force une base."""
    if os.environ.get("PATRIMOINE_DB"):
        return []
    ensure(path, origin)
    return adopt_legacy(path)


def choose(new: Path, current: Path) -> list[str]:
    """`--choisir` : copie les données vers `new`, puis l'app s'en sert. `current` reste intact."""
    if os.environ.get(ENV_NAME) or dotenv_value(ENV_NAME):
        raise DataDirError(f"{ENV_NAME} impose le dossier : le changer là (variable ou .env).")
    default = default_dir()
    new = new.expanduser()
    if new.resolve() != default.resolve():
        check_choice(new)
    if new.resolve() == current.resolve():
        return [f"Les données sont déjà dans {new}."]
    if not new.parent.is_dir():
        raise DataDirError(f"Dossier parent introuvable : {new.parent} (disque débranché ?)")
    from app.launcher import InstanceLock

    lock = InstanceLock(current / LOCK_NAME)
    if not lock.acquire():
        raise DataDirError("Patrimoine est lancé : le fermer avant de changer de dossier.")
    try:
        new.mkdir(exist_ok=True)
        count = copy_data(current, new) if has_data(current) else 0
        default.mkdir(parents=True, exist_ok=True)
        pointer = default / POINTER_NAME
        if new.resolve() == default.resolve():
            pointer.unlink(missing_ok=True)
        else:
            tmp = pointer.with_name(f"{POINTER_NAME}.{secrets.token_hex(4)}.tmp")
            tmp.write_text(str(new.resolve()), encoding="utf-8")
            os.replace(tmp, pointer)
    finally:
        lock.release()
    return [
        f"Dossier des données : {new} ({count} fichier(s) copiés et vérifiés).",
        f"L'ancien dossier {current} n'a pas été touché : le supprimer vous-même une fois vérifié.",
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.datadir", description=__doc__)
    parser.add_argument(
        "--choisir", metavar="DOSSIER", type=Path, help="copier les données dans ce dossier"
    )
    args = parser.parse_args(argv)
    try:
        current, origin = resolve()
        if args.choisir:
            print("\n".join(choose(args.choisir, current)))
            return 0
    except DataDirError as err:
        print(err)
        return 1
    print(f"Dossier des données ({origin}) : {current}")
    if not has_data(current):
        print("  aucune base pour l'instant")
    if has_data(LEGACY_DIR) and LEGACY_DIR.resolve() != current.resolve():  # rappel
        print(f"Ancien dossier encore présent : {LEGACY_DIR} (à supprimer vous-même, vérifié).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
