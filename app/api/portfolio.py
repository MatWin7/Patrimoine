"""Routes de synthèse : positions et total (`/api/portfolio`), évolution de la valeur
(`/api/history`), et le filtre de lignes que suivent Performance, Comparaison et Projection
(D-069)."""

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Literal

import httpx2
from fastapi import APIRouter, Depends, HTTPException, Query

from app import config, db, prices, repository
from app.analytics import eroded_invested, extend_index, smooth_index, yearly_inflation
from app.api.aave import update_defi
from app.api.common import CLASS_LABELS, LIVRETS, category_of, money_or_none, pct, price_text
from app.api.debts import debts_total_eur
from app.api.deps import HttpClient
from app.models import Operation
from app.portfolio import (
    TRADES,
    ZERO,
    PositionError,
    RateHistory,
    allocation,
    compute_positions,
    convert,
    daily_flows,
    daily_income,
    day_change,
    eur_costs,
    ratio,
    sum_by_currency,
    top_parts,
    totals_in_eur,
    value_history,
    with_market_prices,
    with_transfers,
)
from app.schemas import money, plain

router = APIRouter()


def _allocation_out(slices: list[tuple[str, Decimal, str]], rates: dict[str, Decimal]) -> list:
    """Parts du patrimoine en EUR (graphique de répartition), pourcentages calculés ici."""
    parts = allocation(slices, rates)
    whole = sum((value for _, value in parts), ZERO)
    return [
        {"label": label, "value": money(value), "pct": pct(ratio(value, whole))}
        for label, value in parts
    ]


# Regroupements proposés par le graphique de répartition (D-051). Au-delà de TOP_N parts,
# les plus petites sont additionnées en « Autres » ; la liste complète sert à la vue agrandie.
GROUPINGS = ("classe", "categorie", "ligne", "compte", "devise")


TOP_N = 7


def _groupings_out(valued, cash_accounts, accounts, assets, rates) -> dict:
    """Répartition en EUR selon chaque regroupement : {"top", "all", "others"}.

    Les espèces forment une part « Espèces » (par ligne ou par classe), ou s'ajoutent à leur
    compte ou à leur devise.
    """
    keys = {
        "classe": lambda p: CLASS_LABELS.get(assets[p.asset_id].asset_class, "Autres"),
        "categorie": lambda p: category_of(assets[p.asset_id]),
        "ligne": lambda p: assets[p.asset_id].name,
        "compte": lambda p: accounts[p.account_id].name,
        "devise": lambda p: p.currency,
    }
    cash_keys = {
        "classe": _cash_class,
        "categorie": _cash_class,
        "ligne": lambda a: a.name if a.kind == "livret" else "Espèces",
        "compte": lambda a: a.name,
        "devise": lambda a: a.currency,
    }
    out = {}
    for name in GROUPINGS:
        slices = [(keys[name](p), p.value, p.currency) for p in valued]
        slices += [(cash_keys[name](a), a.cash, a.currency) for a in cash_accounts]
        parts = allocation(slices, rates)
        whole = sum((value for _, value in parts), ZERO)
        top, others = top_parts(parts, TOP_N, keep_last=("Espèces",))

        def out_parts(items, whole=whole):
            return [
                {"label": label, "value": money(value), "pct": pct(ratio(value, whole))}
                for label, value in items
            ]

        out[name] = {"top": out_parts(top), "all": out_parts(parts), "others": others}
    return out


def _detail_slices(valued, cash_accounts, assets) -> dict[str, list]:
    """Parts regroupées par classe : (nom de l'actif, valeur, devise) ; espèces par compte.
    Un même actif détenu sur deux comptes n'a qu'une part (allocation additionne par nom)."""
    detail: dict[str, list] = {}
    for p in valued:
        asset = assets[p.asset_id]
        label = CLASS_LABELS.get(asset.asset_class, "Autres")
        detail.setdefault(label, []).append((asset.name, p.value, p.currency))
    for a in cash_accounts:
        detail.setdefault(_cash_class(a), []).append((a.name, a.cash, a.currency))
    return detail


def _cash_class(account) -> str:
    """Classe des espèces d'un compte : « Livrets » pour un livret, « Espèces » sinon."""
    return LIVRETS if account.kind == "livret" else "Espèces"


def _total_out(t: dict[str, Decimal]) -> dict:
    out = {key: money(t[key]) for key in ("value", "cost", "gain")}
    out["gain_pct"] = pct(ratio(t["gain"], t["cost"]))
    if "day_change" in t:
        out["day_change"] = money(t["day_change"])
        out["day_change_pct"] = pct(ratio(t["day_change"], t["value"] - t["day_change"]))
    if "cash" in t:
        out["cash"] = money(t["cash"])
        out["total"] = money(t["value"] + t["cash"])  # titres + espèces
    return out


@dataclass(frozen=True)
class LineFilter:
    """Lignes laissées de côté (D-046, D-050, D-069) : un actif partout (`exclude`), un actif
    dans un compte (`exclude_line=compte-actif`) ou un compte entier, espèces comprises
    (`exclude_account`). Exclure plutôt que choisir : une ligne achetée plus tard apparaît
    d'office."""

    assets: frozenset[int] = frozenset()
    lines: frozenset[tuple[int, int]] = frozenset()
    accounts: frozenset[int] = frozenset()

    def keep(self, account_id: int, asset_id: int | None) -> bool:
        if account_id in self.accounts:
            return False
        return asset_id is None or (
            asset_id not in self.assets and (account_id, asset_id) not in self.lines
        )

    def __bool__(self) -> bool:
        return bool(self.assets or self.lines or self.accounts)


LINE_PATTERN = r"\d{1,9}-\d{1,9}"


def line_filter(
    exclude: Annotated[list[int] | None, Query()] = None,
    exclude_line: Annotated[list[str] | None, Query()] = None,
    exclude_account: Annotated[list[int] | None, Query()] = None,
) -> LineFilter:
    if any(not re.fullmatch(LINE_PATTERN, text) for text in exclude_line or ()):
        raise HTTPException(status_code=422, detail="Ligne à exclure invalide (compte-actif).")
    pairs = (tuple(int(x) for x in text.split("-")) for text in exclude_line or ())
    return LineFilter(frozenset(exclude or ()), frozenset(pairs), frozenset(exclude_account or ()))


Filter = Annotated[LineFilter, Depends(line_filter)]


NO_FILTER = LineFilter()


# Périmètres de la Vue d'ensemble (D-143). Brut : tout ce qui est détenu ; net : brut moins le
# capital restant dû (mêmes lignes, seul le chiffre principal change) ; financier : placements
# placés sur les marchés et les contrats (bourse, crypto, assurance-vie, PER), sans
# l'immobilier (en direct ou en SCPI), les livrets ni l'argent des comptes bancaires (espèces
# d'un compte « Autre ») (choix de l'utilisateur, 2026-11-21).
Scope = Literal["brut", "net", "financier"]
NOT_FINANCIAL_CLASSES = frozenset({"immobilier", "scpi"})
NOT_FINANCIAL_ACCOUNTS = frozenset({"immobilier", "livret"})  # le compte entier
BANK_ACCOUNTS = frozenset({"autre"})  # seulement ses espèces : ses titres restent


def is_financial(account, asset) -> bool:
    """La ligne (compte, actif) entre-t-elle dans le patrimoine financier ? `asset` None : les
    espèces du compte."""
    if account.kind in NOT_FINANCIAL_ACCOUNTS:
        return False
    if asset is None:
        return account.kind not in BANK_ACCOUNTS
    return asset.asset_class not in NOT_FINANCIAL_CLASSES


def _sum_eur(amounts: list[tuple[Decimal, str]], rates: dict[str, Decimal]) -> Decimal:
    """Somme en EUR ; un montant sans taux de change n'y entre pas (comme `totals` sans EUR)."""
    values = (convert(value, currency, "EUR", rates) for value, currency in amounts)
    return sum((v for v in values if v is not None), ZERO)


@router.get("/api/portfolio")
def portfolio(client: HttpClient, keep: Filter, force: bool = False, scope: Scope = "brut") -> dict:
    """Positions et total en EUR, au cours en ligne (cache de PRICE_TTL_MINUTES).

    `keep` : lignes laissées de côté par le filtre de Vue d'ensemble (D-069) ; un compte
    entier exclu emporte ses espèces.

    `scope` (D-143) : `financier` limite positions, totaux et répartition aux lignes de
    `is_financial` ; `brut` et `net` gardent tout. `scopes` donne les trois chiffres
    principaux en EUR, quel que soit `scope`.

    `force=true` (bouton « Actualiser ») ignore le cache. Seuls les tickers, ids CoinGecko et
    codes devises partent sur le réseau, jamais les quantités ni les montants (règle 3).
    """
    now = datetime.now(UTC)  # horodatage du cache des prix, en UTC
    ttl = config.price_ttl()
    with db.connection() as conn:
        update_defi(conn, client)
        accounts = {a.id: a for a in repository.list_accounts(conn)}
        assets = {a.id: a for a in repository.list_assets(conn)}
        operations = repository.list_operations(conn)
        positions = compute_positions(operations)
        positions = [p for p in positions if keep.keep(p.account_id, p.asset_id)]
        held = [assets[i] for i in sorted({p.asset_id for p in positions})]
        quotes = prices.get_quotes(
            conn,
            held,
            client,
            ttl=ttl,
            now=now,
            force=force,
            coingecko_key=config.coingecko_api_key(),
        )
        currencies = {p.currency for p in positions}
        currencies |= {a.currency for a in accounts.values() if a.cash}
        currencies |= {q.price.currency for q in quotes.values() if q.price}
        rates, fx_error = prices.get_rates(
            conn,
            currencies,
            client,
            today=date.today(),
            may_fetch=any(q.fetched for q in quotes.values()),
        )
        if any(q.fetched for q in quotes.values()):  # erreur montrée par Performance
            _update_fx_history(conn, operations, currencies, client, date.today())
        fx = RateHistory(rates, prices.rate_series(conn, currencies))
    market = {i: (q.price.price, q.price.currency) for i, q in quotes.items() if q.price}
    valued = with_market_prices(positions, market, rates)
    held_value = [(p.value, p.currency) for p in valued]  # brut, avant le périmètre
    financial_value = [
        (p.value, p.currency)
        for p in valued
        if is_financial(accounts[p.account_id], assets[p.asset_id])
    ]
    if scope == "financier":
        valued = [p for p in valued if is_financial(accounts[p.account_id], assets[p.asset_id])]
    # Coût en EUR aux taux des jours d'achat : la plus-value en EUR compte l'effet de change.
    costs_eur = eur_costs(operations, fx)

    def gain_eur(p) -> Decimal | None:
        value = convert(p.value, p.currency, "EUR", rates)
        cost = costs_eur.get((p.account_id, p.asset_id))
        if value is None or cost is None:
            return convert(p.gain, p.currency, "EUR", rates)
        return value - cost

    def change_of(p) -> Decimal | None:
        q = quotes.get(p.asset_id)
        if not q or not q.price or q.price.previous_close is None:
            return None
        return day_change(p, q.price.price, q.price.previous_close, q.price.currency, rates)

    changes = [change_of(p) for p in valued]
    totals = totals_in_eur(valued, rates, costs_eur)
    known = [(c, p.currency) for c, p in zip(changes, valued, strict=True) if c is not None]
    for cur, change in sum_by_currency(known, rates).items():
        if cur in totals:
            totals[cur]["day_change"] = change
    # Espèces des comptes : ajoutées à la valeur totale, sans toucher au coût ni à la plus-value.
    # Espèces négatives (achat saisi avant le versement correspondant) : affichées, signalées.
    cash_accounts = [a for a in accounts.values() if a.cash and a.id not in keep.accounts]
    held_value += [(a.cash, a.currency) for a in cash_accounts]
    financial_value += [(a.cash, a.currency) for a in cash_accounts if is_financial(a, None)]
    if scope == "financier":
        cash_accounts = [a for a in cash_accounts if is_financial(a, None)]
    for cur, cash in sum_by_currency([(a.cash, a.currency) for a in cash_accounts], rates).items():
        totals.setdefault(cur, {"value": ZERO, "cost": ZERO, "gain": ZERO})["cash"] = cash

    def class_of(p) -> str:
        return CLASS_LABELS.get(assets[p.asset_id].asset_class, "Autres")

    def value_eur(p) -> Decimal | None:
        return convert(p.value, p.currency, "EUR", rates)

    # Même base que le graphique de répartition : montants positifs convertis en EUR.
    amounts = [value_eur(p) for p in valued]
    amounts += [convert(a.cash, a.currency, "EUR", rates) for a in cash_accounts]
    grand_total = sum((v for v in amounts if v is not None and v > 0), ZERO)

    def price_info(asset_id: int) -> dict:
        q = quotes.get(asset_id)
        p = q.price if q else None
        return {
            "price_source": p.source if p else "operation",
            "price_at": p.fetched_at.strftime(prices.TIMESTAMP) if p else None,
            "price_error": q.error if q else None,
        }

    # Patrimoine net (D-107) : capital restant dû de toutes les dettes, hors filtre de lignes.
    with db.connection() as conn:
        debts_eur = debts_total_eur(conn, rates, date.today())

    # Les trois chiffres principaux (D-143), en EUR, sur les lignes du filtre.
    gross = _sum_eur(held_value, rates)
    scopes = {
        "brut": money(gross),
        "net": money(gross - debts_eur) if debts_eur is not None else money(gross),
        "financier": money(_sum_eur(financial_value, rates)),
    }

    return {
        "scope": scope,
        "scopes": scopes,
        "debts_eur": money_or_none(debts_eur),
        "positions": [
            {
                "account": accounts[p.account_id].name,
                "asset": assets[p.asset_id].name,
                "ticker": assets[p.asset_id].ticker,
                "quantity": plain(p.quantity),
                "pru": money(p.pru, "0.0001"),
                "cost": money(p.cost),
                "last_price": price_text(p.last_price),
                "value": money(p.value),
                "gain": money(p.gain),
                "gain_pct": pct(ratio(p.gain, p.cost)),
                "gain_eur": money_or_none(gain_eur(p)),
                "day_change": None if change is None else money(change),
                "day_change_pct": pct(None if change is None else ratio(change, p.value - change)),
                "currency": p.currency,
                "asset_id": p.asset_id,
                "asset_class": class_of(p),
                "manual": assets[p.asset_id].manual,  # valeur saisie (D-056)
                "color": assets[p.asset_id].color,
                # Poids de la ligne dans le patrimoine total en EUR (espèces comprises).
                "value_eur": money_or_none(value_eur(p)),
                "weight_pct": pct(ratio(value_eur(p), grand_total)) if value_eur(p) else None,
                **price_info(p.asset_id),
            }
            for p, change in zip(valued, changes, strict=True)
        ],
        "cash": [
            {
                "account": a.name,
                "kind": a.kind,
                "cash": money(a.cash),
                "currency": a.currency,
                "cash_date": a.cash_date.isoformat() if a.cash_date else None,
            }
            for a in cash_accounts
        ],
        "totals": {cur: _total_out(t) for cur, t in totals.items()},
        "allocation": _allocation_out(
            [(class_of(p), p.value, p.currency) for p in valued]
            + [(_cash_class(a), a.cash, a.currency) for a in cash_accounts],
            rates,
        ),
        # Détail d'une part (clic sur le camembert) : les lignes de la classe, ou les comptes
        # pour les espèces ; pourcentages calculés dans la classe.
        "groupings": _groupings_out(valued, cash_accounts, accounts, assets, rates),
        "allocation_detail": {
            label: _allocation_out(slices, rates)
            for label, slices in _detail_slices(valued, cash_accounts, assets).items()
        },
        "filtered": bool(keep),
        "fx_error": fx_error,
        "price_ttl_minutes": int(ttl.total_seconds() // 60),
    }


def value_series(client: httpx2.Client, keep: LineFilter = NO_FILTER, placed: bool = False) -> dict:
    """Série quotidienne de la valeur des titres (voir /api/history), et ce qui a servi à la
    calculer. Complète au besoin les clôtures chez Yahoo (au plus une fois par actif et par
    jour) : seuls le ticker et deux dates partent sur le réseau (règle 3).

    `keep` : lignes laissées de côté (filtre de Vue d'ensemble, D-069).
    """
    # Jour local, comme la date des opérations saisies (D-039) : en UTC, une opération du
    # jour saisie entre minuit et 2 h serait absente de la courbe jusqu'à 2 h.
    today = date.today()
    with db.connection() as conn:
        assets = {a.id: a for a in repository.list_assets(conn)}
        operations = repository.list_operations(conn)
        traded = sorted({op.asset_id for op in operations if op.op_type in TRADES})
        # Arrivées des transferts avant le filtre : un compte gardé reçoit ce qu'on lui a envoyé
        # même si le compte de départ est laissé de côté (D-085).
        operations = [
            op for op in with_transfers(operations) if keep.keep(op.account_id, op.asset_id)
        ]
        starts: dict[int, date] = {}
        for op in operations:
            if op.op_type in TRADES:
                starts[op.asset_id] = min(starts.get(op.asset_id, op.op_date), op.op_date)
        errors = prices.update_history(
            conn, [(assets[i], d) for i, d in sorted(starts.items())], client, today=today
        )
        closes = {i: prices.load_history(conn, i) for i in starts}
        latest = {i: prices.latest_price(conn, i) for i in starts}
        currencies = {op.currency for op in operations}
        currencies |= {c for series in closes.values() for _, _, c in series}
        currencies |= {p.currency for p in latest.values() if p}
        rates = prices.stored_rates(conn, currencies)
        fx_error = _update_fx_history(conn, operations, currencies, client, today)
        fx = RateHistory(rates, prices.rate_series(conn, currencies))
    if fx_error:
        errors[0] = fx_error
    try:
        points = value_history(
            operations,
            closes,
            rates,
            today,
            {  # cours du cache, sauf s'il est plus ancien que la dernière clôture connue
                i: (p.price, p.currency)
                for i, p in latest.items()
                if p and (not closes[i] or p.fetched_at.date() >= closes[i][-1][0])
            },
            fx,
            placed=placed,
        )
    except PositionError as err:
        raise HTTPException(status_code=409, detail=str(err)) from err
    return {
        "today": today,
        "traded": [{"id": i, "name": assets[i].name} for i in traded],
        "operations": operations,
        "rates": rates,
        "fx": fx,
        "closes": closes,
        "points": points,
        "without_history": sorted(
            assets[i].name for i in starts if not closes[i] and not assets[i].manual
        ),
        "errors": [f"{assets[i].name} : {msg}" if i else msg for i, msg in errors.items()],
    }


def _update_fx_history(
    conn, operations: list[Operation], currencies: set[str], client: httpx2.Client, today: date
) -> str | None:
    """Historique des taux des devises autres que l'euro, depuis leur première opération
    (D-081) ; seuls les codes devises partent sur le réseau."""
    first = min((o.op_date for o in operations), default=today)
    starts = {cur: first for cur in currencies if cur != "EUR"}
    for o in operations:
        if o.currency in starts:
            starts[o.currency] = min(starts[o.currency], o.op_date)
    return prices.update_fx_history(conn, starts, client, today=today) if starts else None


def series_flows(series: dict) -> tuple[dict[date, Decimal], dict[date, Decimal]]:
    """Apports (achats, ventes, titres déjà détenus à leur valeur de marché) et revenus
    (dividendes moins frais) de chaque jour, en EUR aux taux de leur jour (D-081)."""
    ops, rates, fx = series["operations"], series["rates"], series["fx"]
    return daily_flows(ops, rates, fx, series["closes"]), daily_income(ops, rates, fx)


@router.get("/api/history")
def history(client: HttpClient, keep: Filter, inflation_rate: Decimal = Decimal("2")) -> dict:
    """Valeur des titres et montant investi, en EUR, jour par jour depuis le premier achat.

    Les taux et le dernier cours viennent du cache rempli par /api/portfolio, sans autre appel.
    `invested_eroded` : montant investi moins l'inflation. Indice Eurostat lissé par année
    civile (D-110) pour les mois publiés, puis `inflation_rate` (% par an, saisi dans la
    page) au-delà ou en cas de panne.
    """
    if not inflation_rate.is_finite() or not -10 <= inflation_rate <= 30:
        raise HTTPException(status_code=422, detail="Taux d'inflation invalide (-10 à 30 %).")
    # Investi = argent net placé (D-110) : achats moins ventes, cadeaux exclus, pour que la
    # courbe érodée ne monte qu'avec de l'argent placé en plus.
    series = value_series(client, keep, placed=True)
    points = series["points"]
    measured, error = prices.inflation_index(client) if points else ({}, None)
    # Indice lissé (D-110) : la courbe ne remonte pas aux mois de soldes, seulement si les prix
    # baissent sur une année entière ou si l'investi augmente.
    smoothed = smooth_index(measured)
    index = extend_index(smoothed, inflation_rate, points[0][0], points[-1][0]) if points else {}
    eroded = eroded_invested([(day, invested) for day, _, invested in points], index, flows=True)
    last_month = max(measured) if measured else None
    return {
        "points": [
            {
                "date": day.isoformat(),
                "value": money(value),
                "invested": money(invested),
                "invested_eroded": money_or_none(e),
                "gain": money(value - invested),
                "inflation_loss": None if e is None else money(e - invested),
                # Taux annuel que suit la courbe ce jour-là, pour l'infobulle (D-112).
                "inflation_pct": plain(
                    yearly_inflation(index, day, inflation_rate).quantize(Decimal("0.1"))
                ),
            }
            for (day, value, invested), e in zip(points, eroded, strict=True)
        ],
        # Inflation : indice Eurostat, dont seul le nom de série part sur le réseau (D-045),
        # prolongé au taux saisi après le dernier mois publié (D-067).
        "inflation": {
            "source": "Eurostat, IPCH France",
            "last_month": last_month.strftime("%Y-%m") if last_month else None,
            "rate": plain(inflation_rate),
            "error": error,
        },
        "without_history": series["without_history"],
        "errors": series["errors"],
    }
