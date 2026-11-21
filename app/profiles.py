"""Profils : plusieurs personnes sur le même ordinateur, une base SQLite chacune (D-098).

La liste vit dans profils.json (nom affiché, fichier de la base, empreinte du code), dans le
dossier des données hors du code (D-119), noté data/ ci-dessous. Tant
que ce fichier n'existe pas, l'app garde une seule base, data/patrimoine.db, comme avant. Le
premier profil ajouté enregistre cette base comme profil « Principal », sans la déplacer.

Le code d'un profil est facultatif et n'est jamais gardé en clair : seule son empreinte scrypt
(module standard hashlib) est écrite. Il protège de la curiosité dans l'app, pas de la lecture
du fichier .db, qui n'est pas chiffré. Aucun montant ni aucune quantité dans profils.json.
"""

import base64
import gc
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import sqlite3
import threading
import unicodedata
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app import datadir, db

# Dossier des données de l'utilisateur, hors du code (D-119) : lu une fois au démarrage.
DATA_DIR, DATA_ORIGIN = datadir.resolve()
REGISTRY_NAME = "profils.json"
PROFILES_SUBDIR = "profils"
LEGACY_FILE = "patrimoine.db"  # relatif à DATA_DIR : la base d'avant les profils
ID_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?")
NAME_MAX = 40
CODE_MIN, CODE_MAX = 4, 64
# scrypt : coût choisi pour ~50 ms par essai, ce qui freine les essais en série sans gêner.
SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}
MAX_FAILURES = 5
LOCKOUT = timedelta(minutes=1)
# Noms de fichier déjà pris dans data/ : une sauvegarde est nommée d'après le fichier de la base,
# un profil « Patrimoine » mélangerait sinon ses copies avec celles de la base d'origine.
RESERVED_IDS = {"patrimoine", "demo", "demo-grand", "sauvegardes", "corbeille"}
COLORS = ["#4e79a7", "#f28e2b", "#59a14f", "#e15759", "#b07aa1", "#76b7b2", "#edc948"]


class ProfileError(ValueError):
    """Demande impossible (nom vide, code trop court...) : message montré tel quel."""


class LockedOut(ProfileError):
    """Trop d'essais de code ratés : attendre avant de recommencer."""


@dataclass
class Profile:
    id: str
    name: str
    file: str  # chemin relatif à DATA_DIR, jamais saisi par l'utilisateur
    color: str
    created: str
    code: dict | None = None  # {"salt", "hash"} en hexadécimal, ou None
    tutorial_done: bool = False
    demo: bool = False  # profil Démo : données fictives, recréable (étape 4)
    photo: str | None = None  # petite image (data URL JPEG/PNG/WebP), gardée dans profils.json

    @property
    def has_code(self) -> bool:
        return self.code is not None

    def public(self) -> dict:
        """Ce que l'API montre : jamais l'empreinte ni le chemin du fichier."""
        return {
            "id": self.id,
            "name": self.name,
            "color": self.color,
            "has_code": self.has_code,
            "tutorial_done": self.tutorial_done,
            "demo": self.demo,
            "photo": self.photo,
        }


_lock = threading.Lock()  # lecture-écriture de profils.json, sessions, essais ratés


def registry_path() -> Path:
    return DATA_DIR / REGISTRY_NAME


def enabled() -> bool:
    """Profils actifs : profils.json existe et PATRIMOINE_DB ne force pas une base unique."""
    return not os.environ.get("PATRIMOINE_DB") and registry_path().is_file()


def load() -> list[Profile]:
    path = registry_path()
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    profiles = [Profile(**item) for item in data.get("profiles", [])]
    for p in profiles:  # fichier modifié à la main : refuser tout chemin hors de data/
        if not ID_PATTERN.fullmatch(p.id) or not _safe_file(p.file):
            raise RuntimeError(f"{path} : profil {p.id!r} invalide, fichier non lu.")
    return profiles


def _safe_file(file: str) -> bool:
    return file == LEGACY_FILE or re.fullmatch(rf"{PROFILES_SUBDIR}/{ID_PATTERN.pattern}\.db", file)


def _save(profiles: list[Profile]) -> None:
    """Écriture atomique (fichier temporaire puis remplacement) : jamais de liste à demi écrite."""
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    body = {"version": 1, "profiles": [asdict(p) for p in profiles]}
    tmp.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.chmod(0o600)  # lisible par le seul utilisateur Linux qui lance l'app
    os.replace(tmp, path)


def db_file(profile: Profile) -> Path:
    return DATA_DIR / profile.file


def databases() -> list[Path]:
    """Bases à sauvegarder ou migrer : celle de chaque profil, sinon la base unique."""
    if enabled():
        return [db_file(p) for p in load()]
    return [db.db_path()]


def get(profile_id: str) -> Profile | None:
    return next((p for p in load() if p.id == profile_id), None)


def _clean_name(name: str) -> str:
    name = " ".join(name.split())
    if not name:
        raise ProfileError("Nom du profil obligatoire.")
    if len(name) > NAME_MAX:
        raise ProfileError(f"Nom du profil : {NAME_MAX} caractères au plus.")
    return name


def slug(name: str) -> str:
    """Identifiant du fichier tiré du nom : « Élodie M. » -> « elodie-m »."""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:32].strip("-")
    return text or "profil"


def _unique_id(base: str, taken: set[str]) -> str:
    candidate, n = base, 2
    while candidate in taken:
        suffix = f"-{n}"
        candidate, n = base[: 32 - len(suffix)].rstrip("-") + suffix, n + 1
    return candidate


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


def _base_is_empty(path: Path) -> bool:
    """Base absente ou sans compte, actif ni opération (premier lancement, rien de saisi)."""
    if not path.is_file():
        return True
    try:
        with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as con:
            return not any(
                con.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
                for table in ("account", "asset", "operation")
            )
    except sqlite3.Error:
        return False  # illisible ou inconnue : ne jamais la prendre pour vide


def create(
    name: str,
    code: str | None = None,
    source: Path | None = None,
    *,
    pid: str | None = None,
    demo: bool = False,
    adopt_empty: bool = False,
) -> Profile:
    """Ajoute un profil et crée sa base vide, ou y range `source` (base restaurée, déjà
    vérifiée par `check_restored`). Le premier ajout enregistre d'abord la base actuelle
    (data/patrimoine.db) comme profil « Principal », renommable ensuite.

    `adopt_empty` (tutoriel de premier lancement) : si cette base est encore vide, elle devient
    directement le profil demandé, sans « Principal » inutile à côté."""
    name = _clean_name(name)
    with _lock:
        profiles = load()
        if (
            not profiles
            and adopt_empty
            and source is None
            and _base_is_empty(DATA_DIR / LEGACY_FILE)
        ):
            pid = _unique_id(slug(name), RESERVED_IDS)
            profile = Profile(
                pid, name, LEGACY_FILE, COLORS[0], _today(), _hash(code) if code else None
            )
            db.init_db(DATA_DIR / LEGACY_FILE)  # sans effet si la base existe déjà
            _save([profile])
            return profile
        # Premier ajout : la base actuelle devient « Principal », sauf si elle est vide et que le
        # profil ajouté est la démo (« Explorer avec la démo » au premier lancement) : la démo
        # reste alors seule, sans profil vide à côté. Une base qui contient des données est
        # toujours gardée.
        if not profiles and not (demo and _base_is_empty(DATA_DIR / LEGACY_FILE)):
            profiles.append(Profile("principal", "Principal", LEGACY_FILE, COLORS[0], _today()))
            db.init_db(DATA_DIR / LEGACY_FILE)  # sans effet si la base existe déjà
        if any(p.name.casefold() == name.casefold() for p in profiles):
            raise ProfileError("Un profil porte déjà ce nom.")
        taken = {p.id for p in profiles}
        if pid in taken:
            raise ProfileError("Ce profil existe déjà.")
        pid = pid or _unique_id(slug(name), taken | RESERVED_IDS)
        profile = Profile(
            pid,
            name,
            f"{PROFILES_SUBDIR}/{pid}.db",
            COLORS[len(profiles) % len(COLORS)],
            _today(),
            _hash(code) if code else None,
            demo=demo,
        )
        path = db_file(profile)
        if path.exists():  # reste d'un profil effacé à la main : ne jamais le reprendre
            raise ProfileError("Une base porte déjà ce nom dans le dossier des profils.")
        if source is None:
            db.init_db(path)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(source, path)
        path.chmod(0o600)
        profiles.append(profile)
        _save(profiles)
        return profile


def _update(profile_id: str, **changes) -> Profile:
    with _lock:
        profiles = load()
        profile = next((p for p in profiles if p.id == profile_id), None)
        if profile is None:
            raise ProfileError("Profil inconnu.")
        for key, value in changes.items():
            setattr(profile, key, value)
        _save(profiles)
        return profile


def rename(profile_id: str, name: str) -> Profile:
    name = _clean_name(name)
    others = [p for p in load() if p.id != profile_id]
    if any(p.name.casefold() == name.casefold() for p in others):
        raise ProfileError("Un profil porte déjà ce nom.")
    return _update(profile_id, name=name)


PHOTO_MAX_CHARS = 60_000  # ~45 Ko d'image : une vignette, pas une photo d'appareil
PHOTO_TYPES = {
    "image/jpeg": b"\xff\xd8\xff",
    "image/png": b"\x89PNG\r\n\x1a\n",
    "image/webp": b"RIFF",
}


def set_photo(profile_id: str, photo: str | None) -> Profile:
    """Pose ou retire la photo d'un profil. Vérifiée : type d'image connu, taille bornée, et
    l'en-tête du fichier correspond au type annoncé (le serveur ne fait confiance à rien)."""
    if photo:
        match = re.fullmatch(r"data:(image/(?:jpeg|png|webp));base64,([A-Za-z0-9+/]+={0,2})", photo)
        if not match or len(photo) > PHOTO_MAX_CHARS:
            raise ProfileError("Photo refusée : JPEG, PNG ou WebP, petite vignette.")
        try:
            raw = base64.b64decode(match.group(2), validate=True)
        except ValueError as err:
            raise ProfileError("Photo illisible.") from err
        if not raw.startswith(PHOTO_TYPES[match.group(1)]):
            raise ProfileError("Photo illisible.")
    return _update(profile_id, photo=photo or None)


def set_tutorial_done(profile_id: str, done: bool = True) -> Profile:
    return _update(profile_id, tutorial_done=done)


# --- Exporter, restaurer, supprimer (D-098, étape 3) -------------------------------------------

RESTORE_MAX_BYTES = 200 * 1024 * 1024
TRASH_SUBDIR = "corbeille"
SQLITE_HEADER = b"SQLite format 3\x00"


def _known_tables() -> set[str]:
    """Tables qu'une base de l'app peut contenir : schéma actuel et anciennes versions."""
    sources = [db.SCHEMA_PATH, *sorted(db.MIGRATIONS_DIR.glob("*.sql"))]
    pattern = re.compile(r"CREATE TABLE (?:IF NOT EXISTS )?(\w+)", re.IGNORECASE)
    return {m.lower() for f in sources for m in pattern.findall(f.read_text(encoding="utf-8"))}


def check_restored(path: Path) -> int:
    """Vérifie qu'un fichier reçu est bien une base de l'app avant de l'accepter, puis la met
    à jour si elle est plus ancienne. Un fichier .db peut contenir du code SQL qui s'exécute
    tout seul (déclencheurs, vues) : il est refusé, comme toute table inconnue. Renvoie la
    version du schéma d'origine."""
    with path.open("rb") as f:
        if f.read(16) != SQLITE_HEADER:
            raise ProfileError("Ce fichier n'est pas une base de l'app (.db).")
    conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        conn.execute("PRAGMA trusted_schema = OFF")
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ProfileError("Base abîmée : restauration refusée.")
        rows = conn.execute("SELECT type, name FROM sqlite_master").fetchall()
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    except sqlite3.DatabaseError as err:
        raise ProfileError("Ce fichier n'est pas une base de l'app (.db).") from err
    finally:
        conn.close()
    if any(kind in ("trigger", "view") for kind, _ in rows):
        raise ProfileError("Base refusée : elle contient du code SQL qui n'est pas celui de l'app.")
    known = _known_tables()
    unknown = {n for kind, n in rows if kind == "table" and not n.startswith("sqlite_")} - known
    if unknown:
        raise ProfileError("Base refusée : tables inconnues de l'app.")
    if not 1 <= version <= db.SCHEMA_VERSION:
        raise ProfileError("Base d'une version de l'app plus récente ou inconnue : refusée.")
    if version < db.SCHEMA_VERSION:
        db.migrate(path)
    return version


def restore(name: str, data: bytes) -> Profile:
    """Nouveau profil à partir d'une base exportée : jamais par-dessus un profil existant."""
    if len(data) > RESTORE_MAX_BYTES:
        raise ProfileError("Fichier trop gros pour une base de l'app.")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = DATA_DIR / f".restauration-{secrets.token_hex(8)}.db"
    try:
        tmp.write_bytes(data)
        tmp.chmod(0o600)
        check_restored(tmp)
        return create(name, source=tmp)
    finally:
        tmp.unlink(missing_ok=True)


def export(profile_id: str) -> bytes:
    """Copie cohérente de la base d'un profil (API de sauvegarde de SQLite), en mémoire."""
    profile = get(profile_id)
    if profile is None:
        raise ProfileError("Profil inconnu.")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = DATA_DIR / f".export-{secrets.token_hex(8)}.db"
    src, dst = sqlite3.connect(db_file(profile)), sqlite3.connect(tmp)
    try:
        src.backup(dst)
        dst.close()
        return tmp.read_bytes()
    finally:
        dst.close()
        src.close()
        tmp.unlink(missing_ok=True)


def delete(profile_id: str, confirm: str, code: str = "") -> Path:
    """Retire un profil de la liste : sauvegarde, puis sa base part dans data/corbeille/ (rien
    n'est effacé). Il faut retaper son nom, et son code s'il en a un. Le dernier profil reste.
    Renvoie le chemin de la base dans la corbeille."""
    from app import backup  # import ici : backup importe db, comme ce module

    profile = get(profile_id)
    if profile is None:
        raise ProfileError("Profil inconnu.")
    if " ".join(confirm.split()).casefold() != profile.name.casefold():
        raise ProfileError("Retape exactement le nom du profil pour confirmer.")
    _verify_code(profile, code)
    with _lock:
        profiles = load()
        if len(profiles) < 2:
            raise ProfileError("Impossible de supprimer le seul profil.")
        source = db_file(profile)
        trash = DATA_DIR / TRASH_SUBDIR
        trash.mkdir(parents=True, exist_ok=True)
        target = trash / f"{profile.id}-{datetime.now():%Y-%m-%d-%H%M%S}.db"
        if source.is_file():
            backup.backup(source, DATA_DIR / "sauvegardes", label="avant-suppression")
            release_connections()
            shutil.move(source, target)
            target.chmod(0o600)
        _save([p for p in profiles if p.id != profile_id])
        for token in [t for t, sess in _sessions.items() if sess.profile_id == profile_id]:
            del _sessions[token]
        return target


def release_connections() -> None:
    """Ferme les connexions SQLite que plus rien n'utilise, avant de remplacer ou déplacer la
    base d'un profil. Une connexion de CPython se référence elle-même (son cache de requêtes) :
    sans `close()`, elle ne se ferme qu'au passage du ramasse-miettes. Sous Windows, un fichier
    encore ouvert ne peut être ni remplacé ni déplacé (« Recréer » la démo, supprimer un profil
    juste après l'avoir utilisé)."""
    gc.collect()


# --- Profil Démo (D-098, étape 4) --------------------------------------------------------------

DEMO_ID = "demonstration"
DEMO_NAME = "Démo"


def create_demo() -> Profile:
    """Profil Démo rempli des données fictives de app.demo, pour découvrir l'app sans rien saisir.
    S'il existe déjà, sa base est remplacée par une base neuve (« Recréer ») : tout y est fictif."""
    from app import demo  # import ici : demo importe db et repository

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = DATA_DIR / f".demo-{secrets.token_hex(8)}.db"
    try:
        db.init_db(tmp)
        with db.connection(tmp) as conn:
            demo.fill_demo(conn)
        existing = get(DEMO_ID)
        if existing is not None:
            os.replace(tmp, db_file(existing))
            db_file(existing).chmod(0o600)
            return existing
        return create(DEMO_NAME, source=tmp, pid=DEMO_ID, demo=True)
    finally:
        tmp.unlink(missing_ok=True)


# --- Code ---------------------------------------------------------------------------------


def _check_code(code: str) -> None:
    if not CODE_MIN <= len(code) <= CODE_MAX:
        raise ProfileError(f"Code : de {CODE_MIN} à {CODE_MAX} caractères.")


def _hash(code: str, salt: bytes | None = None) -> dict:
    _check_code(code)
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(code.encode("utf-8"), salt=salt, **SCRYPT)
    return {"salt": salt.hex(), "hash": digest.hex()}


def _matches(profile: Profile, code: str) -> bool:
    if profile.code is None:
        return True
    expected = bytes.fromhex(profile.code["hash"])
    salt = bytes.fromhex(profile.code["salt"])
    digest = hashlib.scrypt(code.encode("utf-8"), salt=salt, **SCRYPT)
    return hmac.compare_digest(digest, expected)  # même durée quel que soit l'écart


def set_code(profile_id: str, new_code: str | None, current_code: str = "") -> Profile:
    """Pose, change ou retire le code d'un profil ; l'ancien code est demandé s'il y en a un."""
    profile = get(profile_id)
    if profile is None:
        raise ProfileError("Profil inconnu.")
    if profile.demo and new_code:
        raise ProfileError("Le profil Démo n'a pas de code : ses données sont fictives.")
    _verify_code(profile, current_code, "Code actuel incorrect.")
    return _update(profile_id, code=_hash(new_code) if new_code else None)


def remove_code(profile_id: str) -> Profile:
    """Code oublié : retiré depuis le terminal seulement (`uv run python -m app.profiles`)."""
    return _update(profile_id, code=None)


# --- Sessions -----------------------------------------------------------------------------
# Le cookie ne contient pas le nom du profil (on pourrait le changer à la main et passer
# l'écran du code) mais un jeton tiré au hasard, connu du seul serveur, en mémoire : il
# disparaît quand l'app s'arrête.


@dataclass
class _Session:
    profile_id: str
    last_seen: datetime


@dataclass
class _Failures:
    count: int = 0
    until: datetime | None = None


_sessions: dict[str, _Session] = {}
_failures: dict[str, _Failures] = {}


def _verify_code(profile: Profile, code: str, message: str = "Code incorrect.") -> None:
    """Vérifie le code d'un profil en comptant les essais ratés : cinq de suite ferment toutes
    les portes une minute (ouvrir, supprimer, changer le code). Avant D-138, seule l'ouverture
    comptait : la suppression laissait essayer des codes sans limite. Sans code : rien à faire."""
    if profile.code is None:
        return
    now = _now()
    with _lock:
        fails = _failures.setdefault(profile.id, _Failures())
        if fails.until and now < fails.until:
            seconds = int((fails.until - now).total_seconds()) + 1
            raise LockedOut(f"Trop d'essais : réessayer dans {seconds} s.")
        # L'essai est compté avant la comparaison, qui est lente exprès (scrypt) : des essais
        # lancés en même temps passaient tous avant que le premier échec soit compté.
        fails.count += 1
        if fails.count >= MAX_FAILURES:
            fails.count, fails.until = 0, now + LOCKOUT
    if _matches(profile, code):
        with _lock:
            _failures.pop(profile.id, None)  # bon code : compteur et délai effacés
        return
    raise ProfileError(message)


def idle_limit() -> timedelta:
    """Verrouillage après ce temps sans activité : .env, sinon carte Réglages (D-130)."""
    from app import settings  # import ici : settings importe ce module

    return timedelta(minutes=settings.number("profile_lock_minutes"))


def _now() -> datetime:
    return datetime.now(UTC)


def open_session(profile_id: str, code: str = "", just_created: bool = False) -> str:
    """Vérifie le code (s'il y en a un) et renvoie un nouveau jeton de session. Celui qui vient
    de créer le profil (`just_created`) l'ouvre d'office : il vient de choisir ce code."""
    profile = get(profile_id)
    if profile is None:
        raise ProfileError("Profil inconnu.")
    if not just_created:
        _verify_code(profile, code)
    token = secrets.token_urlsafe(32)
    with _lock:
        _failures.pop(profile_id, None)
        _sessions[token] = _Session(profile_id, _now())
    return token


def session_profile(token: str | None) -> Profile | None:
    """Profil d'un jeton encore valide ; prolonge la session. None si absent, inconnu, expiré."""
    if not token:
        return None
    now = _now()
    with _lock:
        session = _sessions.get(token)
        if session is None:
            return None
        if now - session.last_seen > idle_limit():
            del _sessions[token]
            return None
        session.last_seen = now
    profile = get(session.profile_id)
    if profile is None:  # profil retiré de la liste pendant la session
        close_session(token)
    return profile


def close_session(token: str | None) -> None:
    with _lock:
        _sessions.pop(token or "", None)


def reset_sessions() -> None:
    """Pour les tests : oublie sessions et essais ratés."""
    with _lock:
        _sessions.clear()
        _failures.clear()


def main(argv: list[str] | None = None) -> None:
    """`uv run python -m app.profiles` : liste ; `... sans-code <id>` : retire un code oublié."""
    import sys

    args = sys.argv[1:] if argv is None else argv
    if args[:1] == ["sans-code"] and len(args) == 2:
        try:
            profile = remove_code(args[1])
        except ProfileError as err:
            raise SystemExit(str(err)) from err
        print(f"Code retiré du profil {profile.name} ({profile.id}).")
        return
    if args:
        raise SystemExit("Usage : python -m app.profiles [sans-code <id>]")
    items = load()
    if not items:
        print(f"Aucun profil : base unique {DATA_DIR / LEGACY_FILE}.")
    for p in items:
        print(f"{p.id:<20} {p.name:<24} {'code' if p.has_code else 'sans code':<10} {p.file}")


if __name__ == "__main__":
    main()
