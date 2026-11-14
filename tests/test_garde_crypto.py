"""Garde d'un compte crypto et comptes à l'étranger (D-104). Données FICTIVES."""

import re
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import custody, db
from app.main import app

MIGRATION = Path(__file__).parent.parent / "app" / "migrations" / "019_garde_et_etranger.sql"


@pytest.fixture
def client(db_file):
    with TestClient(app) as c:
        yield c


def post(client, path, body, status=201):
    res = client.post(path, json=body)
    assert res.status_code == status, res.text
    return res.json()


def test_garde_devinee_d_apres_le_nom_ou_l_etablissement():
    assert custody.guess_custody("Mon Ledger", None) == "wallet"
    assert custody.guess_custody("Crypto", "Kraken") == "exchange"
    assert custody.guess_custody("Coinbase Wallet", None) == "wallet"  # pas la plateforme
    assert custody.guess_custody("Crypto fictif", "Inconnue") is None  # jamais de supposition
    assert custody.guess_abroad("Crypto", "Binance", "exchange")
    assert not custody.guess_abroad("Crypto", "Paymium", "exchange")  # plateforme française
    assert not custody.guess_abroad("Binance", None, "wallet")  # un portefeuille ne se déclare pas
    assert custody.guess_abroad("CTO", "Trade Republic")


def test_la_migration_reprend_les_listes_du_code():
    """Le script SQL devine comme app/custody.py : chaque nom des listes y figure."""
    names = set(re.findall(r"LIKE '%([^%]+)%'", MIGRATION.read_text(encoding="utf-8")))
    assert set(custody.WALLETS) | set(custody.EXCHANGES) | set(custody.ABROAD) <= names


def test_migration_v19_devine_les_comptes_existants(db_file):
    db.init_db(db_file)
    conn = sqlite3.connect(db_file)
    conn.executescript(
        """
        ALTER TABLE account DROP COLUMN abroad; ALTER TABLE account DROP COLUMN custody;
        INSERT INTO account (id, name, kind, institution) VALUES
            (1, 'Binance fictif', 'crypto', NULL),
            (2, 'Clé fictive', 'crypto', 'Ledger'),
            (3, 'Crypto fictif', 'crypto', 'Inconnue'),
            (4, 'CTO fictif', 'cto', 'Trade Republic'),
            (5, 'PEA fictif', 'pea', 'Banque fictive'),
            (6, 'Aave Ethereum', 'crypto', 'Aave');
        PRAGMA user_version = 18;
        """
    )
    conn.close()
    assert db.migrate() == (18, db.SCHEMA_VERSION)
    conn = sqlite3.connect(db_file)
    rows = conn.execute("SELECT id, custody, abroad FROM account ORDER BY id").fetchall()
    conn.close()
    assert rows == [
        (1, "exchange", 1),
        (2, "wallet", 0),
        (3, None, 0),  # à préciser
        (4, None, 1),  # pas de garde hors crypto, mais compte à déclarer (3916)
        (5, None, 0),
        (6, "wallet", 0),
    ]


def test_api_creation_modification_et_regles(client):
    binance = post(
        client, "/api/accounts", {"name": "Crypto", "kind": "crypto", "institution": "Binance"}
    )
    assert (binance["custody"], binance["abroad"]) == ("exchange", True)
    wallet = post(
        client,
        "/api/accounts",
        {"name": "Clé", "kind": "crypto", "custody": "wallet", "abroad": True},
    )
    assert (wallet["custody"], wallet["abroad"]) == ("wallet", False)  # rien à déclarer
    cto = post(client, "/api/accounts", {"name": "CTO", "kind": "cto", "custody": "exchange"})
    assert cto["custody"] is None  # la garde n'existe que pour la crypto
    post(client, "/api/accounts", {"name": "X", "kind": "crypto", "custody": "banque"}, status=422)
    # Modification sans ces champs : gardés ; avec : remplacés.
    res = client.patch(
        f"/api/accounts/{binance['id']}", json={"name": "Crypto 2", "kind": "crypto"}
    )
    assert (res.json()["custody"], res.json()["abroad"]) == ("exchange", True)
    res = client.patch(
        f"/api/accounts/{binance['id']}",
        json={"name": "Crypto 2", "kind": "crypto", "custody": None, "abroad": False},
    )
    assert (res.json()["custody"], res.json()["abroad"]) == (None, False)


def test_fiscalite_liste_les_comptes_a_declarer(client):
    post(
        client,
        "/api/accounts",
        {"name": "Crypto", "kind": "crypto", "institution": "Kraken", "opened_on": "2024-03-01"},
    )
    post(client, "/api/accounts", {"name": "CTO", "kind": "cto", "institution": "Trade Republic"})
    post(client, "/api/accounts", {"name": "PEA", "kind": "pea", "institution": "Banque fictive"})
    post(client, "/api/accounts", {"name": "Clé", "kind": "crypto", "institution": "Ledger"})
    post(client, "/api/accounts", {"name": "Autre crypto", "kind": "crypto"})
    out = client.get("/api/tax").json()
    assert [(r["account"], r["form"], r["opened_on"]) for r in out["foreign_accounts"]] == [
        ("Crypto", "3916-bis", "2024-03-01"),
        ("CTO", "3916", None),
    ]
    assert out["custody_unknown"] == ["Autre crypto"]
