"""Page de démarrage réglable (D-134) : fond et mot d'accueil choisis dans Réglages.

Réglage de la machine, comme le reste de `reglages.json` (clé `accueil`) : la page de démarrage
s'affiche avant qu'un profil soit ouvert, elle ne peut donc pas dépendre d'un profil. L'image
personnelle est un fichier du dossier des données (`accueil/fond.jpg`), jamais un chemin vers
un autre dossier : l'app ne lit rien ailleurs, et la copie du dossier des données l'emporte.

Le serveur ne fait confiance à rien de ce qu'il reçoit : fond pris dans une liste, texte borné
et sans caractère de contrôle, image bornée en taille dont l'en-tête doit être celui d'un JPEG
(la page réduit et réencode toute image en JPEG avant l'envoi, ce qui retire aussi ses
métadonnées : lieu de la prise de vue, appareil).
"""

from __future__ import annotations

from pathlib import Path

from app import profiles, settings

SECTION = "accueil"
IMAGE = "image"
# « auto » suit le thème (clair ou sombre) ; les autres noms sont ceux de web/styles.css.
BACKGROUNDS = ("auto", "aucun", "aube", "ciel", "dore", "crepuscule", "nuit", IMAGE)
DEFAULT_TEXT = "Tes comptes et tes chiffres, sur ton ordinateur."
TEXT_MAX = 120
IMAGE_DIR = "accueil"
IMAGE_NAME = "fond.jpg"
IMAGE_MAX_BYTES = 3 * 1024 * 1024  # une image de 2 560 px réencodée par la page pèse moins de 2 Mo
# Caractères invisibles qui inversent le sens d'écriture : un texte s'afficherait autrement
# qu'il est écrit. Le liant des émojis composés (U+200D) reste permis.
BIDI_CONTROLS = frozenset("\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")
JPEG_START = b"\xff\xd8\xff"
JPEG_END = b"\xff\xd9"


class StartPageError(ValueError):
    """Valeur refusée, avec un message à afficher tel quel."""


def image_file() -> Path:
    return profiles.DATA_DIR / IMAGE_DIR / IMAGE_NAME


def _stored() -> dict:
    section = settings.read_file().get(SECTION)
    return section if isinstance(section, dict) else {}


def _image_stamp() -> int | None:
    """Date de l'image en millisecondes, None sans image : sert d'adresse différente à chaque
    remplacement, pour que la page ne garde pas l'ancienne à l'écran. Des millisecondes et non
    des nanosecondes : un nombre que JavaScript lit sans l'arrondir."""
    try:
        return image_file().stat().st_mtime_ns // 1_000_000
    except OSError:
        return None


def status() -> dict:
    """Réponse de GET /api/start : ce que la page de démarrage affiche. Une valeur abîmée du
    fichier, ou le fond « image » sans son fichier, revient au réglage par défaut."""
    stored = _stored()
    stamp = _image_stamp()
    background = stored.get("fond")
    if background not in BACKGROUNDS or (background == IMAGE and stamp is None):
        background = "auto"
    text = stored.get("texte")
    try:
        text = check_text(text) if isinstance(text, str) else None
    except StartPageError:
        text = None
    return {
        "fond": background,
        "texte": text or DEFAULT_TEXT,
        "texte_defaut": DEFAULT_TEXT,
        "texte_max": TEXT_MAX,
        "image": stamp,
    }


def check_text(value: str | None) -> str | None:
    """Mot d'accueil saisi : une ligne, espaces resserrés ; vide ou None remet le texte
    d'origine. Il est affiché comme du texte, jamais interprété comme du HTML."""
    if value is None:
        return None
    if any(ord(char) < 32 and char not in "\t\n\r" or ord(char) == 127 for char in value):
        raise StartPageError("Mot d'accueil refusé : caractère invalide.")
    if any(char in BIDI_CONTROLS for char in value):
        raise StartPageError("Mot d'accueil refusé : caractère invalide.")
    text = " ".join(value.split())
    if len(text) > TEXT_MAX:
        raise StartPageError(f"Mot d'accueil trop long : {TEXT_MAX} caractères au plus.")
    return text or None


def update(changes: dict) -> dict:
    """Applique les champs reçus (`fond`, `texte`) ; un champ absent ne bouge pas."""
    with settings.file_lock:
        stored = dict(_stored())
        if "fond" in changes:
            background = changes["fond"] or "auto"
            if background not in BACKGROUNDS:
                raise StartPageError("Fond inconnu.")
            if background == IMAGE and _image_stamp() is None:
                raise StartPageError("Aucune image enregistrée : en choisir une d'abord.")
            stored["fond"] = background
        if "texte" in changes:
            text = check_text(changes["texte"])
            if text is None or text == DEFAULT_TEXT:
                stored.pop("texte", None)
            else:
                stored["texte"] = text
        settings.write_section(SECTION, stored)
    return status()


def set_image(data: bytes) -> dict:
    """Enregistre l'image personnelle et la choisit comme fond. Écrite d'un bloc : jamais un
    fichier à moitié écrit, même si l'app est fermée au mauvais moment."""
    if len(data) > IMAGE_MAX_BYTES:
        raise StartPageError("Image trop lourde : 3 Mo au plus.")
    if not data.startswith(JPEG_START) or not data.rstrip(b"\x00").endswith(JPEG_END):
        raise StartPageError("Image illisible : un fichier JPEG est attendu.")
    with settings.file_lock:
        path = image_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + ".tmp")
        temp.write_bytes(data)
        temp.replace(path)
        return update({"fond": IMAGE})


def remove_image() -> dict:
    """Retire l'image personnelle ; le fond revient à celui du thème si elle était choisie."""
    with settings.file_lock:
        image_file().unlink(missing_ok=True)
        stored = dict(_stored())
        if stored.get("fond") == IMAGE:
            stored["fond"] = "auto"
            settings.write_section(SECTION, stored)
    return status()


def image_bytes() -> bytes | None:
    try:
        return image_file().read_bytes()
    except OSError:
        return None
