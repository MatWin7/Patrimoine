"""Cache des prix et des taux, sur une base jetable et de fausses sources (données FICTIVES)."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D

import httpx2
import pytest
from fastapi.testclient import TestClient

from app import db, prices, repository
from app.main import app, http_client
from tests.conftest import fake_client

TTL = timedelta(minutes=5)


class FakeYahoo:
    """Faux Yahoo : compte les appels, renvoie un prix réglable ou une erreur 500."""

    def __init__(self, price="5.50", previous=None):
        self.price = price
        self.previous = previous
        self.requests = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if self.price is None:
            return httpx2.Response(500)
        meta = {"currency": "EUR", "regularMarketPrice": float(self.price)}
        if self.previous:
            meta["chartPreviousClose"] = float(self.previous)
        return httpx2.Response(200, json={"chart": {"result": [{"meta": meta}]}})


@pytest.fixture
def conn(db_file):
    db.init_db()
    with db.connect() as c:
        yield c
    c.close()


@pytest.fixture
def etf(conn):
    return repository.create_asset(conn, "ETF fictif", "etf", None, "FAKE.PA", None, "EUR")


def quote(conn, etf, server, now, force=False):
    return prices.get_quotes(conn, [etf], fake_client(server), ttl=TTL, now=now, force=force)[
        etf.id
    ]


def test_cache_de_cinq_minutes(conn, etf):
    server = FakeYahoo()
    t0 = datetime.now(UTC)
    assert quote(conn, etf, server, t0).fetched
    assert not quote(conn, etf, server, t0 + timedelta(minutes=4)).fetched  # cache
    assert len(server.requests) == 1
    assert quote(conn, etf, server, t0 + timedelta(minutes=6)).fetched  # cache expiré
    assert len(server.requests) == 2


def test_force_ignore_le_cache(conn, etf):
    server = FakeYahoo()
    t0 = datetime.now(UTC)
    quote(conn, etf, server, t0)
    assert quote(conn, etf, server, t0, force=True).fetched
    assert len(server.requests) == 2


def test_source_en_panne_garde_le_dernier_prix(conn, etf):
    server = FakeYahoo("5.50")
    t0 = datetime.now(UTC)
    quote(conn, etf, server, t0)
    server.price = None
    q = quote(conn, etf, server, t0 + timedelta(hours=1))
    assert q.price.price == D("5.5") and not q.fetched
    assert "500" in q.error


def test_taux_euro_seul_sans_reseau(conn):
    rates, error = prices.get_rates(
        conn, {"EUR"}, fake_client(), today=date.today(), may_fetch=True
    )
    assert (rates, error) == ({"EUR": D("1")}, None)  # fake_client() lèverait au moindre appel


def test_taux_manquant_puis_cache(conn):
    calls = []

    def frankfurter(request):
        calls.append(request)
        return httpx2.Response(200, json={"date": "2025-01-03", "rates": {"USD": 1.25}})

    today = date(2025, 1, 6)
    rates, _ = prices.get_rates(
        conn, {"USD"}, fake_client(frankfurter), today=today, may_fetch=False
    )
    assert rates["USD"] == D("1.25") and len(calls) == 1  # manquant : appel même sans may_fetch
    prices.get_rates(conn, {"USD"}, fake_client(frankfurter), today=today, may_fetch=False)
    assert len(calls) == 1  # taux ancien mais prix non rafraîchis : pas d'appel
    prices.get_rates(conn, {"USD"}, fake_client(frankfurter), today=today, may_fetch=True)
    assert len(calls) == 2


def test_variation_du_jour_nulle_affichee_a_zero(db_file):
    """Cours égal à la veille : 0,00 %, pas « inconnu » (D-039)."""
    server = FakeYahoo("6.00", previous="6.00")
    with TestClient(app) as client:
        account = client.post("/api/accounts", json={"name": "PEA test", "kind": "pea"}).json()
        asset = client.post(
            "/api/assets", json={"name": "ETF fictif", "asset_class": "etf", "ticker": "fake.pa"}
        ).json()
        client.post(
            "/api/operations",
            json={
                "account_id": account["id"],
                "asset_id": asset["id"],
                "op_date": "2025-01-02",
                "op_type": "achat",
                "quantity": "10",
                "unit_price": "5.00",
            },
        )
        app.dependency_overrides[http_client] = lambda: fake_client(server)
        [pos] = client.get("/api/portfolio").json()["positions"]
    assert (pos["day_change"], pos["day_change_pct"]) == ("0.00", "0.00")


def test_api_portfolio_au_cours_en_ligne(db_file):
    server = FakeYahoo("6.00", previous="5.80")
    with TestClient(app) as client:
        # 51 € d'espèces, tout juste dépensés par l'achat : le total ne contient que les titres.
        account = client.post(
            "/api/accounts", json={"name": "PEA test", "kind": "pea", "cash": "51"}
        ).json()
        asset = client.post(
            "/api/assets", json={"name": "ETF fictif", "asset_class": "etf", "ticker": "fake.pa"}
        ).json()
        client.post(
            "/api/operations",
            json={
                "account_id": account["id"],
                "asset_id": asset["id"],
                "op_date": "2025-01-02",
                "op_type": "achat",
                "quantity": "10",
                "unit_price": "5.00",
                "fees": "1.00",
            },
        )
        app.dependency_overrides[http_client] = lambda: fake_client(server)
        data = client.get("/api/portfolio").json()
        again = client.get("/api/portfolio").json()  # servi par le cache

    [pos] = data["positions"]
    assert (pos["day_change"], pos["day_change_pct"]) == ("2.00", "3.45")  # 10 × (6,00 - 5,80)
    assert again["positions"][0]["day_change"] == "2.00"  # veille gardée en mémoire
    assert (pos["last_price"], pos["value"], pos["price_source"]) == ("6.0", "60.00", "yahoo")
    assert data["totals"] == {
        "EUR": {
            "value": "60.00",
            "cost": "51.00",
            "gain": "9.00",
            "gain_pct": "17.65",
            "day_change": "2.00",
            "day_change_pct": "3.45",
        }
    }
    # Règle 3 : seul le ticker est parti, ni la quantité (10) ni le montant investi (51).
    [req] = server.requests
    assert (
        str(req.url)
        == "https://query1.finance.yahoo.com/v8/finance/chart/FAKE.PA?range=1d&interval=1d"
    )
    assert req.content == b""


def test_api_tester_un_ticker(db_file):
    server = FakeYahoo("7.25")
    app.dependency_overrides[http_client] = lambda: fake_client(server)
    with TestClient(app) as client:
        res = client.get("/api/quote", params={"ticker": " fake.pa "})
        assert res.json() == {"price": "7.25", "currency": "EUR", "source": "yahoo"}
        assert server.requests[0].url.path == "/v8/finance/chart/FAKE.PA"
        server.price = None
        assert client.get("/api/quote", params={"ticker": "X.PA"}).status_code == 404
        assert client.get("/api/quote").status_code == 422
