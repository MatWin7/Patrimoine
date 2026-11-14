"""Accès SQLite : chemin de la base, connexion, création du schéma, conversions Decimal."""

import os
import sqlite3
from contextlib import closing
from contextvars import ContextVar
from decimal import Decimal
from pathlib import Path

from app.config import APP_DIR

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
SCHEMA_VERSION = 21  # doit correspondre au PRAGMA user_version de schema.sql
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

# Base du profil de la requête en cours (D-098), posée par le middleware de app.main. Une
# ContextVar est propre à chaque requête : deux onglets sur deux profils ne se mélangent pas.
current_profile_db: ContextVar[Path | None] = ContextVar("current_profile_db", default=None)


class NoProfileSelected(RuntimeError):
    """Profils actifs mais aucun choisi : ne jamais retomber en silence sur une autre base."""


def db_path() -> Path:
    """Base à utiliser : PATRIMOINE_DB (ex. data/demo.db) si définie, sinon celle du profil de
    la requête, sinon data/patrimoine.db quand les profils ne sont pas activés."""
    value = os.environ.get("PATRIMOINE_DB")
    if value:
        path = Path(value)
        return path if path.is_absolute() else APP_DIR / path  # Patrimoine.exe : son dossier
    if (chosen := current_profile_db.get()) is not None:
        return chosen
    from app import profiles  # import ici : profiles importe db

    if profiles.enabled():
        raise NoProfileSelected("Aucun profil choisi pour cette requête.")
    return profiles.DATA_DIR / profiles.LEGACY_FILE


def conn_key(conn: sqlite3.Connection) -> str:
    """Fichier de la base d'une connexion : clé des mémoires de processus (prix de la veille,
    historiques déjà complétés...) pour que deux profils ne partagent pas ces repères (D-098)."""
    return conn.execute("PRAGMA database_list").fetchone()[2] or ":memory:"


def connect(path: Path | None = None) -> sqlite3.Connection:
    """Ouvre une connexion avec les clés étrangères actives (SQLite les désactive par défaut)."""
    conn = sqlite3.connect(path or db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def describe(path: Path | None = None) -> str:
    """Base utilisée et nombre d'opérations, affichés au lancement (D-039).

    Une faute dans PATRIMOINE_DB créerait sinon une base vide sans prévenir. Seul un
    nombre d'opérations est affiché, jamais un montant.
    """
    path = path or db_path()
    if not path.is_file():
        return f"Base utilisée : {path} (absente : elle va être créée vide)"
    with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as conn:
        try:
            count = conn.execute("SELECT count(*) FROM operation").fetchone()[0]
        except sqlite3.DatabaseError:  # base sans table operation, ou fichier qui n'en est pas une
            return f"Base utilisée : {path} (illisible ou sans table des opérations)"
    return f"Base utilisée : {path} ({count} opération{'s' if count > 1 else ''})"


def schema_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def init_db(path: Path | None = None) -> int:
    """Crée la base depuis schema.sql si elle est vide ; renvoie la version du schéma.

    Ne migre pas : le lanceur l'a fait juste avant, sauvegarde comprise (app.migrate, D-120).
    Une base restée plus ancienne ou plus récente que le code provoque une erreur.
    """
    path = path or db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as conn:
        version = schema_version(conn)
        if version == 0:
            conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
            version = schema_version(conn)
    conn.close()
    if version != SCHEMA_VERSION:
        raise RuntimeError(
            f"Base {path} en version {version}, le code attend {SCHEMA_VERSION} : "
            "relancer Patrimoine ou `uv run python -m app.migrate` (sauvegarde puis mise à jour)."
        )
    return version


def pending_migrations(version: int) -> list[Path]:
    """Scripts NNN_nom.sql à appliquer pour passer de `version` à SCHEMA_VERSION, dans l'ordre."""
    scripts = sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql"))
    return [s for s in scripts if version < int(s.name[:3]) <= SCHEMA_VERSION]


def migrate(path: Path | None = None) -> tuple[int, int]:
    """Applique les migrations en attente ; renvoie (ancienne version, nouvelle version).

    Chaque script tourne dans une transaction : une erreur annule tout le script. La sauvegarde
    préalable est faite par app.migrate, pas ici.
    """
    path = path or db_path()
    conn = connect(path)
    try:
        before = schema_version(conn)
        if before == 0:
            raise RuntimeError(f"Base {path} vide : elle sera créée au lancement de l'app.")
        if before > SCHEMA_VERSION:
            raise RuntimeError(f"Base {path} en version {before}, plus récente que le code.")
        # Clés étrangères coupées pendant les migrations : recréer une table référencée (pour
        # changer une contrainte CHECK) effacerait sinon en cascade les lignes qui la citent.
        # Elles sont vérifiées avant de valider chaque script (procédure conseillée par SQLite).
        conn.execute("PRAGMA foreign_keys = OFF")
        for script in pending_migrations(before):
            conn.executescript(f"BEGIN;\n{script.read_text(encoding='utf-8')}")
            if conn.execute("PRAGMA foreign_key_check").fetchall():
                raise RuntimeError(f"Migration {script.name} : références incohérentes, annulée.")
            conn.commit()
        return before, schema_version(conn)
    except (sqlite3.Error, RuntimeError):
        if conn.in_transaction:
            conn.rollback()
        raise
    finally:
        conn.close()


def to_db(value: Decimal | None) -> str | None:
    """Decimal -> TEXT. Refuse les float pour ne jamais perdre en précision (D-002)."""
    if value is None:
        return None
    if not isinstance(value, Decimal):
        raise TypeError(f"Decimal attendu, reçu {type(value).__name__}")
    return str(value)


def from_db(value: str | None) -> Decimal | None:
    """TEXT -> Decimal."""
    return None if value is None else Decimal(value)
