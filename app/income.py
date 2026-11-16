"""Revenus (dividendes, coupons) et frais payés, fonctions pures (D-057, D-060).

Montants convertis en EUR au taux de change du jour de l'opération quand l'historique des
taux est fourni (`history`, D-081 : les mêmes euros que dans Fiscalité, D-138), sinon au dernier
taux connu ; une opération dans une devise sans taux est laissée de côté.
"""

from collections import defaultdict
from collections.abc import Iterable
from datetime import date, timedelta
from decimal import Decimal

from app.models import Operation
from app.portfolio import RateHistory, convert

ZERO = Decimal("0")
HUNDRED = Decimal("100")


def _eur(
    amount: Decimal,
    currency: str,
    rates: dict[str, Decimal],
    history: RateHistory | None = None,
    day: date | None = None,
) -> Decimal | None:
    if history is not None and day is not None:
        return history.to_eur(amount, currency, day)
    return convert(amount, currency, "EUR", rates)


def income_summary(
    operations: Iterable[Operation],
    rates: dict[str, Decimal],
    today: date,
    history: RateHistory | None = None,
) -> dict:
    """Dividendes reçus en EUR : par mois (et par ligne dans le mois), par année, par ligne ;
    12 derniers mois, les 12 mois d'avant et année en cours. `by_asset` = {id d'actif: (total,
    12 derniers mois)} ; `withheld` = impôt retenu à la source par actif (D-076)."""
    since = today - timedelta(days=365)
    before = since - timedelta(days=365)
    by_month: dict[str, Decimal] = defaultdict(lambda: ZERO)
    by_month_asset: dict[str, dict[int, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))
    by_year: dict[int, Decimal] = defaultdict(lambda: ZERO)
    by_asset: dict[int, list[Decimal]] = defaultdict(lambda: [ZERO, ZERO])
    withheld: dict[int, Decimal] = defaultdict(lambda: ZERO)
    total = last_12m = prev_12m = ytd = ZERO
    for op in operations:
        if op.op_type != "dividende":
            continue
        amount = _eur(op.amount, op.currency, rates, history, op.op_date)
        if amount is None:
            continue
        month = f"{op.op_date:%Y-%m}"
        by_month[month] += amount
        by_month_asset[month][op.asset_id] += amount
        by_year[op.op_date.year] += amount
        by_asset[op.asset_id][0] += amount
        total += amount
        if op.tax_withheld:
            withheld[op.asset_id] += (
                _eur(op.tax_withheld, op.currency, rates, history, op.op_date) or ZERO
            )
        if op.op_date > since:
            last_12m += amount
            by_asset[op.asset_id][1] += amount
        elif op.op_date > before:
            prev_12m += amount
        if op.op_date.year == today.year:
            ytd += amount
    return {
        "total": total,
        "last_12m": last_12m,
        "prev_12m": prev_12m,
        "ytd": ytd,
        "by_month": dict(sorted(by_month.items())),
        "by_month_asset": {m: dict(v) for m, v in by_month_asset.items()},
        "by_year": dict(sorted(by_year.items())),
        "by_asset": {k: tuple(v) for k, v in by_asset.items()},
        "withheld": dict(withheld),
    }


def change_ratio(current: Decimal, previous: Decimal) -> Decimal | None:
    """Évolution d'une période à la précédente (rapport, 0,18 pour +18 %) ; None sans base."""
    return None if previous <= 0 else current / previous - 1


def yield_on_cost(last_12m: Decimal, cost: Decimal) -> Decimal | None:
    """Rendement sur le prix de revient : dividendes des 12 derniers mois ÷ coût (rapport,
    0,03 pour 3 %)."""
    return None if cost <= 0 else last_12m / cost


def fees_paid(op: Operation) -> Decimal:
    """Frais payés par l'utilisateur sur une opération. Achat offert (D-078) : la part offerte
    paie d'abord les titres, puis les frais ; ceux qu'elle couvre ne sont pas les siens."""
    if op.op_type == "frais":
        return op.amount
    if op.op_type == "achat" and op.offered:
        covered = op.offered - op.quantity * op.unit_price
        return min(op.fees, max(ZERO, op.fees - covered))
    return op.fees


def fees_by_year(
    operations: Iterable[Operation],
    rates: dict[str, Decimal],
    history: RateHistory | None = None,
) -> dict[int, Decimal]:
    """Frais payés en EUR par année : frais des achats et ventes (sauf ceux payés par la
    plateforme, D-078), plus les opérations « frais » (droits de garde, frais de gestion)."""
    out: dict[int, Decimal] = defaultdict(lambda: ZERO)
    for op in operations:
        paid = fees_paid(op)
        if not paid:
            continue
        amount = _eur(paid, op.currency, rates, history, op.op_date)
        if amount is not None:
            out[op.op_date.year] += amount
    return dict(sorted(out.items()))


def fees_by_asset(
    operations: Iterable[Operation],
    rates: dict[str, Decimal],
    history: RateHistory | None = None,
) -> dict[int | None, Decimal]:
    """Frais payés en EUR par actif (None : frais sans actif, droits de garde d'un compte)."""
    out: dict[int | None, Decimal] = defaultdict(lambda: ZERO)
    for op in operations:
        paid = fees_paid(op)
        amount = _eur(paid, op.currency, rates, history, op.op_date) if paid else None
        if amount is not None:
            out[op.asset_id] += amount
    return dict(out)


def ter_cost(value: Decimal, ter_pct: Decimal, growth_pct: Decimal, years: int) -> dict:
    """Effet des frais courants (TER) d'un fonds : coût de la première année, et manque à
    gagner après `years` ans si le fonds rapporte `growth_pct` % par an avant frais.

    Manque à gagner = valeur finale sans frais − valeur finale avec frais (intérêts composés).
    """
    ter, growth = ter_pct / HUNDRED, growth_pct / HUNDRED
    without = value * (1 + growth) ** years
    with_fees = value * (1 + growth - ter) ** years
    return {"first_year": value * ter, "over_years": without - with_fees}
