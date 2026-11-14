"""Dettes (D-107) : capital restant dû d'un prêt, fonctions pures.

Prêt amortissable (taux et durée connus) : mensualités constantes, la première un mois après le
début, au même quantième (31 janvier -> 28 ou 29 février). Après chaque échéance, capital
restant dû = capital × (1 + taux/12) − mensualité. Sans durée, la dette ne baisse pas toute
seule (prêt in fine, crédit sans échéancier connu) : seul un capital restant dû saisi la change.
Un capital restant dû saisi à une date recale le calcul : les échéances suivantes partent de lui,
avec la même mensualité.
"""

import calendar
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

ZERO = Decimal("0")
TWELVE = Decimal("12")
KINDS = ("immobilier", "conso", "etudiant", "autre")


@dataclass(frozen=True)
class Debt:
    id: int
    name: str
    kind: str
    institution: str | None
    currency: str
    principal: Decimal
    rate: Decimal | None  # taux annuel nominal (0,035 pour 3,5 %)
    start_date: date
    months: int | None
    position: int = 0


def add_months(day: date, months: int) -> date:
    """Même quantième `months` mois plus tard, ramené au dernier jour du mois si besoin."""
    index = day.month - 1 + months
    year, month = day.year + index // 12, index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def payment(debt: Debt) -> Decimal | None:
    """Mensualité constante (hors assurance), None sans durée."""
    if not debt.months:
        return None
    r = (debt.rate or ZERO) / TWELVE
    if r == 0:
        return debt.principal / debt.months
    return debt.principal * r / (1 - (1 + r) ** -debt.months)


def due_dates(debt: Debt) -> list[date]:
    """Dates des échéances, de la première à la dernière."""
    return [add_months(debt.start_date, m) for m in range(1, (debt.months or 0) + 1)]


def balance_at(debt: Debt, balances: dict[date, Decimal], day: date) -> Decimal:
    """Capital restant dû le jour `day`, échéance du jour payée. `balances` : capital restant
    dû saisi par date ; le plus récent avant ou le jour même sert de départ."""
    known = [d for d in balances if debt.start_date <= d <= day]
    anchor = max(known, default=None)
    start, balance = (anchor, balances[anchor]) if anchor else (debt.start_date, debt.principal)
    pay = payment(debt)
    if pay is None or day < debt.start_date:
        return balance if day >= debt.start_date else ZERO
    r = (debt.rate or ZERO) / TWELVE
    for due in due_dates(debt):
        if start < due <= day:
            balance = balance * (1 + r) - pay
    # Dernière échéance passée : plus rien à devoir (arrondis compris).
    if debt.months and day >= add_months(debt.start_date, debt.months):
        return ZERO
    return max(balance, ZERO)


def summary(debt: Debt, balances: dict[date, Decimal], day: date) -> dict:
    """Capital restant dû, mensualité, fin, échéances restantes et intérêts restant à payer
    (somme des mensualités restantes − capital restant dû)."""
    balance = balance_at(debt, balances, day)
    pay = payment(debt)
    left = [d for d in due_dates(debt) if d > day]
    interest = None if pay is None else max(pay * len(left) - balance, ZERO)
    return {
        "balance": balance,
        "payment": pay,
        "end_date": add_months(debt.start_date, debt.months) if debt.months else None,
        "payments_left": len(left) if debt.months else None,
        "interest_left": interest,
        "repaid_share": None if debt.principal <= 0 else 1 - balance / debt.principal,
    }
