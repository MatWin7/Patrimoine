"""Historique des clôtures et API des graphiques (données FICTIVES, aucun réseau)."""

from datetime import UTC, date, datetime
from decimal import Decimal as D

import httpx2
import pytest
from fastapi.testclient import TestClient

from app import db, prices, repository
from app.main import app, http_client
from app.providers import ProviderError, yahoo
from tests.conftest import fake_client

PARIS = 7200  # gmtoffset de Yahoo pour Euronext Paris en été (UTC+2), en secondes


def stamp(day: date) -> int:
    """Horodatage Yahoo d'une séance parisienne : 9 h, heure de Paris."""
    return int(datetime(day.year, day.month, day.day, 7, tzinfo=UTC).timestamp())


class FakeHistory:
    """Faux Yahoo : clôtures réglables, ou erreur 500 si `closes` vaut None."""

    def __init__(self, closes):
        self.closes = closes  # {date: float ou None}
        self.requests = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if self.closes is None:
            return httpx2.Response(500)
        days = sorted(self.closes)
        result = {
            "meta": {"currency": "EUR", "gmtoffset": PARIS, "regularMarketPrice": 6.0},
            "timestamp": [stamp(d) for d in days],
            "indicators": {"quote": [{"close": [self.closes[d] for d in days]}]},
        }
        return httpx2.Response(200, json={"chart": {"result": [result]}})


def test_yahoo_historique_en_decimal():
    server = FakeHistory({date(2025, 6, 2): 5.1234, date(2025, 6, 3): None, date(2025, 6, 4): 5.2})
    currency, closes = yahoo.get_history(
        fake_client(server), "FAKE.PA", date(2025, 6, 1), date(2025, 6, 4)
    )
    assert currency == "EUR"
    assert closes == [(date(2025, 6, 2), D("5.1234")), (date(2025, 6, 4), D("5.2"))]  # null ignoré
    [req] = server.requests
    assert req.url.path == "/v8/finance/chart/FAKE.PA"
    # Règle 3 : un ticker et deux dates, rien d'autre.
    assert dict(req.url.params) == {
        "period1": "1748736000",
        "period2": "1749081600",
        "interval": "1d",
    }


def test_yahoo_historique_illisible():
    def handler(request):
        return httpx2.Response(200, json={"chart": {"result": None}})

    with pytest.raises(ProviderError, match="historique"):
        yahoo.get_history(fake_client(handler), "FAKE.PA", date(2025, 6, 1), date(2025, 6, 4))


@pytest.fixture
def conn(db_file):
    db.init_db()
    with db.connect() as c:
        yield c
    c.close()


def test_historique_complete_une_fois_par_jour(conn):
    etf = repository.create_asset(conn, "ETF fictif", "etf", None, "FAKE.PA", None, "EUR")
    server = FakeHistory({date(2025, 6, 2): 5.0, date(2025, 6, 3): 5.1})
    today = date(2025, 6, 3)
    assert (
        prices.update_history(conn, [(etf, date(2025, 6, 2))], fake_client(server), today=today)
        == {}
    )
    prices.update_history(conn, [(etf, date(2025, 6, 2))], fake_client(server), today=today)
    assert len(server.requests) == 1  # déjà complété aujourd'hui
    assert prices.load_history(conn, etf.id) == [
        (date(2025, 6, 2), D("5.0"), "EUR"),
        (date(2025, 6, 3), D("5.1"), "EUR"),
    ]

    # Le lendemain : seuls les derniers jours sont redemandés, la clôture du 3 est corrigée.
    server.closes = {date(2025, 6, 3): 5.15, date(2025, 6, 4): 5.3}
    prices.update_history(
        conn, [(etf, date(2025, 6, 2))], fake_client(server), today=date(2025, 6, 4)
    )
    assert server.requests[1].url.params["period1"] == str(stamp(date(2025, 5, 29)) - 7 * 3600)
    assert [c for _, c, _ in prices.load_history(conn, etf.id)] == [D("5.0"), D("5.15"), D("5.3")]

    # Source en panne : l'historique stocké reste, l'erreur est renvoyée.
    server.closes = None
    errors = prices.update_history(
        conn, [(etf, date(2025, 6, 2))], fake_client(server), today=date(2025, 6, 5)
    )
    assert "HTTP 500" in errors[etf.id]
    assert len(prices.load_history(conn, etf.id)) == 3


def test_changement_de_ticker_efface_l_historique(conn):
    etf = repository.create_asset(conn, "ETF fictif", "etf", None, "FAKE.PA", None, "EUR")
    server = FakeHistory({date(2025, 6, 2): 5.0})
    prices.update_history(
        conn, [(etf, date(2025, 6, 2))], fake_client(server), today=date(2025, 6, 2)
    )
    repository.update_asset(conn, etf.id, "ETF fictif", "etf", None, "AUTRE.PA", None, "EUR")
    assert prices.load_history(conn, etf.id) == []


def test_api_historique_et_repartition(db_file):
    today = datetime.now(UTC).date()
    start = date(2025, 6, 2)
    server = FakeHistory({start: 5.0, date(2025, 6, 3): 5.5})
    with TestClient(app) as client:
        account = client.post(
            "/api/accounts", json={"name": "PEA test", "kind": "pea", "deposits_before": "71"}
        ).json()
        asset = client.post(
            "/api/assets", json={"name": "ETF fictif", "asset_class": "etf", "ticker": "fake.pa"}
        ).json()
        client.post(
            "/api/operations",
            json={
                "account_id": account["id"],
                "asset_id": asset["id"],
                "op_date": start.isoformat(),
                "op_type": "achat",
                "quantity": "10",
                "unit_price": "5.00",
                "fees": "1.00",
            },
        )
        offline = client.get("/api/history").json()
        app.dependency_overrides[http_client] = lambda: fake_client(server)
        data = client.get("/api/history").json()
        portfolio = client.get("/api/portfolio").json()

    assert offline["without_history"] == ["ETF fictif"]
    assert offline["errors"] == ["ETF fictif : source injoignable (ConnectError)"]
    assert offline["points"][0] == {
        "date": "2025-06-02",
        "value": "50.00",
        "invested": "51.00",
        # Hors ligne : l'inflation suit le taux saisi (2 % par an par défaut, D-067).
        "invested_eroded": "51.00",
        "gain": "-1.00",
        "inflation_loss": "0.00",
        "inflation_pct": "2.0",  # hors ligne : taux saisi (D-112)
    }
    assert offline["inflation"]["last_month"] is None and offline["inflation"]["rate"] == "2"

    assert data["errors"] == [] and data["without_history"] == []
    assert [(p["date"], p["value"], p["invested"], p["gain"]) for p in data["points"][:2]] == [
        ("2025-06-02", "50.00", "51.00", "-1.00"),
        ("2025-06-03", "55.00", "51.00", "4.00"),
    ]
    assert data["points"][-1]["date"] == today.isoformat()
    # Règle 3 : la requête d'historique ne contient ni quantité ni montant.
    assert server.requests[0].content == b""
    assert set(server.requests[0].url.params) == {"period1", "period2", "interval"}

    assert portfolio["allocation"] == [
        {"label": "ETF", "value": "60.00", "pct": "75.00"},  # 10 × 6,00 (cours en ligne)
        {"label": "Espèces", "value": "20.00", "pct": "25.00"},
    ]
    assert portfolio["positions"][0]["gain_eur"] == "9.00"
    # Détail d'une part : les lignes de la classe, en % de la classe.
    assert portfolio["allocation_detail"]["ETF"] == [
        {"label": "ETF fictif", "value": "60.00", "pct": "100.00"}
    ]
    assert portfolio["allocation_detail"]["Espèces"][0]["pct"] == "100.00"


def test_api_performance_par_jour(db_file):
    server = FakeHistory({date(2025, 6, 2): 5.0, date(2025, 6, 3): 5.5})
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
                "op_date": "2025-06-02",
                "op_type": "achat",
                "quantity": "10",
                "unit_price": "5.00",
                "fees": "1.00",
            },
        )
        app.dependency_overrides[http_client] = lambda: fake_client(server)
        params = {"period": "day", "end": "2025-06-03"}
        data = client.get("/api/performance", params=params).json()
        inverse = client.get("/api/performance", params={**params, "start": "2025-06-04"})
        unknown = client.get("/api/performance", params={"period": "siecle"})

    first, second = data["periods"]
    # Jour de l'achat : 51 € apportés (frais compris), 50 € de valeur le soir.
    assert (first["flows"], first["gain"], first["performance"]) == ("51.00", "-1.00", "-1.96")
    assert (second["gain"], second["performance"]) == ("5.00", "10.00")
    assert (second["cumulative_gain"], second["cumulative_performance"]) == ("4.00", "7.84")
    summary = data["summary"]
    assert (summary["gain"], summary["performance"], summary["annualized"]) == (
        "4.00",
        "7.84",
        None,
    )
    assert summary["best"] == {"label": "2025-06-03", "performance": "10.00"}
    assert summary["max_drawdown"] == "-1.96"
    assert inverse.status_code == 422 and unknown.status_code == 422


def achat_fictif(client, day="2025-06-02"):
    account = client.post("/api/accounts", json={"name": "PEA test", "kind": "pea"}).json()
    asset = client.post(
        "/api/assets", json={"name": "ETF fictif", "asset_class": "etf", "ticker": "fake.pa"}
    ).json()
    client.post(
        "/api/operations",
        json={
            "account_id": account["id"],
            "asset_id": asset["id"],
            "op_date": day,
            "op_type": "achat",
            "quantity": "10",
            "unit_price": "5.00",
            "fees": "0",
        },
    )


def test_api_export_csv(db_file):
    server = FakeHistory({date(2025, 6, 2): 5.0, date(2025, 6, 3): 5.5})
    with TestClient(app) as client:
        achat_fictif(client)
        app.dependency_overrides[http_client] = lambda: fake_client(server)
        res = client.get("/api/performance.csv", params={"period": "day", "end": "2025-06-03"})
    assert res.headers["content-type"] == "text/csv; charset=utf-8"
    assert "attachment" in res.headers["content-disposition"]
    lines = res.text.lstrip("\ufeff").splitlines()
    assert lines[0].startswith("Période;Du;Au;")
    assert lines[2] == "2025-06-03;2025-06-03;2025-06-03;50,00;0,00;55,00;5,00;10,00;5,00;10,00"


def test_api_indice(db_file):
    portefeuille = FakeHistory({date(2025, 6, 2): 5.0, date(2025, 6, 3): 5.5})
    indice = FakeHistory({date(2025, 6, 2): 100.0, date(2025, 6, 3): 102.0})
    seen = []

    def handler(request):
        seen.append(request)
        server = indice if "INDICE" in request.url.path else portefeuille
        return server(request)

    with TestClient(app) as client:
        achat_fictif(client)
        app.dependency_overrides[http_client] = lambda: fake_client(handler)
        data = client.get("/api/benchmark", params={"ticker": "indice", "end": "2025-06-03"})
        bad = client.get("/api/benchmark", params={"ticker": "a b"})
    assert bad.status_code == 422
    body = data.json()
    assert body["points"] == [
        {"date": "2025-06-02", "portfolio": "50.00", "benchmark": "50.00"},
        {"date": "2025-06-03", "portfolio": "55.00", "benchmark": "51.00"},
    ]
    assert body["summary"]["difference"] == "8.00"  # +10 % contre +2 %
    assert body["summary"]["value_difference"] == "7.84"  # 55 € réels contre 51 € dans l'indice
    # Règle 3 : la requête vers l'indice ne contient que son ticker et des dates.
    [req] = [r for r in seen if "INDICE" in r.url.path]
    assert set(req.url.params) == {"period1", "period2", "interval"} and req.content == b""


def test_api_projection(db_file):
    with TestClient(app) as client:
        data = client.get(
            "/api/projection",
            params={"monthly": "100", "years": "1", "target": "600", "rates": "0;6"},
        ).json()
        bad = client.get("/api/projection", params={"rates": "abc"})
    assert bad.status_code == 422
    assert data["start_value"] == "0.00" and len(data["months"]) == 12
    zero, six = data["scenarios"]
    assert (zero["rate"], zero["final"], zero["reach"]) == ("0.00", "1200.00", data["months"][5])
    assert data["invested"][-1] == "1200.00"
    assert D(six["final"]) > D("1200")
