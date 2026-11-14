"""Listes de suivi de l'onglet Visualisations (D-072). Données FICTIVES."""

from datetime import date, timedelta
from decimal import Decimal as D

import pytest
from fastapi.testclient import TestClient

from app import watchlist as watch
from app.main import app, http_client
from tests.conftest import fake_client
from tests.test_historique import FakeHistory, achat_fictif


def test_dates_de_depart():
    assert watch.month_before(date(2026, 3, 31)) == date(2026, 2, 28)
    assert watch.years_before(date(2028, 2, 29), 1) == date(2027, 2, 28)
    starts = watch.start_dates(date(2026, 10, 22))
    assert starts["ytd"] == date(2025, 12, 31) and starts["1m"] == date(2026, 9, 22)


def test_rendements_par_periode():
    today = date(2026, 10, 22)
    closes = [
        (date(2025, 10, 22), D("80")),  # il y a un an pile
        (date(2025, 12, 31), D("90")),  # fin de l'année précédente
        (date(2026, 9, 21), D("95")),  # le 22 septembre est un jour sans cotation
        (date(2026, 10, 21), D("99")),
        (date(2026, 10, 22), D("100")),
    ]
    r = watch.period_returns(closes, today)
    assert r["1j"] == D("100") / D("99") - 1
    assert r["1m"] == D("100") / D("95") - 1  # dernière clôture avant le départ
    assert r["ytd"] == D("100") / D("90") - 1
    assert r["1a"] == D("0.25")
    assert r["3a"] is None and r["5a"] is None  # historique trop court
    assert watch.period_returns([], today) == dict.fromkeys(watch.PERIODS)


def test_listes_et_performance(db_file):
    today = date.today()
    server = FakeHistory({today - timedelta(days=400): 50.0, today: 60.0})
    app.dependency_overrides[http_client] = lambda: fake_client(server)
    with TestClient(app) as client:
        liste = client.post("/api/watchlists", json={"name": " À surveiller "}).json()
        assert liste == {"id": liste["id"], "name": "À surveiller", "items": []}
        assert client.post("/api/watchlists", json={"name": "À surveiller"}).status_code == 409
        url = f"/api/watchlists/{liste['id']}/items"
        added = client.post(url, json={"label": "Action fictive", "ticker": "fake.pa"}).json()
        assert added["items"][0]["ticker"] == "FAKE.PA"
        assert client.post(url, json={"label": "Doublon", "ticker": "FAKE.PA"}).status_code == 409
        assert client.post(url, json={"label": "Mauvais", "ticker": "A B"}).status_code == 422

        perf = client.get(f"/api/watchlists/{liste['id']}/performance").json()
        [row] = perf["rows"]
        assert (row["label"], row["last"], row["currency"]) == ("Action fictive", "60.00", "EUR")
        assert row["1a"] == "20.00" and row["5a"] is None and row["error"] is None
        # Règle 3 : seul le ticker (et deux dates) part vers Yahoo.
        assert {r.url.path for r in server.requests} == {"/v8/finance/chart/FAKE.PA"}

        renamed = client.patch(f"/api/watchlists/{liste['id']}", json={"name": "ETF"}).json()
        assert renamed["name"] == "ETF"
        item_id = added["items"][0]["id"]
        assert client.delete(f"{url}/{item_id}").status_code == 204
        assert client.delete(f"/api/watchlists/{liste['id']}").status_code == 204
        assert client.get("/api/watchlists").json() == []


def test_mes_lignes(db_file):
    server = FakeHistory({date.today() - timedelta(days=3): 5.0, date.today(): 5.5})
    app.dependency_overrides[http_client] = lambda: fake_client(server)
    with TestClient(app) as client:
        achat_fictif(client)
        client.post(
            "/api/assets", json={"name": "Fonds euros fictif", "asset_class": "fonds_euros"}
        )
        rows = client.get("/api/watchlists/lines/performance").json()["rows"]
    assert [r["label"] for r in rows] == ["ETF fictif"]  # non détenu ou sans cours : absent
    assert rows[0]["1j"] == "10.00"


def test_panne_yahoo_sur_une_ligne(db_file):
    app.dependency_overrides[http_client] = lambda: fake_client(FakeHistory(None))
    with TestClient(app) as client:
        liste = client.post("/api/watchlists", json={"name": "Test"}).json()
        client.post(
            f"/api/watchlists/{liste['id']}/items", json={"label": "Indice", "ticker": "^FAKE"}
        )
        [row] = client.get(f"/api/watchlists/{liste['id']}/performance").json()["rows"]
    assert row["error"] and row["last"] is None


def test_mes_lignes_avec_une_crypto(db_file, monkeypatch):
    """Une crypto (id CoinGecko, sans ticker Yahoo) a son rendement, pas « indisponible »."""
    today = date.today()
    appels = []

    def coin_history(client, coin_id, day, api_key=None, sleep=None):
        appels.append(coin_id)
        return [(today - timedelta(days=400 - i), D(100 + i)) for i in range(401)]

    monkeypatch.setattr("app.prices.coin_history", coin_history)
    app.dependency_overrides[http_client] = lambda: fake_client(FakeHistory(None))
    with TestClient(app) as client:
        account = client.post(
            "/api/accounts", json={"name": "Crypto test", "kind": "crypto"}
        ).json()
        asset = client.post(
            "/api/assets",
            json={"name": "Jeton fictif", "asset_class": "crypto", "coingecko_id": "fictif"},
        ).json()
        client.post(
            "/api/operations",
            json={
                "account_id": account["id"],
                "asset_id": asset["id"],
                "op_date": (today - timedelta(days=5)).isoformat(),
                "op_type": "achat",
                "quantity": "1",
                "unit_price": "100",
                "fees": "0",
            },
        )
        [row] = client.get("/api/watchlists/lines/performance").json()["rows"]
    assert appels == ["fictif"]  # seul l'id part (règle 3)
    assert row["error"] is None and row["currency"] == "EUR" and row["last"] == "500.00"
    assert row["1j"] is not None and row["1a"] is not None
    assert row["3a"] is None  # CoinGecko : 365 jours seulement


def test_ordre_des_actifs_d_une_liste(db_file):
    with TestClient(app) as client:
        liste = client.post("/api/watchlists", json={"name": "Ordre"}).json()
        url = f"/api/watchlists/{liste['id']}/items"
        for label, ticker in (("A", "AAA"), ("B", "BBB"), ("C", "CCC")):
            added = client.post(url, json={"label": label, "ticker": ticker}).json()
        ids = [i["id"] for i in added["items"]]
        assert client.put(f"{url}/order", json={"ids": ids[::-1]}).status_code == 204
        [liste] = client.get("/api/watchlists").json()
        assert [i["label"] for i in liste["items"]] == ["C", "B", "A"]
        # Liste incomplète : refusée, rien ne change.
        assert client.put(f"{url}/order", json={"ids": ids[:2]}).status_code == 409
        assert client.put("/api/watchlists/999/items/order", json={"ids": []}).status_code == 404


def test_crypto_sans_historique_garde_le_cours_du_jour(db_file, monkeypatch):
    """CoinGecko limite l'historique sans clé (429) : la ligne montre le cours du jour."""
    from datetime import UTC, datetime

    from app.providers import Price

    monkeypatch.setattr("app.prices.coin_history", lambda *a, **k: [])
    monkeypatch.setattr(
        "app.main.coingecko.get_price",
        lambda *a, **k: Price(D("110"), "EUR", "coingecko", datetime.now(UTC), D("100")),
    )
    app.dependency_overrides[http_client] = lambda: fake_client(FakeHistory(None))
    with TestClient(app) as client:
        account = client.post(
            "/api/accounts", json={"name": "Crypto test", "kind": "crypto"}
        ).json()
        asset = client.post(
            "/api/assets",
            json={"name": "Jeton fictif", "asset_class": "crypto", "coingecko_id": "fictif"},
        ).json()
        client.post(
            "/api/operations",
            json={
                "account_id": account["id"],
                "asset_id": asset["id"],
                "op_date": (date.today() - timedelta(days=5)).isoformat(),
                "op_type": "achat",
                "quantity": "1",
                "unit_price": "100",
                "fees": "0",
            },
        )
        [row] = client.get("/api/watchlists/lines/performance").json()["rows"]
    assert row["error"] is None and row["last"] == "110.00" and row["1j"] == "10.00"
    assert row["1a"] is None and "Historique indisponible" in row["note"]


# --- Cryptos par Yahoo (D-111) ----------------------------------------------------------


def test_table_des_tickers_crypto():
    from app.crypto_tickers import YAHOO_TICKERS, resolve_crypto, yahoo_ticker

    assert all(t.endswith(("-EUR", "-USD")) and t == t.upper() for t in YAHOO_TICKERS.values())
    assert yahoo_ticker(None, "bitcoin") == "BTC-EUR"
    assert yahoo_ticker("BTC-USD", "bitcoin") == "BTC-USD"  # le ticker saisi l'emporte
    assert yahoo_ticker(None, "jeton-fictif") is None
    assert resolve_crypto("Bitcoin") == resolve_crypto("btc") == "BTC-EUR"
    assert resolve_crypto("ARB") == "ARB11841-USD"  # « ARB-USD » est un autre jeton chez Yahoo
    assert resolve_crypto("fake-usd") == "FAKE-USD" and resolve_crypto("inconnu") is None


def _crypto_detenue(client, coingecko_id, ticker=None):
    account = client.post("/api/accounts", json={"name": "Crypto test", "kind": "crypto"}).json()
    body = {"name": "Jeton test", "asset_class": "crypto", "coingecko_id": coingecko_id}
    asset = client.post("/api/assets", json=body | ({"ticker": ticker} if ticker else {})).json()
    client.post(
        "/api/operations",
        json={
            "account_id": account["id"],
            "asset_id": asset["id"],
            "op_date": (date.today() - timedelta(days=5)).isoformat(),
            "op_type": "achat",
            "quantity": "1",
            "unit_price": "100",
            "fees": "0",
        },
    )


def test_mes_lignes_crypto_connue_par_yahoo(db_file, monkeypatch):
    """Une crypto de la table passe par Yahoo : 5 ans d'historique, sans appel à CoinGecko."""
    today = date.today()
    monkeypatch.setattr("app.prices.coin_history", lambda *a, **k: pytest.fail("CoinGecko"))
    server = FakeHistory({today - timedelta(days=2000): 20.0, today: 100.0})
    app.dependency_overrides[http_client] = lambda: fake_client(server)
    with TestClient(app) as client:
        _crypto_detenue(client, "bitcoin")
        [row] = client.get("/api/watchlists/lines/performance").json()["rows"]
    assert row["ticker"] == "BTC-EUR" and row["error"] is None
    assert row["5a"] == "400.00"  # 20 -> 100
    assert "/v8/finance/chart/BTC-EUR" in {r.url.path for r in server.requests}


def test_mes_lignes_crypto_yahoo_en_panne_repli_coingecko(db_file, monkeypatch):
    today = date.today()
    monkeypatch.setattr(
        "app.prices.coin_history",
        lambda *a, **k: [(today - timedelta(days=1), D("90")), (today, D("99"))],
    )
    app.dependency_overrides[http_client] = lambda: fake_client(FakeHistory(None))
    with TestClient(app) as client:
        _crypto_detenue(client, "bitcoin")
        [row] = client.get("/api/watchlists/lines/performance").json()["rows"]
    assert row["error"] is None and row["1j"] == "10.00"
    assert row["ticker"] is None  # pas de bouton Calendrier sans clôtures Yahoo


def test_ajout_d_une_crypto_dans_une_liste(db_file):
    app.dependency_overrides[http_client] = lambda: fake_client(FakeHistory(None))
    with TestClient(app) as client:
        liste = client.post("/api/watchlists", json={"name": "Cryptos"}).json()
        url = f"/api/watchlists/{liste['id']}/items"

        def ajout(texte, crypto=True):
            return client.post(url, json={"label": texte, "ticker": texte, "crypto": crypto})

        assert ajout("bitcoin").json()["items"][-1]["ticker"] == "BTC-EUR"
        assert ajout("ETH").json()["items"][-1]["ticker"] == "ETH-EUR"
        assert ajout("polygon-ecosystem-token").json()["items"][-1]["ticker"] == "POL28321-EUR"
        assert ajout("btc").status_code == 409  # déjà là sous BTC-EUR
        refus = ajout("jeton-inconnu")
        assert refus.status_code == 400 and "ticker Yahoo" in refus.json()["detail"]
        # En bourse, « SOL » reste un ticker (action américaine), pas Solana.
        assert ajout("sol", crypto=False).json()["items"][-1]["ticker"] == "SOL"


def test_cours_du_jour_crypto_repli_yahoo(monkeypatch):
    """CoinGecko refuse : le cours vient de Yahoo si la devise correspond, sinon l'erreur."""
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from app import prices
    from app.providers import Price, ProviderError

    def refus(*a, **k):
        raise ProviderError("réponse HTTP 429")

    monkeypatch.setattr("app.prices.coingecko.get_price", refus)
    yahoo_eur = Price(D("60000"), "EUR", "yahoo", datetime.now(UTC), None)
    monkeypatch.setattr("app.prices.yahoo.get_price", lambda client, t: yahoo_eur)
    btc = SimpleNamespace(coingecko_id="bitcoin", ticker=None, currency="EUR")
    assert prices.fetch_price(None, btc, None) is yahoo_eur
    with pytest.raises(ProviderError, match="429"):
        prices.fetch_price(None, SimpleNamespace(**vars(btc) | {"currency": "USD"}), None)
    with pytest.raises(ProviderError, match="429"):
        prices.fetch_price(None, SimpleNamespace(**vars(btc) | {"coingecko_id": "fictif"}), None)
