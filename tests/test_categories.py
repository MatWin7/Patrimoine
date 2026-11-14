"""Catégories personnelles et allocation cible par catégorie (D-061). Données FICTIVES."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app


@pytest.fixture
def client(db_file):
    with TestClient(app) as c:
        yield c


def post(client, path, body, status=201):
    res = client.post(path, json=body)
    assert res.status_code == status, res.text
    return res.json()


@pytest.fixture
def lignes(client):
    pea = post(client, "/api/accounts", {"name": "PEA fictif", "kind": "pea"})["id"]

    def actif(name, ticker, category=None):
        body = {"name": name, "asset_class": "etf", "ticker": ticker, "category": category}
        return post(client, "/api/assets", body)

    monde = actif("ETF Monde fictif", "M.PA", " Monde ")
    monde2 = actif("ETF Monde bis fictif", "M2.PA", "Monde")
    emergents = actif("ETF Émergents fictif", "E.PA", "Émergents")
    sans = actif("ETF sans catégorie", "S.PA", "")
    for asset, qty in ((monde, "60"), (monde2, "10"), (emergents, "10"), (sans, "20")):
        body = {
            "account_id": pea,
            "asset_id": asset["id"],
            "op_date": "2025-01-10",
            "op_type": "achat",
            "quantity": qty,
            "unit_price": "10",
        }
        post(client, "/api/operations", body)
    return monde, sans


def test_categorie_saisie_ou_classe(client, lignes):
    monde, sans = lignes
    assert (monde["category"], monde["category_label"]) == ("Monde", "Monde")
    assert (sans["category"], sans["category_label"]) == (None, "ETF")  # vide = la classe
    parts = client.get("/api/portfolio").json()["groupings"]["categorie"]["all"]
    assert {p["label"]: p["value"] for p in parts} == {
        "Monde": "700.00",
        "ETF": "200.00",
        "Émergents": "100.00",
    }


def test_cibles_par_categorie(client, lignes):
    body = {"targets": {"Monde": "60", "Émergents": "30", "Or": "10"}}
    res = client.put("/api/targets/categories", json=body)
    assert res.status_code == 200, res.text
    data = client.get("/api/targets?level=categorie&contribution=300").json()
    rows = {r["category"]: r for r in data["rows"]}
    # Visé sur 800 + 300 = 1 100 € : 660 / 330 / 110 ; manquent 0 / 230 / 110 = 340 > 300.
    assert rows["Monde"]["buy"] == "0.00"
    assert rows["Émergents"]["buy"] == "202.94"  # 230 / 340 × 300
    assert rows["Émergents"]["buy_asset"] == "ETF Émergents fictif"
    assert rows["Or"]["value"] == "0.00" and rows["Or"]["buy_asset"] is None  # rien de détenu
    assert rows["Monde"]["lines"] == ["ETF Monde fictif", "ETF Monde bis fictif"]
    assert rows["ETF"]["target"] is None and rows["ETF"]["buy"] is None
    # Les cibles par ligne restent à part.
    assert client.get("/api/targets").json()["targets_total"] == "0"


def test_achat_sur_la_plus_grosse_ligne(client, lignes):
    client.put("/api/targets/categories", json={"targets": {"Monde": "100"}})
    [row] = [
        r
        for r in client.get("/api/targets?level=categorie&contribution=50").json()["rows"]
        if r["category"] == "Monde"
    ]
    assert (row["buy"], row["buy_asset"]) == ("50.00", "ETF Monde fictif")


def test_cible_sur_un_actif_pas_encore_achete(client, lignes):
    """Un actif créé mais jamais acheté peut recevoir une cible, par ligne ou par catégorie."""
    body = {"name": "ETF Or fictif", "asset_class": "etf", "ticker": "OR.PA", "category": "Or"}
    gold = post(client, "/api/assets", body)
    data = client.get("/api/targets").json()
    assert data["addable"] == [{"asset_id": gold["id"], "asset": "ETF Or fictif"}]
    assert client.get("/api/targets?level=categorie").json()["addable"] == ["Or"]

    res = client.put("/api/targets", json={"targets": {str(gold["id"]): "10"}})
    assert res.status_code == 200, res.text
    data = client.get("/api/targets?contribution=100").json()
    assert data["addable"] == []
    [row] = [r for r in data["rows"] if r["asset_id"] == gold["id"]]
    assert (row["value"], row["buy"]) == ("0.00", "100.00")  # seule ligne visée

    client.put("/api/targets/categories", json={"targets": {"Or": "10"}})
    data = client.get("/api/targets?level=categorie&contribution=100").json()
    [row] = [r for r in data["rows"] if r["category"] == "Or"]
    # Rien de détenu : l'achat est proposé sur l'actif créé de la catégorie.
    assert (row["lines"], row["buy"], row["buy_asset"]) == ([], "100.00", "ETF Or fictif")
    assert data["addable"] == []


@pytest.mark.parametrize(
    "targets",
    [{"Monde": "70", "Émergents": "40"}, {"Monde": "-1"}, {"x" * 41: "10"}, {"  ": "10"}],
)
def test_cibles_refusees(client, targets):
    assert client.put("/api/targets/categories", json={"targets": targets}).status_code == 422


def test_niveau_inconnu(client):
    assert client.get("/api/targets?level=classe").status_code == 422


def test_migration_v8_ajoute_la_categorie(db_file):
    db.init_db(db_file)
    conn = sqlite3.connect(db_file)
    conn.executescript(
        """
        ALTER TABLE account DROP COLUMN abroad;
        ALTER TABLE account DROP COLUMN custody;
        DROP TABLE category_target;
        DROP TABLE target;
        DROP TABLE asset;
        CREATE TABLE asset (id INTEGER PRIMARY KEY, name TEXT NOT NULL, asset_class TEXT,
            isin TEXT, ticker TEXT, coingecko_id TEXT, currency TEXT, color INTEGER, ter TEXT);
        CREATE TABLE target (asset_id INTEGER PRIMARY KEY REFERENCES asset(id), pct TEXT);
        INSERT INTO asset (name, asset_class, ticker, currency) VALUES ('ETF', 'etf', 'F', 'EUR');
        ALTER TABLE operation DROP COLUMN crypto_swap;
        ALTER TABLE operation DROP COLUMN portfolio_value;
        ALTER TABLE operation DROP COLUMN offered;
        ALTER TABLE operation DROP COLUMN tax_withheld;
        ALTER TABLE account DROP COLUMN position;
        PRAGMA user_version = 8;
        """
    )
    conn.close()
    assert db.migrate() == (8, db.SCHEMA_VERSION)
    conn = sqlite3.connect(db_file)
    assert conn.execute("SELECT category FROM asset").fetchall() == [(None,)]
    assert conn.execute("SELECT count(*) FROM category_target").fetchone()[0] == 0
    conn.close()
