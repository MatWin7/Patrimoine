"""Espèces saisies pour CTO, crypto et Autre (D-075). Données FICTIVES."""

from decimal import Decimal as D

import pytest
from fastapi.testclient import TestClient

from app import reconcile
from app.db import connect
from app.main import app


@pytest.fixture
def client(db_file):
    with TestClient(app) as c:
        yield c


def achat(client, account_id, quantity="2", unit_price="50"):
    n = len(client.get("/api/assets").json())
    etf = client.post(
        "/api/assets", json={"name": f"ETF fictif {n}", "asset_class": "etf", "ticker": f"f{n}.pa"}
    ).json()
    body = {"account_id": account_id, "asset_id": etf["id"], "op_date": "2025-01-02"}
    body |= {"op_type": "achat", "quantity": quantity, "unit_price": unit_price}
    assert client.post("/api/operations", json=body).status_code in (200, 201)


def compte(client, account_id):
    return next(a for a in client.get("/api/accounts").json() if a["id"] == account_id)


def test_cto_part_du_solde_saisi(client):
    cto = client.post(
        "/api/accounts", json={"name": "CTO fictif", "kind": "cto", "cash": "300"}
    ).json()
    assert cto["cash_date"] is not None and D(cto["cash"]) == 300
    achat(client, cto["id"])  # 100 € d'achat après la saisie
    after = compte(client, cto["id"])
    assert D(after["cash"]) == 200 and D(after["deposits_before"]) == 0


def test_cto_sans_especes_et_achat_suppose_un_versement(client):
    cto = client.post("/api/accounts", json={"name": "CTO vide", "kind": "cto"}).json()
    assert cto["cash_date"] is not None  # « 0 aujourd'hui » est aussi un solde saisi
    achat(client, cto["id"])
    assert D(compte(client, cto["id"])["cash"]) == 0


def test_ancien_cto_prend_le_solde_a_l_enregistrement(client, db_file):
    cto = client.post(
        "/api/accounts", json={"name": "CTO ancien", "kind": "cto", "deposits_before": "500"}
    ).json()
    achat(client, cto["id"])
    # Compte créé avant D-075 : pas de date de solde, espèces déduites des versements.
    with connect(db_file) as conn:
        conn.execute("UPDATE account SET cash = '0', cash_date = NULL WHERE id = ?", (cto["id"],))
    old = compte(client, cto["id"])
    assert old["cash_date"] is None and D(old["cash"]) == 400
    body = {"name": "CTO ancien", "kind": "cto", "currency": "EUR", "cash": old["cash"]}
    assert client.patch(f"/api/accounts/{cto['id']}", json=body).status_code == 200
    saved = compte(client, cto["id"])
    assert saved["cash_date"] is not None and D(saved["cash"]) == 400
    achat(client, cto["id"], quantity="1")
    assert D(compte(client, cto["id"])["cash"]) == 350


def test_pea_garde_ses_versements(client):
    pea = client.post(
        "/api/accounts", json={"name": "PEA fictif", "kind": "pea", "deposits_before": "500"}
    ).json()
    assert pea["cash_date"] is None
    achat(client, pea["id"])
    assert D(compte(client, pea["id"])["cash"]) == 400


def test_verifier_un_cto_propose_le_solde():
    check = reconcile.cash_check(D("80"), D("130"), livret=False, entered=True)
    assert check["correction"] == {"kind": "solde", "amount": D("130")}
