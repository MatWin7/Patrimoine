"""Ticker Yahoo d'un ISIN (D-083) : table locale, puis recherche sur un faux serveur Yahoo.
Titres FICTIFS, sauf les ISIN publics de la table."""

import json

import httpx2
import pytest
from fastapi.testclient import TestClient

from app import tickers
from app.main import app, http_client
from tests.conftest import fake_client

ISIN = "FR00TEST0001"
NOM = "ETF Monde fictif UCITS ETF"
COURS = {"FAKE.L": ("USD", 12), "FAKE.DE": ("EUR", 10), "FR00TEST0001.SG": ("EUR", 10)}


def yahoo(request: httpx2.Request) -> httpx2.Response:
    if request.url.path.endswith("/search"):
        q = request.url.params["q"]
        found = ["FAKE.L"] if q == ISIN else ["FAKE.DE", "FR00TEST0001.SG", "FAKE.L"]
        quotes = [{"symbol": s, "longname": NOM, "quoteType": "ETF"} for s in found]
        quotes.append({"symbol": "FAKE=F", "longname": NOM, "quoteType": "FUTURE"})
        return httpx2.Response(200, json={"quotes": quotes if q in (ISIN, NOM) else []})
    symbol = request.url.path.rsplit("/", 1)[-1]
    currency, price = COURS[symbol]
    meta = {"currency": currency, "regularMarketPrice": price}
    return httpx2.Response(200, content=json.dumps({"chart": {"result": [{"meta": meta}]}}))


def test_table_locale():
    assert tickers.known("ie00b4l5y983", "eur") == "IWDA.AS"
    assert tickers.known("US0378331005", "USD") == "AAPL"
    assert tickers.known("US0378331005", "GBP") is None
    assert all(tickers.ISIN.match(isin) for isin in tickers.KNOWN)


def test_recherche_cotation_dans_la_devise_d_abord():
    quotes = tickers.search(fake_client(yahoo), ISIN, "EUR")
    # Yahoo ne donne que la cotation de Londres pour l'ISIN : le nom trouve les autres places.
    assert [(q["ticker"], q["currency"]) for q in quotes] == [
        ("FAKE.DE", "EUR"),
        ("FR00TEST0001.SG", "EUR"),
        ("FAKE.L", "USD"),
    ]


@pytest.fixture
def client(db_file):
    app.dependency_overrides[http_client] = lambda: fake_client(yahoo)
    with TestClient(app) as c:
        yield c


def test_api_tickers(client):
    res = client.get("/api/tickers", params={"isin": "IE00B4L5Y983"}).json()
    assert res == {"source": "table", "quotes": [{"ticker": "IWDA.AS", "currency": "EUR"}]}
    res = client.get("/api/tickers", params={"isin": ISIN, "currency": "EUR"}).json()
    assert res["source"] == "yahoo" and res["quotes"][0]["ticker"] == "FAKE.DE"
    assert client.get("/api/tickers", params={"isin": "pas un isin"}).status_code == 422
