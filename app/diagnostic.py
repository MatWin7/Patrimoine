"""Diagnostic à copier dans un signalement (D-132) : de quoi comprendre un problème, sans rien
qui décrive le patrimoine ni la personne.

Le texte est fait pour être collé dans une issue publique sur GitHub. Il contient la version,
le système, le mode d'affichage, l'état des bases (version du schéma), des sauvegardes, des
réglages et des services joints, et les dernières erreurs inattendues du serveur. Il ne
contient jamais : montant, quantité, prix, nom de compte, d'actif, de profil ou de fichier
choisi, ticker, identifiant de profil, chemin (le nom d'utilisateur y figure souvent), adresse
de portefeuille, ni le message d'une erreur (il peut citer une valeur ou un chemin). Les tailles
de base sont données par tranche (« 100 à 999 opérations »).

Rien ne part d'ici : le texte est montré à l'utilisateur, qui le copie et le colle lui-même.
Les erreurs relevées restent en mémoire, jamais dans un fichier.

`uv run python -m app.diagnostic` affiche la partie qui ne demande pas l'app lancée (version,
système, bases, réglages) : utile quand l'app ne démarre pas.
"""

from __future__ import annotations

import errno
import os
import platform
import re
import shutil
import sqlite3
import sys
import threading
import traceback
from collections import deque
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from importlib import metadata
from pathlib import Path

from app import backup, config, db, outbound, profiles, settings, updates
from app.version import __version__

APP_ROOT = Path(__file__).resolve().parent  # dossier app/ : seules ses lignes sont citées
MAX_ERRORS = 10  # gardées en mémoire ; le diagnostic montre les SHOWN_ERRORS dernières
SHOWN_ERRORS = 5
MAX_FRAMES = 4
MAX_SQLITE_MESSAGE = 120
STARTED = datetime.now()
# Choix du modèle de signalement (problème, idée, faille en privé) : un lien, jamais une requête.
REPORT_URL = f"https://github.com/{updates.REPO}/issues/new/choose"
# Messages fixes d'UpdateError (app/updates.py) ; « GitHub a répondu NNN » est reconnu à part.
UPDATE_ERRORS = {
    "trop de redirections",
    "GitHub est injoignable",
    "réponse de GitHub inattendue",
    "numéro de version inattendu",
    "réponse de GitHub trop grande",
    "redirection de GitHub sans adresse",
    "redirection de GitHub refusée",
}


@dataclass(frozen=True)
class ErrorSeen:
    at: datetime
    where: str  # « GET /api/portfolio » (modèle de route, jamais l'adresse réelle) ou un rôle
    kind: str  # type de l'erreur, avec le message de SQLite ou le code errno quand il y en a
    frames: tuple[str, ...]  # « app/portfolio.py:212 (ratio) », de la plus profonde à la moins


_lock = threading.Lock()
_errors: deque[ErrorSeen] = deque(maxlen=MAX_ERRORS)
_error_count = 0


# --- Erreurs inattendues ----------------------------------------------------------------------


def _frame_label(frame: traceback.FrameSummary) -> str | None:
    """Ligne de code de l'app (« app/db.py:88 (connect) ») ; None hors de l'app : le chemin
    d'une bibliothèque contient le dossier de l'utilisateur."""
    try:
        relative = Path(frame.filename).resolve().relative_to(APP_ROOT)
    except ValueError:
        return None
    return f"app/{relative.as_posix()}:{frame.lineno} ({frame.name})"


def _library(frames: traceback.StackSummary) -> str | None:
    """Bibliothèque où l'erreur est née, sans chemin : « sqlite3 », « httpx2 »..."""
    if not frames:
        return None
    parts = Path(frames[-1].filename).parts
    if "site-packages" in parts:
        rest = parts[parts.index("site-packages") + 1 :]
        return rest[0].removesuffix(".py") if rest else None
    return Path(frames[-1].filename).stem or None


def describe_error(exc: BaseException) -> tuple[str, tuple[str, ...]]:
    """(type, lignes de l'app) d'une erreur. Le message n'est gardé que pour SQLite, dont les
    messages ne citent que des tables et des colonnes (« database is locked ») ; pour une erreur
    du système, seul le code errno (EACCES) : son message contient le chemin du fichier."""
    kind = type(exc).__name__
    if isinstance(exc, sqlite3.Error):
        text = " ".join(str(exc).split())[:MAX_SQLITE_MESSAGE]
        kind = f"{kind} : {text}" if text else kind
    elif isinstance(exc, OSError) and exc.errno in errno.errorcode:
        kind = f"{kind} ({errno.errorcode[exc.errno]})"
    frames = traceback.extract_tb(exc.__traceback__)
    labels = [label for frame in frames if (label := _frame_label(frame))]
    inside = tuple(reversed(labels))[:MAX_FRAMES]
    if (lib := _library(frames)) and (not frames or _frame_label(frames[-1]) is None):
        inside = (f"dans {lib}", *inside)[:MAX_FRAMES]
    return kind, inside


def record(exc: BaseException, where: str) -> None:
    """Retient une erreur inattendue (500 du serveur, fenêtre, sauvegarde) pour le diagnostic."""
    global _error_count
    kind, frames = describe_error(exc)
    with _lock:
        _errors.append(ErrorSeen(datetime.now(), where, kind, frames))
        _error_count += 1


def errors() -> tuple[int, list[ErrorSeen]]:
    with _lock:
        return _error_count, list(_errors)


def reset() -> None:
    """Oublie les erreurs relevées (tests)."""
    global _error_count
    with _lock:
        _errors.clear()
        _error_count = 0


# --- Ce qui est relevé ------------------------------------------------------------------------


def size_range(count: int) -> str:
    """Taille par tranche : assez pour un problème de lenteur, trop vague pour décrire un
    patrimoine."""
    if count <= 0:
        return "0"
    low = 10 ** (len(str(count)) - 1)
    if low >= 10_000:
        return "10 000 et plus"
    if low == 1:
        return "1 à 9"
    return f"{low:,} à {low * 10 - 1:,}".replace(",", " ")


def system_line() -> str:
    machine = platform.machine() or "?"
    if sys.platform == "win32":
        release, build, *_ = platform.win32_ver()
        name = f"Windows {release} ({build})"
    elif sys.platform == "darwin":
        name = f"macOS {platform.mac_ver()[0]}"
    else:
        try:
            name = platform.freedesktop_os_release().get("PRETTY_NAME", "Linux")
        except OSError:
            name = "Linux"
        from app.launcher import is_wsl

        name += f", noyau {platform.release()}" + (" (WSL)" if is_wsl() else "")
    return f"{name}, {machine} · Python {platform.python_version()}"


def _package(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def display_line() -> str:
    from app import window

    if window.info()["window"]:
        engine = _package("pywebview")
        return f"fenêtre de l'app (pywebview {engine})" if engine else "fenêtre de l'app"
    from app.launcher import is_wsl

    reason = window.unavailable_reason(wsl=is_wsl())
    if reason is None:
        return "navigateur (fenêtre possible, non ouverte)"
    return "navigateur (" + reason.split(" : ")[0] + ")"


def data_line() -> str:
    if os.environ.get("PATRIMOINE_DB"):
        origin, folder = "base forcée (PATRIMOINE_DB)", db.db_path().parent
    else:
        origin, folder = f"dossier {profiles.DATA_ORIGIN}", profiles.DATA_DIR
    try:
        free = shutil.disk_usage(folder).free // 1_000_000_000
    except OSError:
        return f"{origin}, dossier introuvable"
    return f"{origin}, {free} Go libres sur son disque"


def _read_version(path: Path) -> str:
    if not path.is_file():
        return "absente"
    try:
        with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as conn:
            return f"v{db.schema_version(conn)}"
    except sqlite3.Error:
        return "illisible"


def bases_line(unready: int = 0) -> str:
    forced = bool(os.environ.get("PATRIMOINE_DB"))
    if forced:
        paths = [db.db_path()]
    elif profiles.enabled():
        paths = profiles.databases()
    else:
        paths = [profiles.DATA_DIR / profiles.LEGACY_FILE]
    versions = [_read_version(p) for p in paths]
    summary = ", ".join(f"{n} × {v}" for v, n in _counted(versions)) or "aucune"
    kind = f"{len(paths)} profil(s)" if profiles.enabled() and not forced else "base unique"
    line = f"{kind} ; schémas : {summary} (attendu v{db.SCHEMA_VERSION})"
    if unready:
        line += f" ; {unready} profil(s) non ouvrable(s)"
    return line


def _counted(values: list[str]) -> list[tuple[str, int]]:
    seen: dict[str, int] = {}
    for value in values:
        seen[value] = seen.get(value, 0) + 1
    return sorted(seen.items())


def open_base_line(path: Path | None) -> str | None:
    """Taille de la base ouverte, par tranches. None : aucun profil ouvert."""
    if path is None or not path.is_file():
        return None
    try:
        with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as conn:
            sizes = [
                (label, conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
                for table, label in (
                    ("account", "comptes"),
                    ("asset", "actifs"),
                    ("operation", "opérations"),
                )
            ]
    except sqlite3.Error as err:
        return f"illisible ({type(err).__name__})"
    return ", ".join(f"{label} {size_range(n)}" for label, n in sizes)


def backups_line(stem: str | None) -> str:
    last = backup.last_auto_day(stem) if stem else None
    try:
        copy = "réglé" if settings.copy_dir() else "non réglé"
    except RuntimeError:
        copy = "réglé, en erreur"
    if stem is None:
        return f"aucun profil ouvert ; autre disque : {copy}"
    return f"dernière du profil ouvert : {last or 'aucune'} ; autre disque : {copy}"


def settings_line() -> str:
    def value(name: str, unit: str) -> str:
        spec = settings.NUMBERS[name]
        try:
            shown = f"{settings.number(name)}{unit}"
        except RuntimeError:
            shown = "illisible"
        return shown + (" (.env)" if settings.source(spec.env) else "")

    key = "oui" if settings.coingecko_key() else "non"
    env = "oui" if (config.APP_DIR / ".env").is_file() else "non"
    return (
        f"cache des prix {value('price_ttl_minutes', ' min')}, sauvegardes gardées "
        f"{value('backup_keep', '')}, verrouillage {value('profile_lock_minutes', ' min')}, "
        f"clé CoinGecko {key}, fichier .env {env}"
    )


def updates_line() -> str:
    state = updates.load()
    choice = {True: "activé", False: "coupé", None: "pas encore choisi"}[state.enabled]
    line = f"avis {choice}"
    if state.checked:
        line += f", vérifié le {state.checked}"
    if state.latest:
        line += f", dernière version vue {state.latest}"
    if state.error:  # relu dans reglages.json : seulement un message connu d'app.updates
        known = state.error in UPDATE_ERRORS or re.fullmatch(r"GitHub a répondu \d{3}", state.error)
        line += f", erreur : {state.error if known else 'message non reconnu'}"
    return line


def services_lines() -> list[str]:
    seen = outbound.health()
    lines = []
    for service in seen["services"]:
        parts = [f"{service['count']} requête(s)"]
        if service["failed"]:
            codes = ", ".join(f"HTTP {code} × {n}" for code, n in service["failed"].items())
            parts.append(f"en erreur : {codes}")
        if service["unanswered"]:
            parts.append(f"{service['unanswered']} sans réponse")
        lines.append(f"  {service['name']} : " + " ; ".join(parts))
    if not lines:
        lines.append("  aucune requête")
    if seen["refused"]:
        lines.append(f"  bloquées (adresse non déclarée) : {seen['refused']}")
    return lines


def errors_lines() -> list[str]:
    total, seen = errors()
    if not total:
        return ["  aucune"]
    lines = []
    for error in seen[-SHOWN_ERRORS:]:
        lines.append(f"  {error.at:%H:%M} {error.where} : {error.kind}")
        lines.extend(f"      {frame}" for frame in error.frames)
    if total > SHOWN_ERRORS:
        lines.append(f"  ({total} en tout depuis le lancement, les {SHOWN_ERRORS} dernières ici)")
    return lines


def _uptime(now: datetime) -> str:
    minutes = int((now - STARTED).total_seconds()) // 60
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d}" if hours else f"{minutes} min"


def report(open_db: Path | None = None, *, running: bool = True, unready: int = 0) -> str:
    """Texte du diagnostic. `open_db` : base du profil ouvert (None : aucun) ; `running` faux
    pour la commande, lancée hors de l'app : ni compteurs ni erreurs, qui vivent dans l'app."""
    now = datetime.now()
    build = "Patrimoine.exe" if config.FROZEN else "depuis le code"
    lines = [
        f"Patrimoine {__version__} ({build})",
        f"Relevé : {now:%Y-%m-%d %H:%M}"
        + (f", app lancée depuis {_uptime(now)}" if running else ", hors de l'app"),
        f"Système : {system_line()}",
    ]
    if running:
        lines.append(f"Affichage : {display_line()}")
    lines += [
        f"Données : {data_line()}",
        f"Bases : {bases_line(unready)}",
    ]
    if (size := open_base_line(open_db)) is not None:
        lines.append(f"Profil ouvert : {size}")
    lines += [
        f"Sauvegardes : {backups_line(open_db.stem if open_db else None)}",
        f"Réglages : {settings_line()}",
        f"Mises à jour : {updates_line()}",
    ]
    if running:
        lines += ["Services joints depuis le lancement :", *services_lines()]
        lines += ["Erreurs inattendues :", *errors_lines()]
    return "\n".join(lines)


def main() -> None:
    config.load_dotenv()
    forced = db.db_path() if os.environ.get("PATRIMOINE_DB") else None
    print(report(forced, running=False))


if __name__ == "__main__":
    main()
