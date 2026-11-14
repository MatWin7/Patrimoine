"""Fiscalité du PER (D-064) : versements de l'année face au plafond, impôt économisé, impôt si
sortie en capital. Données fictives."""

from decimal import Decimal as D

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.tax import pea_exit, per_exit, per_year


def test_plafond_et_economie_d_impot():
    st = per_year(D("5000"), D("4000"), D("30"))
    assert (st["deductible"], st["over"], st["remaining"]) == (D("4000"), D("1000"), D("0"))
    assert st["saving"] == D("1200")
    assert not st["low_tmi"]
    st = per_year(D("1000"), None, D("11"))  # sans plafond saisi : tout est déductible
    assert (st["deductible"], st["remaining"], st["saving"]) == (D("1000"), None, D("110"))
    assert st["low_tmi"]


def test_sortie_en_capital():
    out = per_exit(D("12000"), D("10000"), D("30"), D("30"))
    # 10 000 versés imposés à 30 % + 2 000 de gains au prélèvement forfaitaire de 30 %.
    assert (out["tax_paid_in"], out["tax_gains"], out["tax"]) == (D("3000"), D("600"), D("3600"))
    assert out["net"] == D("8400")
    out = per_exit(D("8000"), D("10000"), D("11"), D("30"))  # en perte : pas de gain imposé
    assert (out["gains"], out["tax"], out["net"]) == (D("-2000"), D("880"), D("7120"))


@pytest.fixture
def client(db_file):
    with TestClient(app) as c:
        yield c


def post(client, path, body):
    res = client.post(path, json=body)
    assert res.status_code == 201, res.text
    return res.json()


def test_carte_per(client):
    per = post(client, "/api/accounts", {"name": "PER fictif", "kind": "per",
                                         "deposits_before": "5000"})  # fmt: skip
    etf = post(client, "/api/assets", {"name": "ETF", "asset_class": "etf", "ticker": "E.PA"})
    buy = {"account_id": per["id"], "asset_id": etf["id"], "op_type": "achat", "fees": "0"}
    post(client, "/api/operations", {**buy, "op_date": "2024-02-01", "quantity": "40",
                                     "unit_price": "100"})  # fmt: skip
    post(client, "/api/operations", {"account_id": per["id"], "op_type": "versement",
                                     "op_date": "2025-03-01", "amount": "1000"})  # fmt: skip
    post(client, "/api/operations", {**buy, "op_date": "2025-04-01", "quantity": "30",
                                     "unit_price": "120"})  # fmt: skip
    data = client.get("/api/tax?year=2025&tmi=30&pfu=30&per_ceiling=2000").json()["per"]
    [row] = data["rows"]
    # 1 000 versés + 1 600 que l'achat de 3 600 suppose (il ne restait que 2 000 d'espèces).
    assert (row["deposits"], row["deposits_year"]) == ("7600.00", "2600.00")
    assert (row["value"], row["gains"]) == ("8400.00", "800.00")  # 70 parts au dernier prix
    assert (row["tax"], row["net"]) == ("2520.00", "5880.00")
    assert (data["over"], data["remaining"], data["saving"]) == ("600.00", "0.00", "600.00")
    assert not data["low_tmi"]
    # Le champ « Versements » sans date compte pour les années passées, pas pour 2024.
    data = client.get("/api/tax?year=2024&tmi=11").json()["per"]
    assert (data["deposits_year"], data["ceiling"], data["saving"]) == ("0.00", None, "0.00")
    assert data["low_tmi"]


def test_reglages_invalides(client):
    assert client.get("/api/tax?tmi=25").status_code == 422
    assert client.get("/api/tax?per_ceiling=-1").status_code == 422


def test_pea_impot_au_retrait_seulement():
    # Après 5 ans : prélèvements sociaux seuls (30 − 12,8 = 17,2 %) sur la part de gains.
    out = pea_exit(D("12000"), D("10000"), True, D("30"))
    assert (out["gain"], out["rate_pct"], out["tax"]) == (D("2000"), D("17.2"), D("344.0"))
    assert pea_exit(D("12000"), D("10000"), False, D("30"))["tax"] == D("600")
    assert pea_exit(D("9000"), D("10000"), False, D("30"))["tax"] == D("0")  # en perte


def test_carte_pea_latentes(client):
    body = {"name": "PEA fictif", "kind": "pea", "opened_on": "2015-01-01"}
    pea = post(client, "/api/accounts", {**body, "deposits_before": "1000"})
    a = post(client, "/api/assets", {"name": "A", "asset_class": "action", "ticker": "A.PA"})
    b = post(client, "/api/assets", {"name": "B", "asset_class": "action", "ticker": "B.PA"})
    trades = [(a, "2025-01-02", "5", "100"), (a, "2025-02-03", "1", "120"),
              (b, "2025-01-02", "2", "100"), (b, "2025-02-03", "1", "50")]  # fmt: skip
    for asset, day, qty, price in trades:
        post(client, "/api/operations", {"account_id": pea["id"], "asset_id": asset["id"],
                                         "op_type": "achat", "op_date": day, "quantity": qty,
                                         "unit_price": price, "fees": "0"})  # fmt: skip
    post(client, "/api/operations", {"account_id": pea["id"], "asset_id": a["id"],
                                     "op_type": "dividende", "op_date": "2025-06-01",
                                     "amount": "10"})  # fmt: skip
    [p] = client.get("/api/tax?year=2025&pfu=30").json()["pea"]
    # A : 6 parts à 120 pour 620 payés (+100) ; B : 3 parts à 50 pour 250 payés (−100).
    assert (p["latent_gains"], p["latent_losses"], p["dividends"]) == ("100.00", "100.00", "10.00")
    # Valeur 720 + 150 + 140 d'espèces = 1 010 pour 1 000 versés : 10 de gain à 17,2 %.
    assert (p["exit_gain"], p["exit_rate_pct"], p["exit_tax"]) == ("10.00", "17.2", "1.72")
