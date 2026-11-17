"""Routes des dettes (D-107). Patrimoine net = actifs − capital restant dû. Calcul hors ligne,
rien ne part sur le réseau."""

import sqlite3
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, HTTPException

from app import db, debts, prices, repository
from app.api.aave import borrows_report
from app.api.common import backup_before_delete, money_or_none, pct, reorder
from app.portfolio import ZERO, convert
from app.schemas import DebtBalanceIn, DebtIn, OrderIn, money, plain

router = APIRouter()


# --- Dettes (D-107) -----------------------------------------------------------------------
# Patrimoine net = actifs − capital restant dû. Calcul hors ligne, rien ne part sur le réseau.
DEBT_KINDS = {
    "immobilier": "Crédit immobilier",
    "conso": "Prêt conso",
    "etudiant": "Prêt étudiant",
    "autre": "Autre",
}


def _debt_out(d: debts.Debt, balances: dict[date, Decimal], rates, today: date) -> dict:
    info = debts.summary(d, balances, today)
    return {
        "id": d.id,
        "name": d.name,
        "kind": d.kind,
        "kind_label": DEBT_KINDS[d.kind],
        "institution": d.institution,
        "currency": d.currency,
        "principal": money(d.principal),
        "rate_pct": None if d.rate is None else plain((d.rate * 100).normalize()),
        "start_date": d.start_date.isoformat(),
        "months": d.months,
        "balance": money(info["balance"]),
        "balance_eur": money_or_none(convert(info["balance"], d.currency, "EUR", rates)),
        "payment": money_or_none(info["payment"]),
        "end_date": info["end_date"].isoformat() if info["end_date"] else None,
        "payments_left": info["payments_left"],
        "interest_left": money_or_none(info["interest_left"]),
        "repaid_pct": pct(info["repaid_share"]),
        "balances": [
            {"date": day.isoformat(), "balance": money(v)}
            for day, v in sorted(balances.items(), reverse=True)
        ],
    }


def debts_total_eur(conn: sqlite3.Connection, rates, today: date) -> Decimal | None:
    """Capital restant dû de toutes les dettes en EUR, emprunts Aave compris (D-108) ; None
    sans dette."""
    found = repository.list_debts(conn)
    aave_debt = borrows_report(conn, today)["debt_eur"]
    if not found:
        return aave_debt
    balances = repository.debt_balances(conn)
    rates = {**prices.stored_rates(conn, {d.currency for d in found}), **rates}
    total = ZERO
    for d in found:
        value = convert(
            debts.balance_at(d, balances.get(d.id, {}), today), d.currency, "EUR", rates
        )
        total += value or ZERO
    return total + (aave_debt or ZERO)


def _debt_values(body: DebtIn) -> dict:
    values = body.model_dump()
    rate = values.pop("rate_pct")
    values["rate"] = None if rate is None else rate / 100
    return values


@router.get("/api/debts")
def list_debts() -> dict:
    """Dettes avec capital restant dû du jour, mensualité, fin et intérêts restant à payer."""
    today = date.today()
    with db.connect() as conn:
        found = repository.list_debts(conn)
        balances = repository.debt_balances(conn)
        rates = prices.stored_rates(conn, {d.currency for d in found})
    conn.close()
    rows = [_debt_out(d, balances.get(d.id, {}), rates, today) for d in found]
    total = sum((Decimal(r["balance_eur"]) for r in rows if r["balance_eur"]), ZERO)
    # Emprunts Aave (D-108) : rappelés ici en lecture, gérés dans Rendements crypto.
    with db.connect() as conn:
        aave_rows = borrows_report(conn, today)["borrows"]
    conn.close()
    total += sum((Decimal(r["value_eur"]) for r in aave_rows if r["value_eur"]), ZERO)
    return {"debts": rows, "aave": aave_rows, "total_eur": money(total)}


@router.post("/api/debts", status_code=201)
def create_debt(body: DebtIn) -> dict:
    with db.connect() as conn:
        debt = repository.create_debt(conn, _debt_values(body))
    conn.close()
    return {"id": debt.id}


@router.put("/api/debts/order", status_code=204)
def reorder_debts(body: OrderIn) -> None:
    reorder("debt", body)


@router.put("/api/debts/{debt_id}")
def update_debt(debt_id: int, body: DebtIn) -> dict:
    with db.connect() as conn:
        if repository.update_debt(conn, debt_id, _debt_values(body)) is None:
            raise HTTPException(status_code=404, detail="Dette inconnue.")
    conn.close()
    return {"id": debt_id}


@router.delete("/api/debts/{debt_id}", status_code=204)
def delete_debt(debt_id: int) -> None:
    """Supprime la dette et ses capitaux saisis, sauvegarde de la base juste avant (D-074)."""
    with db.connect() as conn:
        if repository.get_debt(conn, debt_id) is None:
            raise HTTPException(status_code=404, detail="Dette inconnue.")
    conn.close()
    backup_before_delete()
    with db.connect() as conn:
        repository.delete_debt(conn, debt_id)
    conn.close()


@router.post("/api/debts/{debt_id}/balances", status_code=201)
def save_debt_balance(debt_id: int, body: DebtBalanceIn) -> None:
    """Capital restant dû lu sur un relevé : les échéances suivantes partent de lui."""
    with db.connect() as conn:
        debt = repository.get_debt(conn, debt_id)
        if debt is None:
            raise HTTPException(status_code=404, detail="Dette inconnue.")
        if body.balance_date < debt.start_date:
            raise HTTPException(status_code=422, detail="Date antérieure au début du prêt.")
        repository.save_debt_balance(conn, debt_id, body.balance_date, body.balance)
    conn.close()


@router.delete("/api/debts/{debt_id}/balances/{balance_date}", status_code=204)
def delete_debt_balance(debt_id: int, balance_date: date) -> None:
    with db.connect() as conn:
        if not repository.delete_debt_balance(conn, debt_id, balance_date):
            raise HTTPException(status_code=404, detail="Capital saisi inconnu.")
    conn.close()
