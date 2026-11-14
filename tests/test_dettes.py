"""Dettes et patrimoine net (D-107). Données FICTIVES."""

import sqlite3
from datetime import date
from decimal import Decimal as D

import pytest
from fastapi.testclient import TestClient

from app import db, debts
from app.main import app


def loan(**kw) -> debts.Debt:
    base = dict(
        id=1, name="Prêt fictif", kind="immobilier", institution=None, currency="EUR",
        principal=D("200000"), rate=D("0.036"), start_date=date(2024, 1, 31), months=240,
    )  # fmt: skip
    return debts.Debt(**{**base, **kw})


def test_mensualite_et_capital_restant_du():
    """200 000 € à 3,6 % sur 20 ans : mensualité ≈ 1 170,22 € ; après k échéances,
    capital = P(1+r)^k − m((1+r)^k − 1)/r ; rien à devoir après la dernière."""
    d = loan()
    m = debts.payment(d)
    assert round(m, 2) == D("1170.22")
    r = D("0.003")
    expected = D("200000") * (1 + r) ** 12 - m * ((1 + r) ** 12 - 1) / r
    # 12 échéances payées au 31 janvier 2025 (29 février 2024 compris).
    assert round(debts.balance_at(d, {}, date(2025, 1, 31)), 6) == round(expected, 6)
    assert debts.balance_at(d, {}, date(2025, 1, 30)) > expected  # la 12e pas encore payée
    assert debts.balance_at(d, {}, date(2044, 1, 31)) == 0
    assert debts.balance_at(d, {}, date(2023, 12, 1)) == 0  # avant le prêt
    assert debts.add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)


def test_capital_saisi_recale_le_calcul():
    d = loan()
    m = debts.payment(d)
    saisi = {date(2025, 6, 15): D("180000")}
    # Deux échéances après la saisie (30 juin, 31 juillet) partent de 180 000 €.
    after = (D("180000") * D("1.003") - m) * D("1.003") - m
    assert debts.balance_at(d, saisi, date(2025, 7, 31)) == after
    # Une date avant la saisie ignore celle-ci.
    assert debts.balance_at(d, saisi, date(2025, 6, 1)) == debts.balance_at(d, {}, date(2025, 6, 1))


def test_sans_duree_la_dette_ne_bouge_que_par_saisie():
    d = loan(months=None, rate=None, principal=D("5000"))
    assert debts.payment(d) is None
    assert debts.balance_at(d, {}, date(2030, 1, 1)) == D("5000")
    assert debts.balance_at(d, {date(2026, 1, 1): D("3000")}, date(2030, 1, 1)) == D("3000")
    # Taux zéro avec durée : capital divisé en parts égales.
    zero = loan(rate=D("0"), principal=D("1200"), months=12)
    assert debts.payment(zero) == D("100")
    assert debts.balance_at(zero, {}, date(2024, 4, 30)) == D("900")
    info = debts.summary(zero, {}, date(2024, 4, 30))
    assert (info["payments_left"], info["interest_left"], info["end_date"]) == (
        9,
        D("0"),
        date(2025, 1, 31),
    )


@pytest.fixture
def client(db_file):
    with TestClient(app) as c:
        yield c


def test_api_dettes_et_patrimoine_net(client):
    assert client.get("/api/portfolio").json()["debts_eur"] is None
    body = {
        "name": "Prêt conso fictif",
        "kind": "conso",
        "principal": "12000",
        "rate_pct": "0",
        "start_date": "2020-01-15",
        "months": 12,
    }
    res = client.post("/api/debts", json=body)
    assert res.status_code == 201, res.text
    debt_id = res.json()["id"]
    assert client.post("/api/debts", json={**body, "kind": "voiture"}).status_code == 422
    # Prêt fini : plus rien à devoir.
    out = client.get("/api/debts").json()
    assert out["debts"][0]["balance"] == "0.00" and out["total_eur"] == "0.00"
    # Prêt sans durée : le montant reste dû, recalé par un capital saisi.
    put = {**body, "months": None, "rate_pct": None}
    assert client.put(f"/api/debts/{debt_id}", json=put).status_code == 200
    res = client.post(
        f"/api/debts/{debt_id}/balances", json={"balance_date": "2021-01-01", "balance": "7000"}
    )
    assert res.status_code == 201, res.text
    bad = {"balance_date": "2019-01-01", "balance": "1"}
    assert client.post(f"/api/debts/{debt_id}/balances", json=bad).status_code == 422
    [row] = client.get("/api/debts").json()["debts"]
    assert (row["balance"], row["kind_label"], row["balances"][0]["balance"]) == (
        "7000.00",
        "Prêt conso",
        "7000.00",
    )
    assert client.get("/api/portfolio").json()["debts_eur"] == "7000.00"
    assert client.delete(f"/api/debts/{debt_id}/balances/2021-01-01").status_code == 204
    assert client.delete(f"/api/debts/{debt_id}").status_code == 204
    assert client.get("/api/debts").json()["debts"] == []


def test_migration_v20_ajoute_les_tables_de_dettes(db_file):
    db.init_db(db_file)
    conn = sqlite3.connect(db_file)
    conn.executescript(
        """
        DROP TABLE debt_balance; DROP TABLE debt;
        INSERT INTO account (id, name, kind) VALUES (1, 'PEA fictif', 'pea');
        PRAGMA user_version = 19;
        """
    )
    conn.close()
    assert db.migrate() == (19, db.SCHEMA_VERSION)
    conn = sqlite3.connect(db_file)
    assert conn.execute("SELECT name FROM account").fetchall() == [("PEA fictif",)]
    assert conn.execute("SELECT count(*) FROM debt").fetchone() == (0,)
    conn.close()
