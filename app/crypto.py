"""Onglet Rendements crypto (D-092, D-106) : récompenses reçues (intérêts Earn, staking,
airdrops, cashback, parrainage), fonctions pures.

Une récompense est un achat offert (D-078) d'une crypto : compte de type crypto ou actif de
classe crypto. Seule la part offerte compte : jetons reçus = quantité × part offerte du
montant, valeur = part offerte en EUR au taux BCE du jour de réception (D-081).

Rendement en jetons sur 12 mois (≈ APY constaté) : jetons reçus sur la période ÷ quantité
moyenne détenue les jours où la crypto était détenue, ramené à un an. Il ne dépend pas du cours.
"""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.models import Account, Asset, Operation
from app.portfolio import OUTGOING, TRADES, RateHistory, with_transfers

ZERO = Decimal("0")
ONE = Decimal("1")
KINDS = ("earn", "staking", "airdrop", "cashback", "parrainage", "autre")
WINDOW_DAYS = 365
MIN_DAYS = 30  # en dessous, un rendement annualisé ne veut rien dire


@dataclass(frozen=True)
class Reward:
    operation_id: int
    account_id: int
    asset_id: int
    day: date
    kind: str
    quantity: Decimal  # jetons reçus gratuitement
    value: Decimal | None  # EUR au jour de réception ; None sans taux de change


def in_scope(op: Operation, accounts: dict[int, Account], assets: dict[int, Asset]) -> bool:
    """Opération d'un compte crypto, ou sur un actif de classe crypto."""
    account, asset = accounts.get(op.account_id), assets.get(op.asset_id)
    return (account is not None and account.kind == "crypto") or (
        asset is not None and asset.asset_class == "crypto"
    )


def rewards(
    operations: Iterable[Operation],
    accounts: dict[int, Account],
    assets: dict[int, Asset],
    fx: RateHistory,
) -> list[Reward]:
    """Récompenses reçues, dans l'ordre chronologique. Achat en partie offert (poussière d'un
    jeton reçu gratuitement, D-082) : seule la part offerte compte."""
    out = []
    for op in operations:
        if op.op_type != "achat" or not op.offered or not in_scope(op, accounts, assets):
            continue
        gross = op.quantity * op.unit_price
        share = ONE if gross <= 0 else min(ONE, op.offered / gross)
        out.append(
            Reward(
                op.id,
                op.account_id,
                op.asset_id,
                op.op_date,
                op.reward_kind or "autre",
                op.quantity * share,
                fx.to_eur(op.offered, op.currency, op.op_date),
            )
        )
    return sorted(out, key=lambda r: (r.day, r.operation_id))


def held_days(
    operations: Iterable[Operation],
    asset_id: int,
    account_ids: set[int],
    start: date,
    end: date,
) -> tuple[Decimal, int]:
    """Quantité détenue (comptes donnés, transferts entre eux neutres) cumulée jour par jour de
    `start` à `end` inclus, et nombre de jours où elle était positive. Quantité d'un jour : après
    ses opérations."""
    deltas: dict[date, Decimal] = defaultdict(lambda: ZERO)
    for op in with_transfers(operations):
        if op.asset_id != asset_id or op.account_id not in account_ids or op.op_type not in TRADES:
            continue
        deltas[op.op_date] += -op.quantity if op.op_type in OUTGOING else op.quantity
    qty = sum((d for day, d in deltas.items() if day < start), ZERO)
    total, days = ZERO, 0
    day = start
    while day <= end:
        qty += deltas.get(day, ZERO)
        if qty > 0:
            total += qty
            days += 1
        day += timedelta(days=1)
    return total, days


def token_yield(received: Decimal, held_total: Decimal, days: int) -> Decimal | None:
    """Rendement annualisé en jetons : reçus ÷ quantité moyenne détenue × 365 ÷ jours détenus
    (rapport, 0,05 pour 5 %). None si détenu moins de MIN_DAYS jours."""
    if days < MIN_DAYS or held_total <= 0:
        return None
    average = held_total / days
    return received / average * Decimal(WINDOW_DAYS) / days


def summary(
    items: list[Reward],
    operations: list[Operation],
    accounts: dict[int, Account],
    assets: dict[int, Asset],
    prices_eur: dict[int, Decimal],
    held_now: dict[int, Decimal],
    today: date,
) -> dict:
    """Totaux, par jeton, par type et par mois. `prices_eur` : cours actuel d'un jeton en EUR
    (absent si inconnu) ; `held_now` : quantité détenue aujourd'hui (comptes du périmètre)."""
    start = today - timedelta(days=WINDOW_DAYS - 1)
    scope = {o.account_id for o in operations if in_scope(o, accounts, assets)}
    operations = with_transfers(operations)  # une fois pour tous les jetons

    def now_value(asset_id: int, qty: Decimal) -> Decimal | None:
        price = prices_eur.get(asset_id)
        return None if price is None else qty * price

    by_asset: dict[int, dict] = {}
    by_kind: dict[str, dict] = {}
    by_month: dict[str, dict[int, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))
    by_month_kind: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))
    totals = {"value": ZERO, "ytd": ZERO, "last_12m": ZERO, "now": ZERO, "unknown_now": False}
    for r in items:
        a = by_asset.setdefault(
            r.asset_id, {"quantity": ZERO, "value": ZERO, "count": 0, "qty_12m": ZERO}
        )
        k = by_kind.setdefault(r.kind, {"value": ZERO, "count": 0, "now": ZERO, "unknown": False})
        value = r.value or ZERO
        a["quantity"] += r.quantity
        a["value"] += value
        a["count"] += 1
        k["value"] += value
        k["count"] += 1
        current = now_value(r.asset_id, r.quantity)
        if current is None:
            k["unknown"] = totals["unknown_now"] = True
        else:
            k["now"] += current
            totals["now"] += current
        totals["value"] += value
        if r.day.year == today.year:
            totals["ytd"] += value
        if r.day >= start:
            totals["last_12m"] += value
            a["qty_12m"] += r.quantity
        by_month[f"{r.day:%Y-%m}"][r.asset_id] += value
        by_month_kind[f"{r.day:%Y-%m}"][r.kind] += value

    rows = []
    for asset_id, a in by_asset.items():
        total, days = held_days(operations, asset_id, scope, start, today)
        held = held_now.get(asset_id, ZERO)
        rows.append(
            {
                "asset_id": asset_id,
                "quantity": a["quantity"],
                "count": a["count"],
                "value": a["value"],
                "now": now_value(asset_id, a["quantity"]),
                "held": held,
                # Part des jetons détenus aujourd'hui qui viennent de récompenses (au plus 100 %).
                "share_held": None if held <= 0 else min(ONE, a["quantity"] / held),
                "yield_12m": token_yield(a["qty_12m"], total, days),
            }
        )
    kinds = [
        {
            "kind": kind,
            "count": k["count"],
            "value": k["value"],
            "now": k["now"],
            "now_partial": k["unknown"],  # un jeton au moins sans cours : somme incomplète
        }
        for kind, k in sorted(by_kind.items(), key=lambda x: KINDS.index(x[0]))
    ]
    return {
        "total": totals["value"],
        "ytd": totals["ytd"],
        "last_12m": totals["last_12m"],
        "now": totals["now"],
        "now_partial": totals["unknown_now"],
        "assets": rows,
        "kinds": kinds,
        "months": {m: dict(v) for m, v in sorted(by_month.items())},
        "months_kind": {m: dict(v) for m, v in sorted(by_month_kind.items())},
    }


# --- Rendements crypto (D-106) -------------------------------------------------------------
# Un placement = (compte, jeton) qui produit des jetons : une ligne Aave suivie, ou des intérêts
# Earn / du staking reçus sur 12 mois. Le capital d'un compte ne se découpe pas entre staking et
# Earn (l'app ne sait pas quelle part de l'ETH d'une plateforme est stakée) : une ligne par
# (compte, jeton), ses types listés. Airdrops, cashback et parrainage sont des cadeaux, pas un
# rendement du capital : ils comptent dans les revenus, jamais dans un taux.
YIELD_KINDS = ("earn", "staking")
# Les intérêts arrivent par période (fin de mois pour Aave, un gain par mois à l'import
# Binance) : la période en cours n'est pas encore versée. Le rendement réalisé s'arrête donc au
# dernier versement s'il date de moins de PAID_GAP jours, sinon il court jusqu'à aujourd'hui
# (placement arrêté : le taux baisse, c'est voulu).
PAID_GAP = 35


@dataclass(frozen=True)
class Placement:
    account_id: int
    asset_id: int
    kinds: tuple[str, ...]  # types de rendement reçus sur 12 mois (ordre de KINDS)
    held: Decimal  # jetons détenus aujourd'hui sur ce compte
    capital: Decimal | None  # valeur EUR des jetons détenus ; None sans cours
    received_12m: Decimal  # jetons de rendement reçus sur 12 mois
    income_12m: Decimal  # leur valeur EUR à réception
    realized: Decimal | None  # rendement réalisé en jetons, annualisé (0,05 = 5 %)
    rate_today: Decimal | None  # taux public du jour (Aave), sinon None
    rate: Decimal | None  # taux retenu pour l'estimation : du jour, sinon réalisé
    estimated: Decimal | None  # revenu estimé sur un an, EUR : capital × taux


def placements(
    items: list[Reward],
    operations: list[Operation],
    held: dict[tuple[int, int], tuple[Decimal, Decimal | None]],
    rates_today: dict[tuple[int, int], Decimal],
    today: date,
) -> list[Placement]:
    """Placements, du plus gros revenu estimé au plus petit. `held` : jetons détenus aujourd'hui
    et leur valeur EUR (None sans cours) par (compte, jeton) ; `rates_today` : taux de dépôt
    du jour d'une ligne Aave ; `operations` déjà passées par `with_transfers`."""
    start = today - timedelta(days=WINDOW_DAYS - 1)
    found: dict[tuple[int, int], dict] = {key: {"kinds": set()} for key in rates_today}
    for r in items:
        if r.kind in YIELD_KINDS and r.day >= start:
            found.setdefault((r.account_id, r.asset_id), {"kinds": set()})["kinds"].add(r.kind)
    out = []
    for (account_id, asset_id), info in found.items():
        mine = [
            r
            for r in items
            if (r.account_id, r.asset_id) == (account_id, asset_id)
            and r.kind in YIELD_KINDS
            and r.day >= start
        ]
        received = sum((r.quantity for r in mine), ZERO)
        last = max((r.day for r in mine), default=today)
        end = last if (today - last).days <= PAID_GAP else today
        total, days = held_days(operations, asset_id, {account_id}, start, end)
        realized = token_yield(received, total, days)
        today_rate = rates_today.get((account_id, asset_id))
        rate = today_rate if today_rate is not None else realized
        qty, capital = held.get((account_id, asset_id), (ZERO, ZERO))
        out.append(
            Placement(
                account_id,
                asset_id,
                tuple(k for k in KINDS if k in info["kinds"]),
                qty,
                capital,
                received,
                sum((r.value or ZERO for r in mine), ZERO),
                realized,
                today_rate,
                rate,
                None if capital is None or rate is None else capital * rate,
            )
        )
    return sorted(out, key=lambda p: -(p.estimated or ZERO))


def overview(
    found: list[Placement],
    idle: list[tuple[int, int, Decimal]],
    items: list[Reward],
    today: date,
) -> dict:
    """Chiffres clés. `idle` : (compte, jeton, valeur EUR) des cryptos détenues hors placement.
    Rendement moyen = revenu estimé ÷ capital des placements dont le taux est connu (moyenne
    pondérée par le capital) ; part qui rapporte = capital placé ÷ (placé + hors placement)."""
    start = today - timedelta(days=WINDOW_DAYS - 1)
    rated = [p for p in found if p.estimated is not None and p.capital]
    rated_capital = sum((p.capital for p in rated), ZERO)
    estimated = sum((p.estimated for p in rated), ZERO)
    placed = sum((p.capital or ZERO for p in found), ZERO)
    idle_value = sum((v for _, _, v in idle), ZERO)
    gifts = [r for r in items if r.kind not in YIELD_KINDS and r.day >= start]
    return {
        "income_12m": sum((r.value or ZERO for r in items if r.day >= start), ZERO),
        "gifts_12m": sum((r.value or ZERO for r in gifts), ZERO),
        "total": sum((r.value or ZERO for r in items), ZERO),
        "average_rate": estimated / rated_capital if rated_capital > 0 else None,
        "estimated": estimated,
        "placed": placed,
        "placed_share": placed / (placed + idle_value) if placed + idle_value > 0 else None,
    }
