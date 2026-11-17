"""Route des revenus et des frais (D-057, D-060, D-076) : dividendes reçus, frais payés, coût
des frais courants. Calculés sur le cache des cours et des taux, sans aucun appel réseau."""

from datetime import date
from decimal import Decimal

from fastapi import APIRouter

from app import crypto, income
from app.api.common import by_asset_eur, last_months, money_or_none, offline_state, pct
from app.portfolio import ZERO, convert, ratio
from app.schemas import money, plain

router = APIRouter()


TER_GROWTH = Decimal("5")  # rendement annuel supposé avant frais, pour l'effet des frais courants


TER_YEARS = 10


@router.get("/api/income")
def income_report() -> dict:
    """Dividendes reçus (par mois et par ligne, année, ligne ; rendements, impôt retenu), frais
    payés par année, et effet des frais courants (TER) des fonds sur 10 ans, par compte."""
    state = offline_state()
    today = date.today()
    summary = income.income_summary(state["operations"], state["rates"], today, state["fx"])
    # Bourse seulement : les frais crypto vont dans l'onglet Crypto (D-092).
    accounts, assets = state["accounts"], state["assets"]
    stock_ops = [o for o in state["operations"] if not crypto.in_scope(o, accounts, assets)]
    fees = income.fees_by_year(stock_ops, state["rates"], state["fx"])
    costs = by_asset_eur(state, "cost")
    values = by_asset_eur(state, "value")
    assets = state["assets"]
    years = sorted(set(summary["by_year"]) | set(fees))
    months = [f"{d:%Y-%m}" for d in last_months(today, 24)]
    by_month_asset = summary["by_month_asset"]
    last_12m = summary["last_12m"]
    calendar_months = months[-12:]
    calendar = []
    for asset_id, (_, last) in summary["by_asset"].items():
        if last <= 0:
            continue
        cells = [by_month_asset.get(m, {}).get(asset_id) for m in calendar_months]
        calendar.append(
            {
                "asset": assets[asset_id].name,
                "total": money(last),
                "cells": [money_or_none(c) for c in cells],
            }
        )
    calendar.sort(key=lambda r: -Decimal(r["total"]))
    return {
        "dividends": {
            "total": money(summary["total"]),
            "last_12m": money(last_12m),
            "prev_12m": money(summary["prev_12m"]),
            "change_pct": pct(income.change_ratio(last_12m, summary["prev_12m"])),
            "monthly": money(last_12m / 12),
            "ytd": money(summary["ytd"]),
            # Rendement des titres cotés (sans cryptos ni valeurs saisies : immobilier, fonds
            # euros) : dividendes des 12 derniers mois ÷ valeur actuelle, et ÷ argent investi.
            "yield_value_pct": pct(ratio(last_12m, sum(_titles(values, assets), ZERO))),
            "yield_cost_pct": pct(
                income.yield_on_cost(last_12m, sum(_titles(costs, assets), ZERO))
            ),
            "withheld": money(sum(summary["withheld"].values(), ZERO)),
        },
        "months": [
            {
                "month": m,
                "amount": money(summary["by_month"].get(m, ZERO)),
                "lines": [
                    {"asset": assets[a].name, "amount": money(v)}
                    for a, v in sorted(by_month_asset.get(m, {}).items(), key=lambda kv: -kv[1])
                ],
            }
            for m in months
        ],
        "calendar": {"months": calendar_months, "rows": calendar},
        "years": [
            {
                "year": y,
                "dividends": money(summary["by_year"].get(y, ZERO)),
                "fees": money(fees.get(y, ZERO)),
            }
            for y in years
        ],
        "lines": [
            {
                "asset": assets[asset_id].name,
                "total": money(total),
                "last_12m": money(last),
                "cost": money_or_none(costs.get(asset_id)),
                "yield_pct": pct(income.yield_on_cost(last, costs.get(asset_id, ZERO))),
                "withheld": money(summary["withheld"].get(asset_id, ZERO)),
            }
            for asset_id, (total, last) in sorted(
                summary["by_asset"].items(), key=lambda kv: -kv[1][0]
            )
        ],
        "fees_total": money(sum(fees.values(), ZERO)),
        "ter": _ter_report(state, values),
    }


def _titles(amounts: dict[int, Decimal], assets: dict) -> list[Decimal]:
    """Montants des titres cotés qui peuvent verser un dividende (ni crypto ni valeur saisie)."""
    return [
        v for i, v in amounts.items() if not assets[i].manual and assets[i].asset_class != "crypto"
    ]


def _ter_report(state: dict, values: dict[int, Decimal]) -> dict:
    """Frais courants des fonds (D-060), un bloc par compte (D-076) avec son sous-total."""
    assets, accounts, rates = state["assets"], state["accounts"], state["rates"]
    blocks: dict[int, list[dict]] = {}
    for p in state["valued"]:
        ter = assets[p.asset_id].ter
        value = convert(p.value, p.currency, "EUR", rates)
        if ter is None or value is None:
            continue
        cost = income.ter_cost(value, ter, TER_GROWTH, TER_YEARS)
        blocks.setdefault(p.account_id, []).append(
            {
                "asset": assets[p.asset_id].name,
                "value": value,
                "ter": plain(ter),
                "first_year": cost["first_year"],
                "over_years": cost["over_years"],
            }
        )

    def total(lines: list[dict], key: str) -> Decimal:
        return sum((line[key] for line in lines), ZERO)

    def out(line: dict) -> dict:
        return {k: money(v) if isinstance(v, Decimal) else v for k, v in line.items()}

    every = [line for lines in blocks.values() for line in lines]
    return {
        "accounts": [
            {
                "account": accounts[i].name,
                "account_id": i,
                "value": money(total(blocks[i], "value")),
                "first_year": money(total(blocks[i], "first_year")),
                "over_years": money(total(blocks[i], "over_years")),
                "lines": [out(line) for line in sorted(blocks[i], key=lambda x: -x["value"])],
            }
            for i in accounts
            if i in blocks
        ],
        "first_year": money(total(every, "first_year")),
        "over_years": money(total(every, "over_years")),
        "growth_pct": plain(TER_GROWTH),
        "years": TER_YEARS,
        "without_ter": sorted(
            assets[i].name
            for i in values
            if assets[i].ter is None
            and assets[i].asset_class in ("etf", "fonds", "scpi", "fonds_euros")
        ),
    }
