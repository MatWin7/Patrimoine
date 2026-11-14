"""Cours des actions et ETF via l'URL publique de graphiques de Yahoo Finance (D-019).

Pas de bibliothèque yfinance : un seul appel GET suffit, la réponse est du JSON.
Exemple : https://query1.finance.yahoo.com/v8/finance/chart/CW8.PA?range=1d&interval=1d
L'historique (get_history) vient de la même URL, sur une période donnée (jalon 6).
"""

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from urllib.parse import quote

import httpx2

from app.providers import Price, ProviderError, get_json, positive_decimal

URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
SEARCH = "https://query1.finance.yahoo.com/v1/finance/search"


def get_price(client: httpx2.Client, ticker: str) -> Price:
    data = get_json(
        client,
        URL.format(ticker=quote(ticker, safe="")),
        params={"range": "1d", "interval": "1d"},
    )
    try:
        meta = data["chart"]["result"][0]["meta"]
        price = positive_decimal(meta["regularMarketPrice"])
        currency = str(meta["currency"]).upper()
    except (KeyError, IndexError, TypeError) as err:
        raise ProviderError(f"ticker {ticker} introuvable chez Yahoo") from err
    previous = meta.get("chartPreviousClose") or meta.get("previousClose")
    previous = previous if isinstance(previous, Decimal) and previous > 0 else None
    return Price(price, currency, "yahoo", datetime.now(UTC), previous)


def get_history(
    client: httpx2.Client, ticker: str, start: date, end: date
) -> tuple[str, list[tuple[date, Decimal]]]:
    """Clôtures quotidiennes de `start` à `end` inclus : (devise, [(jour, clôture), ...]).

    Même URL que get_price, avec une période (period1/period2, en secondes Unix) au lieu de
    range=1d. Le jour de chaque clôture est celui de la place de cotation (décalage gmtoffset).
    Le jour en cours a une « clôture » provisoire (dernier cours) : elle est corrigée au
    prochain appel, qui redemande les derniers jours.
    """
    period1 = int(datetime.combine(start, time(), UTC).timestamp())
    period2 = int(datetime.combine(end + timedelta(days=1), time(), UTC).timestamp())
    data = get_json(
        client,
        URL.format(ticker=quote(ticker, safe="")),
        params={"period1": period1, "period2": period2, "interval": "1d"},
    )
    try:
        result = data["chart"]["result"][0]
        currency = str(result["meta"]["currency"]).upper()
        offset = int(result["meta"].get("gmtoffset") or 0)
        stamps = result.get("timestamp") or []
        closes = result["indicators"]["quote"][0].get("close") or []
    except (KeyError, IndexError, TypeError, ValueError) as err:
        raise ProviderError(f"historique de {ticker} introuvable chez Yahoo") from err
    history = {}
    for stamp, close in zip(stamps, closes, strict=False):
        # Jour sans cotation : close vaut null.
        if isinstance(close, Decimal) and close.is_finite() and close > 0:
            day = datetime.fromtimestamp(int(stamp) + offset, UTC).date()
            history[day] = close
    return currency, sorted(history.items())


def search(client: httpx2.Client, query: str) -> list[dict]:
    """Titres trouvés par Yahoo pour un ISIN ou un nom (D-083) : [{symbol, name}, ...]. Seul le
    texte cherché part sur le réseau, jamais un montant."""
    data = get_json(client, SEARCH, params={"q": query, "quotesCount": 10, "newsCount": 0})
    try:
        quotes = data["quotes"]
    except (KeyError, TypeError) as err:
        raise ProviderError("réponse de recherche Yahoo inattendue") from err
    return [
        {"symbol": str(q["symbol"]).upper(), "name": q.get("longname") or q.get("shortname")}
        for q in quotes
        if isinstance(q, dict) and q.get("symbol") and q.get("quoteType") in ("EQUITY", "ETF")
    ]
