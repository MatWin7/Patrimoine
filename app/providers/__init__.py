"""Sources de prix publics. Une source par fichier, même forme : get_price(client, id) -> Price.

Règle 3 du projet : une requête ne contient qu'un ticker, un id ou un code devise, jamais
un montant ni une quantité. Le client HTTP est passé en paramètre pour que les tests le
remplacent par un faux transport (aucun accès réseau pendant les tests).
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

import httpx2

TIMEOUT_SECONDS = 10
# Yahoo refuse les requêtes sans User-Agent de navigateur (erreur 429).
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) Patrimoine/0.1"


class ProviderError(Exception):
    """Prix indisponible : réseau coupé, ticker inconnu, réponse inattendue..."""


@dataclass(frozen=True)
class Price:
    price: Decimal
    currency: str
    source: str  # "yahoo", "coingecko"
    fetched_at: datetime  # UTC, moment de la récupération (sert au cache)
    previous_close: Decimal | None = None  # clôture de la veille, pour la variation du jour


def new_client() -> httpx2.Client:
    return httpx2.Client(timeout=TIMEOUT_SECONDS, headers={"User-Agent": USER_AGENT})


def get_json(client: httpx2.Client, url: str, **kwargs) -> object:
    """GET puis JSON, nombres décimaux lus directement en Decimal (jamais de float)."""
    try:
        response = client.get(url, **kwargs)
        response.raise_for_status()
        return response.json(parse_float=Decimal, parse_int=Decimal)
    except httpx2.HTTPStatusError as err:
        raise ProviderError(f"réponse HTTP {err.response.status_code}") from err
    except httpx2.HTTPError as err:
        raise ProviderError(f"source injoignable ({type(err).__name__})") from err
    except ValueError as err:
        raise ProviderError("réponse illisible") from err


def positive_decimal(value: object) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise ProviderError(f"prix invalide : {value!r}")
    return value
