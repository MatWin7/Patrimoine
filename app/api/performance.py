"""Routes de la performance par période (TWR, D-029), de la comparaison à un indice ou à un
livret (D-044) et du calendrier des rendements mensuels."""

from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated, Literal

import httpx2
from fastapi import APIRouter, HTTPException, Query, Response

from app import crypto_tickers, db, prices, providers, repository
from app.analytics import (
    blend,
    livret_closes,
    monthly_returns,
    points_per_year,
    simulate_benchmark,
    volatility,
    xirr,
    yearly_returns,
)
from app.api.common import pct
from app.api.deps import HttpClient
from app.api.portfolio import NO_FILTER, Filter, series_flows, value_series
from app.portfolio import (
    ZERO,
    annualized,
    compute_positions,
    convert,
    max_drawdown,
    performance,
    ratio,
)
from app.schemas import money, plain

router = APIRouter()


Period = Literal["day", "week", "month", "year"]


def _range(series: dict, start: date | None, end: date | None) -> tuple[date, date]:
    """Bornes de l'analyse : du premier jour connu à aujourd'hui par défaut."""
    first_day = series["points"][0][0]
    start = max(start or first_day, first_day)
    end = min(end or series["today"], series["today"])
    if start > end:
        raise HTTPException(status_code=422, detail="La date de début est après la date de fin.")
    return start, end


def _performance_data(client: httpx2.Client, period: str, start, end, keep=NO_FILTER) -> dict:
    series = value_series(client, keep)
    out = {"assets": series["traded"], "periods": [], "summary": None}
    if not series["points"]:
        return {**out, "start": None, "end": None}
    start, end = _range(series, start, end)
    points = [(day, value) for day, value, _ in series["points"]]
    flows, income = series_flows(series)
    results = performance(points, flows, period, start, end, income)
    daily = performance(points, flows, "day", start, end, income)
    out |= {"start": start.isoformat(), "end": end.isoformat()}
    if not results:
        return out
    last = results[-1]
    ranked = sorted(results, key=lambda r: r.factor)
    days = (end - start).days + 1
    yearly = annualized(last.cumulative_factor, days)
    vol = volatility([r.factor for r in daily], points_per_year(len(daily), start, end))
    # TRI : la valeur de départ comme un versement, la valeur finale comme un retrait ; les
    # dividendes reçus comptent comme de l'argent rendu, les frais comme de l'argent versé.
    cashflows = [(start, -results[0].start_value)]
    cashflows += [(d, -f) for d, f in flows.items() if start <= d <= end]
    cashflows += [(d, i) for d, i in income.items() if start <= d <= end]
    cashflows.append((end, last.end_value))
    out["periods"] = [
        {
            "label": r.label,
            "first": r.first.isoformat(),
            "last": r.last.isoformat(),
            "start_value": money(r.start_value),
            "flows": money(r.flows),
            "income": money(r.income),
            "end_value": money(r.end_value),
            "gain": money(r.gain),
            "performance": pct(r.performance),
            "cumulative_gain": money(r.cumulative_gain),
            "cumulative_performance": pct(r.cumulative_factor - 1),
        }
        for r in results
    ]
    out["summary"] = {
        "start_value": money(results[0].start_value),
        "end_value": money(last.end_value),
        "flows": money(sum((r.flows for r in results), ZERO)),
        "income": money(sum((r.income for r in results), ZERO)),
        "gain": money(last.cumulative_gain),
        "performance": pct(last.cumulative_factor - 1),
        "annualized": pct(yearly),
        "irr": pct(xirr(cashflows)) if days >= 30 else None,
        "volatility": pct(vol),
        "risk_ratio": money(yearly / vol) if yearly is not None and vol else None,
        "max_drawdown": pct(max_drawdown(daily)),
        "best": {"label": ranked[-1].label, "performance": pct(ranked[-1].performance)},
        "worst": {"label": ranked[0].label, "performance": pct(ranked[0].performance)},
        "positive_periods": sum(1 for r in results if r.factor > 1),
        "count": len(results),
    }
    return out


@router.get("/api/performance")
def performance_by_period(
    client: HttpClient,
    keep: Filter,
    period: Period = "month",
    start: date | None = None,
    end: date | None = None,
) -> dict:
    """Gain et performance (TWR) par jour, semaine, mois ou année, entre `start` et `end`.

    Par défaut : du premier achat à aujourd'hui. Chaque période donne aussi le cumul depuis
    `start`, comme un suivi mensuel « depuis le début / sur le mois ». Le résumé ajoute le
    rendement personnel annualisé (TRI, à partir de 30 jours), la volatilité et la baisse max.
    `keep` : lignes laissées de côté (LineFilter) ; `assets` liste toutes les lignes.
    """
    return _performance_data(client, period, start, end, keep)


CSV_COLUMNS = [
    ("label", "Période"),
    ("first", "Du"),
    ("last", "Au"),
    ("start_value", "Valeur début (EUR)"),
    ("flows", "Apports (EUR)"),
    ("end_value", "Valeur fin (EUR)"),
    ("gain", "Gain (EUR)"),
    ("performance", "Performance (%)"),
    ("cumulative_gain", "Cumul (EUR)"),
    ("cumulative_performance", "Cumul (%)"),
]


@router.get("/api/performance.csv")
def performance_csv(
    client: HttpClient,
    keep: Filter,
    period: Period = "month",
    start: date | None = None,
    end: date | None = None,
) -> Response:
    """Le tableau de performance en CSV pour un tableur (format français : « ; » et virgule).

    Le fichier est généré et téléchargé en local : il ne part nulle part (règle 2).
    """
    data = _performance_data(client, period, start, end, keep)
    lines = [";".join(title for _, title in CSV_COLUMNS)]
    for row in data["periods"]:
        lines.append(";".join(str(row[key]).replace(".", ",") for key, _ in CSV_COLUMNS))
    name = f"performance-{period}-{data['start'] or 'vide'}-{data['end'] or 'vide'}.csv"
    # BOM UTF-8 en tête : sans lui, Excel lit les accents de travers.
    return Response(
        "\ufeff" + "\r\n".join(lines) + "\r\n",
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


TickerParam = Annotated[str, Query(pattern=r"^[A-Za-z0-9.^=\-]{1,20}$")]


# Taux d'un livret en % par an : saisi par l'utilisateur, il ne part jamais sur le réseau.
RateParam = Annotated[Decimal | None, Query(ge=-5, le=30)]


def _reference_closes(
    client: httpx2.Client, ticker: str, rate: Decimal | None, start: date, today: date
) -> tuple[str, list[tuple[date, Decimal]]]:
    """Clôtures d'un indice (Yahoo) ou « cours » fictif d'un livret à taux fixe (calculé ici)."""
    if rate is not None:
        return "EUR", livret_closes(rate, start - prices.MARGIN, today)
    try:
        return prices.benchmark_history(client, ticker.upper(), start, today)
    except providers.ProviderError as err:
        raise HTTPException(status_code=502, detail=f"Indice {ticker} : {err}") from err


@router.get("/api/benchmark")
def benchmark(
    client: HttpClient,
    keep: Filter,
    ticker: TickerParam = "IWDA.AS",
    rate: RateParam = None,
    start: date | None = None,
    end: date | None = None,
) -> dict:
    """Ton portefeuille face à un indice ou à un livret (`rate`, en % par an) : le même argent,
    versé aux mêmes dates, placé dans l'indice ou sur le livret. Seul le ticker de l'indice
    part sur le réseau (règle 3) ; un livret est calculé sur place. `exclude` : lignes
    laissées de côté, comme en Performance (D-050)."""
    series = value_series(client, keep)
    if not series["points"]:
        return {"points": [], "summary": None}
    start, end = _range(series, start, end)
    currency, closes = _reference_closes(client, ticker, rate, start, series["today"])
    with db.connect() as conn:
        rates, fx_error = prices.get_rates(
            conn, {currency}, client, today=series["today"], may_fetch=False
        )
    conn.close()
    in_eur = [(d, convert(c, currency, "EUR", rates)) for d, c in closes]
    if any(c is None for _, c in in_eur):
        raise HTTPException(status_code=502, detail=f"Taux de change {currency} : {fx_error}")
    points = [(d, v) for d, v, _ in series["points"] if d <= end]
    before = [v for d, v in points if d < start]
    start_value = before[-1] if before else ZERO
    days = [d for d, _ in points if d >= start]
    flows, income = series_flows(series)
    simulated = dict(simulate_benchmark(days, flows, in_eur, start_value, start))
    mine = dict(points)
    rows = [d for d in days if d in simulated]
    perf = performance(points, flows, "year", start, end, income)

    def close_on(day: date) -> Decimal | None:
        known = [c for d, c in in_eur if d <= day]
        return known[-1] if known else (in_eur[0][1] if in_eur else None)

    first_close, last_close = close_on(start), close_on(end)
    index_perf = last_close / first_close - 1 if first_close and last_close else None
    my_perf = perf[-1].cumulative_factor - 1 if perf else None
    return {
        "ticker": None if rate is not None else ticker.upper(),
        "rate": None if rate is None else plain(rate),
        "currency": currency,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "points": [
            {"date": d.isoformat(), "portfolio": money(mine[d]), "benchmark": money(simulated[d])}
            for d in rows
        ],
        "summary": {
            "portfolio_performance": pct(my_perf),
            "benchmark_performance": pct(index_perf),
            "difference": pct(my_perf - index_perf)
            if my_perf is not None and index_perf is not None
            else None,
            "portfolio_value": money(mine[rows[-1]]) if rows else None,
            "benchmark_value": money(simulated[rows[-1]]) if rows else None,
            # Écart en vrai % : valeur réelle comparée à la valeur si tout était dans l'indice.
            "value_difference": pct(ratio(mine[rows[-1]], simulated[rows[-1]]) - 1)
            if rows and simulated[rows[-1]]
            else None,
        },
    }


CALENDAR_YEARS = 10  # profondeur d'un calendrier d'indice ou de livret


@router.get("/api/calendar")
def calendar(
    client: HttpClient,
    keep: Filter,
    ticker: TickerParam | None = None,
    rate: RateParam = None,
) -> dict:
    """Rendement de chaque mois et de chaque année.

    Sans paramètre : ton portefeuille (TWR, achats neutralisés). Les mois qui précèdent ton
    premier achat, sur CALENDAR_YEARS années pleines au plus, sont « estimés » : rendement de
    tes lignes actuelles, pondérées par leur prix de revient, comme si tu les avais déjà eues
    (D-044, D-047).
    Avec `ticker` ou `rate` : un indice ou un livret, sur CALENDAR_YEARS ans, dans sa devise.
    `exclude` : lignes du portefeuille laissées de côté, comme en Performance (D-050).
    """
    today = date.today()
    if ticker or rate is not None:
        start = date(today.year - CALENDAR_YEARS, 1, 1)
        currency, closes = _reference_closes(client, ticker or "", rate, start, today)
        months = monthly_returns(closes)
        return _calendar_out(months, set(), currency)
    series = value_series(client, keep)
    if not series["points"]:
        return _calendar_out({}, set(), "EUR")
    points = [(day, value) for day, value, _ in series["points"]]
    flows, income = series_flows(series)
    first = points[0][0]
    real = {
        r.label: r.factor - 1 for r in performance(points, flows, "month", first, today, income)
    }
    estimated = _months_before(client, series, first)
    return _calendar_out({**estimated, **real}, set(estimated), "EUR")


def _months_before(client: httpx2.Client, series: dict, first: date) -> dict[str, Decimal]:
    """Mois d'avant le premier achat (depuis janvier, CALENDAR_YEARS ans plus tôt), estimés à
    partir des cours passés des lignes détenues aujourd'hui (seul leur ticker part sur le
    réseau, jusqu'à aujourd'hui : jamais la date du premier achat, règle 3). Une ligne cotée
    depuis moins longtemps compte à partir de sa cotation."""
    since = date(first.year - CALENDAR_YEARS, 1, 1)
    with db.connect() as conn:
        assets = {a.id: a for a in repository.list_assets(conn)}
    conn.close()
    weighted = []
    for p in compute_positions(series["operations"]):
        asset = assets[p.asset_id]
        # Crypto : son ticker Yahoo, saisi ou connu (D-111).
        ticker = crypto_tickers.yahoo_ticker(asset.ticker, asset.coingecko_id)
        if not ticker:
            continue  # crypto inconnue de Yahoo : pas d'historique d'avant l'achat
        try:
            # Dernière clôture de décembre d'avant : point de départ du premier janvier.
            _, closes = prices.benchmark_history(
                client, ticker, since - timedelta(days=31), date.today()
            )
        except providers.ProviderError:
            continue
        weighted.append((p.cost, monthly_returns(closes)))
    before = f"{first.year}-{first.month:02d}"
    start = f"{since.year}-01"
    return {k: v for k, v in blend(weighted).items() if start <= k < before}


def _calendar_out(months: dict[str, Decimal], estimated: set[str], currency: str) -> dict:
    # Année à cheval sur le premier achat : son total n'enchaîne que les mois réels. Seule une
    # année sans aucun mois réel garde un total estimé (D-047).
    real_years = {k[:4] for k in months if k not in estimated}
    kept = {k: v for k, v in months.items() if k not in estimated or k[:4] not in real_years}
    years = yearly_returns(kept)
    return {
        "currency": currency,
        "months": {k: pct(v) for k, v in sorted(months.items())},
        "years": {k: pct(v) for k, v in sorted(years.items())},
        "estimated": sorted(estimated),
        # Années dont le total est estimé : aucun mois réel.
        "estimated_years": sorted({k[:4] for k in estimated} - real_years),
    }
