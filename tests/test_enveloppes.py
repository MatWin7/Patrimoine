"""Nouvelles enveloppes et actifs à valeur saisie (D-056). Données FICTIVES."""

import sqlite3
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app


@pytest.fixture
def client(db_file):
    with TestClient(app) as c:
        yield c


def compte(client, name, kind, cash="0"):
    # Livret : solde saisi ; autres comptes : versements, d'où les espèces découlent (D-062).
    field = "cash" if kind == "livret" else "deposits_before"
    res = client.post("/api/accounts", json={"name": name, "kind": kind, field: cash})
    assert res.status_code == 201, res.text
    return res.json()["id"]


def actif(client, name, asset_class, **ids):
    res = client.post("/api/assets", json={"name": name, "asset_class": asset_class, **ids})
    assert res.status_code == 201, res.text
    return res.json()["id"]


def montant(client, account_id, asset_id, day, amount, op_type="achat"):
    body = {
        "account_id": account_id,
        "asset_id": asset_id,
        "op_date": day,
        "op_type": op_type,
        "amount": amount,
    }
    return client.post("/api/operations", json=body)


def valeur(client, asset_id, day, value):
    return client.post(
        f"/api/assets/{asset_id}/valuations", json={"value_date": day, "value": value}
    )


def ligne(client, name):
    [p] = [p for p in client.get("/api/portfolio").json()["positions"] if p["asset"] == name]
    return p


def test_actif_sans_cours_seulement_hors_titres_cotes(client):
    actif(client, "Fonds euros fictif", "fonds_euros")
    actif(client, "Appartement fictif", "immobilier")
    res = client.post("/api/assets", json={"name": "ETF sans ticker", "asset_class": "etf"})
    assert res.status_code == 422


def test_fonds_euros_en_montants_puis_valeur_saisie(client):
    av = compte(client, "Assurance-vie fictive", "assurance_vie")
    fe = actif(client, "Fonds euros fictif", "fonds_euros")
    assert montant(client, av, fe, "2025-01-10", "1000").status_code == 201
    p = ligne(client, "Fonds euros fictif")
    assert (p["value"], p["cost"]) == ("1000.00", "1000.00")  # une part = 1 € au départ

    assert valeur(client, fe, "2025-12-31", "1050").status_code == 201
    p = ligne(client, "Fonds euros fictif")
    assert (p["value"], p["gain"], p["price_source"]) == ("1050.00", "50.00", "manuel")
    assert p["price_error"] is None  # aucun appel réseau tenté pour un actif sans cours

    # Nouveau versement : converti en parts au prix du jour (1,05 €), la valeur suit.
    assert montant(client, av, fe, "2026-01-05", "525").status_code == 201
    p = ligne(client, "Fonds euros fictif")
    assert (p["value"], p["cost"]) == ("1575.00", "1525.00")

    # Rachat partiel en montant.
    assert montant(client, av, fe, "2026-02-01", "105", "vente").status_code == 201
    assert ligne(client, "Fonds euros fictif")["value"] == "1470.00"


def test_liste_et_suppression_des_valeurs(client):
    immo = compte(client, "Biens fictifs", "immobilier")
    appart = actif(client, "Appartement fictif", "immobilier")
    titres = {
        "account_id": immo,
        "asset_id": appart,
        "op_date": "2024-06-01",
        "op_type": "solde_initial",
        "amount": "200000",
    }
    assert client.post("/api/operations", json=titres).status_code == 201
    valeur(client, appart, "2025-01-01", "210000")
    valeur(client, appart, "2026-01-01", "215000")
    valeur(client, appart, "2026-01-01", "212000")  # même jour : remplace
    rows = client.get(f"/api/assets/{appart}/valuations").json()
    assert [(r["date"], r["value"]) for r in rows] == [
        ("2026-01-01", "212000.00"),
        ("2025-01-01", "210000.00"),
    ]
    assert ligne(client, "Appartement fictif")["value"] == "212000.00"
    assert client.delete(f"/api/assets/{appart}/valuations/2026-01-01").status_code == 204
    assert ligne(client, "Appartement fictif")["value"] == "210000.00"
    assert client.delete(f"/api/assets/{appart}/valuations/2026-01-01").status_code == 404
    # Les espèces du compte ne bougent pas : des titres déjà détenus, pas un achat.
    [acc] = client.get("/api/accounts").json()
    assert acc["cash"] == "0"


def test_valeur_refusee(client):
    av = compte(client, "AV fictive", "assurance_vie")
    fe = actif(client, "Fonds euros fictif", "fonds_euros")
    etf = actif(client, "ETF fictif", "etf", ticker="FAKE.PA")
    res = valeur(client, fe, "2025-01-01", "100")
    assert res.status_code == 422 and "Aucune part" in res.json()["detail"]
    assert valeur(client, etf, "2025-01-01", "100").status_code == 422
    futur = date.today().replace(year=date.today().year + 1).isoformat()
    assert valeur(client, fe, futur, "1").status_code == 422
    # Un actif coté se saisit toujours en quantité.
    assert montant(client, av, etf, "2025-01-01", "100").status_code == 422


def test_livret_dans_la_repartition(client):
    compte(client, "Livret A fictif", "livret", cash="5000")
    compte(client, "PEA fictif", "pea", cash="300")
    data = client.get("/api/portfolio").json()
    classes = {p["label"]: p["value"] for p in data["groupings"]["classe"]["all"]}
    assert classes == {"Livrets": "5000.00", "Espèces": "300.00"}
    lignes = {p["label"] for p in data["groupings"]["ligne"]["all"]}
    assert lignes == {"Livret A fictif", "Espèces"}


def test_migration_v6_garde_prix_et_achats_programmes(db_file):
    """Recréer account et asset ne doit rien effacer en cascade (prix, achats programmés)."""
    db.init_db(db_file)
    conn = sqlite3.connect(db_file)
    conn.executescript(
        """
        ALTER TABLE account DROP COLUMN abroad;
        ALTER TABLE account DROP COLUMN custody;
        INSERT INTO account (name, kind) VALUES ('PEA fictif', 'pea');
        INSERT INTO asset (name, asset_class, ticker, currency, color)
            VALUES ('ETF fictif', 'etf', 'F.PA', 'EUR', 3);
        INSERT INTO operation (account_id, asset_id, op_date, op_type, quantity, unit_price,
            currency) VALUES (1, 1, '2025-01-02', 'achat', '2', '10', 'EUR');
        INSERT INTO price VALUES (1, '2025-01-03T10:00:00Z', '11', 'EUR', 'yahoo');
        INSERT INTO price_history VALUES (1, '2025-01-02', '10.5', 'EUR', 'yahoo');
        INSERT INTO recurring (account_id, asset_id, amount, day, start_date)
            VALUES (1, 1, '100', 5, '2025-01-01');
        ALTER TABLE operation DROP COLUMN crypto_swap;
        ALTER TABLE operation DROP COLUMN portfolio_value;
        ALTER TABLE operation DROP COLUMN offered;
        ALTER TABLE operation DROP COLUMN tax_withheld;
        PRAGMA user_version = 6;
        """
    )
    conn.close()
    assert db.migrate() == (6, db.SCHEMA_VERSION)
    conn = sqlite3.connect(db_file)
    counts = [
        conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        for t in ("account", "asset", "operation", "price", "price_history", "recurring")
    ]
    color = conn.execute("SELECT color FROM asset").fetchone()[0]
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()
    assert counts == [1, 1, 1, 1, 1, 1] and color == 3
    conn = db.connect(db_file)
    with conn:
        conn.execute("INSERT INTO account (name, kind) VALUES ('AV fictive', 'assurance_vie')")
        conn.execute(
            "INSERT INTO asset (name, asset_class, currency) VALUES ('FE', 'fonds_euros', 'EUR')"
        )
    conn.close()
