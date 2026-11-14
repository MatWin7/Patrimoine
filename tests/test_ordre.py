"""Ordre choisi des comptes et des actifs (D-070) : données fictives uniquement."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app


@pytest.fixture
def client(db_file):
    with TestClient(app) as c:
        yield c


def names(client, path):
    return [x["name"] for x in client.get(path).json()]


def test_nouveau_compte_en_fin_de_liste_puis_ordre_choisi(client):
    ids = [
        client.post("/api/accounts", json={"name": n, "kind": "cto"}).json()["id"]
        for n in ("Zèbre fictif", "Alpha fictif", "Milieu fictif")
    ]
    assert names(client, "/api/accounts") == ["Zèbre fictif", "Alpha fictif", "Milieu fictif"]
    res = client.put("/api/accounts/order", json={"ids": [ids[2], ids[0], ids[1]]})
    assert res.status_code == 204
    assert names(client, "/api/accounts") == ["Milieu fictif", "Zèbre fictif", "Alpha fictif"]


def test_ordre_refuse_si_la_liste_a_change(client):
    a = client.post("/api/assets", json={"name": "A", "asset_class": "autre", "currency": "EUR"})
    b = client.post("/api/assets", json={"name": "B", "asset_class": "autre", "currency": "EUR"})
    a, b = a.json()["id"], b.json()["id"]
    for ids in ([a], [a, b, 999], [a, a]):
        assert client.put("/api/assets/order", json={"ids": ids}).status_code == 409
    assert client.put("/api/assets/order", json={"ids": [b, a]}).status_code == 204
    assert names(client, "/api/assets") == ["B", "A"]


def test_migration_v10_garde_l_ordre_alphabetique(db_file):
    db.init_db(db_file)
    conn = sqlite3.connect(db_file)
    conn.executescript(
        """
        ALTER TABLE account DROP COLUMN abroad;
        ALTER TABLE account DROP COLUMN custody;
        ALTER TABLE account DROP COLUMN position;
        ALTER TABLE asset DROP COLUMN position;
        ALTER TABLE operation DROP COLUMN offered;
        ALTER TABLE operation DROP COLUMN tax_withheld;
        INSERT INTO account (name, kind) VALUES ('Zèbre fictif', 'cto'), ('Alpha fictif', 'pea');
        INSERT INTO asset (name, asset_class, currency)
            VALUES ('Y', 'autre', 'EUR'), ('X', 'autre', 'EUR'), ('X', 'autre', 'EUR');
        PRAGMA user_version = 10;
        """
    )
    conn.close()
    assert db.migrate() == (10, db.SCHEMA_VERSION)
    conn = sqlite3.connect(db_file)
    assert conn.execute("SELECT name, position FROM account ORDER BY id").fetchall() == [
        ("Zèbre fictif", 2),
        ("Alpha fictif", 1),
    ]
    assert conn.execute("SELECT id, position FROM asset ORDER BY id").fetchall() == [
        (1, 3),
        (2, 1),
        (3, 2),
    ]
    conn.close()
