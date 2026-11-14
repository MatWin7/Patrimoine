"""Cours des cryptomonnaies via l'API publique de CoinGecko (id du type "bitcoin").

La clé « demo » gratuite est facultative (COINGECKO_API_KEY dans .env) ; elle relève la
limite d'appels. Elle part dans un en-tête HTTP, jamais dans le dépôt.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import httpx2

from app.providers import Price, ProviderError, get_json, positive_decimal

URL = "https://api.coingecko.com/api/v3/simple/price"
HISTORY_URL = "https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"


def get_price(
    client: httpx2.Client, coin_id: str, currency: str = "EUR", api_key: str | None = None
) -> Price:
    headers = {"x-cg-demo-api-key": api_key} if api_key else {}
    cur = currency.lower()
    params = {"ids": coin_id, "vs_currencies": cur, "include_24hr_change": "true"}
    data = get_json(client, URL, params=params, headers=headers)
    try:
        price = positive_decimal(data[coin_id][cur])
    except (KeyError, TypeError) as err:
        raise ProviderError(f"id {coin_id} introuvable chez CoinGecko") from err
    # La crypto cote en continu : la « veille » est le prix d'il y a 24 h, déduit de la variation.
    change = data[coin_id].get(f"{cur}_24h_change")
    previous = price / (1 + change / 100) if isinstance(change, Decimal) and change > -100 else None
    return Price(price, currency.upper(), "coingecko", datetime.now(UTC), previous)


def get_history(
    client: httpx2.Client, coin_id: str, currency: str = "EUR", api_key: str | None = None
) -> list[tuple[date, Decimal]]:
    """Cours quotidiens des 365 derniers jours (limite de l'API gratuite), D-082. Seuls l'id
    et la devise partent sur le réseau, jamais une date d'achat ni une quantité (règle 3)."""
    if not coin_id.replace("-", "").isalnum():
        raise ProviderError(f"id CoinGecko invalide : {coin_id!r}")
    headers = {"x-cg-demo-api-key": api_key} if api_key else {}
    params = {"vs_currency": currency.lower(), "days": "365", "interval": "daily"}
    data = get_json(client, HISTORY_URL.format(coin_id=coin_id), params=params, headers=headers)
    out: dict[date, Decimal] = {}
    try:
        for stamp, price in data["prices"]:
            day = datetime.fromtimestamp(int(stamp) / 1000, UTC).date()
            out[day] = positive_decimal(price)  # le dernier point du jour l'emporte
    except (KeyError, TypeError, ValueError) as err:
        raise ProviderError(f"historique de {coin_id} illisible") from err
    return sorted(out.items())
