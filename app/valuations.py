"""Valeurs saisies à la main pour les actifs sans cours public (D-056), fonctions SQL.

L'utilisateur saisit la valeur totale d'une ligne à une date (« appartement : 250 000 € au
01/01/2026 »). L'app la range comme un prix unitaire : valeur ÷ quantité détenue ce jour-là,
dans `price_history` (source 'manuel') pour la courbe d'évolution, et la plus récente dans
`price` pour le total. Les achats et ventes d'un tel actif se saisissent en montant : la
quantité (des « parts ») vient du dernier prix unitaire connu, 1 au départ.
"""

import sqlite3
from datetime import UTC, date, datetime, time
from decimal import Decimal

from app.db import from_db, to_db
from app.models import Asset
from app.portfolio import compute_positions
from app.prices import TIMESTAMP
from app.repository import list_operations

SOURCE = "manuel"
ONE = Decimal("1")


def quantity_on(conn: sqlite3.Connection, asset_id: int, day: date) -> Decimal:
    """Quantité détenue de l'actif à la fin de `day`, tous comptes confondus."""
    ops = [o for o in list_operations(conn) if o.asset_id == asset_id and o.op_date <= day]
    return sum((p.quantity for p in compute_positions(ops)), Decimal("0"))


def unit_on(conn: sqlite3.Connection, asset_id: int, day: date) -> Decimal:
    """Prix d'une part à `day` : dernière valeur saisie à cette date ou avant, sinon prix de la
    dernière opération, sinon 1 (premier versement : une part = 1 unité de devise)."""
    row = conn.execute(
        "SELECT close FROM price_history WHERE asset_id = ? AND price_date <= ?"
        " ORDER BY price_date DESC LIMIT 1",
        (asset_id, day.isoformat()),
    ).fetchone()
    if row:
        return from_db(row["close"])
    row = conn.execute(
        "SELECT unit_price FROM operation WHERE asset_id = ? AND op_date <= ?"
        " AND unit_price IS NOT NULL ORDER BY op_date DESC, id DESC LIMIT 1",
        (asset_id, day.isoformat()),
    ).fetchone()
    return from_db(row["unit_price"]) if row and from_db(row["unit_price"]) > 0 else ONE


def refresh_latest(conn: sqlite3.Connection, asset: Asset) -> None:
    """`price` = dernière valeur saisie, datée de son jour (affichée « valeur au ... »)."""
    conn.execute("DELETE FROM price WHERE asset_id = ? AND source = ?", (asset.id, SOURCE))
    row = conn.execute(
        "SELECT price_date, close FROM price_history WHERE asset_id = ? AND source = ?"
        " ORDER BY price_date DESC LIMIT 1",
        (asset.id, SOURCE),
    ).fetchone()
    if row:
        day = datetime.combine(date.fromisoformat(row["price_date"]), time(), UTC)
        conn.execute(
            "INSERT INTO price (asset_id, fetched_at, price, currency, source)"
            " VALUES (?, ?, ?, ?, ?)",
            (asset.id, day.strftime(TIMESTAMP), row["close"], asset.currency, SOURCE),
        )


def save_valuation(conn: sqlite3.Connection, asset: Asset, day: date, value: Decimal) -> None:
    """Enregistre (ou remplace) la valeur totale de la ligne au jour `day`."""
    quantity = quantity_on(conn, asset.id, day)
    if quantity <= 0:
        raise ValueError(
            f"Aucune part de « {asset.name} » détenue le {day:%d/%m/%Y} : "
            "saisis d'abord l'achat ou les titres déjà détenus."
        )
    conn.execute(
        "INSERT OR REPLACE INTO price_history (asset_id, price_date, close, currency, source)"
        " VALUES (?, ?, ?, ?, ?)",
        (asset.id, day.isoformat(), to_db(value / quantity), asset.currency, SOURCE),
    )
    refresh_latest(conn, asset)


def delete_valuation(conn: sqlite3.Connection, asset: Asset, day: date) -> bool:
    deleted = conn.execute(
        "DELETE FROM price_history WHERE asset_id = ? AND price_date = ? AND source = ?",
        (asset.id, day.isoformat(), SOURCE),
    ).rowcount
    refresh_latest(conn, asset)
    return deleted > 0


def list_valuations(conn: sqlite3.Connection, asset: Asset) -> list[tuple[date, Decimal]]:
    """Valeurs saisies, la plus récente d'abord : (jour, valeur de la ligne ce jour-là)."""
    rows = conn.execute(
        "SELECT price_date, close FROM price_history WHERE asset_id = ? AND source = ?"
        " ORDER BY price_date DESC",
        (asset.id, SOURCE),
    ).fetchall()
    out = []
    for r in rows:
        day = date.fromisoformat(r["price_date"])
        out.append((day, from_db(r["close"]) * quantity_on(conn, asset.id, day)))
    return out
