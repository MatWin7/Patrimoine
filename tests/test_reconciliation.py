"""Réconciliation d'un compte avec le relevé de la banque (D-063). Données FICTIVES."""

from decimal import Decimal as D

import pytest
from fastapi.testclient import TestClient

from app import reconcile
from app.main import app


def test_ecart_d_especes_hors_livret():
    up = reconcile.cash_check(D("80"), D("130"), livret=False)
    assert (up["gap"], up["ok"]) == (D("50"), False)
    assert up["correction"] == {"kind": "versement", "amount": D("50")}
    down = reconcile.cash_check(D("80"), D("75.5"), livret=False)
    assert down["correction"] == {"kind": "frais", "amount": D("4.5")}
    same = reconcile.cash_check(D("80"), D("80.004"), livret=False)
    assert same["ok"] and same["correction"] is None  # moins d'un centime : pas d'écart


def test_livret_reprend_le_solde_de_la_banque():
    check = reconcile.cash_check(D("1000"), D("1015"), livret=True)
    assert check["correction"] == {"kind": "solde", "amount": D("1015")}
    assert "des intérêts versés" in check["causes"]


def test_ecart_de_quantite():
    assert reconcile.line_check(D("10"), D("10.0"))["ok"]
    more = reconcile.line_check(D("10"), D("12"))
    assert (more["gap"], more["causes"][0]) == (D("2"), "un achat non saisi")
    assert reconcile.line_check(D("10"), D("7"))["causes"][0] == "une vente non saisie"


@pytest.fixture
def client(db_file):
    with TestClient(app) as c:
        yield c


def test_api_lecture_seule(client):
    acc = client.post(
        "/api/accounts", json={"name": "PEA fictif", "kind": "pea", "deposits_before": "100"}
    ).json()
    etf = client.post(
        "/api/assets", json={"name": "ETF fictif", "asset_class": "etf", "ticker": "f.pa"}
    ).json()
    body = {"account_id": acc["id"], "asset_id": etf["id"], "op_date": "2025-01-02"}
    client.post(
        "/api/operations", json=body | {"op_type": "achat", "quantity": "3", "unit_price": "20"}
    )
    url = f"/api/accounts/{acc['id']}/reconcile"
    res = client.post(url, json={"cash": "45", "quantities": {str(etf["id"]): "4"}}).json()
    assert res["ok"] is False
    assert res["cash"] | {"causes": None} == {
        "app": "40.00",
        "bank": "45.00",
        "gap": "5.00",
        "ok": False,
        "causes": None,
        "correction": {"kind": "versement", "amount": "5.00"},
    }
    [line] = res["lines"]
    assert (line["app"], line["bank"], line["gap"], line["ok"]) == ("3", "4", "1", False)
    # Rien n'a été enregistré.
    assert len(client.get("/api/operations").json()) == 1
    assert client.get("/api/accounts").json()[0]["cash"] == "40"
    # Tout concorde ; une ligne non fournie n'est pas vérifiée.
    res = client.post(url, json={"cash": "40"}).json()
    assert res["ok"] is True and "ok" not in res["lines"][0]
    assert client.post(url, json={}).json()["ok"] is False  # rien de vérifié
    ten = client.post(url, json={"quantities": {str(etf["id"]): "10"}}).json()["lines"][0]
    assert (ten["bank"], ten["gap"]) == ("10", "7")  # jamais « 1E+1 »
    assert client.post("/api/accounts/99/reconcile", json={}).status_code == 404
    bad = {"quantities": {str(etf["id"]): "-1"}}
    assert client.post(url, json=bad).status_code == 422
