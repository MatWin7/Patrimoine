"""Liste de comparaison, livrets et calendrier (D-042, D-044). Données FICTIVES."""

from datetime import date
from decimal import Decimal as D

from fastapi.testclient import TestClient

from app.analytics import blend, livret_closes, monthly_returns, yearly_returns
from app.main import app, http_client
from tests.conftest import fake_client
from tests.test_historique import FakeHistory, achat_fictif


def test_rendements_mensuels_et_annuels():
    closes = [
        (date(2025, 1, 31), D("100")),
        (date(2025, 2, 28), D("110")),
        (date(2025, 3, 3), D("99")),
    ]
    months = monthly_returns(closes)
    assert months == {"2025-02": D("0.1"), "2025-03": D("-0.1")}
    assert yearly_returns(months) == {"2025": D("1.1") * D("0.9") - 1}


def test_moyenne_ponderee_des_lignes():
    series = [(D("1"), {"2025-01": D("0.1")}), (D("3"), {"2025-01": D("0.2"), "2025-02": D("0.5")})]
    assert blend(series) == {
        "2025-01": D("0.175"),
        "2025-02": D("0.5"),
    }  # février : une seule ligne


def test_livret_capitalise_au_taux_annuel():
    closes = livret_closes(D("3"), date(2025, 1, 1), date(2026, 1, 1))
    assert closes[0] == (date(2025, 1, 1), D("1"))
    assert closes[-1] == (date(2026, 1, 1), D("1.03"))  # 365 jours = un an


def test_liste_modifiable(db_file):
    with TestClient(app) as client:
        start = client.get("/api/benchmarks").json()
        assert [b["ticker"] for b in start][:2] == ["IWDA.AS", "CW8.PA"]  # créée par le schéma
        nikkei = client.post(
            "/api/benchmarks", json={"kind": "indice", "label": "Nikkei", "ticker": "^n225"}
        ).json()
        livret = client.post(
            "/api/benchmarks", json={"kind": "livret", "label": "Livret A", "rate": "1,7"}
        )
        assert livret.status_code == 422  # le serveur attend un point décimal
        livret = client.post(
            "/api/benchmarks",
            json={"kind": "livret", "label": "Livret A", "rate": "1.7", "grp": "Livrets"},
        ).json()
        assert (nikkei["ticker"], livret["rate"], livret["ticker"]) == ("^N225", "1.7", None)
        bad = client.post("/api/benchmarks", json={"kind": "livret", "label": "Sans taux"})
        assert bad.status_code == 422
        client.post(f"/api/benchmarks/{livret['id']}/move", params={"step": -1})
        ids = [b["id"] for b in client.get("/api/benchmarks").json()]
        assert ids[-2:] == [livret["id"], nikkei["id"]]
        renamed = {"kind": "indice", "label": "Nikkei 225", "ticker": "^N225"}
        assert (
            client.patch(f"/api/benchmarks/{nikkei['id']}", json=renamed).json()["label"]
            == "Nikkei 225"
        )
        assert client.delete(f"/api/benchmarks/{nikkei['id']}").status_code == 204
        assert client.delete(f"/api/benchmarks/{nikkei['id']}").status_code == 404
        assert len(client.get("/api/benchmarks").json()) == len(start) + 1


def test_comparaison_a_un_livret_sans_reseau(db_file):
    server = FakeHistory({date(2025, 6, 2): 5.0, date(2025, 6, 3): 5.5})
    with TestClient(app) as client:
        achat_fictif(client)
        app.dependency_overrides[http_client] = lambda: fake_client(server)
        data = client.get("/api/benchmark", params={"rate": "3", "end": "2025-06-03"}).json()
    assert (data["ticker"], data["rate"], data["currency"]) == (None, "3", "EUR")
    assert data["points"][0]["benchmark"] == "50.00"
    assert data["points"][1]["benchmark"] == "50.00"  # un jour à 3 %/an : moins d'un centime
    # Seul l'historique de l'ETF a été demandé : le livret ne part pas sur le réseau.
    assert all("FAKE" in r.url.path for r in server.requests)


def test_calendrier_portefeuille_avec_mois_estimes(db_file):
    # Clôtures fictives de l'ETF avant et après l'achat du 2 juin.
    closes = {
        date(2023, 12, 29): 4.0,
        date(2024, 1, 31): 3.6,  # années pleines d'avant l'achat estimées aussi : -10 %
        date(2024, 12, 31): 4.0,
        date(2025, 1, 31): 4.4,  # janvier : +10 %
        date(2025, 2, 28): 4.4,
        date(2025, 3, 31): 4.4,
        date(2025, 4, 30): 4.4,
        date(2025, 5, 30): 5.0,
        date(2025, 6, 2): 5.0,
        date(2025, 6, 30): 5.5,
    }
    server = FakeHistory(closes)
    with TestClient(app) as client:
        achat_fictif(client)
        app.dependency_overrides[http_client] = lambda: fake_client(server)
        data = client.get("/api/calendar").json()
        index = client.get("/api/calendar", params={"ticker": "INDICE"}).json()
        livret = client.get("/api/calendar", params={"rate": "2"}).json()
    assert data["months"]["2025-01"] == "10.00"
    assert data["months"]["2024-01"] == "-10.00"
    assert data["estimated"] == [
        "2024-01",
        "2024-12",
        "2025-01",
        "2025-02",
        "2025-03",
        "2025-04",
        "2025-05",
    ]
    assert data["months"]["2025-06"] == "10.00"  # réel : 5,00 -> 5,50
    # 2024 : entièrement estimée, son total aussi. 2025 : seuls les mois réels comptent
    # (juin +10 %, puis rien d'autre), pas les estimations de janvier à mai.
    assert data["estimated_years"] == ["2024"]
    assert data["years"]["2025"] == "10.00"
    assert index["estimated"] == [] and index["months"]["2025-01"] == "10.00"
    assert livret["currency"] == "EUR" and len(livret["months"]) > 100
