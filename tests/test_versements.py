"""Versements, retraits, espèces calculées, correction d'une opération (D-041). Données FICTIVES."""

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app


@pytest.fixture
def client(db_file):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def pea(client):
    account = client.post("/api/accounts", json={"name": "PEA test", "kind": "pea"}).json()
    asset = client.post(
        "/api/assets", json={"name": "ETF fictif", "asset_class": "etf", "ticker": "fake.pa"}
    ).json()
    return account["id"], asset["id"]


def operation(client, pea, op_type, **fields):
    account_id, asset_id = pea
    body = {"account_id": account_id, "op_date": "2025-01-02", "op_type": op_type, **fields}
    if op_type in ("achat", "vente"):
        body["asset_id"] = asset_id
    return client.post("/api/operations", json=body)


def cash(client):
    return client.get("/api/accounts").json()[0]["cash"]


def test_versement_achat_vente_retrait(client, pea):
    res = operation(client, pea, "versement", amount="500")
    assert res.status_code == 201
    assert (res.json()["asset_id"], res.json()["total"], res.json()["currency"]) == (
        None,
        "500.00",
        "EUR",
    )
    achat = operation(client, pea, "achat", quantity="10", unit_price="20", fees="2")
    assert achat.json()["total"] == "202.00"  # frais compris
    assert cash(client) == "298"
    operation(client, pea, "vente", quantity="5", unit_price="30", fees="1")
    operation(client, pea, "retrait", amount="100")
    assert cash(client) == "347"  # 500 - 202 + 149 - 100


def test_versement_invalide(client, pea):
    assert operation(client, pea, "versement", amount="0").status_code == 422
    assert operation(client, pea, "retrait", amount="-5").status_code == 422
    assert operation(client, pea, "versement").status_code == 422  # montant manquant
    assert operation(client, pea, "versement", amount="5", op_date="2999-01-01").status_code == 422


def test_recalage_d_un_livret(client):
    """Seul un livret garde un solde saisi (D-062) : il redevient la référence."""
    livret = client.post("/api/accounts", json={"name": "Livret fictif", "kind": "livret"}).json()
    body = {"account_id": livret["id"], "op_date": "2025-01-02", "op_type": "versement"}
    client.post("/api/operations", json={**body, "amount": "500"})
    patch = {"name": "Livret fictif", "kind": "livret", "cash": "512"}  # intérêts versés
    assert client.patch(f"/api/accounts/{livret['id']}", json=patch).json()["cash"] == "512"
    client.post("/api/operations", json={**body, "op_type": "retrait", "amount": "12"})
    assert cash(client) == "500"


def test_achat_sans_versement_suppose_un_versement(client, pea):
    """On ne peut pas acheter sans avoir versé : l'achat fait monter les versements (D-062)."""
    operation(client, pea, "achat", quantity="2", unit_price="10")
    [acc] = client.get("/api/accounts").json()
    assert (acc["cash"], acc["deposits"], acc["deposits_implied"]) == ("0", "20", "20")
    data = client.get("/api/portfolio").json()
    assert [p["label"] for p in data["allocation"]] == ["ETF"]  # pas de part d'espèces vide
    # Champ « Versements » saisi : les espèces en découlent.
    patch = {"name": "PEA test", "kind": "pea", "deposits_before": "100", "cash": "999"}
    acc = client.patch(f"/api/accounts/{pea[0]}", json=patch).json()
    assert (acc["cash"], acc["deposits"]) == ("80", "100")  # solde saisi ignoré hors livret


def test_modifier_une_operation(client, pea):
    account_id, asset_id = pea
    operation(client, pea, "versement", amount="100")
    op = operation(client, pea, "achat", quantity="2", unit_price="10").json()
    body = {
        "account_id": account_id,
        "asset_id": asset_id,
        "op_date": "2025-01-03",
        "op_type": "achat",
        "quantity": "3",
        "unit_price": "10",
        "fees": "1",
        "note": "corrigée",
    }
    res = client.patch(f"/api/operations/{op['id']}", json=body)
    assert res.status_code == 200
    assert (res.json()["id"], res.json()["quantity"], res.json()["note"]) == (
        op["id"],
        "3",
        "corrigée",
    )
    assert cash(client) == "69"  # 100 - 31 : la correction se répercute
    # Une erreur de type se corrige aussi : l'achat devient un versement.
    cash_body = {"account_id": account_id, "op_date": "2025-01-03", "op_type": "versement"}
    res = client.patch(f"/api/operations/{op['id']}", json={**cash_body, "amount": "5"})
    assert (res.json()["op_type"], res.json()["asset_id"], cash(client)) == (
        "versement",
        None,
        "105",
    )
    assert client.patch("/api/operations/999", json={**cash_body, "amount": "5"}).status_code == 404


def test_modification_qui_casserait_une_vente_refusee(client, pea):
    account_id, asset_id = pea
    op = operation(client, pea, "achat", quantity="3", unit_price="10").json()
    operation(client, pea, "vente", quantity="3", unit_price="12")
    body = {**op, "op_date": "2025-01-02", "quantity": "1"}
    body = {
        k: body[k]
        for k in ("account_id", "asset_id", "op_date", "op_type", "quantity", "unit_price")
    }
    assert client.patch(f"/api/operations/{op['id']}", json=body).status_code == 422
    assert client.get("/api/operations").json()[0]["quantity"] == "3"  # rien n'a changé


V3 = Path(__file__).parent / "fixtures" / "schema_v3.sql"


def test_migration_v3_garde_les_especes(db_file):
    """Base v3 avec un solde saisi après un achat : la migration garde opérations et comptes."""
    conn = sqlite3.connect(db_file)
    conn.executescript(V3.read_text(encoding="utf-8"))
    conn.executescript(
        """
        INSERT INTO account (name, kind, cash, cash_date)
            VALUES ('PEA fictif', 'pea', '40', '2025-02-01');
        INSERT INTO asset (name, asset_class, ticker, currency)
            VALUES ('ETF fictif', 'etf', 'F.PA', 'EUR');
        INSERT INTO operation
            (account_id, asset_id, op_date, op_type, quantity, unit_price, currency)
            VALUES (1, 1, '2025-01-02', 'achat', '2', '10', 'EUR');
        """
    )
    conn.close()
    assert db.migrate() == (3, db.SCHEMA_VERSION)
    with TestClient(app) as client:
        # Hors livret, le solde saisi ne sert plus (D-062) : l'achat suppose 20 € versés.
        assert cash(client) == "0"
        assert client.get("/api/accounts").json()[0]["deposits"] == "20"
        assert client.get("/api/operations").json()[0]["quantity"] == "2"
    conn = sqlite3.connect(db_file)
    assert conn.execute("SELECT count(*) FROM benchmark").fetchone()[0] == 7
    conn.close()


def test_regroupements_et_poids_des_lignes(client, pea):
    account_id, _ = pea
    operation(client, pea, "versement", amount="100")
    operation(client, pea, "achat", quantity="3", unit_price="20")  # 60 € de titres, 40 € d'espèces
    cto = client.post("/api/accounts", json={"name": "CTO test", "kind": "cto"}).json()
    action = client.post(
        "/api/assets", json={"name": "Action fictive", "asset_class": "action", "ticker": "x.pa"}
    ).json()
    client.post(
        "/api/operations",
        json={
            "account_id": cto["id"],
            "asset_id": action["id"],
            "op_date": "2025-01-02",
            "op_type": "solde_initial",
            "quantity": "1",
            "unit_price": "100",
        },
    )
    data = client.get("/api/portfolio").json()
    groupings = data["groupings"]
    assert [(p["label"], p["pct"]) for p in groupings["ligne"]["top"]] == [
        ("Action fictive", "50.00"),
        ("ETF fictif", "30.00"),
        ("Espèces", "20.00"),
    ]
    assert [(p["label"], p["value"]) for p in groupings["compte"]["all"]] == [
        ("PEA test", "100.00"),  # titres + espèces du compte
        ("CTO test", "100.00"),
    ]
    assert groupings["devise"]["top"] == [{"label": "EUR", "value": "200.00", "pct": "100.00"}]
    assert groupings["classe"]["others"] == 0
    weights = {p["asset"]: (p["value_eur"], p["weight_pct"]) for p in data["positions"]}
    assert weights == {"ETF fictif": ("60.00", "30.00"), "Action fictive": ("100.00", "50.00")}


def test_couleur_choisie_pour_un_actif(client, pea):
    _, asset_id = pea
    body = {"name": "ETF fictif", "asset_class": "etf", "ticker": "fake.pa", "color": 3}
    assert client.patch(f"/api/assets/{asset_id}", json=body).json()["color"] == 3
    assert client.patch(f"/api/assets/{asset_id}", json={**body, "color": 13}).status_code == 422
    operation(client, pea, "achat", quantity="1", unit_price="10")
    assert client.get("/api/portfolio").json()["positions"][0]["color"] == 3
    # Sans le champ : retour à la couleur automatique.
    body.pop("color")
    assert client.patch(f"/api/assets/{asset_id}", json=body).json()["color"] is None


def test_rejeu_des_especes_dans_l_ordre():
    """Vente réinvestie : pas de versement supposé en plus ; le même jour, l'entrée d'abord."""
    from datetime import date
    from decimal import Decimal as D

    from app.models import Operation
    from app.portfolio import replay_cash

    def op(i, day, kind, price, currency="EUR"):
        return Operation(
            i, 1, 1, date(2025, 1, day), kind, D("1"), D(price), None, D("0"), currency, None
        )

    ops = [
        op(3, 3, "achat", "100"),  # payé avec la vente du même jour
        op(2, 3, "vente", "100"),
        op(1, 2, "achat", "100"),  # 30 versés : il manque 70
        op(4, 4, "achat", "50"),  # il manque 50
        op(5, 4, "achat", "999", "USD"),  # autre devise que le compte : ignorée
    ]
    assert replay_cash(D("30"), "EUR", ops) == (D("0"), D("120"))
