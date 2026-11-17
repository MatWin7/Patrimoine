"""Routes qui testent un identifiant avant de créer un actif : cours d'un ticker ou d'un id
CoinGecko, ticker Yahoo d'un ISIN (D-083). Seul l'identifiant public part sur le réseau."""

import re

from fastapi import APIRouter, HTTPException

from app import config, providers, tickers
from app.api.common import price_text
from app.api.deps import HttpClient
from app.providers import coingecko, yahoo

router = APIRouter()


@router.get("/api/quote")
def check_quote(
    client: HttpClient, ticker: str | None = None, coingecko_id: str | None = None, currency="EUR"
) -> dict:
    """Teste un identifiant avant de créer ou modifier un actif : renvoie le cours trouvé.

    Rien n'est enregistré. Seuls l'identifiant et la devise partent sur le réseau.
    """
    try:
        if coingecko_id and coingecko_id.strip():
            coin = coingecko_id.strip().lower()
            # Même alphabet fermé que l'id enregistré sur un actif (`CoingeckoId`, D-138).
            if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,99}", coin):
                raise HTTPException(status_code=422, detail="Id CoinGecko invalide.")
            if not re.fullmatch(r"[A-Za-z]{3}", currency.strip()):
                raise HTTPException(status_code=422, detail="Devise invalide.")
            price = coingecko.get_price(client, coin, currency.strip(), config.coingecko_api_key())
        elif ticker and ticker.strip():
            price = yahoo.get_price(client, ticker.strip().upper())
        else:
            raise HTTPException(status_code=422, detail="Indique un ticker ou un id CoinGecko.")
    except providers.ProviderError as err:
        raise HTTPException(status_code=404, detail=f"Cours introuvable : {err}") from err
    return {"price": price_text(price.price), "currency": price.currency, "source": price.source}


@router.get("/api/tickers")
def find_ticker(client: HttpClient, isin: str, currency: str = "EUR") -> dict:
    """Ticker Yahoo d'un ISIN (D-083) : table locale, sinon recherche chez Yahoo, demandée par
    un clic. Cotations dans la devise voulue d'abord. Seuls l'ISIN et le nom du titre partent."""
    isin, currency = isin.strip().upper(), currency.strip().upper()
    if not tickers.ISIN.match(isin) or not re.fullmatch(r"[A-Z]{3}", currency):
        raise HTTPException(status_code=422, detail="ISIN ou devise invalide.")
    if ticker := tickers.known(isin, currency):
        return {"source": "table", "quotes": [{"ticker": ticker, "currency": currency}]}
    try:
        quotes = tickers.search(client, isin, currency)
    except providers.ProviderError as err:
        raise HTTPException(status_code=502, detail=f"Recherche Yahoo impossible : {err}") from err
    return {"source": "yahoo", "quotes": quotes}
