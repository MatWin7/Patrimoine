"""Taux de change de la BCE via Frankfurter : 1 EUR = rate devise (D-010).

La BCE publie un taux par jour ouvré, vers 16 h (heure de Paris).
"""

from datetime import date
from decimal import Decimal

import httpx2

from app.providers import ProviderError, get_json, positive_decimal

URL = "https://api.frankfurter.dev/v1/latest"
SERIES_URL = "https://api.frankfurter.dev/v1/{start}..{end}"


def get_rates(client: httpx2.Client, currencies: set[str]) -> tuple[date, dict[str, Decimal]]:
    """Derniers taux publiés pour les devises demandées (hors EUR)."""
    data = get_json(client, URL, params={"base": "EUR", "symbols": ",".join(sorted(currencies))})
    try:
        rate_date = date.fromisoformat(data["date"])
        rates = {cur: positive_decimal(data["rates"][cur]) for cur in currencies}
    except (KeyError, TypeError, ValueError) as err:
        raise ProviderError("taux de change absents de la réponse Frankfurter") from err
    return rate_date, rates


def get_history(
    client: httpx2.Client, currencies: set[str], start: date, end: date
) -> dict[str, list[tuple[date, Decimal]]]:
    """Taux de chaque jour ouvré de `start` à `end` (D-081) : seuls les codes devises et deux
    dates partent sur le réseau (règle 3)."""
    data = get_json(
        client,
        SERIES_URL.format(start=start.isoformat(), end=end.isoformat()),
        params={"base": "EUR", "symbols": ",".join(sorted(currencies))},
    )
    out: dict[str, list[tuple[date, Decimal]]] = {cur: [] for cur in currencies}
    try:
        for day, rates in sorted(data["rates"].items()):
            for cur in currencies:
                if cur in rates:
                    out[cur].append((date.fromisoformat(day), positive_decimal(rates[cur])))
    except (KeyError, TypeError, ValueError, AttributeError) as err:
        raise ProviderError("taux de change absents de la réponse Frankfurter") from err
    return out
