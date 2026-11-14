"""Transferts entre comptes de l'app (D-085), sur des données FICTIVES."""

from datetime import date
from decimal import Decimal as D

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import Operation
from app.portfolio import RateHistory, compute_positions, daily_flows, eur_costs
from app.tax import crypto_sales, realized_sales


def op(id, account, jour, op_type, qty, price, to=None, fees="0"):
    return Operation(
        id=id,
        account_id=account,
        asset_id=1,
        op_date=date(2025, 1, jour),
        op_type=op_type,
        quantity=D(qty),
        unit_price=D(price),
        amount=None,
        fees=D(fees),
        currency="EUR",
        note=None,
        to_account_id=to,
    )


OPS = [
    op(1, 1, 2, "achat", "2", "100", fees="2"),
    op(2, 1, 5, "transfert_sortant", "0.5", "150", to=2),
]


def test_positions_suivent_au_prix_de_revient():
    by_account = {p.account_id: p for p in compute_positions(OPS)}
    assert by_account[1].quantity == D("1.5") and by_account[1].cost == D("151.5")
    assert by_account[2].quantity == D("0.5") and by_account[2].cost == D("50.5")
    costs = eur_costs(OPS, RateHistory({}))
    assert costs[(1, 1)] == D("151.5") and costs[(2, 1)] == D("50.5")


def test_flux_nul_et_aucune_cession():
    flows = daily_flows(OPS, {})
    assert flows[date(2025, 1, 5)] == 0  # 75 € sortis d'un compte, entrés dans l'autre
    assert realized_sales(OPS, RateHistory({}), pooled=True) == []
    # Portefeuille crypto global : un transfert entre comptes crypto ne consomme rien.
    _, acquisition = crypto_sales(OPS, {}, lambda *_: None, {}, internal={1, 2})
    assert acquisition == D("202")
    _, outside = crypto_sales(OPS, {}, lambda *_: None, {})
    assert outside < D("202")  # vers un compte hors portefeuille crypto : part consommée


def test_vente_apres_transfert_cto_prix_moyen_commun():
    ops = [*OPS, op(3, 2, 8, "vente", "0.5", "200")]
    [sale] = realized_sales(ops, RateHistory({}), pooled=True)
    assert sale.cost == D("50.5")


@pytest.fixture
def client(db_file):
    with TestClient(app) as c:
        yield c


def test_api_transfert_et_retrait_vers_un_compte(client):
    a = client.post("/api/accounts", json={"name": "Plateforme fictive", "kind": "crypto"})
    b = client.post("/api/accounts", json={"name": "Wallet fictif", "kind": "crypto"})
    a, b = a.json()["id"], b.json()["id"]
    livret = client.post("/api/accounts", json={"name": "Livret fictif", "kind": "livret"})
    livret = livret.json()["id"]
    asset = client.post(
        "/api/assets",
        json={"name": "Crypto fictive", "asset_class": "crypto", "coingecko_id": "fictif"},
    ).json()["id"]
    trade = {"account_id": a, "asset_id": asset, "op_date": "2025-01-02"}
    client.post("/api/operations", json={**trade, "op_type": "achat", "quantity": "2",
                                         "unit_price": "100"})  # fmt: skip
    bad = {**trade, "op_type": "transfert_sortant", "quantity": "1", "unit_price": "120"}
    assert client.post("/api/operations", json={**bad, "to_account_id": a}).status_code == 422
    assert client.post("/api/operations", json={**bad, "to_account_id": 999}).status_code == 404
    res = client.post("/api/operations", json={**bad, "to_account_id": b})
    assert res.status_code == 201 and res.json()["to_account_id"] == b
    out = client.post(
        "/api/operations",
        json={**bad, "op_date": "2025-01-03", "quantity": "0.5", "destination": "Ledger"},
    ).json()
    assert out["destination"] == "Ledger" and out["to_account_id"] is None
    # Une vente ne garde pas de destination.
    sale = client.post(
        "/api/operations",
        json={**trade, "op_type": "vente", "quantity": "0.1", "unit_price": "130",
              "to_account_id": b},
    ).json()  # fmt: skip
    assert sale["to_account_id"] is None
    held = {
        (line["account"], line["quantity"])
        for line in client.get("/api/portfolio").json()["positions"]
    }
    assert held == {("Wallet fictif", "1"), ("Plateforme fictive", "0.4")}
    # Retrait vers un livret : versement sur le livret.
    client.post("/api/operations", json={"account_id": a, "op_date": "2025-01-04",
                "op_type": "versement", "amount": "500"})  # fmt: skip
    client.post("/api/operations", json={"account_id": a, "op_date": "2025-01-05",
                "op_type": "retrait", "amount": "80", "to_account_id": livret})  # fmt: skip
    cash = {x["id"]: D(x["cash"]) for x in client.get("/api/accounts").json()}
    assert cash[livret] == D("80")


def test_compte_qui_na_recu_que_des_transferts_se_filtre(client):
    """Un wallet sans opération à lui, alimenté seulement par un transfert importé : la Vue
    d'ensemble le liste dans « Lignes prises en compte » grâce au `to_account_id` de
    /api/operations, et l'exclure retire bien sa ligne du total."""
    a = client.post("/api/accounts", json={"name": "Plateforme fictive", "kind": "crypto"})
    b = client.post("/api/accounts", json={"name": "Wallet fictif", "kind": "crypto"})
    a, b = a.json()["id"], b.json()["id"]
    asset = client.post(
        "/api/assets",
        json={"name": "Crypto fictive", "asset_class": "crypto", "coingecko_id": "fictif"},
    ).json()["id"]
    trade = {"account_id": a, "asset_id": asset, "unit_price": "100"}
    client.post("/api/operations", json={**trade, "op_date": "2025-01-02", "op_type": "achat",
                                         "quantity": "2"})  # fmt: skip
    client.post("/api/operations", json={**trade, "op_date": "2025-01-05", "quantity": "0.5",
                "op_type": "transfert_sortant", "to_account_id": b})  # fmt: skip
    ops = client.get("/api/operations").json()
    assert not [o for o in ops if o["account_id"] == b]  # rien d'enregistré sur le wallet
    assert {(o["to_account_id"], o["asset_id"]) for o in ops if o["to_account_id"]} == {(b, asset)}

    def held(**params):
        res = client.get("/api/portfolio", params=params).json()["positions"]
        return {(p["account"], p["quantity"]) for p in res}

    both = {("Plateforme fictive", "1.5"), ("Wallet fictif", "0.5")}
    assert held() == both
    assert held(exclude_account=b) == {("Plateforme fictive", "1.5")}
    assert held(exclude_line=f"{b}-{asset}") == {("Plateforme fictive", "1.5")}
