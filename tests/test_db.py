import sqlite3
from decimal import Decimal

import pytest

from app import db


def test_init_db_cree_les_tables(db_file):
    assert db.init_db() == db.SCHEMA_VERSION
    with db.connect() as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {r["name"] for r in rows}
    assert {"account", "asset", "operation", "price", "fx_rate"} <= tables


def test_init_db_idempotent(db_file):
    db.init_db()
    assert db.init_db() == db.SCHEMA_VERSION


def test_version_inattendue_refusee(db_file):
    db.init_db()
    with db.connect() as conn:
        conn.execute("PRAGMA user_version = 99")
    with pytest.raises(RuntimeError, match="app.migrate"):
        db.init_db()


def test_cles_etrangeres_actives(db_file):
    db.init_db()
    with db.connect() as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO operation (account_id, asset_id, op_date, op_type, quantity, unit_price,"
            " currency) VALUES (999, 999, '2026-01-02', 'achat', '1', '10', 'EUR')"
        )


def test_decimal_aller_retour(db_file):
    """Données fictives : un compte, un ETF, un achat."""
    db.init_db()
    with db.connect() as conn:
        conn.execute("INSERT INTO account (name, kind) VALUES ('PEA test', 'pea')")
        conn.execute(
            "INSERT INTO asset (name, asset_class, ticker, currency)"
            " VALUES ('ETF fictif', 'etf', 'FAKE.PA', 'EUR')"
        )
        conn.execute(
            "INSERT INTO operation (account_id, asset_id, op_date, op_type, quantity, unit_price,"
            " currency) VALUES (1, 1, '2026-01-02', 'achat', ?, ?, 'EUR')",
            (db.to_db(Decimal("3")), db.to_db(Decimal("0.1"))),
        )
        row = conn.execute("SELECT quantity, unit_price FROM operation").fetchone()
    total = db.from_db(row["quantity"]) * db.from_db(row["unit_price"])
    assert total == Decimal("0.3")  # exact, contrairement à 3 * 0.1 en float


def test_to_db_refuse_les_float():
    with pytest.raises(TypeError):
        db.to_db(0.1)
