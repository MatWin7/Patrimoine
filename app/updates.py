"""Avis de mise à jour façon KeePass (D-129) : l'app prévient, l'utilisateur télécharge.

Seule requête : le numéro de la dernière version publiée sur GitHub (API publique « dernière
release »), une fois par jour au plus et seulement si l'utilisateur l'a accepté (question au
premier lancement, aucune requête avant sa réponse). Rien n'est envoyé : ni montant, ni
identifiant (User-Agent commun aux sources, figé) ; GitHub voit l'adresse IP, comme pour
toute page web. Rien n'est téléchargé ni installé : l'avis donne un lien vers la page de la
version, construit ici à partir du numéro validé (jamais une adresse lue dans la réponse).

Le choix vaut pour la machine (tous les profils) : il est rangé avec la date et le résultat de
la dernière vérification dans `reglages.json`, dans le dossier des données.
"""

import json
import re
import threading
from dataclasses import dataclass
from datetime import date

import httpx2

from app import settings as settings_file
from app.version import __version__

REPO = "MatWin7/Patrimoine"
API_HOST = "api.github.com"
LATEST_URL = f"https://{API_HOST}/repos/{REPO}/releases/latest"
RELEASES_URL = f"https://github.com/{REPO}/releases"
# Une release normale, jamais une préversion : « v1.2.3 » seulement (GitHub exclut déjà
# brouillons et préversions de « latest »). Chiffres ASCII seulement ([0-9] et non \d, qui
# accepterait d'autres écritures), sans zéro en tête (le lien construit ne correspondrait plus
# à l'étiquette), au plus 4 par nombre : le numéro finit dans un lien et un texte de la page.
_PART = r"(0|[1-9][0-9]{0,3})"
STABLE_TAG = re.compile(rf"v?{_PART}\.{_PART}\.{_PART}")
MAX_RESPONSE_BYTES = 1_000_000  # une release fait quelques Ko ; au-delà, lecture arrêtée
MAX_REDIRECTS = 2  # dépôt renommé ou transféré : GitHub répond 301 vers la nouvelle adresse
REDIRECTS = (301, 302, 307, 308)

_lock = threading.Lock()
# Dernier jour où une vérification automatique est partie, gardé aussi en mémoire : la limite
# d'une par jour tient même si reglages.json ne peut pas être écrit.
_last_attempt: str | None = None


class UpdateError(Exception):
    """Vérification impossible : réseau coupé, GitHub injoignable, réponse inattendue."""


def parse_version(text: str) -> tuple[int, int, int] | None:
    """« v0.2.0 » ou « 0.2.0 » -> (0, 2, 0) ; None pour une préversion ou tout autre texte."""
    match = STABLE_TAG.fullmatch(text) if isinstance(text, str) else None
    return tuple(int(part) for part in match.groups()) if match else None


def canonical(text: str) -> str | None:
    """« v0.2.0 » -> « 0.2.0 » : la seule forme rangée et affichée ; None si invalide."""
    version = parse_version(text)
    return ".".join(str(part) for part in version) if version else None


def is_newer(latest: str | None, current: str = __version__) -> bool:
    """La version publiée dépasse-t-elle celle qui tourne ? Une préversion locale (0.2.0-rc.1)
    est comparée sur son numéro : la 0.2.0 publiée ne lui est pas proposée."""
    mine = parse_version(current.split("-", 1)[0])
    theirs = parse_version(latest) if latest else None
    return bool(mine and theirs and theirs > mine)


def release_page(version: str) -> str:
    """Page GitHub d'une version déjà validée par parse_version."""
    return f"{RELEASES_URL}/tag/v{version}"


def fetch_latest(client: httpx2.Client) -> str | None:
    """Numéro de la dernière release publiée (« 0.2.0 »), None s'il n'y en a pas encore.

    La requête ne porte que l'URL publique, l'en-tête Accept et le User-Agent commun aux sources
    (figé, sans la vraie version) : aucune donnée de l'app. Une redirection n'est suivie que vers
    l'API de GitHub en https (dépôt transféré) ; la lecture s'arrête au-delà de 1 Mo."""
    url = httpx2.URL(LATEST_URL)
    try:
        for _ in range(MAX_REDIRECTS + 1):
            status, location, body = _get(client, url)
            if status not in REDIRECTS:
                break
            url = _redirect_target(url, location)
        else:
            raise UpdateError("trop de redirections")
    except httpx2.HTTPError as err:
        raise UpdateError("GitHub est injoignable") from err
    if status == 404:
        return None  # aucune release publiée
    if status != 200:
        raise UpdateError(f"GitHub a répondu {status}")
    try:
        data = json.loads(body)
        tag = data["tag_name"]
    except (ValueError, KeyError, TypeError) as err:
        raise UpdateError("réponse de GitHub inattendue") from err
    version = canonical(tag)
    if version is None or data.get("draft") or data.get("prerelease"):
        raise UpdateError("numéro de version inattendu")
    return version


def _get(client: httpx2.Client, url: httpx2.URL) -> tuple[int, str | None, bytes]:
    """Statut, en-tête Location et corps (lu par morceaux, 1 Mo au plus)."""
    headers = {"Accept": "application/vnd.github+json"}
    with client.stream("GET", url, headers=headers, follow_redirects=False) as response:
        if response.status_code != 200:
            return response.status_code, response.headers.get("location"), b""
        body = bytearray()
        for chunk in response.iter_bytes():
            body += chunk
            if len(body) > MAX_RESPONSE_BYTES:
                raise UpdateError("réponse de GitHub trop grande")
        return 200, None, bytes(body)


def _redirect_target(current: httpx2.URL, location: str | None) -> httpx2.URL:
    """Adresse d'une redirection, acceptée seulement vers l'API de GitHub en https."""
    if not location:
        raise UpdateError("redirection de GitHub sans adresse")
    target = current.join(location)
    if target.scheme != "https" or target.host != API_HOST or target.port not in (None, 443):
        raise UpdateError("redirection de GitHub refusée")
    return target


@dataclass
class Settings:
    enabled: bool | None = None  # None : question pas encore posée, aucune requête
    checked: str | None = None  # date ISO de la dernière tentative (réussie ou non)
    latest: str | None = None  # dernière version vue, validée
    error: str | None = None  # échec de la dernière tentative


def load() -> Settings:
    """Réglages de mise à jour ; fichier absent ou abîmé : question à reposer."""
    data = settings_file.read_file().get("updates", {})
    if not isinstance(data, dict):
        return Settings()
    enabled = data.get("enabled")
    latest = data.get("latest")
    checked = data.get("checked")
    error = data.get("error")
    return Settings(
        enabled=enabled if isinstance(enabled, bool) else None,
        checked=checked if isinstance(checked, str) and _is_date(checked) else None,
        latest=canonical(latest) if isinstance(latest, str) else None,
        error=error if isinstance(error, str) else None,
    )


def _is_date(text: str) -> bool:
    try:
        date.fromisoformat(text)
    except ValueError:
        return False
    return True


def save(settings: Settings) -> None:
    """Écrit les réglages sans perdre les autres clés du fichier (carte Réglages, D-130)."""
    settings_file.write_section(
        "updates",
        {
            "enabled": settings.enabled,
            "checked": settings.checked,
            "latest": settings.latest,
            "error": settings.error,
        },
    )


def set_enabled(enabled: bool) -> Settings:
    with _lock:
        settings = load()
        settings.enabled = enabled
        if not enabled:
            settings.error = None
        save(settings)
        return settings


def check(client: httpx2.Client, today: date, *, force: bool = False) -> Settings:
    """Vérifie si l'utilisateur l'a accepté, une fois par jour au plus. `force` : clic sur
    « Vérifier maintenant », qui marche aussi avis coupé (vérification à la demande). Un échec
    compte comme la vérification du jour : pas de nouvel appel à chaque ouverture de page quand
    GitHub ou le réseau est coupé."""
    global _last_attempt
    day = today.isoformat()
    with _lock:
        settings = load()
        if not force and (settings.enabled is not True or day in (settings.checked, _last_attempt)):
            return settings
        _last_attempt = settings.checked = day
        try:
            latest = fetch_latest(client)
        except UpdateError as err:
            settings.error = str(err)
        else:
            settings.latest, settings.error = latest, None
        save(settings)
        return settings


def status(settings: Settings) -> dict:
    """Réponse de /api/updates : ce que la page affiche."""
    newer = is_newer(settings.latest)  # le bandeau, lui, ne s'affiche qu'avis accepté
    return {
        "enabled": settings.enabled,
        "current": __version__,
        "latest": settings.latest,
        "newer": newer,
        "url": release_page(settings.latest) if newer else None,
        "releases": RELEASES_URL,
        "checked": settings.checked,
        "error": settings.error,
    }
