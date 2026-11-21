"""Démo complète (D-140) : données FICTIVES qui remplissent chaque partie de l'app, hors ligne.

Garde-fou : si une fonctionnalité de l'app n'a plus rien à montrer dans la démo, un de ces tests
échoue. Une nouvelle fonctionnalité ajoute ici ce que la démo doit en montrer.
"""

from datetime import date
from decimal import Decimal as D

import pytest
from fastapi.testclient import TestClient

from app import db, demo
from app.main import app


@pytest.fixture
def client(db_file):
    db.init_db(db_file)
    with db.connect(db_file) as conn:
        demo.fill_demo(conn)
    conn.close()
    with TestClient(app) as c:
        yield c


def test_comptes_coherents(client):
    accounts = {a["name"]: a for a in client.get("/api/accounts").json()}
    assert {a["kind"] for a in accounts.values()} >= {
        "pea", "cto", "assurance_vie", "per", "livret", "immobilier", "autre",
    }  # fmt: skip
    for name in (
        "PEA démo",
        "CTO démo",
        "Assurance-vie démo",
        "PER démo",
        "Plateforme crypto démo",
    ):
        # Aucun achat sans assez d'espèces : pas de versement « supposé » (D-062).
        assert accounts[name]["deposits_implied"] == "0", name
    assert all(not a["cash"].startswith("-") for a in accounts.values())
    assert accounts["CTO démo"]["cash_date"] is not None  # espèces saisies (D-075)


def test_vue_d_ensemble(client):
    data = client.get("/api/portfolio").json()
    assert data["fx_error"] is None  # taux du dollar fictifs en base : rien à chercher
    assert len(data["positions"]) >= 14
    assert {g["label"] for g in data["groupings"]["devise"]["all"]} == {"EUR", "USD"}
    assert data["debts_eur"] != "0.00"  # prêt immobilier et prêt auto (D-107)
    scopes = {k: D(v) for k, v in data["scopes"].items()}  # brut, net, financier (D-143)
    assert scopes["financier"] < scopes["net"] < scopes["brut"]
    points = client.get("/api/history").json()["points"]
    assert points[0]["date"] < "2018-01-01"  # assurance-vie ouverte en 2017


def test_performance_sur_plusieurs_annees(client):
    periods = client.get("/api/performance?period=year").json()["periods"]
    assert len(periods) >= date.today().year - 2017 + 1
    assert any(p["performance"].startswith("-") for p in periods) or any(
        p["performance"].startswith("-")
        for p in client.get("/api/performance?period=month").json()["periods"]
    )  # des creux aussi


def test_revenus_et_frais(client):
    data = client.get("/api/income").json()
    assert data["dividends"]["last_12m"] != "0.00"
    assert data["dividends"]["withheld"] != "0.00"  # impôt retenu à la source (D-076)


def test_fiscalite(client):
    tax = client.get("/api/tax").json()
    assert tax["pea"][0]["milestone_reached"] is True  # PEA de plus de 5 ans
    assert tax["assurance_vie"][0]["milestone_reached"] is True  # AV de plus de 8 ans
    # Rachat partiel de 2025 : part de gains calculée, gains restants et rachat sans impôt (D-144).
    av = client.get("/api/tax?year=2025").json()["life_insurance"]
    assert av["buybacks"][0]["gain"] != "0.00" and not av["buybacks"][0]["estimated"]
    assert tax["assurance_vie"][0]["tax_free"] != "0.00"
    assert tax["per"]["deposits_year"] != "0.00"
    assert tax["cto"]["sales"]  # plus-value réalisée cette année


def test_saisie_et_reglages(client):
    pending = client.get("/api/recurring").json()["pending"]
    assert {p["frequency"] for p in pending} == {"mensuel", "bimensuel"}
    assert len(client.get("/api/debts").json()["debts"]) == 2
    assert any(r["target"] for r in client.get("/api/targets").json()["rows"])
    cats = client.get("/api/targets?level=categorie").json()["rows"]
    assert any(r.get("target") for r in cats)
    assert len(client.get("/api/watchlists").json()) == 2
    assets = client.get("/api/assets").json()
    assert any(a.get("sold_out") for a in assets)  # ligne vendue entièrement
    assert any(a.get("color") for a in assets)


def test_rien_ne_sort_pour_les_lignes_de_la_demo(db_file):
    """Tickers DEMO~* : cours et clôtures générés en local, jamais demandés en ligne (D-140)."""
    import httpx2

    from app.api.deps import http_client
    from tests.conftest import fake_client

    asked = []

    def spy(request: httpx2.Request) -> httpx2.Response:
        asked.append(str(request.url))
        return httpx2.Response(503)

    db.init_db(db_file)
    with db.connect(db_file) as conn:
        demo.fill_demo(conn)
    conn.close()
    app.dependency_overrides[http_client] = lambda: fake_client(spy)
    with TestClient(app) as c:
        quotes = c.get("/api/portfolio?force=true").json()["positions"]
        assert all(not p.get("price_error") for p in quotes)
        assert c.get("/api/history").json()["errors"] == []
        rows = c.get("/api/watchlists/lines/performance").json()["rows"]
        assert rows and all(r.get("error") is None and r["last"] for r in rows)
        assert c.get("/api/calendar").status_code == 200
    assert asked  # le reste (inflation, indices) part bien, au faux serveur
    assert not [u for u in asked if "demo" in u.lower()]


def test_variation_du_jour_de_la_demo(client):
    """Veille d'une ligne de démo = son avant-dernière clôture générée : la carte du portefeuille
    et l'alerte du jour ont une variation à montrer, sans rien demander en ligne."""
    positions = client.get("/api/portfolio").json()["positions"]
    assert any(p.get("day_change_pct") for p in positions)


def test_crypto_et_aave(client):
    tax = client.get("/api/tax").json()
    assert tax["crypto"]["sales"] and tax["crypto"]["swaps"] == 1  # vente imposable, échange
    assert [f["form"] for f in tax["foreign_accounts"]] == ["3916-bis"]  # plateforme étrangère
    assert tax["custody_unknown"] == []
    rewards = client.get("/api/crypto/rewards").json()
    assert {k["kind"] for k in rewards["kinds"]} >= {
        "earn",
        "staking",
        "cashback",
        "airdrop",
        "parrainage",
    }
    assert any(p["aave_line_id"] for p in rewards["placements"]) and rewards["idle"]
    assert rewards["fees"]["total"] != "0.00"
    lines = client.get("/api/aave/lines").json()
    assert len(lines) == 2 and any(m["verified"] for m in lines[0]["months"])
    borrows = client.get("/api/aave/borrows").json()
    assert borrows["markets"][0]["health_factor"] is not None  # dépôts face à l'emprunt
    positions = client.get("/api/portfolio").json()["positions"]
    assert any(p["account"] == "Portefeuille démo" for p in positions)  # transfert reçu
