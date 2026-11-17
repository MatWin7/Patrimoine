"""Route de l'onglet Rendements crypto (D-092, D-106) : récompenses par jeton, type et mois,
placements et revenu estimé. Calculés hors ligne."""

from datetime import date
from decimal import Decimal

from fastapi import APIRouter

from app import crypto, db, defi_sync, income, prices
from app.api.aave import borrows_report
from app.api.common import last_months, money_or_none, offline_state, pct, quantity_text
from app.portfolio import ZERO, convert
from app.schemas import money

router = APIRouter()


REWARD_MONTHS = 24  # barres mensuelles de l'onglet Crypto


@router.get("/api/crypto/rewards")
def crypto_rewards() -> dict:
    """Onglet Rendements crypto (D-092, D-106) : revenus reçus (intérêts Earn, staking,
    airdrops...) par jeton, par type et par mois ; placements (compte, jeton) avec capital,
    taux et revenu estimé ; cryptos qui ne rapportent rien. Hors ligne : seuls les cours et
    taux déjà en base servent."""
    with db.connect() as conn:
        defi_sync.safe_sync(conn, date.today())  # intérêts Aave des mois clos (hors ligne)
    conn.close()
    state = offline_state()
    today = date.today()
    accounts, assets, operations = state["accounts"], state["assets"], state["operations"]
    items = crypto.rewards(operations, accounts, assets, state["fx"])
    asset_ids = {r.asset_id for r in items}
    with db.connect() as conn:
        latest = {i: prices.latest_price(conn, i) for i in asset_ids}
    conn.close()
    prices_eur = {}
    for asset_id, quote in latest.items():
        if quote is not None:
            price = convert(quote.price, quote.currency, "EUR", state["rates"])
            if price is not None:
                prices_eur[asset_id] = price
    scope = {o.account_id for o in operations if crypto.in_scope(o, accounts, assets)}
    held: dict[int, Decimal] = {}
    for p in state["valued"]:
        if p.account_id in scope:
            held[p.asset_id] = held.get(p.asset_id, ZERO) + p.quantity
    report = crypto.summary(items, operations, accounts, assets, prices_eur, held, today)
    months = [f"{d:%Y-%m}" for d in last_months(today, REWARD_MONTHS)]
    # Frais payés sur la crypto (échanges, frais en BNB...), sortis de « Revenus et frais ».
    crypto_ops = [o for o in operations if crypto.in_scope(o, accounts, assets)]
    fees = income.fees_by_year(crypto_ops, state["rates"], state["fx"])
    fees_asset = income.fees_by_asset(crypto_ops, state["rates"], state["fx"])
    # Rendements (D-106) : placements (compte, jeton) et cryptos qui ne rapportent rien.
    positions: dict[tuple[int, int], tuple[Decimal, Decimal | None]] = {}
    for p in state["valued"]:
        account, asset = accounts[p.account_id], assets[p.asset_id]
        if account.kind != "crypto" and asset.asset_class != "crypto":
            continue
        positions[(p.account_id, p.asset_id)] = (
            p.quantity,
            convert(p.value, p.currency, "EUR", state["rates"]),
        )
    rates_today = {}
    with db.connect() as conn:
        defi_lines = defi_sync.list_lines(conn)
        for line in defi_lines:
            rates = defi_sync.load_rates(conn, line)
            if rates:
                rates_today[(line.account_id, line.asset_id)] = rates[max(rates)]
    conn.close()
    found = crypto.placements(
        items, crypto.with_transfers(operations), positions, rates_today, today
    )
    keys = {(p.account_id, p.asset_id) for p in found}
    idle = [
        (acc, asset, value)
        for (acc, asset), (qty, value) in positions.items()
        if (acc, asset) not in keys and qty > 0 and value
    ]
    totals = crypto.overview(found, idle, items, today)
    lines = {(line.account_id, line.asset_id): line.id for line in defi_lines}
    with db.connect() as conn:
        borrowed = borrows_report(conn, today)  # emprunts Aave (D-108) : coût et santé
    conn.close()
    health = [m["health_factor"] for m in borrowed["markets"] if m["health_factor"] is not None]
    return {
        "overview": {
            "income_12m": money(totals["income_12m"]),
            "gifts_12m": money(totals["gifts_12m"]),
            "total": money(totals["total"]),
            "average_rate_pct": pct(totals["average_rate"]),
            "estimated": money(totals["estimated"]),
            # Revenu net des intérêts d'emprunt Aave ; santé : le marché le plus fragile.
            "borrow_cost": money(borrowed["cost_eur"]),
            "net_estimated": money(totals["estimated"] - borrowed["cost_eur"]),
            "borrowed": money_or_none(borrowed["debt_eur"]),
            "health_factor": min(health, key=Decimal) if health else None,
            "placed": money(totals["placed"]),
            "placed_share_pct": pct(totals["placed_share"]),
        },
        "placements": [
            {
                "account_id": p.account_id,
                "account": accounts[p.account_id].name,
                "asset_id": p.asset_id,
                "asset": assets[p.asset_id].name,
                "kinds": list(p.kinds),
                "aave_line_id": lines.get((p.account_id, p.asset_id)),
                "held": quantity_text(p.held),
                "capital": money_or_none(p.capital),
                "received_12m": quantity_text(p.received_12m),
                "income_12m": money(p.income_12m),
                "realized_pct": pct(p.realized),
                "rate_today_pct": pct(p.rate_today),
                "estimated": money_or_none(p.estimated),
            }
            for p in found
        ],
        "idle": [
            {
                "account": accounts[acc].name,
                "asset": assets[asset].name,
                "held": quantity_text(positions[(acc, asset)][0]),
                "value": money(value),
            }
            for acc, asset, value in sorted(idle, key=lambda x: -x[2])
        ],
        "fees": {
            "total": money(sum(fees.values(), ZERO)),
            "years": [{"year": y, "fees": money(v)} for y, v in fees.items()],
            "other": money(fees_asset.get(None, ZERO)),  # frais sans jeton (compte)
        },
        "total": money(report["total"]),
        "ytd": money(report["ytd"]),
        "last_12m": money(report["last_12m"]),
        "now": money(report["now"]) if items else None,
        "now_partial": report["now_partial"],
        "assets": [
            {
                "asset_id": a["asset_id"],
                "asset": assets[a["asset_id"]].name,
                "color": assets[a["asset_id"]].color,
                "quantity": quantity_text(a["quantity"]),
                "count": a["count"],
                "value": money(a["value"]),
                "now": money_or_none(a["now"]),
                "held": quantity_text(a["held"]),
                "share_held_pct": pct(a["share_held"]),
                "yield_12m_pct": pct(a["yield_12m"]),
            }
            for a in sorted(report["assets"], key=lambda a: -a["value"])
        ],
        "kinds": [
            {
                "kind": k["kind"],
                "count": k["count"],
                "value": money(k["value"]),
                "now": money(k["now"]),
                "now_partial": k["now_partial"],
            }
            for k in report["kinds"]
        ],
        "months": [
            {
                "month": m,
                "by_asset": {str(i): money(v) for i, v in report["months"].get(m, {}).items()},
                "by_kind": {k: money(v) for k, v in report["months_kind"].get(m, {}).items()},
            }
            for m in months
        ],
        "recent": [
            {
                "date": r.day.isoformat(),
                "account": accounts[r.account_id].name,
                "asset": assets[r.asset_id].name,
                "kind": r.kind,
                "quantity": quantity_text(r.quantity),
                "value": money_or_none(r.value),
            }
            for r in reversed(items)
        ],
    }
