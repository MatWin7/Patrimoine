"""Ce qui sort de cet ordinateur (D-131) : chaque service joint par l'app, ce qui lui est envoyé,
quand, et comment s'en passer. Cette liste fait foi à trois endroits :

- la page « Ce qui sort de cet ordinateur » l'affiche telle quelle (`GET /api/about`) ;
- le client HTTP de l'app (`providers.new_client`) refuse toute adresse qui n'y figure pas :
  une source ajoutée sans être déclarée ici ne part pas ;
- un test relève chaque adresse https:// écrite dans app/ et vérifie qu'elle y figure.

Le compte des requêtes depuis le lancement reste en mémoire : ni fichier, ni journal, et rien
de la requête n'est gardé (seulement le service et l'heure).
"""

import threading
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx2


@dataclass(frozen=True)
class Service:
    key: str
    name: str
    hosts: tuple[str, ...]
    purpose: str  # à quoi sert l'appel
    sent: str  # ce que contient la requête
    when: str  # quand elle part
    stop: str  # comment s'en passer


SERVICES: tuple[Service, ...] = (
    Service(
        key="yahoo",
        name="Yahoo Finance",
        hosts=("query1.finance.yahoo.com",),
        purpose="Cours des actions, ETF, indices et cryptos ; recherche d'un ticker.",
        sent="Le ticker (CW8.PA), ou l'ISIN et le nom public du titre cherché, et une période.",
        when=(
            "Quand une page s'affiche et que le dernier cours a plus de quelques minutes ; "
            "historique une fois par jour ; recherche seulement sur clic."
        ),
        stop="Un actif sans ticker devient une valeur saisie à la main : rien ne part.",
    ),
    Service(
        key="coingecko",
        name="CoinGecko",
        hosts=("api.coingecko.com",),
        purpose=(
            "Cours des cryptos qui ont un identifiant CoinGecko (Yahoo en repli), listes de "
            "suivi ; cours passés à l'import Binance et aux dépôts Aave."
        ),
        sent=(
            "L'identifiant du jeton (bitcoin) et la devise ; ta clé CoinGecko si tu en as "
            "enregistré une."
        ),
        when="Comme Yahoo, pour les cryptos qui ont un identifiant CoinGecko.",
        stop="Ne pas donner d'identifiant CoinGecko à l'actif.",
    ),
    Service(
        key="frankfurter",
        name="Frankfurter (taux BCE)",
        hosts=("api.frankfurter.dev",),
        purpose="Taux de change publiés par la Banque centrale européenne.",
        sent="Les codes des devises (USD) et une période.",
        when="Seulement si un compte ou un actif n'est pas en euros, en même temps que les cours.",
        stop="Tout tenir en euros.",
    ),
    Service(
        key="eurostat",
        name="Eurostat",
        hosts=("ec.europa.eu",),
        purpose="Indice des prix (inflation en France) du graphique d'évolution.",
        sent="Le code du pays (FR) et celui de la série.",
        when=(
            "Une fois par jour, quand le graphique d'évolution s'affiche (nouvel essai une heure "
            "après un échec)."
        ),
        stop="Pas de réglage : la requête ne dit rien de toi ni de tes lignes.",
    ),
    Service(
        key="aave",
        name="Aave",
        hosts=("api.v3.aave.com",),
        purpose="Taux de dépôt et d'emprunt publics des marchés Aave.",
        sent=(
            "Le numéro de la chaîne et les adresses des contrats d'Aave (marché, jeton), "
            "jamais celle de ton portefeuille."
        ),
        when=(
            "Une fois par jour au plus si une ligne Aave est suivie (taux, seuils de "
            "liquidation) ; liste des marchés sur clic."
        ),
        stop="Ne suivre aucune ligne Aave.",
    ),
    Service(
        key="github",
        name="GitHub",
        hosts=("api.github.com",),
        purpose="Numéro de la dernière version publiée de Patrimoine.",
        sent="Rien d'autre que l'adresse fixe de la page des versions.",
        when="Une fois par jour au plus, seulement si tu l'as accepté ; ou sur clic.",
        stop="Décocher « Prévenir quand une nouvelle version sort » (Gérer, Mises à jour).",
    ),
)

NEVER_SENT = (
    "Jamais envoyé : montants, quantités, noms de comptes ou de profils, adresses de "
    "portefeuille, ni le contenu de tes bases."
)
SEEN_ANYWAY = (
    "Chaque service voit, comme pour toute page web, l'adresse IP de ta connexion et l'heure ; "
    "d'après les tickers demandés, il peut deviner quels titres tu suis, jamais combien tu en "
    "as. Toutes les requêtes portent le même en-tête figé (« Mozilla/5.0 (X11; Linux x86_64) "
    "Patrimoine/0.1 »), qui ne dit rien de toi. Avec une clé CoinGecko, CoinGecko sait en plus "
    "que ses requêtes viennent de ton compte chez lui."
)
OUTSIDE_APP = (
    "Hors de l'app, sans aucun montant : le moteur de la fenêtre. Sous Windows, WebView2 envoie "
    "sa propre télémétrie selon le réglage « Diagnostics » de Windows, qui le met aussi à jour "
    "(l'app y coupe SmartScreen) ; sous macOS, WebKit fait partie du système et suit ses "
    "réglages de confidentialité. Ouverte dans un navigateur, la page suit ses réglages."
)

_BY_HOST = {host: service.key for service in SERVICES for host in service.hosts}
_lock = threading.Lock()
_counts: dict[str, int] = {}
_last: dict[str, datetime] = {}
_refused = 0


class OutboundRefused(httpx2.RequestError):
    """Adresse absente de SERVICES : la requête ne part pas."""


def service_of(host: str) -> str | None:
    return _BY_HOST.get(host.lower())


def on_request(request: httpx2.Request) -> None:
    """Crochet du client HTTP, appelé avant chaque envoi (redirection comprise) : refuse une
    adresse non déclarée, un autre schéma que https ou un autre port que 443, sinon compte la
    requête."""
    global _refused
    url = request.url
    key = service_of(url.host) if url.scheme == "https" and url.port in (None, 443) else None
    with _lock:
        if key is None:
            _refused += 1
        else:
            _counts[key] = _counts.get(key, 0) + 1
            _last[key] = datetime.now(UTC)
    if key is None:
        raise OutboundRefused(f"adresse non déclarée : {url.host}", request=request)


def summary() -> dict:
    """La liste, avec le nombre de requêtes de chaque service depuis le lancement."""
    with _lock:
        counts, last, refused = dict(_counts), dict(_last), _refused
    return {
        "services": [
            {
                "key": s.key,
                "name": s.name,
                "hosts": list(s.hosts),
                "purpose": s.purpose,
                "sent": s.sent,
                "when": s.when,
                "stop": s.stop,
                "count": counts.get(s.key, 0),
                "last_at": last[s.key].isoformat(timespec="seconds") if s.key in last else None,
            }
            for s in SERVICES
        ],
        "refused": refused,
        "never_sent": NEVER_SENT,
        "seen_anyway": SEEN_ANYWAY,
        "outside_app": OUTSIDE_APP,
    }


def reset() -> None:
    """Remet les compteurs à zéro (tests)."""
    global _refused
    with _lock:
        _counts.clear()
        _last.clear()
        _refused = 0
