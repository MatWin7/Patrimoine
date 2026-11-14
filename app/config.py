"""Réglages lus dans les variables d'environnement, complétées par le fichier .env local."""

import os
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # code de l'app : web/, schema.sql, CHANGELOG.md
# Patrimoine.exe (PyInstaller, D-126) : le code est rangé dans le sous-dossier _internal ; le
# dossier que l'utilisateur voit, et où il poserait un .env, est celui de l'exécutable.
FROZEN = bool(getattr(sys, "frozen", False))
APP_DIR = Path(sys.executable).resolve().parent if FROZEN else ROOT
# Dossiers synchronisés vers un cloud, refusés pour les données et BACKUP_COPY_DIR (règle 2,
# D-114, D-119). Filet de sécurité : un nom inconnu passe.
SYNCED_FOLDERS = (
    "onedrive",
    "dropbox",
    "google drive",
    "googledrive",
    "icloud",
    "pcloud",
    "mobile documents",  # iCloud Drive sous macOS (~/Library/Mobile Documents)
    "clouddocs",
    "cloudstorage",  # ~/Library/CloudStorage : Box, Google Drive, OneDrive... sous macOS
    "nextcloud",
    "proton drive",
    "megasync",
    "mega drive",
    "synology drive",
    "seafile",
)


def _dotenv_lines(path: Path) -> list[tuple[str, str]]:
    """Paires NOM=valeur de .env (valeurs vides ignorées)."""
    if not path.is_file():
        return []
    pairs = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        value = value.strip().strip("\"'")
        if value:  # une ligne vide dans .env (ex. PATRIMOINE_DB=) garde le défaut
            pairs.append((name.strip(), value))
    return pairs


def dotenv_value(name: str, path: Path = APP_DIR / ".env") -> str | None:
    """Une valeur de .env sans toucher à l'environnement : lue avant load_dotenv (D-119)."""
    return next((value for key, value in _dotenv_lines(path) if key == name), None)


def load_dotenv(path: Path = APP_DIR / ".env") -> None:
    """Charge les lignes NOM=valeur de .env, sans écraser une variable déjà définie.

    Version minimale (pas de guillemets multilignes ni de substitution) : suffisante pour
    les trois réglages de .env.example, sans ajouter de dépendance.
    """
    for name, value in _dotenv_lines(path):
        os.environ.setdefault(name, value)


def price_ttl() -> timedelta:
    """Durée pendant laquelle un prix en cache est réutilisé sans rappeler la source (D-007) :
    .env, sinon carte Réglages (D-130), sinon 5 min."""
    from app import settings  # import ici : settings importe profiles, qui importe config

    return timedelta(minutes=settings.number("price_ttl_minutes"))


def backup_keep() -> int:
    """Nombre de sauvegardes automatiques gardées (une par jour de lancement)."""
    from app import settings

    return settings.number("backup_keep")


def backup_copy_dir() -> Path | None:
    """Dossier où recopier les sauvegardes du jour (D-114), None si non réglé.

    Refusé s'il est relatif, dans le dossier de l'app ou des données (une copie sur le même
    disque virtuel ne protège de rien) ou synchronisé vers un cloud, OneDrive par exemple
    (règle 2) ; choisi dans la carte Réglages, aussi s'il est sur le réseau (D-130).
    """
    from app import settings

    return settings.copy_dir()


def coingecko_api_key() -> str | None:
    from app import settings

    return settings.coingecko_key()
