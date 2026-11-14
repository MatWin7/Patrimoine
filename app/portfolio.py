"""Calcul des positions à partir des opérations (fonctions pures, sans base de données).

Méthode du prix moyen pondéré : chaque achat ajoute quantité × prix + frais au coût ;
une vente retire une part du coût proportionnelle à la quantité vendue, le PRU ne bouge pas.
C'est la méthode utilisée pour le PEA et le CTO en France.
"""

from bisect import bisect_right
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal

from app.models import Operation, Position

ZERO = Decimal("0")
ONE = Decimal("1")


class PositionError(ValueError):
    """Opérations incohérentes, par exemple une vente supérieure à la quantité détenue."""


# Seuls types qui changent la quantité détenue. Un transfert sortant (titres ou crypto envoyés
# ailleurs, D-082) fait baisser la quantité comme une vente, sans toucher aux espèces ni réaliser
# de plus-value : sa valeur estimée sort de la performance comme un retrait.
TRADES = ("solde_initial", "achat", "vente", "transfert_sortant", "transfert_entrant")
OUTGOING = ("vente", "transfert_sortant")
# Transfert entrant : jamais saisi, déduit d'un transfert sortant vers un compte de l'app (D-085)
# par `with_transfers`. Il apporte les titres au prix de revient qu'ils avaient au départ.
INCOMING = "transfert_entrant"
CASH_TYPES = ("versement", "retrait")  # argent qui entre sur le compte ou en sort


def cash_movement(op: Operation) -> Decimal:
    """Effet d'une opération sur les espèces du compte, dans la devise de l'opération.

    Un achat débite quantité × prix + frais, moins la part offerte par la plateforme (D-078) ;
    une vente crédite quantité × prix − frais. Un solde initial ne touche pas aux espèces : il
    décrit des titres déjà détenus, payés avant.
    """
    if op.op_type == "achat":
        return -(op.quantity * op.unit_price + op.fees - (op.offered or ZERO))
    if op.op_type == "vente":
        return op.quantity * op.unit_price - op.fees
    if op.op_type in ("versement", "dividende"):
        return op.amount
    if op.op_type in ("retrait", "frais"):
        return -op.amount
    return ZERO


# Dans une même journée, l'argent qui entre passe avant celui qui sort (versement puis achat).
INFLOWS = frozenset({"versement", "vente", "dividende"})


def replay_cash(
    start: Decimal, currency: str, operations: Iterable[Operation]
) -> tuple[Decimal, Decimal]:
    """Espèces d'un compte rejouées dans l'ordre à partir des versements saisis `start` (D-062).

    Renvoie (espèces, versements supposés). Chaque fois que les espèces passeraient sous zéro
    (achat, titres déjà détenus, frais), c'est qu'un versement non saisi a payé : il est compté
    et les espèces repartent de zéro. Les titres déjà détenus consomment donc des versements
    (ils ont été payés un jour), contrairement à `cash_movement`. Opérations dans une autre devise
    que le compte : laissées de côté (taux appliqué par la banque inconnu).
    """
    running, implied = _replay(start, currency, operations)
    return running, sum((amount for _, amount in implied), ZERO)


def implied_deposits(
    start: Decimal, currency: str, operations: Iterable[Operation]
) -> list[tuple[date, Decimal]]:
    """Versements supposés par `replay_cash`, datés du jour de l'opération qui les a rendus
    nécessaires (fiscalité du PER : versements de l'année, D-064)."""
    return _replay(start, currency, operations)[1]


def _replay(
    start: Decimal, currency: str, operations: Iterable[Operation]
) -> tuple[Decimal, list[tuple[date, Decimal]]]:
    ops = sorted(
        (o for o in operations if o.currency == currency),
        key=lambda o: (o.op_date, o.op_type not in INFLOWS, o.id),
    )
    running, implied = start, []
    for op in ops:
        if op.op_type == "solde_initial":
            running -= (op.quantity or ZERO) * (op.unit_price or ZERO) + op.fees
        else:
            running += cash_movement(op)
        if running < 0:
            implied.append((op.op_date, -running))
            running = ZERO
    return running, implied


def current_cash(
    entered: Decimal, after_op_id: int, currency: str, operations: Iterable[Operation]
) -> Decimal:
    """Espèces d'un compte : solde saisi + opérations enregistrées après la saisie (id plus grand).

    `operations` = celles du compte. Une opération dans une autre devise que le compte (action
    américaine sur un CTO en euros) est laissée de côté : le taux appliqué par la banque n'est
    pas connu, il faudra recaler le solde à la main (D-041).
    """
    return entered + sum(
        (cash_movement(o) for o in operations if o.id > after_op_id and o.currency == currency),
        ZERO,
    )


def _apply(state: dict[tuple[int, int], dict], op: Operation) -> None:
    """Ajoute une opération d'achat, de vente ou de solde initial à l'état des positions.

    Un achat offert (D-078) entre à son prix réel, part offerte comprise : seule la hausse qui
    suit est un gain."""
    key = (op.account_id, op.asset_id)
    pos = state.setdefault(key, {"qty": ZERO, "cost": ZERO, "price": ZERO, "cur": op.currency})
    if op.op_type in OUTGOING:
        if op.quantity > pos["qty"]:
            what = "vendues" if op.op_type == "vente" else "transférées"
            raise PositionError(
                f"{'Vente' if op.op_type == 'vente' else 'Transfert'} du {op.op_date} :"
                f" {op.quantity} parts {what}, {pos['qty']} détenues."
            )
        pos["cost"] -= pos["cost"] * op.quantity / pos["qty"]
        pos["qty"] -= op.quantity
    else:
        pos["qty"] += op.quantity
        pos["cost"] += op.quantity * op.unit_price + op.fees
    pos["price"] = op.unit_price
    pos["cur"] = op.currency


def with_transfers(operations: Iterable[Operation]) -> list[Operation]:
    """Opérations plus l'arrivée de chaque transfert vers un compte de l'app (D-085).

    Transfert sortant de titres : un « transfert entrant » sur le compte reçu, même date, au
    prix de revient unitaire qu'avaient les titres au départ (pas une vente : ni gain ni
    perte). Son `amount` garde la valeur estimée du départ, pour que la performance voie un
    flux nul. Retrait d'argent : un versement sur le compte reçu, même montant et devise.
    L'arrivée garde l'id de l'opération de départ. Sans effet sur une liste déjà complétée."""
    ops = list(operations)
    if not any(o.to_account_id for o in ops) or any(o.op_type == INCOMING for o in ops):
        return ops
    out = list(ops)
    state: dict[tuple[int, int], dict] = {}
    pending = sorted((o for o in ops if o.op_type in TRADES), key=lambda o: (o.op_date, o.id))
    for op in pending:
        if op.op_type == "transfert_sortant" and op.to_account_id:
            pos = state.get((op.account_id, op.asset_id))
            unit = pos["cost"] / pos["qty"] if pos and pos["qty"] > 0 else op.unit_price
            arrival = replace(
                op,
                account_id=op.to_account_id,
                op_type=INCOMING,
                unit_price=unit,
                amount=op.quantity * op.unit_price - op.fees,
                fees=ZERO,
                to_account_id=None,
            )
            _apply(state, op)
            _apply(state, arrival)
            out.append(arrival)
        else:
            _apply(state, op)
    for op in ops:
        if op.op_type == "retrait" and op.to_account_id:
            out.append(
                replace(op, account_id=op.to_account_id, op_type="versement", to_account_id=None)
            )
    return out


def _trades(operations: Iterable[Operation]) -> list[Operation]:
    """Achats, ventes et soldes initiaux dans l'ordre chronologique (dividendes et frais exclus),
    arrivées des transferts comprises, juste après leur départ."""
    return sorted(
        (o for o in with_transfers(operations) if o.op_type in TRADES),
        key=lambda o: (o.op_date, o.id, o.op_type == INCOMING),
    )


def compute_positions(operations: Iterable[Operation]) -> list[Position]:
    """Positions non nulles, par (compte, actif), dans l'ordre chronologique des opérations."""
    state: dict[tuple[int, int], dict] = {}
    for op in _trades(operations):
        _apply(state, op)

    return [
        Position(
            account_id=account_id,
            asset_id=asset_id,
            quantity=p["qty"],
            cost=p["cost"],
            last_price=p["price"],
            currency=p["cur"],
        )
        for (account_id, asset_id), p in state.items()
        if p["qty"] > 0
    ]


def convert(
    amount: Decimal, from_cur: str, to_cur: str, rates: dict[str, Decimal]
) -> Decimal | None:
    """Conversion via l'euro avec des taux « 1 EUR = rate devise » (D-010) ; None sans taux."""
    if from_cur == to_cur:
        return amount
    if from_cur not in rates or to_cur not in rates:
        return None
    return amount / rates[from_cur] * rates[to_cur]


class RateHistory:
    """Taux « 1 EUR = rate devise » jour par jour (D-081) : celui du jour de l'opération pour
    une plus-value en devise, comme le retient l'impôt, et non celui d'aujourd'hui (D-020).

    `latest` : derniers taux connus, repli d'une devise sans historique. Avant le premier taux
    de l'historique : ce premier taux, le plus proche connu.
    """

    def __init__(
        self,
        latest: dict[str, Decimal],
        series: dict[str, list[tuple[date, Decimal]]] | None = None,
    ):
        self.latest = latest
        self.series = {cur: sorted(s) for cur, s in (series or {}).items() if s}
        self._days = {cur: [d for d, _ in s] for cur, s in self.series.items()}
        self._cache: dict[date, dict[str, Decimal]] = {}

    def on(self, day: date) -> dict[str, Decimal]:
        if day not in self._cache:
            rates = dict(self.latest)
            for cur, series in self.series.items():
                i = bisect_right(self._days[cur], day)
                rates[cur] = series[max(i - 1, 0)][1]
            rates["EUR"] = ONE
            self._cache[day] = rates
        return self._cache[day]

    def to_eur(self, amount: Decimal, currency: str, day: date) -> Decimal | None:
        return convert(amount, currency, "EUR", self.on(day))


def eur_costs(
    operations: Iterable[Operation], history: RateHistory, pooled: bool = False
) -> dict[tuple[int, int], Decimal]:
    """Prix de revient restant de chaque ligne en EUR, chaque achat converti au taux de son jour
    (prix moyen pondéré en euros, D-081). `pooled` : prix moyen commun à tous les comptes pour
    un même actif (comptes-titres, règle fiscale), réparti au prorata des parts de chaque compte.
    Une ligne dont un achat n'a pas de taux est absente."""
    qty: dict[tuple[int, int], Decimal] = {}
    cost: dict[tuple[int, int], Decimal | None] = {}
    for op in _trades(operations):
        key = (0 if pooled else op.account_id, op.asset_id)
        qty[key] = qty.get(key, ZERO) + (-op.quantity if op.op_type in OUTGOING else op.quantity)
        dest = op.to_account_id and (0 if pooled else op.to_account_id)
        _add_cost_eur(cost, replace(op, account_id=key[0], to_account_id=dest), qty[key], history)
    out = {}
    for p in compute_positions(operations):
        key = (0 if pooled else p.account_id, p.asset_id)
        if cost.get(key) is not None and qty[key] > 0:
            out[(p.account_id, p.asset_id)] = cost[key] * p.quantity / qty[key]
    return out


def with_market_prices(
    positions: Iterable[Position],
    prices: dict[int, tuple[Decimal, str]],
    rates: dict[str, Decimal],
) -> list[Position]:
    """Remplace le prix de la dernière opération par le cours en ligne (asset_id -> prix, devise).

    Sans cours, ou sans taux pour le convertir dans la devise de la position, le prix de la
    dernière opération reste en place.
    """
    result = []
    for p in positions:
        if p.asset_id in prices:
            price, currency = prices[p.asset_id]
            converted = convert(price, currency, p.currency, rates)
            if converted is not None:
                p = replace(p, last_price=converted)
        result.append(p)
    return result


def totals_in_eur(
    positions: Iterable[Position],
    rates: dict[str, Decimal],
    costs_eur: dict[tuple[int, int], Decimal] | None = None,
) -> dict[str, dict[str, Decimal]]:
    """Valeur, coût et plus-value cumulés, convertis en EUR au taux du jour.

    Une devise sans taux connu reste dans sa propre ligne plutôt que d'être convertie au
    hasard. `costs_eur` (`eur_costs`) : coût de chaque ligne en EUR aux taux des jours d'achat,
    pour que la plus-value compte l'effet de change (D-081) ; sans lui, coût au taux du jour.
    """
    totals: dict[str, dict[str, Decimal]] = {}
    costs_eur = costs_eur or {}
    for p in positions:
        value = convert(p.value, p.currency, "EUR", rates)
        cost = costs_eur.get((p.account_id, p.asset_id))
        if cost is None:
            cost = convert(p.cost, p.currency, "EUR", rates)
        if value is None or cost is None:
            cur, value, cost = p.currency, p.value, p.cost
        else:
            cur = "EUR"
        t = totals.setdefault(cur, {"value": ZERO, "cost": ZERO})
        t["value"] += value
        t["cost"] += cost
    for t in totals.values():
        t["gain"] = t["value"] - t["cost"]
    return totals


def ratio(part: Decimal, whole: Decimal) -> Decimal | None:
    """part / whole, None si whole est nul (ex. plus-value d'une ligne reçue gratuitement)."""
    return part / whole if whole else None


def day_change(
    position: Position,
    price: Decimal,
    previous: Decimal,
    currency: str,
    rates: dict[str, Decimal],
) -> Decimal | None:
    """Variation de valeur depuis la clôture de la veille, dans la devise de la position."""
    now = convert(price, currency, position.currency, rates)
    before = convert(previous, currency, position.currency, rates)
    if now is None or before is None:
        return None
    return position.quantity * (now - before)


def sum_by_currency(
    amounts: Iterable[tuple[Decimal, str]], rates: dict[str, Decimal]
) -> dict[str, Decimal]:
    """Somme de montants (montant, devise) convertis en EUR ; une devise sans taux reste à part."""
    sums: dict[str, Decimal] = {}
    for amount, currency in amounts:
        in_eur = convert(amount, currency, "EUR", rates)
        cur, value = ("EUR", in_eur) if in_eur is not None else (currency, amount)
        sums[cur] = sums.get(cur, ZERO) + value
    return sums


def _add_cost_eur(
    cost_eur: dict[tuple[int, int], Decimal | None],
    op: Operation,
    qty_after: Decimal,
    history: RateHistory,
) -> None:
    """Coût investi d'une ligne en EUR après `op` : achat au taux de son jour, vente au prorata
    (None : un achat sans taux, la ligne est laissée de côté)."""
    key = (op.account_id, op.asset_id)
    if op.op_type == INCOMING:
        return  # coût apporté par le départ du transfert, ci-dessous
    cost = cost_eur.get(key, ZERO)
    if op.op_type in OUTGOING:
        held = qty_after + op.quantity
        moved = None if cost is None else cost * op.quantity / held if held else ZERO
        if cost is not None:
            cost_eur[key] = cost - moved
        if op.to_account_id:  # transfert vers un compte de l'app : le coût en euros suit
            dest = (op.to_account_id, op.asset_id)
            before = cost_eur.get(dest, ZERO)
            cost_eur[dest] = None if before is None or moved is None else before + moved
    elif cost is None:
        return
    else:
        paid = history.to_eur(op.quantity * op.unit_price + op.fees, op.currency, op.op_date)
        cost_eur[key] = None if paid is None else cost + paid


def value_history(
    operations: Iterable[Operation],
    closes: dict[int, list[tuple[date, Decimal, str]]],
    rates: dict[str, Decimal],
    end: date,
    latest: dict[int, tuple[Decimal, str]] | None = None,
    history: RateHistory | None = None,
    placed: bool = False,
) -> list[tuple[date, Decimal, Decimal]]:
    """Valeur des titres et montant investi, en EUR, jour par jour : [(jour, valeur, investi)].

    `placed` (D-110) : « investi » devient l'argent net placé, achats moins ventes au montant
    payé ou reçu (part offerte exclue : cadeaux et récompenses n'y entrent pas). Une vente
    suivie d'un rachat, ou un échange crypto, le laisse inchangé, à l'inverse du prix de
    revient. Solde initial et transferts : variation du prix de revient.

    Un point par jour de cotation (et par jour d'opération) du premier achat à `end`. Chaque
    jour, la quantité détenue est valorisée à la dernière clôture connue ; sans clôture, au
    prix de la dernière opération. Le dernier point utilise `latest` (cours en ligne du cache)
    pour finir au même montant que le total affiché. Valeur au taux de change de chaque jour
    (`history`, D-081 ; sans lui, taux du jour), coût investi aux taux des jours d'achat ; une
    ligne sans taux est laissée de côté.
    """
    trades = _trades(operations)
    if not trades:
        return []
    history = history or RateHistory(rates)
    start = trades[0].op_date
    days = {d for series in closes.values() for d, _, _ in series if start <= d <= end}
    days |= {o.op_date for o in trades if o.op_date <= end} | {end}
    latest = latest or {}

    state: dict[tuple[int, int], dict] = {}
    cost_eur: dict[tuple[int, int], Decimal | None] = {}  # coût investi, taux des jours d'achat
    pending = iter(trades)
    op = next(pending, None)
    seen = {asset_id: 0 for asset_id in closes}  # index de la prochaine clôture à lire
    close_of: dict[int, tuple[Decimal, str]] = {}
    points = []
    net_placed = ZERO
    for day in sorted(days):
        while op is not None and op.op_date <= day:
            _apply(state, op)
            before = _known_total(cost_eur) if placed else ZERO
            _add_cost_eur(cost_eur, op, state[(op.account_id, op.asset_id)]["qty"], history)
            if placed:
                net_placed += _placed_flow(op, history, _known_total(cost_eur) - before)
            op = next(pending, None)
        for asset_id, series in closes.items():
            while seen[asset_id] < len(series) and series[seen[asset_id]][0] <= day:
                _, close, currency = series[seen[asset_id]]
                close_of[asset_id] = (close, currency)
                seen[asset_id] += 1
        value = invested = ZERO
        day_rates = history.on(day)
        for key, pos in state.items():
            if pos["qty"] <= 0:
                continue
            asset_id = key[1]
            market = latest.get(asset_id) if day == end else None
            market = market or close_of.get(asset_id)
            price = convert(market[0], market[1], pos["cur"], day_rates) if market else None
            if price is None:
                price = pos["price"]  # pas de clôture : prix de la dernière opération
            line_value = convert(pos["qty"] * price, pos["cur"], "EUR", day_rates)
            line_cost = cost_eur.get(key)
            if line_value is None or line_cost is None:
                continue
            value += line_value
            invested += line_cost
        points.append((day, value, net_placed if placed else invested))
    return points


def _known_total(cost_eur: dict[tuple[int, int], Decimal | None]) -> Decimal:
    return sum((c for c in cost_eur.values() if c is not None), ZERO)


def _placed_flow(op: Operation, history: RateHistory, cost_change: Decimal) -> Decimal:
    """Argent placé (+) ou retiré (-) par une opération, en EUR au taux de son jour (D-110)."""
    gross = op.quantity * op.unit_price
    if op.op_type == "achat":
        amount = history.to_eur(gross + op.fees - (op.offered or ZERO), op.currency, op.op_date)
    elif op.op_type == "vente":
        amount = history.to_eur(op.fees - gross, op.currency, op.op_date)
    else:  # solde initial, transferts : au prix de revient, comme l'investi
        return cost_change
    return amount or ZERO


def allocation(
    slices: Iterable[tuple[str, Decimal, str]], rates: dict[str, Decimal]
) -> list[tuple[str, Decimal]]:
    """Regroupe des (libellé, montant, devise) par libellé, en EUR, du plus gros au plus petit.

    Un montant sans taux de change est laissé de côté : un graphique en parts ne peut pas
    mélanger des devises. Les montants nuls ou négatifs n'ont pas de part à afficher.
    """
    sums: dict[str, Decimal] = {}
    for label, amount, currency in slices:
        in_eur = convert(amount, currency, "EUR", rates)
        if in_eur is not None and in_eur > 0:
            sums[label] = sums.get(label, ZERO) + in_eur
    return sorted(sums.items(), key=lambda item: item[1], reverse=True)


def top_parts(
    parts: list[tuple[str, Decimal]], n: int, keep_last: Iterable[str] = ()
) -> tuple[list[tuple[str, Decimal]], int]:
    """Les `n` plus grosses parts, le reste additionné en une seule (D-051).

    Renvoie (parts gardées, nombre de parts regroupées) ; la somme du reste est la dernière
    part gardée, sous le libellé "Autres", seulement s'il y a au moins 2 parts à regrouper
    (une seule part n'a pas besoin d'être cachée). Les libellés de `keep_last` (« Espèces »)
    ne comptent pas dans les `n` et restent visibles en fin de liste.
    """
    pinned = [p for p in parts if p[0] in keep_last]
    ranked = [p for p in parts if p[0] not in keep_last]
    if len(ranked) <= n + 1:
        return ranked + pinned, 0
    rest = ranked[n:]
    other = ("Autres", sum((value for _, value in rest), ZERO))
    return ranked[:n] + [other] + pinned, len(rest)


# --- Performance par période --------------------------------------------------

PERIODS = ("day", "week", "month", "year")


def daily_flows(
    operations: Iterable[Operation],
    rates: dict[str, Decimal],
    history: RateHistory | None = None,
    closes: dict[int, list[tuple[date, Decimal, str]]] | None = None,
) -> dict[date, Decimal]:
    """Argent apporté (+) ou retiré (-) chaque jour, en EUR : achats frais compris, ventes nettes.

    Ces flux ne sont pas de la performance : un achat fait monter la valeur sans rien gagner.
    La part offerte d'un achat (D-078) est un apport de la plateforme, pas un gain. Des titres
    déjà détenus entrent à leur valeur de marché du jour (dernière clôture de `closes`), pas à
    leur PRU : la plus-value faite avant leur saisie n'est pas la performance de ce jour-là
    (D-081). Montants au taux de change de leur jour (`history`, sinon taux du jour).
    """
    history = history or RateHistory(rates)
    closes = closes or {}
    flows: dict[date, Decimal] = {}
    for op in _trades(operations):
        if op.op_type in OUTGOING:
            amount = -(op.quantity * op.unit_price - op.fees)
        elif op.op_type == INCOMING:
            amount = op.amount  # valeur estimée au départ : avec lui, un flux nul (D-085)
        elif op.op_type == "solde_initial" and (close := _close_on(closes, op)) is not None:
            amount = convert(op.quantity * close[0], close[1], op.currency, history.on(op.op_date))
            if amount is None:
                amount = op.quantity * op.unit_price + op.fees
        else:
            amount = op.quantity * op.unit_price + op.fees
        in_eur = history.to_eur(amount, op.currency, op.op_date)
        if in_eur is not None:
            flows[op.op_date] = flows.get(op.op_date, ZERO) + in_eur
    return flows


def _close_on(
    closes: dict[int, list[tuple[date, Decimal, str]]], op: Operation
) -> tuple[Decimal, str] | None:
    """Dernière clôture de l'actif de `op` à sa date ou avant (cours, devise)."""
    series = closes.get(op.asset_id) or []
    i = bisect_right([d for d, _, _ in series], op.op_date)
    return (series[i - 1][1], series[i - 1][2]) if i else None


def daily_income(
    operations: Iterable[Operation],
    rates: dict[str, Decimal],
    history: RateHistory | None = None,
) -> dict[date, Decimal]:
    """Revenus nets de chaque jour en EUR : dividendes reçus (+) moins opérations « frais »
    (droits de garde, frais de gestion, −). Ils font partie de la performance (D-081) : un
    dividende détaché fait baisser le cours sans rien faire perdre, des droits de garde
    coûtent sans faire baisser la valeur des titres."""
    history = history or RateHistory(rates)
    income: dict[date, Decimal] = {}
    for op in operations:
        if op.op_type not in ("dividende", "frais") or not op.amount:
            continue
        amount = op.amount if op.op_type == "dividende" else -op.amount
        in_eur = history.to_eur(amount, op.currency, op.op_date)
        if in_eur is not None:
            income[op.op_date] = income.get(op.op_date, ZERO) + in_eur
    return income


def period_key(day: date, period: str) -> tuple[str, date, date]:
    """(libellé, premier jour, dernier jour) de la période qui contient `day`."""
    if period == "day":
        return day.isoformat(), day, day
    if period == "week":  # semaine ISO : du lundi au dimanche
        monday = day - timedelta(days=day.weekday())
        year, week, _ = day.isocalendar()
        return f"{year}-S{week:02d}", monday, monday + timedelta(days=6)
    if period == "month":
        first = day.replace(day=1)
        last = (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        return f"{day.year}-{day.month:02d}", first, last
    if period == "year":
        return str(day.year), date(day.year, 1, 1), date(day.year, 12, 31)
    raise ValueError(f"période inconnue : {period}")


@dataclass
class PeriodResult:
    label: str
    first: date
    last: date
    start_value: Decimal = ZERO  # valeur juste avant la période
    end_value: Decimal = ZERO
    flows: Decimal = ZERO  # apports nets pendant la période
    income: Decimal = ZERO  # dividendes moins frais pendant la période (D-081)
    factor: Decimal = ONE  # (1 + performance) de la période, chaînée jour par jour
    cumulative_factor: Decimal = ONE  # depuis le début de l'analyse
    cumulative_gain: Decimal = ZERO

    @property
    def gain(self) -> Decimal:
        return self.end_value - self.start_value - self.flows + self.income

    @property
    def performance(self) -> Decimal:
        return self.factor - ONE


def performance(
    points: list[tuple[date, Decimal]],
    flows: dict[date, Decimal],
    period: str,
    start: date,
    end: date,
    income: dict[date, Decimal] | None = None,
) -> list[PeriodResult]:
    """Gain et performance de chaque période entre `start` et `end` inclus.

    Performance « pondérée par le temps » (TWR, Time-Weighted Return) : chaque jour, le
    rendement est (valeur du soir + argent sorti + revenus) / (valeur de la veille + argent
    apporté) ; on multiplie ces rendements entre eux. Un achat ne compte donc pas comme un
    gain, et un mois compte autant qu'un autre quelle que soit la somme investie. L'argent
    apporté entre le matin, celui d'une vente sort le soir, au cours du jour (D-081) : compter
    une vente le matin rendait la journée absurde (+1 000 % pour 90 % vendus un jour à +10 %).
    Revenus (`income`, daily_income) : dividendes moins frais, rendus le soir. Le gain en
    euros est valeur de fin - valeur de début - apports + revenus.
    `points` : (jour, valeur en EUR) triés, comme ceux de value_history.
    """
    income = income or {}
    before = [value for day, value in points if day < start]
    previous = before[-1] if before else ZERO
    results: list[PeriodResult] = []
    cumulative = ONE
    cumulative_flows = cumulative_income = ZERO
    base = previous
    last_day = start - timedelta(days=1)
    for day, value in points:
        if day < start or day > end:
            continue
        flow = sum((f for d, f in flows.items() if last_day < d <= day), ZERO)
        earned = sum((f for d, f in income.items() if last_day < d <= day), ZERO)
        invested = previous + max(flow, ZERO)
        returned = value + max(-flow, ZERO) + earned
        factor = returned / invested if invested > 0 else ONE
        label, first, last = period_key(day, period)
        if not results or results[-1].label != label:
            results.append(PeriodResult(label, max(first, start), min(last, end), previous))
        current = results[-1]
        current.end_value = value
        current.flows += flow
        current.income += earned
        current.factor *= factor
        cumulative *= factor
        cumulative_flows += flow
        cumulative_income += earned
        current.cumulative_factor = cumulative
        current.cumulative_gain = value - base - cumulative_flows + cumulative_income
        previous, last_day = value, day
    return results


def max_drawdown(results: list[PeriodResult]) -> Decimal:
    """Plus forte baisse, depuis un plus haut, de l'indice de performance (0 si jamais baissé)."""
    peak, worst = ONE, ZERO
    for r in results:
        peak = max(peak, r.cumulative_factor)
        worst = min(worst, r.cumulative_factor / peak - ONE)
    return worst


def annualized(factor: Decimal, days: int) -> Decimal | None:
    """Performance ramenée à un an ; None sous un an (l'extrapoler tromperait)."""
    if days < 365 or factor <= 0:
        return None
    return factor ** (Decimal(365) / Decimal(days)) - ONE
