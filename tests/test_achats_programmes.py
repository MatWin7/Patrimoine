"""Achats programmés (D-055) : échéances proposées, validées ou ignorées. Données FICTIVES."""

import sqlite3
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.prices import Price, save_price
from app.recurring import MAX_PENDING, pending_dates, period_key, suggested_quantity


def test_echeances_mensuelles_depuis_le_debut():
    today = date(2025, 4, 10)
    assert pending_dates(date(2025, 1, 15), "mensuel", 5, None, today) == [
        date(2025, 2, 5),  # le 5 janvier précède le début
        date(2025, 3, 5),
        date(2025, 4, 5),
    ]
    # Le 15 avril n'est pas encore arrivé.
    assert pending_dates(date(2025, 1, 1), "mensuel", 15, None, today)[-1] == date(2025, 3, 15)


def test_echeances_apres_la_derniere_traitee():
    today = date(2025, 4, 10)
    last = date(2025, 3, 5)
    assert pending_dates(date(2025, 1, 1), "mensuel", 5, last, today) == [date(2025, 4, 5)]
    # Jour changé du 5 au 8 après avoir traité mars : mars n'est pas reproposé.
    assert pending_dates(date(2025, 1, 1), "mensuel", 8, last, today) == [date(2025, 4, 8)]
    assert pending_dates(date(2025, 1, 1), "mensuel", 5, date(2025, 4, 5), today) == []


def test_echeances_trimestrielles():
    due = pending_dates(date(2025, 1, 10), "trimestriel", 15, None, date(2025, 11, 1))
    assert due == [date(2025, 1, 15), date(2025, 4, 15), date(2025, 7, 15), date(2025, 10, 15)]


def test_echeances_hebdomadaires():
    # Le 1er janvier 2025 est un mercredi ; jour 1 = lundi.
    due = pending_dates(date(2025, 1, 1), "hebdomadaire", 1, None, date(2025, 1, 20))
    assert due == [date(2025, 1, 6), date(2025, 1, 13), date(2025, 1, 20)]
    # Passé du lundi au vendredi après avoir traité le lundi 13 : semaine 3 déjà faite.
    after = pending_dates(date(2025, 1, 1), "hebdomadaire", 5, date(2025, 1, 13), date(2025, 1, 25))
    assert after == [date(2025, 1, 24)]
    assert period_key(date(2025, 1, 13), "hebdomadaire") == "2025-W03"


def test_echeances_limitees():
    due = pending_dates(date(2020, 1, 1), "mensuel", 1, None, date(2025, 6, 1))
    assert len(due) == MAX_PENDING
    assert (due[0], due[-1]) == (date(2024, 7, 1), date(2025, 6, 1))


@pytest.mark.parametrize(
    ("amount", "price", "asset_class", "expected"),
    [
        ("100", "30", "etf", D("3")),  # parts entières, arrondi vers le bas
        ("100", "250", "action", D("0.4")),  # moins d'une part : fraction
        ("100", "60000", "crypto", D("0.00166666")),
        ("100", None, "etf", None),  # cours inconnu : rien à proposer
    ],
)
def test_quantite_proposee(amount, price, asset_class, expected):
    price = D(price) if price else None
    assert suggested_quantity(D(amount), price, asset_class) == expected


@pytest.fixture
def client(db_file):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def pea(client):
    account = client.post("/api/accounts", json={"name": "PEA test", "kind": "pea"}).json()
    asset = client.post(
        "/api/assets",
        json={"name": "ETF fictif", "asset_class": "etf", "ticker": "fake.pa"},
    ).json()
    return account["id"], asset["id"]


def months_ago(n: int) -> date:
    today = date.today()
    year, month = today.year, today.month - n
    while month < 1:
        year, month = year - 1, month + 12
    return date(year, month, 1)


def programme(client, pea, start: date, amount="100", day=1):
    account_id, asset_id = pea
    res = client.post(
        "/api/recurring",
        json={
            "account_id": account_id,
            "asset_id": asset_id,
            "amount": amount,
            "day": day,
            "start_date": start.isoformat(),
        },
    )
    assert res.status_code == 201, res.text
    return res.json()


def test_echeances_proposees_avec_le_cours_en_cache(client, pea, db_file):
    conn = sqlite3.connect(db_file)
    with conn:
        save_price(conn, pea[1], Price(D("30"), "EUR", "yahoo", datetime.now(UTC)))
    conn.close()
    r = programme(client, pea, months_ago(2))
    data = client.get("/api/recurring").json()
    assert [i["id"] for i in data["items"]] == [r["id"]]
    assert data["items"][0]["asset"] == "ETF fictif"
    pending = data["pending"]
    assert [p["due_date"] for p in pending] == [months_ago(n).isoformat() for n in (2, 1, 0)]
    assert pending[0]["price"] == "30" and pending[0]["quantity"] == "3"


def test_valider_cree_un_achat_et_passe_au_mois_suivant(client, pea):
    r = programme(client, pea, months_ago(1))
    first, second = (months_ago(n).isoformat() for n in (1, 0))
    confirm = {
        "due_date": first,
        "op_date": months_ago(1).isoformat(),
        "quantity": "3",
        "unit_price": "31.5",
        "fees": "0.5",
    }
    res = client.post(f"/api/recurring/{r['id']}/confirm", json=confirm)
    assert res.status_code == 201, res.text
    [op] = client.get("/api/operations").json()
    assert (op["op_type"], op["quantity"], op["unit_price"], op["fees"]) == (
        "achat",
        "3",
        "31.5",
        "0.5",
    )
    assert op["note"] == "Achat programmé"
    assert [p["due_date"] for p in client.get("/api/recurring").json()["pending"]] == [second]
    # Valider deux fois le même mois est refusé : il est déjà traité.
    assert client.post(f"/api/recurring/{r['id']}/confirm", json=confirm).status_code == 409


def test_echeances_traitees_dans_l_ordre(client, pea):
    r = programme(client, pea, months_ago(1))
    recent = months_ago(0).isoformat()
    res = client.post(f"/api/recurring/{r['id']}/skip", json={"due_date": recent})
    assert res.status_code == 409
    assert months_ago(1).strftime("%d/%m/%Y") in res.json()["detail"]


def test_ignorer_n_enregistre_rien(client, pea):
    r = programme(client, pea, months_ago(0))
    due = months_ago(0).isoformat()
    res = client.post(f"/api/recurring/{r['id']}/skip", json={"due_date": due})
    assert res.status_code == 204
    assert client.get("/api/recurring").json()["pending"] == []
    assert client.get("/api/operations").json() == []


def test_modifier_et_supprimer_garde_les_achats(client, pea):
    r = programme(client, pea, months_ago(0))
    due = months_ago(0).isoformat()
    confirm = {"due_date": due, "op_date": date.today().isoformat(), "quantity": "1"}
    res = client.post(f"/api/recurring/{r['id']}/confirm", json={**confirm, "unit_price": "10"})
    assert res.status_code == 201
    body = {
        "account_id": pea[0],
        "asset_id": pea[1],
        "amount": "250",
        "day": 28,
        "start_date": months_ago(0).isoformat(),
    }
    res = client.patch(f"/api/recurring/{r['id']}", json=body)
    assert res.json()["amount"] == "250" and res.json()["last_due"] == due
    assert client.delete(f"/api/recurring/{r['id']}").status_code == 204
    assert client.get("/api/recurring").json() == {"items": [], "pending": []}
    assert len(client.get("/api/operations").json()) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"day": 29},
        {"amount": "0"},
        {"amount": "-5"},
        {"asset_id": 999},
        {"account_id": 999},
        {"frequency": "quotidien"},
        {"frequency": "hebdomadaire", "day": 8},
    ],
)
def test_modele_invalide_refuse(client, pea, change):
    body = {
        "account_id": pea[0],
        "asset_id": pea[1],
        "amount": "100",
        "day": 5,
        "start_date": "2025-01-01",
        **change,
    }
    assert client.post("/api/recurring", json=body).status_code in (404, 422)


def test_migration_v5_ajoute_la_table(db_file):
    db.init_db(db_file)
    conn = sqlite3.connect(db_file)
    conn.executescript(
        "ALTER TABLE account DROP COLUMN abroad;"
        "ALTER TABLE account DROP COLUMN custody;"
        "DROP TABLE recurring;"
        "ALTER TABLE operation DROP COLUMN crypto_swap;"
        "ALTER TABLE operation DROP COLUMN portfolio_value;"
        "ALTER TABLE operation DROP COLUMN offered;"
        "ALTER TABLE operation DROP COLUMN tax_withheld; PRAGMA user_version = 5;"
    )
    conn.close()
    assert db.migrate() == (5, db.SCHEMA_VERSION)
    conn = sqlite3.connect(db_file)
    assert conn.execute("SELECT count(*) FROM recurring").fetchone()[0] == 0
    conn.close()


def test_prix_propose_cloture_du_jour_de_l_echeance(client, pea, db_file):
    """Échéance ancienne : la clôture en cache de ce jour-là (ou d'avant) plutôt que le cours
    d'aujourd'hui."""
    start = months_ago(1)
    conn = sqlite3.connect(db_file)
    with conn:
        save_price(conn, pea[1], Price(D("30"), "EUR", "yahoo", datetime.now(UTC)))
        conn.execute(
            "INSERT INTO price_history (asset_id, price_date, close, currency, source)"
            " VALUES (?, ?, '24', 'EUR', 'yahoo')",
            (pea[1], start.replace(day=1).isoformat()),
        )
    conn.close()
    programme(client, pea, start, day=1)
    pending = client.get("/api/recurring").json()["pending"]
    assert [(p["price"], p["quantity"]) for p in pending] == [("24", "4"), ("24", "4")]


def test_hebdomadaire_par_l_api(client, pea):
    start = date.today() - timedelta(days=13)
    body = {
        "account_id": pea[0],
        "asset_id": pea[1],
        "amount": "20",
        "frequency": "hebdomadaire",
        "day": start.isoweekday(),
        "start_date": start.isoformat(),
    }
    r = client.post("/api/recurring", json=body).json()
    assert r["frequency"] == "hebdomadaire"
    pending = client.get("/api/recurring").json()["pending"]
    assert [p["due_date"] for p in pending] == [
        (start + timedelta(days=d)).isoformat() for d in (0, 7)
    ]
