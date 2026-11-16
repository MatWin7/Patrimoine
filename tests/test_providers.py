"""Sources de prix, avec de fausses réponses HTTP (données FICTIVES, aucun réseau)."""

import json
from decimal import Decimal as D
from pathlib import Path

import httpx2
import pytest

from app.providers import ProviderError, coingecko, frankfurter, yahoo
from tests.conftest import fake_client

FIXTURES = Path(__file__).parent / "fixtures"


def serve(payload, status=200, seen=None):
    """Faux serveur : renvoie `payload` et note les requêtes reçues dans `seen`."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        if seen is not None:
            seen.append(request)
        return httpx2.Response(status, json=payload)

    return fake_client(handler)


def test_yahoo_prix_exact_en_decimal():
    seen = []
    payload = json.loads((FIXTURES / "yahoo_chart.json").read_text(encoding="utf-8"))
    price = yahoo.get_price(serve(payload, seen=seen), "FAKE.PA")
    assert price.price == D("5.4321") and isinstance(price.price, D)  # jamais de float
    assert (price.currency, price.source) == ("EUR", "yahoo")
    assert price.previous_close == D("5.3")
    # Règle 3 : la requête ne contient que le ticker et des paramètres d'affichage.
    [req] = seen
    assert req.url.path == "/v8/finance/chart/FAKE.PA"
    assert dict(req.url.params) == {"range": "1d", "interval": "1d"}


def test_yahoo_ticker_inconnu():
    payload = {"chart": {"result": None, "error": {"code": "Not Found"}}}
    with pytest.raises(ProviderError, match="réponse HTTP 404"):
        yahoo.get_price(serve(payload, status=404), "NOPE.PA")
    with pytest.raises(ProviderError, match="introuvable"):
        yahoo.get_price(serve(payload), "NOPE.PA")


def test_yahoo_prix_nul_refuse():
    payload = {"chart": {"result": [{"meta": {"currency": "EUR", "regularMarketPrice": 0}}]}}
    with pytest.raises(ProviderError, match="prix invalide"):
        yahoo.get_price(serve(payload), "FAKE.PA")


def test_yahoo_devise_absente():
    """Yahoo renvoie parfois `currency` nul pour un indice (Euro Stoxx 50, 2026-11-16) :
    devise connue de l'indice, sinon une erreur claire, jamais une devise « NONE »."""
    from datetime import date

    payload = {
        "chart": {
            "result": [
                {
                    "meta": {"currency": None, "regularMarketPrice": 4000, "gmtoffset": 0},
                    "timestamp": [1748736000],
                    "indicators": {"quote": [{"close": [4000]}]},
                }
            ]
        }
    }
    assert yahoo.get_price(serve(payload), "^stoxx50e").currency == "EUR"
    currency, closes = yahoo.get_history(
        serve(payload), "^STOXX50E", date(2025, 6, 1), date(2025, 6, 2)
    )
    assert currency == "EUR" and closes == [(date(2025, 6, 1), D("4000"))]
    with pytest.raises(ProviderError, match="devise de \\^FAKE inconnue"):
        yahoo.get_price(serve(payload), "^FAKE")
    with pytest.raises(ProviderError, match="devise de \\^FAKE inconnue"):
        yahoo.get_history(serve(payload), "^FAKE", date(2025, 6, 1), date(2025, 6, 2))


def test_reseau_coupe():
    with pytest.raises(ProviderError, match="injoignable"):
        yahoo.get_price(fake_client(), "FAKE.PA")


def test_coingecko_avec_cle_dans_l_entete():
    seen = []
    payload = {"fakecoin": {"eur": 110, "eur_24h_change": 10}}
    price = coingecko.get_price(serve(payload, seen=seen), "fakecoin", "EUR", "cle-fictive")
    assert (price.price, price.currency, price.source) == (D("110"), "EUR", "coingecko")
    assert price.previous_close == D("100")  # 110 / (1 + 10 %)
    [req] = seen
    assert dict(req.url.params) == {
        "ids": "fakecoin",
        "vs_currencies": "eur",
        "include_24hr_change": "true",
    }
    assert req.headers["x-cg-demo-api-key"] == "cle-fictive"


def test_frankfurter_taux():
    seen = []
    payload = {"amount": 1.0, "base": "EUR", "date": "2025-01-03", "rates": {"USD": 1.0321}}
    rate_date, rates = frankfurter.get_rates(serve(payload, seen=seen), {"USD"})
    assert rate_date.isoformat() == "2025-01-03"
    assert rates == {"USD": D("1.0321")}
    assert dict(seen[0].url.params) == {"base": "EUR", "symbols": "USD"}
