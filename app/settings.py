"""Carte Réglages (D-130) : ce que .env réglait, modifiable depuis la page.

Les valeurs sont rangées dans `reglages.json`, dans le dossier des données, sous la clé
`reglages` (la clé `updates` y garde l'avis de mise à jour, D-129). Elles valent pour la
machine : tous les profils les partagent. Ordre de lecture : variable d'environnement (ou
ligne de .env, chargée dans l'environnement au lancement), puis reglages.json, puis la valeur
par défaut. Un réglage fixé par .env garde donc la main : la page le montre sans le modifier.

Deux réglages demandent plus de soin, parce qu'une requête de l'API peut les changer :
- la clé CoinGecko est un secret : elle est écrite, jamais renvoyée à la page ;
- le dossier de copie des sauvegardes reçoit toutes les bases : choisi depuis la page, il doit
  déjà exister et être sur un disque de la machine (pas de partage réseau, règle 2).
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from app import profiles
from app.config import APP_DIR, SYNCED_FOLDERS, dotenv_value

FILE_NAME = "reglages.json"
SECTION = "reglages"


@dataclass(frozen=True)
class Number:
    """Réglage en minutes ou en nombre de copies, borné."""

    env: str
    default: int
    low: int
    high: int


NUMBERS = {
    "price_ttl_minutes": Number("PRICE_TTL_MINUTES", 5, 1, 1440),
    "backup_keep": Number("BACKUP_KEEP", 14, 1, 365),
    "profile_lock_minutes": Number("PROFILE_LOCK_MINUTES", 30, 1, 720),
}
COPY_DIR_ENV = "BACKUP_COPY_DIR"
COINGECKO_ENV = "COINGECKO_API_KEY"
# Clé de l'offre « demo » de CoinGecko (« CG-… ») : lettres, chiffres, - et _ seulement. Elle
# finit dans un en-tête HTTP : aucun espace ni retour à la ligne ne doit pouvoir y entrer.
COINGECKO_KEY = re.compile(r"[A-Za-z0-9_-]{8,128}")
MAX_PATH = 400

# Systèmes de fichiers distants ou synchronisés (Linux, WSL) : une copie y quitterait la machine.
NETWORK_FILESYSTEMS = {
    "nfs",
    "nfs4",
    "cifs",
    "smb3",
    "smbfs",
    "ncpfs",
    "afs",
    "ceph",
    "glusterfs",
    "davfs",
    "fuse.davfs2",
    "fuse.sshfs",
    "sshfs",
    "fuse.rclone",
    "fuse.s3fs",
    "fuse.gcsfuse",
    "fuse.onedriver",
}
MAC_NETWORK_FILESYSTEMS = {"smbfs", "afpfs", "nfs", "webdav", "ftp", "cifs"}
MAC_MOUNT = re.compile(r"(?P<src>.+?) on (?P<point>/.*?) \((?P<kind>[^,)]+)[,)].*")
DRIVE_REMOTE = 4  # GetDriveTypeW : lecteur réseau (Windows)

# Un seul verrou pour tout reglages.json : l'avis de mise à jour et la carte Réglages y écrivent
# chacun leur clé ; sans verrou commun, l'un pourrait effacer ce que l'autre vient d'écrire.
file_lock = threading.RLock()


class SettingsError(ValueError):
    """Valeur refusée, avec un message à afficher tel quel."""


def _file() -> Path:
    return profiles.DATA_DIR / FILE_NAME


def read_file() -> dict:
    """Tout reglages.json ; fichier absent ou abîmé : {} (valeurs par défaut)."""
    try:
        data = json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_section(name: str, value: dict) -> None:
    """Remplace une clé de reglages.json sans toucher aux autres, en un seul bloc."""
    with file_lock:
        data = read_file()
        data[name] = value
        path = _file()
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + ".tmp")
        temp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        temp.replace(path)  # jamais un fichier à moitié écrit


def _stored() -> dict:
    section = read_file().get(SECTION)
    return section if isinstance(section, dict) else {}


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def source(env_name: str) -> str | None:
    """« .env » ou « environnement » si le réglage est fixé hors de la page, sinon None."""
    if not _env(env_name):
        return None
    return ".env" if dotenv_value(env_name) == os.environ[env_name] else "environnement"


# --- Nombres -----------------------------------------------------------------------------------


def number(name: str) -> int:
    """Valeur d'un réglage numérique. Une valeur de .env illisible arrête l'app comme avant
    (RuntimeError) ; une valeur abîmée de reglages.json est ignorée (défaut)."""
    spec = NUMBERS[name]
    if value := _env(spec.env):
        try:
            return max(int(value), 1)
        except ValueError as err:
            raise RuntimeError(f"{spec.env} doit être un nombre entier, pas {value!r}") from err
    stored = _stored().get(name)
    if type(stored) is int and spec.low <= stored <= spec.high:
        return stored
    return spec.default


def check_number(name: str, value: object) -> int | None:
    """Valeur envoyée par la page ; None remet la valeur par défaut."""
    spec = NUMBERS[name]
    if value is None:
        return None
    if type(value) is not int or not spec.low <= value <= spec.high:
        raise SettingsError(f"Valeur attendue : un nombre entier de {spec.low} à {spec.high}.")
    return value


# --- Clé CoinGecko -----------------------------------------------------------------------------


def coingecko_key() -> str | None:
    if value := _env(COINGECKO_ENV):
        return value
    stored = _stored().get("coingecko_api_key")
    return stored if isinstance(stored, str) and COINGECKO_KEY.fullmatch(stored) else None


def check_coingecko_key(value: object) -> str | None:
    """Clé saisie ; "" la retire. Le message d'erreur ne reprend jamais la valeur reçue."""
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not COINGECKO_KEY.fullmatch(value.strip()):
        raise SettingsError("Clé CoinGecko refusée : lettres, chiffres, - et _ seulement.")
    return value.strip()


# --- Dossier de copie des sauvegardes ----------------------------------------------------------


def copy_dir() -> Path | None:
    """Dossier où recopier les sauvegardes (D-114), None si non réglé. RuntimeError si le
    dossier n'est plus acceptable (le message est affiché par la sauvegarde)."""
    if value := _env(COPY_DIR_ENV):
        return check_copy_dir(value, strict=False)
    stored = _stored().get("backup_copy_dir")
    if not isinstance(stored, str) or not stored:
        return None
    return check_copy_dir(stored, strict=True, must_exist=False)


def check_copy_dir(value: str, *, strict: bool = True, must_exist: bool = True) -> Path:
    """Règles du dossier de copie. Celles de .env (D-114) : chemin complet, ni synchronisé, ni
    dans le dossier de l'app ou des données. Depuis la page (`strict`), en plus : pas de partage
    réseau, et dossier déjà existant au moment du choix (`must_exist`), pour qu'une requête ne
    puisse pas créer de dossier n'importe où."""
    if len(value) > MAX_PATH or any(ord(char) < 32 for char in value):
        raise RuntimeError("Dossier de copie refusé : chemin trop long ou caractère invalide")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise RuntimeError(f"Le dossier de copie doit être un chemin complet, pas {value!r}")
    if cloud := _synced(value):
        raise RuntimeError(
            f"Le dossier de copie semble synchronisé ({cloud}) : les copies partiraient dans le "
            "cloud. Choisir un dossier non synchronisé, par exemple sur un disque D: ou une clé USB"
        )
    # Réseau d'abord, avant tout accès au disque : sous Windows, résoudre ou ouvrir un chemin
    # \\serveur\partage contacterait ce serveur (et lui présenterait l'identité de la session).
    network = RuntimeError(
        "Le dossier de copie est sur le réseau : les bases quitteraient l'ordinateur. "
        "Choisir un disque branché sur cette machine"
    )
    if strict and is_network_path(path):
        raise network
    resolved = path.resolve()
    if strict and (cloud := _synced(str(resolved))):  # lien ou jonction vers OneDrive…
        raise RuntimeError(f"Le dossier de copie mène à un dossier synchronisé ({cloud})")
    if resolved.is_relative_to(APP_DIR):
        raise RuntimeError(f"Le dossier de copie doit être hors du dossier de l'app, pas {value!r}")
    if resolved.is_relative_to(profiles.DATA_DIR.resolve()):
        raise RuntimeError(
            f"Le dossier de copie doit être hors du dossier des données, pas {value!r}"
        )
    if strict:
        if is_network_path(resolved):  # lien symbolique vers un partage
            raise network
        if must_exist and not path.is_dir():
            raise RuntimeError(f"Dossier introuvable : {value} (le créer d'abord)")
    return path


def _synced(text: str) -> str | None:
    return next((name for name in SYNCED_FOLDERS if name in text.lower()), None)


def is_network_path(path: Path) -> bool:
    """Partage réseau ? Windows : chemin \\\\serveur\\partage ou lecteur réseau ; Linux et WSL :
    montage NFS, SMB, FUSE… ou partage Windows monté par drvfs ; macOS : smbfs, afpfs, nfs,
    webdav. Filet de sécurité : un lecteur réseau Windows déjà monté par WSL (/mnt/z) ressemble
    à un disque local et passe."""
    text = str(path)
    if text.startswith(("\\\\", "//")):
        return True
    if sys.platform == "win32":
        return _windows_drive_is_remote(path)
    if sys.platform.startswith("linux"):
        return _linux_mount_is_remote(path)
    if sys.platform == "darwin":
        return _mac_mount_is_remote(path)
    return False


def _mac_mount_is_remote(path: Path, output: str | None = None) -> bool:
    """macOS : montage le plus long qui contient `path`, lu dans la sortie de `mount`
    (« //moi@nas/partage on /Volumes/NAS (smbfs, nodev, …) »). Sortie illisible : refus par
    prudence, une copie ne doit pas partir sur un partage faute d'avoir pu vérifier."""
    if output is None:
        import subprocess

        try:
            output = subprocess.run(
                ["/sbin/mount"], capture_output=True, text=True, timeout=5, check=True
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return True
    best, remote = "", True
    for line in output.splitlines():
        match = MAC_MOUNT.fullmatch(line.strip())
        if not match:
            continue
        point, kind = match["point"], match["kind"]
        if path.is_relative_to(point) and len(point) >= len(best):
            best = point
            remote = kind in MAC_NETWORK_FILESYSTEMS or kind.startswith(("macfuse", "osxfuse"))
    return remote


def _windows_drive_is_remote(path: Path) -> bool:
    if not path.drive or path.drive.startswith("\\\\"):
        return bool(path.drive)
    import ctypes

    return ctypes.windll.kernel32.GetDriveTypeW(f"{path.drive}\\") == DRIVE_REMOTE


def _linux_mount_is_remote(path: Path, mounts: str = "/proc/self/mounts") -> bool:
    """Montage le plus long qui contient `path` : distant si son type est réseau, ou si sa
    source est un chemin \\\\serveur (drvfs, écrit \\134 dans /proc/mounts)."""
    try:
        lines = Path(mounts).read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    best, remote = "", False
    for line in lines:
        fields = line.split()
        if len(fields) < 3:
            continue
        src, point, kind = fields[0], fields[1].replace("\\040", " "), fields[2]
        options = fields[3] if len(fields) > 3 else ""
        if path.is_relative_to(point) and len(point) >= len(best):
            best = point
            # WSL 2 : drvfs en 9p, le chemin Windows est dans les options (path=C:\ ou
            # path=\\serveur\partage) ; WSL 1 : dans la source.
            unc = src.startswith(("\\134\\134", "//")) or "path=\\134\\134" in options
            # FUSE (fuse.*) : presque toujours distant (gvfs, kio, rclone, sshfs…) ; refusé par
            # prudence. fuseblk (NTFS, exFAT d'un disque branché) reste local.
            remote = kind in NETWORK_FILESYSTEMS or kind.startswith("fuse.") or unc
    return remote


# --- Lecture et écriture pour la page ----------------------------------------------------------


def status() -> dict:
    """Réponse de GET /api/settings. La clé CoinGecko n'y figure jamais : seulement si elle est
    enregistrée. Une valeur fixée par .env est montrée avec son origine (champ non modifiable)."""
    stored = _stored()
    fields: dict[str, dict] = {}
    for name, spec in NUMBERS.items():
        try:
            value = number(name)
        except RuntimeError:
            value = None  # .env illisible : l'app l'a déjà signalé au lancement
        fields[name] = {
            "value": value,
            "default": spec.default,
            "min": spec.low,
            "max": spec.high,
            "locked": source(spec.env),
        }
    copy_locked = source(COPY_DIR_ENV)
    copy_value = _env(COPY_DIR_ENV) if copy_locked else stored.get("backup_copy_dir")
    try:
        copy_error = None
        copy_dir()
    except RuntimeError as err:
        copy_error = str(err)
    fields["backup_copy_dir"] = {
        "value": copy_value if isinstance(copy_value, str) and copy_value else None,
        "locked": copy_locked,
        "error": copy_error,
    }
    fields["coingecko_api_key"] = {
        "set": coingecko_key() is not None,
        "locked": source(COINGECKO_ENV),
    }
    return {"fields": fields}


def prepare(changes: dict) -> dict:
    """Nouvelle section `reglages` avec les champs reçus (les autres ne bougent pas), sans rien
    écrire. SettingsError si une valeur est refusée ou si le réglage est fixé par .env."""
    stored = dict(_stored())
    for name, value in changes.items():
        env = NUMBERS[name].env if name in NUMBERS else _ENV_OF.get(name)
        if env is None:
            raise SettingsError(f"Réglage inconnu : {name}")
        if where := source(env):
            raise SettingsError(f"Réglage fixé par {where} ({env}) : le changer là-bas.")
        if name in NUMBERS:
            cleaned = check_number(name, value)
        elif name == "coingecko_api_key":
            cleaned = check_coingecko_key(value)
        else:
            cleaned = _clean_copy_dir(value)
        if cleaned is None:
            stored.pop(name, None)
        else:
            stored[name] = cleaned
    return stored


def save(section: dict) -> None:
    write_section(SECTION, section)


_ENV_OF = {"backup_copy_dir": COPY_DIR_ENV, "coingecko_api_key": COINGECKO_ENV}


def _clean_copy_dir(value: object) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise SettingsError("Dossier de copie : un chemin est attendu.")
    try:
        path = check_copy_dir(value.strip(), strict=True)
    except RuntimeError as err:
        raise SettingsError(str(err)) from err
    return str(path)  # « ~ » remplacé : la copie va bien là où la vérification a regardé
