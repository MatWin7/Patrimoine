"""Route de la projection vers un objectif : une simulation à taux constants, pas une
prévision."""

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Query

from app import db, repository
from app.analytics import project
from app.api.common import pct
from app.api.deps import HttpClient
from app.api.portfolio import Filter, value_series
from app.portfolio import ZERO, sum_by_currency
from app.schemas import money

router = APIRouter()


# Jusqu'à 5 taux annuels en %, séparés par « ; » (ex. « 2;5;8 » ou « 3,5;7 »).
RATE = r"-?\d{1,2}([.,]\d{1,2})?"


RATES_PATTERN = rf"^{RATE}(;{RATE}){{0,4}}$"


@router.get("/api/projection")
def projection(
    client: HttpClient,
    keep: Filter,
    monthly: Annotated[Decimal, Query(ge=0, le=1_000_000)] = Decimal("0"),
    years: Annotated[int, Query(ge=1, le=50)] = 10,
    target: Annotated[Decimal | None, Query(ge=0, le=1_000_000_000)] = None,
    rates: Annotated[str, Query(pattern=RATES_PATTERN)] = "2;5;8",
) -> dict:
    """Projection de la valeur actuelle (titres + espèces) avec un versement mensuel, selon
    plusieurs taux annuels constants (en %, séparés par « ; »). Une simulation, pas une
    prévision : les marchés ne montent jamais à taux constant. `keep` : lignes laissées
    de côté ; les espèces restent comptées, sauf d'un compte entier exclu (D-050, D-069)."""
    series = value_series(client, keep)
    with db.connection() as conn:
        accounts = repository.list_accounts(conn)
    # Espèces comptées, sauf celles d'un compte entier laissé de côté (D-069).
    kept = [(a.cash, a.currency) for a in accounts if a.id not in keep.accounts]
    cash = sum_by_currency(kept, series["rates"])
    start_value = (series["points"][-1][1] if series["points"] else ZERO) + cash.get("EUR", ZERO)
    today = series["today"]
    months = years * 12
    labels = []
    for i in range(1, months + 1):
        total = today.year * 12 + today.month - 1 + i
        labels.append(f"{total // 12}-{total % 12 + 1:02d}")
    scenarios = []
    for text in rates.split(";"):
        rate = Decimal(text.replace(",", ".")) / 100
        values = project(start_value, monthly, rate, months)
        reach = next((labels[i] for i, v in enumerate(values) if target and v >= target), None)
        scenarios.append(
            {
                "rate": pct(rate),
                "values": [money(v) for v in values],
                "final": money(values[-1]),
                "reach": reach,
            }
        )
    return {
        "start_value": money(start_value),
        "months": labels,
        "invested": [money(start_value + monthly * (i + 1)) for i in range(months)],
        "target": money(target) if target else None,
        "scenarios": scenarios,
    }
