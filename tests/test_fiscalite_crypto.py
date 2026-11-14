"""Fiscalité des cryptos : portefeuille global, échanges, seuil de 305 € (D-066). FICTIF."""

import sqlite3
from datetime import date
from decimal import Decimal as D

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.models import Operation
from app.tax import crypto_estimate, crypto_exit, crypto_sales, pfu_default

BTC, ETH, SOL = 1, 2, 3


def op(i, day, op_type, asset, qty, price, fees="0", swap=False, value=None):
    return Operation(
        id=i,
        account_id=1,
        asset_id=asset,
        op_date=date.fromisoformat(day),
        op_type=op_type,
        quantity=D(qty),
        unit_price=D(price),
        amount=None,
        fees=D(fees),
        currency="EUR",
        note=None,
        crypto_swap=swap,
        portfolio_value=None if value is None else D(value),
    )


def no_history(asset_id, day):
    return None


BASE = [
    op(1, "2024-01-10", "achat", BTC, "1", "10000"),
    op(2, "2024-02-10", "achat", ETH, "10", "1000"),
]


def test_formule_du_portefeuille_global():
    ops = [
        *BASE,
        # Valeur globale saisie : BTC 30 000 + ETH 30 000.
        op(3, "2025-03-01", "vente", BTC, "0.5", "30000", "50", value="60000"),
        # Valeur estimée : BTC restant au prix de sa dernière opération + ETH au prix de la vente.
        op(4, "2025-06-01", "vente", ETH, "5", "2000"),
    ]
    sales, remaining = crypto_sales(ops, {}, no_history, {})
    first, second = sales
    # 213 = 15 000 ; fraction = 20 000 × 15 000 / 60 000 = 5 000 ; 15 000 − 50 − 5 000.
    assert (first.gross, first.fees, first.fraction, first.gain) == (
        D("15000"),
        D("50"),
        D("5000"),
        D("9950"),
    )
    assert first.estimated is False
    # 0,5 BTC × 30 000 + 10 ETH × 2 000 = 35 000 ; 15 000 × 10 000 / 35 000 ≈ 4 285,71.
    assert second.estimated is True
    assert second.portfolio_value == D("35000")
    assert (second.purchases, second.consumed, second.acquisition) == (
        D("20000"),
        D("5000"),
        D("15000"),
    )
    assert round(second.gain, 2) == D("5714.29")
    assert round(remaining, 2) == D("10714.29")


def test_echange_non_imposable_et_sans_prix_d_achat_en_plus():
    ops = [
        *BASE,
        op(3, "2025-03-01", "achat", SOL, "100", "100"),  # saisi avant l'échange, même jour
        op(4, "2025-03-01", "vente", ETH, "5", "2000", swap=True),
    ]
    sales, remaining = crypto_sales(ops, {}, no_history, {})
    assert sales == []
    assert remaining == D("20000")  # SOL payé avec l'ETH échangé, pas avec des euros


def test_cours_stocke_puis_cours_actuel():
    ops = [*BASE, op(3, "2025-03-01", "vente", BTC, "0.1", "20000")]

    def history(asset_id, day):
        return D("1500") if asset_id == ETH else None

    [sale], _ = crypto_sales(ops, {}, history, {ETH: D("9999")})
    assert sale.portfolio_value == D("35000")  # 1 BTC × 20 000 + 10 ETH × 1 500


def test_annee_seuil_et_moins_values():
    ops = [
        op(1, "2025-01-10", "achat", BTC, "1", "1000"),
        op(2, "2025-05-01", "vente", BTC, "0.25", "1200"),  # 300 € cédés : sous les 305 €
    ]
    sales, remaining = crypto_sales(ops, {}, no_history, {})
    est = crypto_estimate(sales, 2025, D("30"))
    assert (est["total"], est["exempt"], est["net"], est["tax"]) == (
        D("300"),
        True,
        D("50"),
        D("0"),
    )
    assert crypto_estimate(sales, 2024, D("30"))["sales"] == []
    ops.append(op(3, "2025-06-01", "vente", BTC, "0.5", "800"))  # perte compensée dans l'année
    est = crypto_estimate(crypto_sales(ops, {}, no_history, {})[0], 2025, D("30"))
    # 2e vente : 400 − 750 × 400 / 600 = −100 ; net 50 − 100 < 0, pas d'impôt.
    assert (est["exempt"], est["gains"], est["losses"], est["tax"]) == (
        False,
        D("50"),
        D("100"),
        D("0"),
    )


def test_tout_vendre_aujourd_hui_et_taux_par_annee():
    assert crypto_exit(D("12000"), D("10000"), D("30")) == {"gain": D("2000"), "tax": D("600")}
    assert crypto_exit(D("300"), D("100"), D("30"))["tax"] == D("0")  # sous les 305 €
    assert crypto_exit(D("8000"), D("10000"), D("30"))["tax"] == D("0")
    assert (pfu_default(2025), pfu_default(2026)) == (D("30"), D("31.4"))
    # Plus-values et cryptos : hausse de la CSG dès les revenus 2025 (D-081).
    assert (pfu_default(2024, gains=True), pfu_default(2025, gains=True)) == (D("30"), D("31.4"))


@pytest.fixture
def client(db_file):
    with TestClient(app) as c:
        yield c


def post(client, path, body, status=201):
    res = client.post(path, json=body)
    assert res.status_code == status, res.text
    return res.json()


def test_api_vente_crypto_et_carte(client):
    wallet = post(client, "/api/accounts", {"name": "Crypto fictive", "kind": "crypto"})["id"]
    cto = post(client, "/api/accounts", {"name": "CTO fictif", "kind": "cto"})["id"]
    coin = {"name": "Pièce fictive", "asset_class": "crypto", "coingecko_id": "fictive"}
    coin = post(client, "/api/assets", coin)["id"]
    trade = {"account_id": wallet, "asset_id": coin, "op_type": "achat"}
    post(
        client,
        "/api/operations",
        {**trade, "op_date": "2025-01-10", "quantity": "2", "unit_price": "1000"},
    )
    sale = {**trade, "op_type": "vente", "op_date": "2025-04-01", "quantity": "1"}
    out = post(client, "/api/operations", {**sale, "unit_price": "1500", "portfolio_value": "3000"})
    assert (out["crypto_swap"], out["portfolio_value"]) == (False, "3000")
    # Un échange ne garde pas de valeur globale (sans objet).
    swap = post(
        client,
        "/api/operations",
        {**sale, "unit_price": "1500", "crypto_swap": True, "portfolio_value": "9"},
    )
    assert (swap["crypto_swap"], swap["portfolio_value"]) == (True, None)

    data = client.get("/api/tax?year=2025").json()
    assert data["pfu_pct"] == "30"  # taux de l'année choisie
    assert data["pfu_gains_pct"] == "31.4"  # plus-values et cryptos 2025 (D-081)
    c = data["crypto"]
    # 1 500 − 2 000 × 1 500 / 3 000 = 500 ; impôt 31,4 %.
    assert (c["total"], c["net"], c["tax"], c["exempt"], c["swaps"]) == (
        "1500.00",
        "500.00",
        "157.00",
        False,
        1,
    )
    [row] = c["sales"]
    assert (row["portfolio_value"], row["estimated"], row["fraction"]) == (
        "3000.00",
        False,
        "1000.00",
    )
    assert client.get("/api/tax?year=2026").json()["pfu_pct"] == "31.4"

    # Hors compte crypto, la case est ignorée.
    act = {"name": "Action fictive", "asset_class": "action", "ticker": "A.PA"}
    act = post(client, "/api/assets", act)["id"]
    base = {"account_id": cto, "asset_id": act, "op_date": "2025-01-10", "quantity": "1"}
    post(client, "/api/operations", {**base, "op_type": "achat", "unit_price": "10"})
    out = post(
        client,
        "/api/operations",
        {**base, "op_type": "vente", "unit_price": "12", "crypto_swap": True},
    )
    assert (out["crypto_swap"], out["portfolio_value"]) == (False, None)
    # Une vente crypto corrigée en achat perd ses renseignements.
    fixed = client.patch(
        f"/api/operations/{swap['id']}", json={**sale, "op_type": "achat", "unit_price": "1"}
    )
    assert fixed.json()["crypto_swap"] is False


def test_migration_v9_ajoute_les_colonnes(db_file):
    db.init_db(db_file)
    conn = sqlite3.connect(db_file)
    conn.executescript(
        """
        ALTER TABLE account DROP COLUMN abroad;
        ALTER TABLE account DROP COLUMN custody;
        ALTER TABLE operation DROP COLUMN crypto_swap;
        ALTER TABLE operation DROP COLUMN portfolio_value;
        ALTER TABLE operation DROP COLUMN offered;
        ALTER TABLE operation DROP COLUMN tax_withheld;
        ALTER TABLE account DROP COLUMN position;
        ALTER TABLE asset DROP COLUMN position;
        INSERT INTO account (name, kind) VALUES ('Crypto fictive', 'crypto');
        INSERT INTO asset (name, asset_class, coingecko_id, currency)
            VALUES ('Pièce fictive', 'crypto', 'fictive', 'EUR');
        INSERT INTO operation (account_id, asset_id, op_date, op_type, quantity, unit_price,
            currency) VALUES (1, 1, '2025-01-02', 'achat', '2', '10', 'EUR');
        PRAGMA user_version = 9;
        """
    )
    conn.close()
    assert db.migrate() == (9, db.SCHEMA_VERSION)
    conn = sqlite3.connect(db_file)
    assert conn.execute("SELECT crypto_swap, portfolio_value FROM operation").fetchall() == [
        (0, None)
    ]
    conn.close()
