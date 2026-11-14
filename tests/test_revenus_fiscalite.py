"""Revenus, frais, allocation cible et repères fiscaux (D-057 à D-060). Données FICTIVES."""

import sqlite3
from datetime import date, timedelta
from decimal import Decimal as D

import pytest
from fastapi.testclient import TestClient

from app import db
from app.income import ter_cost, yield_on_cost
from app.main import app
from app.rebalance import Line, rebalance
from app.tax import anniversary


def test_reequilibrage_par_le_versement():
    lines = [Line(1, D("700"), D("60")), Line(2, D("200"), D("30")), Line(3, D("100"), D("10"))]
    advice = {a.key: a for a in rebalance(lines, D("500"))}
    # Après versement : 1 500 € ; visés 900 / 450 / 150 ; manquent 200 / 250 / 50 = 500.
    assert [advice[k].buy for k in (1, 2, 3)] == [D("200"), D("250"), D("50")]
    assert advice[1].gap == D("-100")  # 600 visés aujourd'hui, 700 détenus : au-dessus
    assert advice[2].current_pct == D("20")


def test_versement_trop_petit_au_prorata_du_manque():
    lines = [Line(1, D("900"), D("50")), Line(2, D("100"), D("50"))]
    advice = {a.key: a for a in rebalance(lines, D("100"))}
    assert (advice[1].buy, advice[2].buy) == (D("0"), D("100"))  # pas de vente proposée


def test_versement_au_dela_des_cibles():
    lines = [Line(1, D("500"), D("50")), Line(2, D("500"), D("50"))]
    advice = rebalance(lines, D("200"))
    assert [a.buy for a in advice] == [D("100"), D("100")]


def test_frais_courants_et_rendement():
    cost = ter_cost(D("10000"), D("0.2"), D("5"), 10)
    assert cost["first_year"] == D("20")
    # 10 000 × (1,05^10 − 1,048^10) ≈ 307,62 € de manque à gagner sur 10 ans.
    assert D("307.6") < cost["over_years"] < D("307.7")
    assert yield_on_cost(D("30"), D("1000")) == D("0.03")
    assert yield_on_cost(D("30"), D("0")) is None
    assert anniversary(date(2020, 2, 29), 5) == date(2025, 2, 28)


@pytest.fixture
def client(db_file):
    with TestClient(app) as c:
        yield c


def post(client, path, body, status=201):
    res = client.post(path, json=body)
    assert res.status_code == status, res.text
    return res.json()


@pytest.fixture
def comptes(client):
    pea = post(
        client,
        "/api/accounts",
        {
            "name": "PEA fictif",
            "kind": "pea",
            "opened_on": "2020-03-01",
            "deposits_before": "20000",
        },
    )
    cto = post(client, "/api/accounts", {"name": "CTO fictif", "kind": "cto"})
    etf = post(
        client,
        "/api/assets",
        {"name": "ETF fictif", "asset_class": "etf", "ticker": "FAKE.PA", "ter": "0.2"},
    )
    act = post(
        client, "/api/assets", {"name": "Action fictive", "asset_class": "action", "ticker": "A.PA"}
    )
    return pea["id"], cto["id"], etf["id"], act["id"]


def trade(client, account, asset, day, op_type, qty, price, fees="0"):
    body = {
        "account_id": account,
        "asset_id": asset,
        "op_date": day,
        "op_type": op_type,
        "quantity": qty,
        "unit_price": price,
        "fees": fees,
    }
    return post(client, "/api/operations", body)


def test_dividende_et_frais_saisis(client, comptes):
    pea, cto, etf, act = comptes
    trade(client, cto, act, "2025-01-10", "achat", "10", "100", "2")
    recent = (date.today() - timedelta(days=30)).isoformat()
    div = {"account_id": cto, "asset_id": act, "op_date": recent, "op_type": "dividende"}
    post(client, "/api/operations", {**div, "amount": "30"})
    garde = {"account_id": cto, "op_date": recent, "op_type": "frais", "amount": "5"}
    post(client, "/api/operations", garde)
    no_asset = {"account_id": cto, "op_date": recent, "op_type": "dividende", "amount": "1"}
    post(client, "/api/operations", no_asset, status=422)
    [acc] = [a for a in client.get("/api/accounts").json() if a["id"] == cto]
    # Achat de 1 002 sans versement : supposé versé (D-062) ; puis +30 de dividende, −5 de frais.
    assert (acc["cash"], acc["deposits"]) == ("25", "1002")

    data = client.get("/api/income").json()
    assert data["dividends"]["last_12m"] == "30.00"
    [line] = data["lines"]
    assert (line["asset"], line["yield_pct"]) == ("Action fictive", "2.99")  # 30 / 1 002
    assert data["fees_total"] == "7.00"  # 2 de courtage + 5 de droits de garde


def test_allocation_cible(client, comptes):
    pea, cto, etf, act = comptes
    trade(client, pea, etf, "2025-01-10", "achat", "70", "10")
    trade(client, pea, act, "2025-01-10", "achat", "3", "10")
    res = client.put("/api/targets", json={"targets": {str(etf): "60", str(act): "40"}})
    assert res.status_code == 200
    rows = {r["asset"]: r for r in client.get("/api/targets?contribution=300").json()["rows"]}
    # 730 € + 300 € : visés 618 / 412 ; il manque 382 € à l'action, tout le versement y va.
    assert (rows["ETF fictif"]["buy"], rows["Action fictive"]["buy"]) == ("0.00", "300.00")
    assert rows["ETF fictif"]["gap"] == "-262.00"  # 438 € visés aujourd'hui, 700 € détenus
    assert (rows["ETF fictif"]["current_pct"], rows["ETF fictif"]["target_pct"]) == (
        "95.89",
        "60.00",
    )
    over = client.put("/api/targets", json={"targets": {str(etf): "80", str(act): "40"}})
    assert over.status_code == 422


def test_reperes_fiscaux(client, comptes):
    pea, cto, etf, act = comptes
    post(
        client,
        "/api/operations",
        {"account_id": pea, "op_date": "2025-01-02", "op_type": "versement", "amount": "5000"},
    )
    trade(client, cto, act, "2024-01-10", "achat", "10", "100")
    trade(client, cto, act, "2024-06-10", "achat", "10", "120", "2")
    today = date.today().isoformat()
    trade(client, cto, act, today, "vente", "5", "150", "1")
    data = client.get("/api/tax?pfu=30").json()
    [p] = data["pea"]
    assert (p["deposits"], p["remaining"], p["milestone"]) == (
        "25000.00",
        "125000.00",
        "2025-03-01",
    )
    assert p["milestone_reached"] is True
    cto_data = data["cto"]
    # PRU = (1 000 + 1 202) / 20 = 110,10 ; 5 vendues : 750 − 1 − 550,50 = 198,50.
    assert cto_data["net"] == "198.50"
    assert cto_data["tax"] == "59.55"  # 30 % de 198,50
    assert client.get("/api/tax?pfu=31.4").json()["cto"]["tax"] == "62.33"
    # Un dividende net reçu est affiché mais n'augmente pas l'impôt (déjà prélevé à la source).
    div = {"account_id": cto, "asset_id": act, "op_date": today, "op_type": "dividende"}
    post(client, "/api/operations", {**div, "amount": "20"})
    cto_data = client.get("/api/tax?pfu=30").json()["cto"]
    assert (cto_data["dividends"], cto_data["tax"]) == ("20.00", "59.55")


def test_migration_v7_ajoute_les_champs(db_file):
    """Base v7 simulée : account et asset sans les nouvelles colonnes, pas de table target."""
    db.init_db(db_file)
    conn = sqlite3.connect(db_file)
    conn.executescript(
        """
        DROP TABLE target;
        DROP TABLE account;
        CREATE TABLE account (id INTEGER PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
            institution TEXT, currency TEXT NOT NULL DEFAULT 'EUR',
            cash TEXT NOT NULL DEFAULT '0', cash_date TEXT,
            cash_op_id INTEGER NOT NULL DEFAULT 0, created_at TEXT);
        DROP TABLE asset;
        CREATE TABLE asset (id INTEGER PRIMARY KEY, name TEXT NOT NULL, asset_class TEXT,
            isin TEXT, ticker TEXT, coingecko_id TEXT, currency TEXT, color INTEGER);
        INSERT INTO account (name, kind) VALUES ('PEA fictif', 'pea');
        ALTER TABLE operation DROP COLUMN crypto_swap;
        ALTER TABLE operation DROP COLUMN portfolio_value;
        ALTER TABLE operation DROP COLUMN offered;
        ALTER TABLE operation DROP COLUMN tax_withheld;
        PRAGMA user_version = 7;
        """
    )
    conn.close()
    assert db.migrate() == (7, db.SCHEMA_VERSION)
    conn = sqlite3.connect(db_file)
    row = conn.execute("SELECT opened_on, deposits_before FROM account").fetchone()
    assert row == (None, "0")
    assert conn.execute("SELECT count(*) FROM target").fetchone()[0] == 0
    assert conn.execute("SELECT ter FROM asset").fetchall() == []
    conn.close()


def test_versements_pea_deduits_des_achats(client):
    """Sans « versements avant le suivi », les achats passés supposent des versements."""
    pea = post(client, "/api/accounts", {"name": "PEA fictif", "kind": "pea", "cash": "120"})
    etf = post(client, "/api/assets", {"name": "ETF", "asset_class": "etf", "ticker": "E.PA"})
    base = {"account_id": pea["id"], "asset_id": etf["id"], "unit_price": "100"}
    post(client, "/api/operations", {**base, "op_date": "2024-01-10", "op_type": "solde_initial",
                                     "quantity": "10"})  # fmt: skip
    versement = {"account_id": pea["id"], "op_type": "versement", "amount": "300"}
    post(client, "/api/operations", {**versement, "op_date": "2024-03-01"})
    post(client, "/api/operations", {**base, "op_date": "2024-03-01", "op_type": "achat",
                                     "quantity": "5", "fees": "2"})  # fmt: skip
    [p] = client.get("/api/tax").json()["pea"]
    # 1 000 de titres + 502 d'achat − 300 versés : 1 202 supposés versés (les 120 d'espèces
    # saisies ont servi à l'achat, déjà comptés).
    assert (p["deposits_entered"], p["deposits_implied"]) == ("300.00", "1202.00")
    assert p["deposits"] == "1502.00"
    patch = {"name": "PEA fictif", "kind": "pea", "deposits_before": "9000"}
    assert client.patch(f"/api/accounts/{pea['id']}", json=patch).status_code == 200
    [p] = client.get("/api/tax").json()["pea"]
    assert (p["deposits"], p["deposits_implied"]) == ("9300.00", "0.00")
