"""Application FastAPI : API JSON sous /api, page web statique sous /."""

import contextvars
import json
import queue
import re
import sqlite3
import threading
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal

import httpx2
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app import (
    config,
    crypto,
    crypto_tickers,
    custody,
    db,
    debts,
    defi,
    defi_sync,
    diagnostic,
    income,
    prices,
    profiles,
    providers,
    rebalance,
    repository,
    tax,
    tickers,
)
from app.analytics import (
    blend,
    eroded_invested,
    extend_index,
    livret_closes,
    monthly_returns,
    points_per_year,
    project,
    simulate_benchmark,
    smooth_index,
    volatility,
    xirr,
    yearly_inflation,
    yearly_returns,
)
from app.api import accounts, benchmarks, machine, operations, watchlists
from app.api import profiles as profiles_routes
from app.api import recurring as recurring_routes
from app.api.common import (
    CLASS_LABELS,
    LIVRETS,
    category_of,
)
from app.api.common import backup_before_delete as _backup_before_delete
from app.api.common import money_or_none as _money_or_none
from app.api.common import pct as _pct
from app.api.common import price_text as _price_text
from app.api.common import reorder as _reorder
from app.api.deps import SESSION_COOKIE, HttpClient, unready_profiles

# Les tests remplacent ce client par `app.dependency_overrides[http_client]` : le nom reste
# importable d'ici.
from app.api.deps import http_client as http_client
from app.api.operations import save_operation as _save_operation
from app.importers import binance, csv_generique, preparation, trade_republic
from app.models import Account, Asset, Operation
from app.portfolio import (
    TRADES,
    ZERO,
    PositionError,
    RateHistory,
    allocation,
    annualized,
    compute_positions,
    convert,
    daily_flows,
    daily_income,
    day_change,
    eur_costs,
    max_drawdown,
    performance,
    ratio,
    sum_by_currency,
    top_parts,
    totals_in_eur,
    value_history,
    with_market_prices,
    with_transfers,
)
from app.providers import coingecko, yahoo
from app.schemas import (
    CategoryTargetsIn,
    DebtBalanceIn,
    DebtIn,
    DefiBorrowIn,
    DefiBorrowMoveIn,
    DefiCheckIn,
    DefiLineIn,
    DefiMoveIn,
    ImportIn,
    OrderIn,
    SplitIn,
    TargetsIn,
    TradeIn,
    money,
    plain,
)

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    unready_profiles.clear()
    if profiles.enabled():
        for profile in profiles.load():
            try:
                db.init_db(profiles.db_file(profile))
            except RuntimeError as err:
                unready_profiles[profile.id] = str(err)
    else:
        db.init_db()  # crée la base au premier lancement
    yield


# Pas de /docs ni /redoc : ces pages chargent leur JS et leur CSS depuis un CDN (D-028, D-039).
# Télémétrie native de FastAPI (OpenTelemetry) coupée d'office, même si un fournisseur ou les
# variables OTEL_* / FASTAPI_OTEL_AUTO_CONFIGURE apparaissaient sur la machine (règle 2, D-122).
NO_TELEMETRY = {"tracing": False, "metrics": False, "logs": False, "auto_configure": False}
app = FastAPI(
    title="Patrimoine",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    telemetry=NO_TELEMETRY,
)

# Profils (D-098) : chaque requête de l'API porte un cookie de session, jeton aléatoire remis
# à l'ouverture d'un profil, qui désigne sa base. Sans profil valide, seules les routes qui
# servent à en choisir un répondent. Ce middleware est déclaré en premier : il passe après les
# contrôles du nom de site et de l'origine.
OPEN_PROFILE = re.compile(r"/api/profiles/[^/]+/open")


WINDOW_ACTIONS = (
    "/api/window/focus",
    "/api/window/browser",
    "/api/window/folder",
    "/api/window/zoom",
)


def _without_profile(request: Request) -> bool:
    """Routes qui servent à choisir un profil, et celles de la fenêtre (D-125, D-127) : elles ne
    lisent aucune base, et le bouton « Fenêtre » marche aussi sur l'écran de choix du profil,
    comme la barre de menu qu'il remplace. Le diagnostic (D-132) aussi : un profil qui ne
    s'ouvre plus doit pouvoir être signalé ; sans profil ouvert, il ne lit aucune base. La page
    de démarrage (D-134) lit son fond et son mot d'accueil avant le choix du profil."""
    path, method = request.url.path, request.method
    if path in ("/api/health", "/api/profiles", "/api/window", "/api/diagnostic"):
        return method == "GET"
    if path in ("/api/start", "/api/start/image"):  # page de démarrage : lecture seule (D-134)
        return method == "GET"
    return method == "POST" and (
        path == "/api/profiles/lock" or path in WINDOW_ACTIONS or bool(OPEN_PROFILE.fullmatch(path))
    )


@app.middleware("http")
async def profile_scope(request: Request, call_next):
    if not request.url.path.startswith("/api/") or not profiles.enabled():
        return await call_next(request)
    profile = profiles.session_profile(request.cookies.get(SESSION_COOKIE))
    if profile is None:
        if _without_profile(request):
            return await call_next(request)
        return PlainTextResponse("Choisir un profil", 401, headers={"X-Profile": "required"})
    # Les onglets d'un même navigateur partagent le cookie : un onglet resté sur Marie alors
    # qu'un autre a ouvert Paul annonce « Marie » dans X-Profile-Id et reçoit 409 au lieu
    # d'écrire chez Paul. En-tête absent (anciennes pages) : accepté.
    expected = request.headers.get("x-profile-id")
    if expected is not None and expected != profile.id and not _without_profile(request):
        return PlainTextResponse(
            "Profil changé dans un autre onglet : recharger la page",
            409,
            headers={"X-Profile": "changed"},
        )
    if profile.id in unready_profiles and not _without_profile(request):
        return PlainTextResponse(unready_profiles[profile.id], 503)
    request.state.profile = profile
    token = db.current_profile_db.set(profiles.db_file(profile))
    try:
        return await call_next(request)
    finally:
        db.current_profile_db.reset(token)


# Noms de site acceptés dans l'en-tête Host. Tout autre nom est refusé : une page web piégée
# qui ferait pointer son propre domaine vers 127.0.0.1 (« DNS rebinding ») ne peut donc ni
# lire les montants ni modifier la base (D-039). Les tests ajoutent « testserver ».
ALLOWED_HOSTS = {"localhost", "127.0.0.1"}


@app.middleware("http")
async def check_host(request: Request, call_next):
    host = request.headers.get("host", "").rsplit(":", 1)[0].lower()
    if host not in ALLOWED_HOSTS:
        return PlainTextResponse("Nom de site refusé : ouvrir l'app par http://localhost", 400)
    return await call_next(request)


# Un site ouvert dans un autre onglet peut envoyer des requêtes vers localhost avec le bon
# nom de site : le contrôle du Host ne suffit pas. Le navigateur indique d'où vient une
# requête (Sec-Fetch-Site, Origin) : l'API ne répond qu'à la page de l'app elle-même (D-080).
SAME_SITE_VALUES = {"same-origin", "none"}  # « none » : adresse tapée, favori
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@app.middleware("http")
async def check_origin(request: Request, call_next):
    if request.url.path.startswith("/api/"):
        fetch_site = request.headers.get("sec-fetch-site")
        origin = request.headers.get("origin")
        if fetch_site is not None and fetch_site not in SAME_SITE_VALUES:
            return PlainTextResponse("Requête venue d'un autre site : refusée", 403)
        if request.method in UNSAFE_METHODS and origin is not None:
            if origin != f"http://{request.headers.get('host', '')}":
                return PlainTextResponse("Requête venue d'un autre site : refusée", 403)
    return await call_next(request)


# En-têtes de sécurité de chaque réponse (D-080). La politique de contenu (CSP) n'autorise que
# les fichiers de l'app : même un script copié dans web/vendor/ ne pourrait envoyer les montants
# ailleurs (connect-src 'self'), et aucun autre site ne peut afficher l'app dans un cadre
# invisible pour faire cliquer « Supprimer » à l'insu de l'utilisateur (frame-ancestors).
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
        "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; "
        "form-action 'self'; frame-ancestors 'none'"
    ),
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Cross-Origin-Opener-Policy": "same-origin",
}


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.update(SECURITY_HEADERS)
    return response


@app.middleware("http")
async def revalidate_web_files(request: Request, call_next):
    """Page, JS et CSS : le navigateur redemande à chaque chargement s'ils ont changé (réponse
    304 très légère sinon). Sans cela, il peut garder un ancien app.js après une mise à jour.
    API : rien n'est gardé en cache."""
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache"
    elif "cache-control" not in response.headers:
        # Réponses de l'API (montants) : jamais écrites dans le cache disque du navigateur ou du
        # moteur de la fenêtre (D-125), qui survivrait à la fermeture et au chiffrement à venir.
        response.headers["Cache-Control"] = "no-store"
    return response


# Erreur inattendue (D-132) : retenue en mémoire pour le diagnostic (type et lignes de l'app,
# jamais son message ni l'adresse demandée), et un message qui dit quoi faire. L'erreur reste
# écrite dans la console ou le journal local, comme avant.
@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    route = getattr(request.scope.get("route"), "path", None) or "(route inconnue)"
    diagnostic.record(exc, f"{request.method} {route}")
    return JSONResponse(
        {"detail": "Erreur inattendue. Pour la signaler : Réglages, « Copier le diagnostic »."},
        status_code=500,
    )


# Routes de la machine puis des profils (app/api/, D-139), branchées avant celles de ce fichier
# pour garder l'ordre de déclaration d'origine.
app.include_router(machine.router)
app.include_router(profiles_routes.router)


def _allocation_out(slices: list[tuple[str, Decimal, str]], rates: dict[str, Decimal]) -> list:
    """Parts du patrimoine en EUR (graphique de répartition), pourcentages calculés ici."""
    parts = allocation(slices, rates)
    whole = sum((value for _, value in parts), ZERO)
    return [
        {"label": label, "value": money(value), "pct": _pct(ratio(value, whole))}
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
                {"label": label, "value": money(value), "pct": _pct(ratio(value, whole))}
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
    out["gain_pct"] = _pct(ratio(t["gain"], t["cost"]))
    if "day_change" in t:
        out["day_change"] = money(t["day_change"])
        out["day_change_pct"] = _pct(ratio(t["day_change"], t["value"] - t["day_change"]))
    if "cash" in t:
        out["cash"] = money(t["cash"])
        out["total"] = money(t["value"] + t["cash"])  # titres + espèces
    return out


# Comptes, actifs et valeurs saisies, opérations, achats programmés (app/api/).
app.include_router(accounts.router)
app.include_router(operations.router)


app.include_router(recurring_routes.router)


# --- Revenus, frais, allocation cible, repères fiscaux (D-057 à D-060) ---------------
# Calculés sur le cache des cours et des taux, sans aucun appel réseau.


def _offline_state() -> dict:
    """Comptes, actifs, opérations et positions valorisées au dernier cours en cache."""
    with db.connect() as conn:
        accounts = {a.id: a for a in repository.list_accounts(conn)}
        assets = {a.id: a for a in repository.list_assets(conn)}
        operations = repository.list_operations(conn)
        positions = compute_positions(operations)
        latest = {p.asset_id: prices.latest_price(conn, p.asset_id) for p in positions}
        currencies = {o.currency for o in operations} | {a.currency for a in accounts.values()}
        currencies |= {q.currency for q in latest.values() if q}
        rates = prices.stored_rates(conn, currencies)
        fx = RateHistory(rates, prices.rate_series(conn, currencies))
        targets = repository.list_targets(conn)
        category_targets = repository.list_category_targets(conn)
    conn.close()
    market = {i: (q.price, q.currency) for i, q in latest.items() if q}
    return {
        "accounts": accounts,
        "assets": assets,
        "operations": operations,
        "valued": with_market_prices(positions, market, rates),
        "rates": rates,
        "fx": fx,
        "targets": targets,
        "category_targets": category_targets,
    }


def _by_asset_eur(state: dict, attr: str) -> dict[int, Decimal]:
    """Somme par actif (tous comptes) de la valeur ou du coût des positions, en EUR."""
    out: dict[int, Decimal] = {}
    for p in state["valued"]:
        amount = convert(getattr(p, attr), p.currency, "EUR", state["rates"])
        if amount is not None:
            out[p.asset_id] = out.get(p.asset_id, ZERO) + amount
    return out


TER_GROWTH = Decimal("5")  # rendement annuel supposé avant frais, pour l'effet des frais courants
TER_YEARS = 10


@app.get("/api/income")
def income_report() -> dict:
    """Dividendes reçus (par mois et par ligne, année, ligne ; rendements, impôt retenu), frais
    payés par année, et effet des frais courants (TER) des fonds sur 10 ans, par compte."""
    state = _offline_state()
    today = date.today()
    summary = income.income_summary(state["operations"], state["rates"], today, state["fx"])
    # Bourse seulement : les frais crypto vont dans l'onglet Crypto (D-092).
    accounts, assets = state["accounts"], state["assets"]
    stock_ops = [o for o in state["operations"] if not crypto.in_scope(o, accounts, assets)]
    fees = income.fees_by_year(stock_ops, state["rates"], state["fx"])
    costs = _by_asset_eur(state, "cost")
    values = _by_asset_eur(state, "value")
    assets = state["assets"]
    years = sorted(set(summary["by_year"]) | set(fees))
    months = [f"{d:%Y-%m}" for d in _last_months(today, 24)]
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
                "cells": [_money_or_none(c) for c in cells],
            }
        )
    calendar.sort(key=lambda r: -Decimal(r["total"]))
    return {
        "dividends": {
            "total": money(summary["total"]),
            "last_12m": money(last_12m),
            "prev_12m": money(summary["prev_12m"]),
            "change_pct": _pct(income.change_ratio(last_12m, summary["prev_12m"])),
            "monthly": money(last_12m / 12),
            "ytd": money(summary["ytd"]),
            # Rendement des titres cotés (sans cryptos ni valeurs saisies : immobilier, fonds
            # euros) : dividendes des 12 derniers mois ÷ valeur actuelle, et ÷ argent investi.
            "yield_value_pct": _pct(ratio(last_12m, sum(_titles(values, assets), ZERO))),
            "yield_cost_pct": _pct(
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
                "cost": _money_or_none(costs.get(asset_id)),
                "yield_pct": _pct(income.yield_on_cost(last, costs.get(asset_id, ZERO))),
                "withheld": money(summary["withheld"].get(asset_id, ZERO)),
            }
            for asset_id, (total, last) in sorted(
                summary["by_asset"].items(), key=lambda kv: -kv[1][0]
            )
        ],
        "fees_total": money(sum(fees.values(), ZERO)),
        "ter": _ter_report(state, values),
    }


REWARD_MONTHS = 24  # barres mensuelles de l'onglet Crypto


def _quantity(value: Decimal) -> str:
    """Quantité de jetons, 8 décimales au plus, sans notation « 1E+1 »."""
    return format(value.quantize(Decimal("1e-8")).normalize(), "f")


@app.get("/api/crypto/rewards")
def crypto_rewards() -> dict:
    """Onglet Rendements crypto (D-092, D-106) : revenus reçus (intérêts Earn, staking,
    airdrops...) par jeton, par type et par mois ; placements (compte, jeton) avec capital,
    taux et revenu estimé ; cryptos qui ne rapportent rien. Hors ligne : seuls les cours et
    taux déjà en base servent."""
    with db.connect() as conn:
        defi_sync.safe_sync(conn, date.today())  # intérêts Aave des mois clos (hors ligne)
    conn.close()
    state = _offline_state()
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
    months = [f"{d:%Y-%m}" for d in _last_months(today, REWARD_MONTHS)]
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
        borrowed = _borrows_report(conn, today)  # emprunts Aave (D-108) : coût et santé
    conn.close()
    health = [m["health_factor"] for m in borrowed["markets"] if m["health_factor"] is not None]
    return {
        "overview": {
            "income_12m": money(totals["income_12m"]),
            "gifts_12m": money(totals["gifts_12m"]),
            "total": money(totals["total"]),
            "average_rate_pct": _pct(totals["average_rate"]),
            "estimated": money(totals["estimated"]),
            # Revenu net des intérêts d'emprunt Aave ; santé : le marché le plus fragile.
            "borrow_cost": money(borrowed["cost_eur"]),
            "net_estimated": money(totals["estimated"] - borrowed["cost_eur"]),
            "borrowed": _money_or_none(borrowed["debt_eur"]),
            "health_factor": min(health, key=Decimal) if health else None,
            "placed": money(totals["placed"]),
            "placed_share_pct": _pct(totals["placed_share"]),
        },
        "placements": [
            {
                "account_id": p.account_id,
                "account": accounts[p.account_id].name,
                "asset_id": p.asset_id,
                "asset": assets[p.asset_id].name,
                "kinds": list(p.kinds),
                "aave_line_id": lines.get((p.account_id, p.asset_id)),
                "held": _quantity(p.held),
                "capital": _money_or_none(p.capital),
                "received_12m": _quantity(p.received_12m),
                "income_12m": money(p.income_12m),
                "realized_pct": _pct(p.realized),
                "rate_today_pct": _pct(p.rate_today),
                "estimated": _money_or_none(p.estimated),
            }
            for p in found
        ],
        "idle": [
            {
                "account": accounts[acc].name,
                "asset": assets[asset].name,
                "held": _quantity(positions[(acc, asset)][0]),
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
                "quantity": _quantity(a["quantity"]),
                "count": a["count"],
                "value": money(a["value"]),
                "now": _money_or_none(a["now"]),
                "held": _quantity(a["held"]),
                "share_held_pct": _pct(a["share_held"]),
                "yield_12m_pct": _pct(a["yield_12m"]),
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
                "quantity": _quantity(r.quantity),
                "value": _money_or_none(r.value),
            }
            for r in reversed(items)
        ],
    }


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
        "balance_eur": _money_or_none(convert(info["balance"], d.currency, "EUR", rates)),
        "payment": _money_or_none(info["payment"]),
        "end_date": info["end_date"].isoformat() if info["end_date"] else None,
        "payments_left": info["payments_left"],
        "interest_left": _money_or_none(info["interest_left"]),
        "repaid_pct": _pct(info["repaid_share"]),
        "balances": [
            {"date": day.isoformat(), "balance": money(v)}
            for day, v in sorted(balances.items(), reverse=True)
        ],
    }


def _debts_total_eur(conn: sqlite3.Connection, rates, today: date) -> Decimal | None:
    """Capital restant dû de toutes les dettes en EUR, emprunts Aave compris (D-108) ; None
    sans dette."""
    found = repository.list_debts(conn)
    aave_debt = _borrows_report(conn, today)["debt_eur"]
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


@app.get("/api/debts")
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
        aave_rows = _borrows_report(conn, today)["borrows"]
    conn.close()
    total += sum((Decimal(r["value_eur"]) for r in aave_rows if r["value_eur"]), ZERO)
    return {"debts": rows, "aave": aave_rows, "total_eur": money(total)}


@app.post("/api/debts", status_code=201)
def create_debt(body: DebtIn) -> dict:
    with db.connect() as conn:
        debt = repository.create_debt(conn, _debt_values(body))
    conn.close()
    return {"id": debt.id}


@app.put("/api/debts/order", status_code=204)
def reorder_debts(body: OrderIn) -> None:
    _reorder("debt", body)


@app.put("/api/debts/{debt_id}")
def update_debt(debt_id: int, body: DebtIn) -> dict:
    with db.connect() as conn:
        if repository.update_debt(conn, debt_id, _debt_values(body)) is None:
            raise HTTPException(status_code=404, detail="Dette inconnue.")
    conn.close()
    return {"id": debt_id}


@app.delete("/api/debts/{debt_id}", status_code=204)
def delete_debt(debt_id: int) -> None:
    """Supprime la dette et ses capitaux saisis, sauvegarde de la base juste avant (D-074)."""
    with db.connect() as conn:
        if repository.get_debt(conn, debt_id) is None:
            raise HTTPException(status_code=404, detail="Dette inconnue.")
    conn.close()
    _backup_before_delete()
    with db.connect() as conn:
        repository.delete_debt(conn, debt_id)
    conn.close()


@app.post("/api/debts/{debt_id}/balances", status_code=201)
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


@app.delete("/api/debts/{debt_id}/balances/{balance_date}", status_code=204)
def delete_debt_balance(debt_id: int, balance_date: date) -> None:
    with db.connect() as conn:
        if not repository.delete_debt_balance(conn, debt_id, balance_date):
            raise HTTPException(status_code=404, detail="Capital saisi inconnu.")
    conn.close()


# --- Dépôts Aave (D-095) -----------------------------------------------------------------
# Réseau : les taux publics d'un marché (chaîne, pool, jeton), au plus une fois par jour, et
# les listes de chaînes et de marchés à la demande. Jamais une adresse de portefeuille.


def _update_defi(conn: sqlite3.Connection, client: httpx2.Client) -> dict[int, str]:
    """Taux du jour des marchés suivis puis intérêts des mois clos ; {ligne: avertissement}."""
    today = date.today()
    errors = defi_sync.refresh_rates(conn, client, today)
    conn.commit()
    return errors | defi_sync.safe_sync(conn, today)


@app.get("/api/aave/chains")
def aave_chains(client: HttpClient) -> list[dict]:
    try:
        rows = defi_sync.chains(client, date.today())
    except providers.ProviderError as err:
        raise HTTPException(status_code=502, detail=f"Aave injoignable : {err}") from err
    return [{"chain_id": c, "name": n} for c, n in rows]


@app.get("/api/aave/reserves")
def aave_reserves(client: HttpClient, chain_id: Annotated[int, Query(gt=0)]) -> list[dict]:
    """Jetons de chaque marché de la chaîne : taux de dépôt et d'emprunt du jour, seuil."""
    try:
        rows = defi_sync.reserves(client, chain_id, date.today())
    except providers.ProviderError as err:
        raise HTTPException(status_code=502, detail=f"Aave injoignable : {err}") from err
    return [
        r
        | {
            "apy_pct": _pct(r["apy"]),
            "apy": plain(r["apy"]),
            "borrow_apy_pct": _pct(r["borrow_apy"]),
            "borrow_apy": None if r["borrow_apy"] is None else plain(r["borrow_apy"]),
            "liquidation_threshold": plain(r["liquidation_threshold"]),
        }
        for r in rows
    ]


def _defi_line_out(conn, line, state: dict, today: date, warning: str | None) -> dict:
    accounts, assets, operations = state["accounts"], state["assets"], state["operations"]
    asset = assets[line.asset_id]
    rates = defi_sync.load_rates(conn, line)
    checks = defi_sync.load_checks(conn, line.id)
    est = defi_sync.estimate_line(conn, line, operations, today)
    latest = prices.latest_price(conn, line.asset_id)
    price_eur = (
        None if latest is None else convert(latest.price, latest.currency, "EUR", state["rates"])
    )
    recorded = [
        o
        for o in operations
        if o.source == defi_sync.SOURCE
        and (o.account_id, o.asset_id) == (line.account_id, line.asset_id)
    ]
    held = sum(
        (
            p.quantity
            for p in state["valued"]
            if (p.account_id, p.asset_id) == (line.account_id, line.asset_id)
        ),
        ZERO,
    )
    verified = est.verified if est else set()

    def eur(qty: Decimal) -> str | None:
        return None if price_eur is None else money(qty * price_eur)

    start = today - timedelta(days=364)
    return {
        "id": line.id,
        "account_id": line.account_id,
        "account": accounts[line.account_id].name,
        "asset_id": line.asset_id,
        "asset": asset.name,
        "chain_id": line.chain_id,
        "chain": line.chain_name,
        "market_name": line.market_name,
        "symbol": line.symbol,
        "apy_today_pct": _pct(rates[max(rates)]) if rates else None,
        "apy_12m_pct": _pct(defi.average_rate(rates, start, today)),
        "first_rate": min(rates).isoformat() if rates else None,
        "held": _quantity(held),
        "value": eur(held),
        "interest": _quantity(sum((o.quantity for o in recorded), ZERO)),
        "interest_value": money(
            sum(
                (state["fx"].to_eur(o.offered, o.currency, o.op_date) or ZERO for o in recorded),
                ZERO,
            )
        ),
        "current": _quantity(est.current) if est else None,
        "current_value": eur(est.current) if est else None,
        "estimated": _quantity(est.balance) if est else None,
        "extrapolated": bool(est and est.extrapolated),
        "unexplained": _quantity(est.unexplained) if est and est.unexplained else None,
        "months": [
            {
                "month": f"{o.op_date:%Y-%m}",
                "quantity": _quantity(o.quantity),
                "value": _money_or_none(state["fx"].to_eur(o.offered, o.currency, o.op_date)),
                "verified": f"{o.op_date:%Y-%m}" in verified,
            }
            for o in sorted(recorded, key=lambda o: o.op_date, reverse=True)
        ],
        "checks": [
            {"date": d.isoformat(), "quantity": _quantity(q)}
            for d, q in sorted(checks.items(), reverse=True)
        ],
        "warning": warning,
    }


def _defi_lines_out(conn, warnings: dict[int, str]) -> list[dict]:
    state = _offline_state()
    today = date.today()
    return [
        _defi_line_out(conn, line, state, today, warnings.get(line.id))
        for line in defi_sync.list_lines(conn)
    ]


@app.get("/api/aave/lines")
def aave_lines(client: HttpClient) -> list[dict]:
    """Dépôts suivis : taux, intérêts enregistrés et estimés, soldes vérifiés."""
    with db.connect() as conn:
        warnings = _update_defi(conn, client)
        out = _defi_lines_out(conn, warnings)
    conn.close()
    return out


@app.post("/api/aave/lines", status_code=201)
def create_aave_line(body: DefiLineIn, client: HttpClient) -> list[dict]:
    """Relie une ligne (compte, jeton crypto) à un marché Aave choisi dans la liste publique."""
    try:
        reserve = next(
            r
            for r in defi_sync.reserves(client, body.chain_id, date.today())
            if (r["market"].lower(), r["token"].lower())
            == (body.market.lower(), body.token.lower())
        )
        chain = dict(defi_sync.chains(client, date.today())).get(body.chain_id, str(body.chain_id))
    except providers.ProviderError as err:
        raise HTTPException(status_code=502, detail=f"Aave injoignable : {err}") from err
    except StopIteration as err:
        raise HTTPException(status_code=422, detail="Marché ou jeton inconnu chez Aave.") from err
    with db.connect() as conn:
        if body.account_id is None:
            name = f"Aave {chain}"
            same = [a for a in repository.list_accounts(conn) if a.name == name]
            account = same[0] if same else None
            if account is None:
                account = repository.create_account(conn, name, "crypto", "Aave", "EUR")
                repository.set_account_custody(conn, account.id, "wallet", False)  # D-104
            body.account_id = account.id
        account = repository.get_account(conn, body.account_id)
        asset = repository.get_asset(conn, body.asset_id)
        if account is None or asset is None:
            raise HTTPException(status_code=404, detail="Compte ou actif inconnu.")
        if asset.asset_class != "crypto":
            raise HTTPException(status_code=422, detail="Seul un actif crypto peut être suivi.")
        try:
            defi_sync.create_line(
                conn,
                body.account_id,
                body.asset_id,
                reserve | {"chain_id": body.chain_id, "chain_name": chain},
            )
        except sqlite3.IntegrityError as err:
            raise HTTPException(
                status_code=409, detail="Cette ligne est déjà suivie sur Aave."
            ) from err
        if body.deposit is not None:
            if body.deposit.direction != "depot":
                raise HTTPException(status_code=422, detail="Le premier mouvement est un dépôt.")
            try:
                _defi_move(conn, body.account_id, body.asset_id, body.deposit, client)
            except PositionError as err:
                raise HTTPException(status_code=422, detail=str(err)) from err
        conn.commit()
        warnings = _update_defi(conn, client)
        out = _defi_lines_out(conn, warnings)
    conn.close()
    return out


def _token_price(conn, asset: Asset, day: date, client: httpx2.Client) -> Decimal:
    """Cours d'un jeton un jour donné, dans sa devise de cotation, trouvé par l'app : clôture
    en cache, sinon CoinGecko sur 365 jours (EUR converti au taux BCE du jour), sinon cours du
    moment (cache ou source). Seuls l'id du jeton et la devise partent sur le réseau."""
    close = prices.close_on(conn, asset.id, day)
    if close and close[1] == asset.currency and (date.today() - day).days < 7:
        return close[0]
    key = config.coingecko_api_key()
    if asset.coingecko_id and day < date.today():
        series = [
            p
            for d, p in prices.coin_history(client, asset.coingecko_id, date.today(), key)
            if d <= day
        ]
        if series:
            eur = series[-1]
            if asset.currency == "EUR":
                return eur
            currencies = {asset.currency}
            # Taux BCE du jour du dépôt (seul le code devise part, comme D-081).
            prices.update_fx_history(conn, {asset.currency: day}, client, today=date.today())
            fx = RateHistory(
                prices.stored_rates(conn, currencies), prices.rate_series(conn, currencies)
            )
            rate = fx.on(day).get(asset.currency)
            if rate:
                return (eur * rate).quantize(Decimal("1E-8"))
    if close and close[1] == asset.currency:
        return close[0]
    quote = prices.get_quotes(
        conn, [asset], client, ttl=config.price_ttl(), now=datetime.now(UTC), coingecko_key=key
    )[asset.id]
    if quote.price and quote.price.currency == asset.currency:
        return quote.price.price
    raise HTTPException(status_code=502, detail="Cours du jeton introuvable pour l'instant.")


def _defi_move(
    conn, account_id: int, asset_id: int, body: DefiMoveIn, client: httpx2.Client
) -> Operation:
    """Dépôt ou retrait d'une ligne suivie (D-096), enregistré comme une opération ordinaire :
    transfert depuis ou vers un compte de l'app (prix de revient gardé), sinon titres arrivés
    de l'extérieur (« déjà détenus », sans toucher aux espèces) ou partis hors de l'app."""
    asset = repository.get_asset(conn, asset_id)
    price = body.unit_price or _token_price(conn, asset, body.op_date, client)
    other = body.other_account_id
    if other is not None and repository.get_account(conn, other) is None:
        raise HTTPException(status_code=404, detail="Compte inconnu.")
    if other == account_id:
        raise HTTPException(status_code=422, detail="Choisis un autre compte que celui d'Aave.")
    common = {"asset_id": asset_id, "op_date": body.op_date, "quantity": body.quantity}
    common["unit_price"] = price
    if body.direction == "depot" and other is None:
        trade = TradeIn(
            account_id=account_id, op_type="solde_initial", note="Dépôt sur Aave", **common
        )
    elif body.direction == "depot":
        trade = TradeIn(
            account_id=other,
            op_type="transfert_sortant",
            to_account_id=account_id,
            note="Dépôt sur Aave",
            **common,
        )
    else:
        trade = TradeIn(
            account_id=account_id,
            op_type="transfert_sortant",
            to_account_id=other,
            destination=None if other else "Retrait d'Aave",
            note="Retrait d'Aave",
            **common,
        )
    return _save_operation(conn, trade)


@app.post("/api/aave/lines/{line_id}/moves", status_code=201)
def move_aave_line(line_id: int, body: DefiMoveIn, client: HttpClient) -> list[dict]:
    """Dépôt ou retrait sur une ligne suivie, puis intérêts recalculés."""
    try:
        with db.connect() as conn:
            line = defi_sync.get_line(conn, line_id)
            if line is None:
                raise HTTPException(status_code=404, detail="Ligne suivie inconnue.")
            _defi_move(conn, line.account_id, line.asset_id, body, client)
            conn.commit()
            out = _defi_lines_out(conn, _update_defi(conn, client))
    except PositionError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    conn.close()
    return out


@app.delete("/api/aave/lines/{line_id}", status_code=204)
def delete_aave_line(line_id: int) -> None:
    """Arrête le suivi : les intérêts estimés de la ligne sont retirés (sauvegarde avant)."""
    with db.connect() as conn:
        if defi_sync.get_line(conn, line_id) is None:
            raise HTTPException(status_code=404, detail="Ligne suivie inconnue.")
    conn.close()
    _backup_before_delete()
    try:
        with db.connect() as conn:
            defi_sync.delete_line(conn, line_id)
            compute_positions(repository.list_operations(conn))
    except PositionError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err


@app.post("/api/aave/lines/{line_id}/checks")
def check_aave_line(line_id: int, body: DefiCheckIn) -> dict:
    """Solde lu dans Aave : renvoie l'écart avec l'estimation, puis recale les intérêts."""
    today = date.today()
    if body.check_date > today:
        raise HTTPException(status_code=422, detail="Date future.")
    with db.connect() as conn:
        line = defi_sync.get_line(conn, line_id)
        if line is None:
            raise HTTPException(status_code=404, detail="Ligne suivie inconnue.")
        before = defi_sync.estimate_line(
            conn, line, repository.list_operations(conn), body.check_date
        )
        defi_sync.save_check(conn, line_id, body.check_date, body.quantity)
        conn.commit()
        warnings = defi_sync.safe_sync(conn, today)
        out = _defi_lines_out(conn, warnings)
    conn.close()
    estimated = before.balance if before else None
    return {
        "estimated": _quantity(estimated) if estimated is not None else None,
        "gap": _quantity(body.quantity - estimated) if estimated is not None else None,
        "lines": out,
    }


@app.delete("/api/aave/lines/{line_id}/checks/{check_date}", status_code=204)
def delete_aave_check(line_id: int, check_date: date) -> None:
    with db.connect() as conn:
        if not defi_sync.delete_check(conn, line_id, check_date):
            raise HTTPException(status_code=404, detail="Solde vérifié inconnu.")
        conn.commit()
        defi_sync.safe_sync(conn, date.today())
    conn.close()


# --- Emprunts Aave et facteur de santé (D-108) --------------------------------------------
# Dette en jetons, estimée jour par jour au taux d'emprunt public (même calcul que les dépôts,
# D-095), recalée par « Vérifier ». Facteur de santé estimé par marché avec les dépôts suivis.


def _price_eur(conn: sqlite3.Connection, asset_id: int, rates, operations) -> Decimal | None:
    """Dernier cours en EUR, sinon prix du dernier achat du jeton (comme une position)."""
    latest = prices.latest_price(conn, asset_id)
    if latest is not None:
        return convert(latest.price, latest.currency, "EUR", rates)
    buys = [
        o
        for o in operations
        if o.asset_id == asset_id and o.op_type in ("achat", "solde_initial") and o.unit_price
    ]
    last = max(buys, key=lambda o: o.op_date, default=None)
    return None if last is None else convert(last.unit_price, last.currency, "EUR", rates)


def _borrows_report(conn: sqlite3.Connection, today: date) -> dict:
    """Emprunts suivis (dette, taux, coût annuel) et facteur de santé de chaque marché qui a
    une dette. Hors ligne : taux, seuils et cours déjà en base."""
    borrows = defi_sync.list_borrows(conn)
    if not borrows:
        return {"borrows": [], "markets": [], "debt_eur": None, "cost_eur": ZERO}
    lines = defi_sync.list_lines(conn)
    currencies = {"USD"} | {a.currency for a in repository.list_assets(conn)}
    rates = prices.stored_rates(conn, currencies)
    thresholds = defi_sync.load_thresholds(conn)
    operations = repository.list_operations(conn)
    rows, markets = [], {}
    for b in borrows:
        est = defi_sync.estimate_borrow(conn, b, today)
        balance = est.balance if est else sum(defi_sync.borrow_flows(conn, b.id).values(), ZERO)
        balance = max(balance, ZERO)
        price = _price_eur(conn, b.asset_id, rates, operations)
        value = None if price is None else balance * price
        apy_rates = defi_sync.load_rates(conn, b, "borrow")
        apy = apy_rates[max(apy_rates)] if apy_rates else None
        checks = defi_sync.borrow_checks(conn, b.id)
        cost = None if value is None or apy is None else value * apy
        market = markets.setdefault(
            (b.chain_id, b.market.lower()),
            {"chain": b.chain_name, "market_name": b.market_name, "debts": [], "complete": True},
        )
        if value is None:
            market["complete"] = False
        else:
            market["debts"].append(value)
        rows.append(
            {
                "id": b.id,
                "asset_id": b.asset_id,
                "chain_id": b.chain_id,
                "chain": b.chain_name,
                "market_name": b.market_name,
                "symbol": b.symbol,
                "balance": _quantity(balance),
                "value_eur": _money_or_none(value),
                "apy_today_pct": _pct(apy),
                "cost_year_eur": _money_or_none(cost),
                "extrapolated": bool(est and est.extrapolated),
                "checks": [
                    {"date": d.isoformat(), "quantity": _quantity(q)}
                    for d, q in sorted(checks.items(), reverse=True)
                ],
                "_value": value,
                "_cost": cost,
                "_market": (b.chain_id, b.market.lower()),
            }
        )
    for line in lines:
        market = markets.get((line.chain_id, line.market.lower()))
        if market is None:
            continue
        est = defi_sync.estimate_line(conn, line, operations, today)
        price = _price_eur(conn, line.asset_id, rates, operations)
        threshold = thresholds.get(line.key)
        if est is None or price is None or threshold is None:
            market["complete"] = False
            continue
        market.setdefault("collateral", []).append((est.balance * price, threshold))
    out_markets = []
    for key, m in markets.items():
        hf = defi.health_factor(m.get("collateral", []), m["debts"])
        out_markets.append(
            {
                "chain_id": key[0],
                "chain": m["chain"],
                "market_name": m["market_name"],
                "health_factor": None if hf is None else money(hf),
                "warning": hf is not None and hf < defi.HEALTH_WARNING,
                "complete": m["complete"],
            }
        )
        for r in rows:
            if r["_market"] == key:
                r["health_factor"] = None if hf is None else money(hf)
    debt = sum((r["_value"] for r in rows if r["_value"] is not None), ZERO)
    cost = sum((r["_cost"] for r in rows if r["_cost"] is not None), ZERO)
    for r in rows:
        del r["_value"], r["_cost"], r["_market"]
    return {"borrows": rows, "markets": out_markets, "debt_eur": debt, "cost_eur": cost}


def _borrows_out(conn: sqlite3.Connection) -> dict:
    report = _borrows_report(conn, date.today())
    return {
        "borrows": report["borrows"],
        "markets": report["markets"],
        "debt_eur": _money_or_none(report["debt_eur"]),
        "cost_eur": money(report["cost_eur"]),
    }


@app.get("/api/aave/borrows")
def aave_borrows(client: HttpClient) -> dict:
    """Emprunts suivis : dette estimée, taux d'emprunt du jour, coût annuel, facteur de santé."""
    with db.connect() as conn:
        _update_defi(conn, client)
        conn.commit()
        out = _borrows_out(conn)
    conn.close()
    return out


@app.post("/api/aave/borrows", status_code=201)
def create_aave_borrow(body: DefiBorrowIn, client: HttpClient) -> dict:
    """Suit un emprunt sur un marché Aave choisi dans la liste publique, avec son premier
    emprunt en jetons. Les jetons reçus se saisissent à part, sur le compte où ils arrivent."""
    try:
        reserve = next(
            r
            for r in defi_sync.reserves(client, body.chain_id, date.today())
            if (r["market"].lower(), r["token"].lower())
            == (body.market.lower(), body.token.lower())
        )
        chain = dict(defi_sync.chains(client, date.today())).get(body.chain_id, str(body.chain_id))
    except providers.ProviderError as err:
        raise HTTPException(status_code=502, detail=f"Aave injoignable : {err}") from err
    except StopIteration as err:
        raise HTTPException(status_code=422, detail="Marché ou jeton inconnu chez Aave.") from err
    if not reserve["borrowable"]:
        raise HTTPException(status_code=422, detail="Ce jeton ne s'emprunte pas sur ce marché.")
    with db.connect() as conn:
        asset = repository.get_asset(conn, body.asset_id)
        if asset is None or asset.asset_class != "crypto":
            raise HTTPException(status_code=422, detail="Choisis l'actif crypto emprunté.")
        borrow = defi_sync.create_borrow(
            conn, body.asset_id, reserve | {"chain_id": body.chain_id, "chain_name": chain}
        )
        defi_sync.add_borrow_flow(conn, borrow.id, body.op_date, body.quantity)
        conn.commit()
        _update_defi(conn, client)
        conn.commit()
        out = _borrows_out(conn)
    conn.close()
    return out


@app.post("/api/aave/borrows/{borrow_id}/moves", status_code=201)
def move_aave_borrow(borrow_id: int, body: DefiBorrowMoveIn) -> dict:
    """Nouvel emprunt (+) ou remboursement (−), en jetons ; la dette ne descend pas sous 0."""
    with db.connect() as conn:
        borrow = defi_sync.get_borrow(conn, borrow_id)
        if borrow is None:
            raise HTTPException(status_code=404, detail="Emprunt inconnu.")
        quantity = body.quantity if body.direction == "emprunt" else -body.quantity
        if quantity < 0:
            est = defi_sync.estimate_borrow(conn, borrow, body.op_date)
            owed = est.balance if est else sum(defi_sync.borrow_flows(conn, borrow_id).values())
            if body.quantity > owed * Decimal("1.001"):  # marge : estimation au jour près
                raise HTTPException(
                    status_code=422,
                    detail=f"Remboursement supérieur à la dette estimée ({_quantity(owed)}).",
                )
        defi_sync.add_borrow_flow(conn, borrow_id, body.op_date, quantity)
        conn.commit()
        out = _borrows_out(conn)
    conn.close()
    return out


@app.post("/api/aave/borrows/{borrow_id}/checks")
def check_aave_borrow(borrow_id: int, body: DefiCheckIn) -> dict:
    """Dette lue dans Aave : renvoie l'écart avec l'estimation, qui repart de ce chiffre."""
    if body.check_date > date.today():
        raise HTTPException(status_code=422, detail="Date future.")
    with db.connect() as conn:
        borrow = defi_sync.get_borrow(conn, borrow_id)
        if borrow is None:
            raise HTTPException(status_code=404, detail="Emprunt inconnu.")
        before = defi_sync.estimate_borrow(conn, borrow, body.check_date)
        defi_sync.save_borrow_check(conn, borrow_id, body.check_date, body.quantity)
        conn.commit()
        out = _borrows_out(conn)
    conn.close()
    estimated = before.balance if before else None
    return {
        "estimated": None if estimated is None else _quantity(estimated),
        "gap": None if estimated is None else _quantity(body.quantity - estimated),
        **out,
    }


@app.delete("/api/aave/borrows/{borrow_id}/checks/{check_date}", status_code=204)
def delete_aave_borrow_check(borrow_id: int, check_date: date) -> None:
    with db.connect() as conn:
        if not defi_sync.delete_borrow_check(conn, borrow_id, check_date):
            raise HTTPException(status_code=404, detail="Dette vérifiée inconnue.")
    conn.close()


@app.delete("/api/aave/borrows/{borrow_id}", status_code=204)
def delete_aave_borrow(borrow_id: int) -> None:
    """Arrête le suivi de l'emprunt, sauvegarde de la base juste avant (D-074)."""
    with db.connect() as conn:
        if defi_sync.get_borrow(conn, borrow_id) is None:
            raise HTTPException(status_code=404, detail="Emprunt inconnu.")
    conn.close()
    _backup_before_delete()
    with db.connect() as conn:
        defi_sync.delete_borrow(conn, borrow_id)
    conn.close()


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


def _last_months(today: date, count: int) -> list[date]:
    index = today.year * 12 + today.month - 1
    return [date((index - k) // 12, (index - k) % 12 + 1, 1) for k in range(count - 1, -1, -1)]


def _advice_out(a, whole: Decimal, value: Decimal, target) -> dict:
    return {
        "value": money(value),
        "weight_pct": _pct(ratio(value, whole)) if whole else None,
        "target": None if target is None else plain(target),
        # Déjà en % (rebalance travaille en parts sur 100).
        "current_pct": money(a.current_pct) if a else None,
        "target_pct": money(a.target_pct) if a else None,
        "gap": money(a.gap) if a else None,
        "buy": money(a.buy) if a else None,
    }


@app.get("/api/targets")
def targets_report(contribution: Decimal = ZERO, level: str = "ligne") -> dict:
    """Allocation cible par ligne (D-058) ou par catégorie (D-061) : part actuelle et visée,
    écart en EUR, et répartition du prochain versement (`contribution`) sans rien vendre.
    Affichage seulement (règle 1)."""
    if not contribution.is_finite() or contribution < 0:
        raise HTTPException(status_code=422, detail="Versement invalide.")
    if level not in ("ligne", "categorie"):
        raise HTTPException(status_code=422, detail="Niveau inconnu.")
    state = _offline_state()
    if level == "categorie":
        return _category_targets(state, contribution)
    values = _by_asset_eur(state, "value")
    targets = state["targets"]
    assets = state["assets"]
    lines = [
        rebalance.Line(asset_id, values.get(asset_id, ZERO), pct)
        for asset_id, pct in targets.items()
    ]
    advice = {a.key: a for a in rebalance.rebalance(lines, contribution)}
    whole = sum(values.values(), ZERO)
    rows = []
    # Lignes avec une cible d'abord : le tableau défile, elles restent visibles.
    order = sorted(
        set(values) | set(targets), key=lambda i: (i not in targets, -values.get(i, ZERO))
    )
    for asset_id in order:
        value = values.get(asset_id, ZERO)
        out = _advice_out(advice.get(asset_id), whole, value, targets.get(asset_id))
        rows.append({"asset_id": asset_id, "asset": assets[asset_id].name, **out})
    # Actifs créés mais ni détenus ni visés : on peut leur donner une cible avant d'acheter.
    addable = sorted(
        ({"asset_id": i, "asset": a.name} for i, a in assets.items() if i not in order),
        key=lambda r: r["asset"].lower(),
    )
    return {
        "level": "ligne",
        "rows": rows,
        "addable": addable,
        "targets_total": plain(sum(targets.values(), ZERO)),
        "contribution": money(contribution),
    }


def _category_targets(state: dict, contribution: Decimal) -> dict:
    """Cibles par catégorie : le montant à acheter d'une catégorie va sur sa plus grosse ligne
    (un seul ordre, moins de frais), choix retenu pour D-061."""
    values = _by_asset_eur(state, "value")
    assets = state["assets"]
    targets = state["category_targets"]
    members: dict[str, list[int]] = {}
    for asset_id in values:
        members.setdefault(category_of(assets[asset_id]), []).append(asset_id)
    totals = {c: sum((values[i] for i in ids), ZERO) for c, ids in members.items()}
    lines = [rebalance.Line(c, totals.get(c, ZERO), pct) for c, pct in targets.items()]
    advice = {a.key: a for a in rebalance.rebalance(lines, contribution)}
    whole = sum(totals.values(), ZERO)
    rows = []
    order = sorted(
        set(totals) | set(targets), key=lambda c: (c not in targets, -totals.get(c, ZERO))
    )
    # Actifs créés par catégorie, détenus ou non : de quoi acheter dans une catégorie vide.
    created: dict[str, list[str]] = {}
    for asset in sorted(assets.values(), key=lambda a: a.name.lower()):
        created.setdefault(category_of(asset), []).append(asset.name)
    for category in order:
        ids = sorted(members.get(category, []), key=lambda i: -values[i])
        a = advice.get(category)
        # Ligne sur laquelle acheter : la plus grosse ; sinon le premier actif créé de la
        # catégorie (cible posée avant tout achat) ; aucune si la catégorie n'a aucun actif.
        candidates = [assets[ids[0]].name] if ids else created.get(category, [])[:1]
        out = _advice_out(a, whole, totals.get(category, ZERO), targets.get(category))
        rows.append(
            {
                "category": category,
                **out,
                "lines": [assets[i].name for i in ids],
                "buy_asset": candidates[0] if candidates and a and a.buy > 0 else None,
            }
        )
    return {
        "level": "categorie",
        "rows": rows,
        "addable": sorted((c for c in created if c not in order), key=str.lower),
        "targets_total": plain(sum(targets.values(), ZERO)),
        "contribution": money(contribution),
    }


@app.put("/api/targets")
def save_targets(body: TargetsIn) -> dict:
    with db.connect() as conn:
        known = {a.id for a in repository.list_assets(conn)}
        if set(body.targets) - known:
            raise HTTPException(status_code=404, detail="Actif inconnu.")
        repository.save_targets(conn, body.targets)
    conn.close()
    return targets_report()


@app.put("/api/targets/categories")
def save_category_targets(body: CategoryTargetsIn) -> dict:
    """Une catégorie sans ligne détenue est acceptée : on peut viser une catégorie avant d'y
    avoir acheté quoi que ce soit (rien à acheter n'est alors proposé)."""
    with db.connect() as conn:
        repository.save_category_targets(conn, body.targets)
    conn.close()
    return targets_report(level="categorie")


@app.get("/api/tax")
def tax_report(
    year: int | None = None,
    pfu: Decimal | None = None,
    tmi: Decimal = Decimal("30"),
    per_ceiling: Decimal | None = None,
) -> dict:
    """Repères fiscaux, en estimation : plafond et 5 ans des PEA, 8 ans des assurances-vie,
    versements et impôt des PER (D-064), plus-values réalisées, dividendes et impôt estimé des
    comptes-titres et des cryptos (D-066) pour une année. `tmi` et `per_ceiling` (plafond de
    l'avis d'impôt) sont des réglages de la page, jamais enregistrés ; `pfu` vide = taux de
    l'année (31,4 % dès 2026)."""
    today = date.today()
    year = year or today.year
    entered = pfu is not None
    if pfu is None:
        pfu = tax.pfu_default(year)
    if not pfu.is_finite() or not 0 <= pfu <= 100:
        raise HTTPException(status_code=422, detail="Taux invalide.")
    # Plus-values des comptes-titres et des cryptos : 31,4 % dès les revenus 2025 (D-081).
    pfu_gains = pfu if entered else tax.pfu_default(year, gains=True)
    if tmi not in tax.TMI_PCTS:
        raise HTTPException(status_code=422, detail="Tranche d'imposition invalide.")
    if per_ceiling is not None and (not per_ceiling.is_finite() or per_ceiling < 0):
        raise HTTPException(status_code=422, detail="Plafond invalide.")
    state = _offline_state()
    accounts = state["accounts"]
    ops = state["operations"]

    def day(d):
        return d.isoformat() if d else None

    def base(a, st) -> dict:
        return {
            "account": a.name,
            "account_id": a.id,
            "deposits": money(st["deposits"]),
            "deposits_before": money(st["deposits_before"]),
            "deposits_entered": money(st["deposits_entered"]),
            "deposits_implied": money(st["deposits_implied"]),
            "opened_on": day(st["opened_on"]),
            "opened_on_entered": st["opened_on_entered"],
            "milestone": day(st["milestone"]),
            "milestone_reached": st["milestone_reached"],
        }

    def eur(amount: Decimal, currency: str) -> Decimal:
        return convert(amount, currency, "EUR", state["rates"]) or ZERO

    def value_of(a) -> Decimal:
        """Valeur d'un compte en EUR : lignes au dernier cours + espèces."""
        lines = (eur(p.value, p.currency) for p in state["valued"] if p.account_id == a.id)
        return eur(a.cash, a.currency) + sum(lines, ZERO)

    peas = []
    avs = []
    for a in accounts.values():
        if a.kind == "pea":
            st = tax.pea_status(a, ops, today)
            latent = [eur(p.gain, p.currency) for p in state["valued"] if p.account_id == a.id]
            dividends = (
                eur(o.amount, o.currency)
                for o in ops
                if o.account_id == a.id and o.op_type == "dividende" and o.op_date.year == year
            )
            out = tax.pea_exit(
                value_of(a), eur(a.deposits, a.currency), st["milestone_reached"], pfu
            )
            peas.append(
                {
                    **base(a, st),
                    "cap": money(st["cap"]),
                    "remaining": money(st["remaining"]),
                    "used_pct": _pct(ratio(st["deposits"], st["cap"])),
                    "latent_gains": money(sum((g for g in latent if g > 0), ZERO)),
                    "latent_losses": money(-sum((g for g in latent if g < 0), ZERO)),
                    "dividends": money(sum(dividends, ZERO)),
                    "exit_gain": money(out["gain"]),
                    "exit_rate_pct": plain(out["rate_pct"]),
                    "exit_tax": money(out["tax"]),
                }
            )
        elif a.kind == "assurance_vie":
            avs.append(base(a, tax.av_status(a, ops, today)))
    per_rows = []
    per_year_total = ZERO
    for a in accounts.values():  # ordre choisi dans « Gérer » (D-070)
        if a.kind != "per":
            continue
        deposits = eur(a.deposits, a.currency)
        in_year = eur(tax.per_deposits_in_year(a, ops, year), a.currency)
        per_year_total += in_year
        out = tax.per_exit(value_of(a), deposits, tmi, pfu)
        per_rows.append(
            {
                "account": a.name,
                "account_id": a.id,
                "deposits": money(deposits),
                "deposits_year": money(in_year),
                **{k: money(out[k]) for k in ("value", "gains", "tax", "net")},
            }
        )
    per = tax.per_year(per_year_total, per_ceiling, tmi)
    cto_ids = {a.id for a in accounts.values() if a.kind == "cto"}
    fx = state["fx"]
    # Un seul prix moyen par titre pour tous les comptes-titres du foyer (D-081).
    est = tax.cto_estimate(ops, cto_ids, year, state["rates"], pfu_gains, fx, pooled=True)
    per_ids = {a.id for a in accounts.values() if a.kind == "per"}
    pea_ids = {a.id for a in accounts.values() if a.kind == "pea"}
    # Ventes de l'année dans les PEA (mode « Réalisées », D-076) : non imposées dans le plan.
    pea_est = tax.cto_estimate(ops, pea_ids, year, state["rates"], pfu, fx)
    for row in peas:
        one = tax.cto_estimate(ops, {row["account_id"]}, year, state["rates"], pfu, fx)
        row["realized_gains"], row["realized_losses"] = money(one["gains"]), money(one["losses"])

    # Prix de revient en EUR aux taux des jours d'achat, prix moyen commun aux comptes-titres.
    # Toutes les opérations : des titres reçus d'un autre compte gardent leur coût (D-138).
    cto_costs = eur_costs(ops, fx, pooled=True, pool=cto_ids)

    def latent_of(account_id: int) -> tuple[Decimal, Decimal]:
        """Plus et moins-values latentes au prix de revient réel (part offerte comprise, D-078),
        celui que retient l'impôt."""
        gains = [
            eur(p.value, p.currency) - cto_costs[(p.account_id, p.asset_id)]
            if (p.account_id, p.asset_id) in cto_costs
            else eur(p.value - p.pru * p.quantity, p.currency)
            for p in state["valued"]
            if p.account_id == account_id
        ]
        return sum((g for g in gains if g > 0), ZERO), -sum((g for g in gains if g < 0), ZERO)

    def sales_out(sales) -> list[dict]:
        return [
            {
                "date": s.op_date.isoformat(),
                "account": accounts[s.account_id].name,
                "asset": state["assets"][s.asset_id].name,
                "proceeds": money(s.proceeds),
                "cost": money(s.cost),
                "gain": money(s.gain),
                "currency": s.currency,
                "gain_eur": money(gain),
            }
            for s, gain in sales
        ]

    cto_rows = []
    latent_gains = latent_losses = ZERO
    for i in sorted(cto_ids, key=list(accounts).index):
        one = tax.cto_estimate(
            ops, cto_ids, year, state["rates"], pfu_gains, fx, pooled=True, only=i
        )
        gains, losses = latent_of(i)
        latent_gains, latent_losses = latent_gains + gains, latent_losses + losses
        cto_rows.append(
            {
                "account": accounts[i].name,
                "account_id": i,
                "deposits": money(accounts[i].deposits),
                "gains": money(one["gains"]),
                "losses": money(one["losses"]),
                "net": money(one["net"]),
                "dividends": money(one["dividends"]),
                "tax": money(one["tax"]),
                "latent_gains": money(gains),
                "latent_losses": money(losses),
                "latent_net": money(gains - losses),
                "latent_tax": money(max(ZERO, gains - losses) * pfu_gains / 100),
            }
        )
    crypto_ids = {a.id for a in accounts.values() if a.kind == "crypto"}
    years = sorted(
        {o.op_date.year for o in ops if o.account_id in cto_ids | per_ids | pea_ids | crypto_ids}
        | {today.year}
    )
    return {
        "rules_date": tax.RULES_DATE,
        "pfu_pct": plain(pfu),
        "pfu_default_pct": plain(tax.pfu_default(year)),
        "pfu_gains_pct": plain(pfu_gains),
        "crypto": _crypto_tax(state, crypto_ids, year, pfu_gains),
        # Comptes à l'étranger à déclarer chaque année (D-104), et comptes crypto dont la garde
        # reste à préciser (un portefeuille personnel ne se déclare pas).
        "foreign_accounts": [
            r | {"opened_on": day(r["opened_on"])}
            for r in custody.foreign_accounts(accounts.values())
        ],
        "custody_unknown": [
            a.name for a in accounts.values() if a.kind == "crypto" and a.custody is None
        ],
        "pea": peas,
        "pea_sales": sales_out(pea_est["sales"]),
        "pea_realized": {
            "gains": money(pea_est["gains"]),
            "losses": money(pea_est["losses"]),
            "net": money(pea_est["net"]),
        },
        "assurance_vie": avs,
        "per": {
            "year": year,
            "tmi_pct": plain(tmi),
            "pfu_pct": plain(pfu),
            "low_tmi": per["low_tmi"],
            "deposits_year": money(per["deposits"]),
            "ceiling": money(per["ceiling"]) if per["ceiling"] is not None else None,
            "remaining": money(per["remaining"]) if per["remaining"] is not None else None,
            "over": money(per["over"]),
            "saving": money(per["saving"]),
            "rows": per_rows,
        },
        "cto": {
            "year": year,
            "years": years,
            "accounts": sorted(accounts[i].name for i in cto_ids),
            # Une ligne par compte-titres : plus-values réalisées et dividendes de l'année,
            # plus et moins-values latentes et impôt si tout était vendu aujourd'hui.
            "rows": cto_rows,
            "sales": sales_out(est["sales"]),
            # Plus et moins-values de tous les comptes-titres se compensent : impôt sur le solde.
            "latent_gains": money(latent_gains),
            "latent_losses": money(latent_losses),
            "latent_tax": money(max(ZERO, latent_gains - latent_losses) * pfu_gains / 100),
            "gains": money(est["gains"]),
            "losses": money(est["losses"]),
            "net": money(est["net"]),
            "dividends": money(est["dividends"]),
            "rate_pct": plain(est["rate_pct"]),
            "tax": money(est["tax"]),
        },
    }


def _crypto_tax(state: dict, crypto_ids: set[int], year: int, pfu: Decimal) -> dict:
    """Carte « Crypto » de Fiscalité (D-066) : cessions imposables de l'année au portefeuille
    global (aide au formulaire 2086), plus-value latente et impôt si tout était vendu."""
    rates = state["rates"]
    accounts = state["accounts"]
    valued = [p for p in state["valued"] if p.account_id in crypto_ids]

    def eur(amount: Decimal, currency: str) -> Decimal:
        return convert(amount, currency, "EUR", rates) or ZERO

    current: dict[int, Decimal] = {}
    for p in valued:
        current[p.asset_id] = eur(p.last_price, p.currency)
    value = sum((eur(p.value, p.currency) for p in valued), ZERO)
    with db.connect() as conn:

        def price_on(asset_id: int, day: date) -> Decimal | None:
            close = prices.close_on(conn, asset_id, day)
            return None if close is None else state["fx"].to_eur(close[0], close[1], day)

        ops = [o for o in state["operations"] if o.account_id in crypto_ids]
        sales, acquisition = tax.crypto_sales(
            ops, rates, price_on, current, state["fx"], internal=crypto_ids
        )
    conn.close()
    est = tax.crypto_estimate(sales, year, pfu)
    out = tax.crypto_exit(value, acquisition, pfu)
    names = state["assets"]
    return {
        "year": year,
        "accounts": sorted(accounts[i].name for i in crypto_ids),
        "value": money(value),
        "acquisition": money(acquisition),
        "latent": money(value - acquisition),
        "exit_gain": money(out["gain"]),
        "exit_tax": money(out["tax"]),
        "total": money(est["total"]),
        "exempt": est["exempt"],
        "exempt_limit": plain(tax.CRYPTO_EXEMPT),
        "gains": money(est["gains"]),
        "losses": money(est["losses"]),
        "net": money(est["net"]),
        "rate_pct": plain(pfu),
        "tax": money(est["tax"]),
        "estimated": any(s.estimated for s in est["sales"]),
        "swaps": sum(
            1 for o in ops if o.op_type == "vente" and o.crypto_swap and o.op_date.year == year
        ),
        # Une ligne par cession, montants dans l'ordre du formulaire 2086.
        "sales": [
            {
                "op_id": s.op_id,
                "date": s.op_date.isoformat(),
                "asset": names[s.asset_id].name,
                "portfolio_value": money(s.portfolio_value),
                "estimated": s.estimated,
                "gross": money(s.gross),
                "fees": money(s.fees),
                "net": money(s.net),
                "purchases": money(s.purchases),
                "consumed": money(s.consumed),
                "acquisition": money(s.acquisition),
                "fraction": money(s.fraction),
                "gain": money(s.gain),
            }
            for s in est["sales"]
        ],
    }


# --- Import CSV (D-053) ----------------------------------------------------
# Le texte du fichier arrive du navigateur (127.0.0.1), est lu en mémoire et n'est ni enregistré
# ni journalisé : seules les opérations validées entrent dans la base.


PROFILES = {trade_republic.ID: trade_republic, binance.ID: binance}


@dataclass
class _Import:
    header: list[str]
    mapping: dict[str, int | None]
    profile: object | None  # module du profil de plateforme reconnu, ou None (générique)
    prepared: list[preparation.Prepared]
    parts: list[dict]
    plans: list[trade_republic.Plan]
    accounts: dict[str | None, Account]
    account_ids: set[int]  # tous les comptes de l'app (destinations possibles, D-085)
    auto_assets: list[str]  # actifs déjà revendus créés sans demander (D-085)


def _part_accounts(profile, parts: list[str], accounts: list[Account], body: ImportIn):
    """Compte de chaque partie du relevé : celui choisi, sinon l'unique compte de ce type à la
    plateforme (établissement ou nom qui la contient), de préférence celui du formulaire. Sans
    compte à la plateforme, aucun choix : l'aperçu propose de le créer (D-083), pour ne pas
    importer dans le compte d'une autre banque. Obligations et non coté suivent le compte-titres."""
    by_id = {a.id: a for a in accounts}
    chosen = body.accounts or {}
    platform = profile.INSTITUTION.lower()
    out: dict[str | None, Account] = {}
    for part in sorted(parts, key=lambda p: p in ("obligations", "non_cote")):
        kind = profile.PARTS[part][1]
        account = by_id.get(chosen.get(part))
        if account is None and part not in chosen:
            here = [
                a
                for a in accounts
                if a.kind == kind
                and (platform in (a.institution or "").lower() or platform in a.name.lower())
            ]
            if part in ("obligations", "non_cote") and "cto" in out:
                account = out["cto"]
            elif body.account_id in {a.id for a in here}:
                account = by_id[body.account_id]
            elif len(here) == 1:
                account = here[0]
        if account is not None:
            out[part] = account
    return out


Progress = Callable[[int, str], None]  # (pourcentage 0 à 100, étape en cours) : D-089


def _no_progress(percent: int, label: str) -> None:
    """Progression ignorée (aperçu sans suivi, import)."""


def _binance_pricer(
    conn,
    client,
    header: list[str],
    data: list[list[str]],
    progress: Progress = _no_progress,
) -> binance.Pricer:
    """Cours utiles à l'import Binance (D-082) : taux BCE du dollar pour les stablecoins, puis
    cours CoinGecko des 365 derniers jours des seules cryptos dont la valeur ne se déduit pas
    du fichier (gains Earn, frais en BNB, poussière). Seuls un code devise et des id CoinGecko
    partent sur le réseau, jamais une quantité ni une date d'achat (règle 3)."""
    found = binance.coins(header, data)
    if not found:
        return binance.Pricer()
    today = date.today()
    progress(20, "Taux de change")
    prices.update_fx_history(conn, {"USD": min(found.values())}, client, today=today)
    usd = prices.rate_series(conn, {"USD"}).get("USD") or []
    if not usd and "USD" in (stored := prices.stored_rates(conn, {"USD"})):
        usd = [(date.min, stored["USD"])]
    # Premier passage sans CoinGecko : les jetons qui manquent d'un cours du jour.
    probe = binance.Pricer(usd)
    progress(30, "Lecture des opérations")
    binance.parse(header, data, pricer=probe)
    assets = repository.list_assets(conn)
    closes = {}
    asked = sorted(probe.asked)
    for i, symbol in enumerate(asked):
        progress(35 + 50 * i // len(asked), f"Cours de {symbol}")
        asset = preparation.find_asset(symbol, assets)
        coin = binance.COINGECKO_IDS.get(symbol) or (asset.coingecko_id if asset else None)
        if coin:
            closes[symbol] = prices.coin_history(client, coin, today, config.coingecko_api_key())
    return binance.Pricer(usd, closes)


def _read_import(
    conn,
    body: ImportIn,
    client: httpx2.Client,
    create: bool = False,
    progress: Progress = _no_progress,
) -> _Import:
    progress(5, "Lecture du fichier")
    try:
        rows = csv_generique.read_rows(body.text)
    except csv_generique.CsvError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    header = rows[0] if body.header else [f"Colonne {i + 1}" for i in range(len(rows[0]))]
    data = rows[1:] if body.header else rows
    width = max(len(r) for r in rows)
    header += [f"Colonne {i + 1}" for i in range(len(header), width)]
    first = 2 if body.header else 1
    mapping = dict.fromkeys(csv_generique.FIELDS)
    profile = None
    if body.profile in PROFILES:
        profile = PROFILES[body.profile]
    elif body.profile is None and body.header:
        profile = next((p for p in PROFILES.values() if p.detect(header)), None)
    all_accounts = repository.list_accounts(conn)
    if profile is not None:
        if not profile.detect(header):
            raise HTTPException(
                status_code=422, detail=f"Ce fichier n'est pas un export {profile.LABEL}."
            )
        if profile is binance:
            pricer = _binance_pricer(conn, client, header, data, progress)
            progress(88, "Lecture des opérations")
            lines = binance.parse(header, data, first, pricer=pricer)
        else:
            progress(40, "Lecture des opérations")
            lines = profile.parse(header, data, first)
        found = Counter(
            line.part for line in lines if line.part and not line.skip and not line.error
        )
        accounts = _part_accounts(profile, list(found), all_accounts, body)
        parts = [
            {
                "part": part,
                "label": profile.PARTS[part][0],
                "kind": profile.PARTS[part][1],
                "suggested_name": profile.PARTS[part][2],
                "institution": profile.INSTITUTION,
                "count": found[part],
                "account_id": accounts[part].id if part in accounts else None,
            }
            for part in profile.PARTS
            if part in found
        ]
        plans = profile.detect_plans(lines)
    else:
        account = next((a for a in all_accounts if a.id == body.account_id), None)
        if account is None:
            raise HTTPException(status_code=422, detail="Choisis le compte où importer ce fichier.")
        if body.mapping is None:
            mapping.update(csv_generique.guess_mapping(header))
        else:
            for field, column in body.mapping.items():
                if field in mapping and column is not None and 0 <= column < width:
                    mapping[field] = column
        lines = [csv_generique.parse_line(first + i, r, mapping) for i, r in enumerate(data)]
        accounts, parts, plans = {None: account}, [], []
    assets = repository.list_assets(conn)
    fx = None
    auto: list[str] = []
    if profile is binance:
        # Actifs déjà revendus (D-085) : créés sans demander, dans la devise par défaut des
        # cryptos ; à l'aperçu, seulement simulés.
        found_auto = _sold_coins(lines, assets)
        for i, (symbol, coin) in enumerate(found_auto):
            if create:
                asset = repository.create_asset(
                    conn, symbol, "crypto", None, None, coin, CRYPTO_CURRENCY
                )
            else:
                asset = Asset(-1 - i, symbol, "crypto", None, None, coin, CRYPTO_CURRENCY)
            assets.append(asset)
            auto.append(symbol)
        currencies = {a.currency for a in assets} - {"EUR"}
        fx = RateHistory(
            prices.stored_rates(conn, currencies), prices.rate_series(conn, currencies)
        )
    progress(95, "Vérification des doublons")
    prepared = preparation.prepare(
        conn, accounts, lines, assets, repository.list_operations(conn), fx
    )
    account_ids = {a.id for a in all_accounts}
    return _Import(header, mapping, profile, prepared, parts, plans, accounts, account_ids, auto)


CRYPTO_CURRENCY = "USD"  # cotation par défaut d'une crypto (D-085), modifiable par actif


def _sold_coins(lines, assets: list[Asset]) -> list[tuple[str, str]]:
    """Cryptos du relevé à l'id CoinGecko connu, encore absentes de l'app et qui ne valent plus
    rien à la fin du fichier (moins d'une unité de devise) : (symbole, id), dans l'ordre."""
    net: dict[tuple[str, str], Decimal] = {}
    price: dict[tuple[str, str], Decimal] = {}
    for line in sorted(
        (x for x in lines if x.op_type in TRADES and not x.skip and not x.error),
        key=lambda x: (x.op_date, x.seq or 0, x.line),
    ):
        if not line.asset or not line.asset_name or line.asset == line.asset_name:
            continue  # id CoinGecko inconnu : l'utilisateur le saisit en créant l'actif
        if preparation.find_asset(line.asset, assets) or preparation.find_asset(
            line.asset_name, assets
        ):
            continue
        key = (line.asset_name, line.asset)
        sign = -1 if line.op_type in ("vente", "transfert_sortant") else 1
        net[key] = net.get(key, ZERO) + sign * line.quantity
        price[key] = line.unit_price or ZERO
    return [key for key, qty in net.items() if qty * price[key] < 1]


def _plan_rows(conn, imp: _Import) -> list[dict]:
    """Plans détectés, avec ce qui empêche de les créer (actif inconnu, compte, déjà là)."""
    assets = repository.list_assets(conn)
    existing = {(r["account_id"], r["asset_id"]) for r in repository.list_recurring(conn)}
    out = []
    for plan in imp.plans:
        account = imp.accounts.get(plan.part)
        asset = preparation.find_asset(plan.asset, assets)
        problem = None
        if plan.frequency is None:
            problem = "exécutions trop irrégulières : à créer à la main si besoin"
        elif asset is None:
            problem = "crée d'abord l'actif"
        elif account is None:
            problem = "choisis d'abord le compte"
        elif (account.id, asset.id) in existing:
            problem = "déjà dans tes achats programmés"
        elif asset.currency != "EUR":
            problem = "actif coté hors euro : à créer à la main"
        out.append(
            {
                "key": plan.key,
                "part": plan.part,
                "asset": plan.asset,
                "asset_name": asset.name if asset else plan.asset_name,
                "account": account.name if account else None,
                "frequency": plan.frequency,
                "day": plan.day,
                "amount": plan.amount,
                "first": plan.first,
                "last": plan.last,
                "count": plan.count,
                "active": plan.active,
                "problem": problem,
            }
        )
    return out


def _import_row_with_outflow(p, body: ImportIn, accounts: set[int]) -> dict:
    flow = _outflow(p, body, accounts)
    row = _import_row(p)
    row["outflow"] = _outflow_json(flow)
    single = flow is not None and len(flow["legs"]) == 1 and flow["rest"] == 0
    row["to_account_id"] = flow["legs"][0][0] if single else None
    row["destination"] = flow["text"] if flow else None
    return row


def _import_row(p: preparation.Prepared) -> dict:
    line = p.csv

    def text(value: Decimal | None) -> str | None:
        return None if value is None else format(value.normalize(), "f")

    return {
        "line": line.line,
        "status": p.status,
        "message": p.message,
        "op_date": line.op_date.isoformat() if line.op_date else None,
        "op_type": line.op_type,
        "asset": line.asset,
        "asset_name": p.asset.name if p.asset else line.asset_name,
        "asset_class": line.asset_class,
        "known": p.asset is not None,
        "asset_id": p.asset.id if p.asset else None,
        "account": p.account.name if p.account else None,
        "part": line.part,
        "plan": line.plan,
        "quantity": text(line.quantity),
        "unit_price": text(line.unit_price),
        "amount": text(line.amount),
        "fees": text(line.fees) if line.op_type else None,
        "currency": p.currency or line.currency,
        # Actif inconnu repéré par son ISIN : ticker Yahoo de la table locale (D-083).
        "ticker": (
            tickers.known(line.asset, line.currency or "EUR")
            if p.status == "actif_inconnu" and line.asset and line.asset_class != "crypto"
            else None
        ),
    }


def _import_counts(prepared) -> dict[str, int]:
    counts: dict[str, int] = {}
    for p in prepared:
        counts[p.status] = counts.get(p.status, 0) + 1
    return counts


def _preview_data(body: ImportIn, client: httpx2.Client, progress: Progress = _no_progress) -> dict:
    """Aperçu d'un import : profil reconnu ou correspondance des colonnes, parties du relevé et
    leur compte, lignes avec leur statut, plans d'investissement détectés. Rien n'est enregistré.
    Les lignes ignorées sont résumées par raison, sans leur contenu."""
    with db.connect() as conn:
        imp = _read_import(conn, body, client, progress=progress)
        plans = _plan_rows(conn, imp)
    conn.close()
    ignored = Counter(p.message for p in imp.prepared if p.status == "ignoree")
    return {
        "profile": {"id": imp.profile.ID, "label": imp.profile.LABEL} if imp.profile else None,
        "columns": imp.header,
        "mapping": imp.mapping,
        "parts": imp.parts,
        "rows": [
            _import_row_with_outflow(p, body, imp.account_ids)
            for p in imp.prepared
            if p.status != "ignoree"
        ],
        "ignored": [{"reason": r, "count": n} for r, n in ignored.most_common()],
        "counts": _import_counts(imp.prepared),
        "plans": plans,
        "auto_assets": imp.auto_assets,
    }


@app.post("/api/import/preview")
def import_preview(body: ImportIn, client: HttpClient) -> dict:
    """Aperçu d'un import, d'un seul bloc."""
    return _preview_data(body, client)


@app.post("/api/import/preview/stream")
def import_preview_stream(body: ImportIn, client: HttpClient) -> StreamingResponse:
    """Même aperçu, avec sa progression (D-089) : une ligne JSON par événement,
    `{"progress": 0 à 100, "label": étape}` pendant la lecture, puis `{"result": aperçu}` ou
    `{"error": message}`. La lecture tourne dans un fil pour pouvoir rendre la main entre deux
    étapes ; rien ne sort de la machine de plus que dans l'aperçu ordinaire."""
    events: queue.Queue = queue.Queue()

    def work() -> None:
        try:
            result = _preview_data(
                body, client, lambda f, label: events.put({"progress": f, "label": label})
            )
            # Decimal en texte, comme la route ordinaire (D-017) : jamais en nombre flottant.
            encoded = jsonable_encoder(result, custom_encoder={Decimal: str})
            events.put({"result": encoded})
        except HTTPException as err:
            events.put({"error": err.detail})
        except Exception:  # noqa: BLE001 - le message reste générique, rien de privé n'en sort
            events.put({"error": "Aperçu impossible pour l'instant."})
        finally:
            events.put(None)

    # Le fil reçoit une copie du contexte : sans elle, il ne saurait pas quelle base de profil
    # ouvrir (une ContextVar ne passe pas d'elle-même à un nouveau fil, D-098).
    context = contextvars.copy_context()
    threading.Thread(target=context.run, args=(work,), daemon=True).start()

    def lines() -> Iterator[str]:
        while (event := events.get()) is not None:
            yield json.dumps(event) + "\n"

    return StreamingResponse(lines(), media_type="application/x-ndjson")


def _outflow(p, body: ImportIn, accounts: set[int]) -> dict | None:
    """Où part un transfert sortant ou un retrait (D-085, D-087) : parts envoyées à des comptes
    de l'app (`splits`, ou `destinations` pour tout le retrait), le reste « hors de l'app » avec
    son texte libre. Un compte pas encore créé (première utilisation) n'a pas d'identifiant à
    exclure."""
    line = p.csv
    if line.op_type not in TRANSFERS:
        return None
    total = line.quantity if line.quantity is not None else line.amount
    if not total:
        return None
    own = p.account.id if p.account else None
    asked = (body.splits or {}).get(line.line)
    if asked is None:
        to = (body.destinations or {}).get(line.line)
        asked = [SplitIn(account_id=to, value=total)] if to else []
    legs = [
        (s.account_id, s.value) for s in asked if s.account_id in accounts and s.account_id != own
    ]
    rest = total - sum((v for _, v in legs), Decimal(0))
    text = (body.destination_texts or {}).get(line.line)
    return {
        "total": total,
        "legs": legs,
        "rest": rest,
        "text": text if rest > 0 and text else None,
    }


def _outflow_json(flow: dict | None) -> dict | None:
    if flow is None:
        return None
    return {
        "total": format(flow["total"].normalize(), "f"),
        "legs": [{"account_id": a, "value": format(v.normalize(), "f")} for a, v in flow["legs"]],
        "rest": format(max(flow["rest"], Decimal(0)).normalize(), "f"),
        "over": flow["rest"] < 0,
        "text": flow["text"],
    }


def _outflow_parts(flow: dict) -> list[tuple[int | None, str | None, Decimal]]:
    """Opérations à créer pour un retrait : une par compte d'arrivée, puis le reste."""
    parts = [(a, None, v) for a, v in flow["legs"]]
    if flow["rest"] > 0:
        parts.append((None, flow["text"], flow["rest"]))
    return parts


TRANSFERS = ("transfert_sortant", "retrait")


@app.post("/api/import", status_code=201)
def import_operations(body: ImportIn, client: HttpClient) -> dict:
    """Enregistre les lignes « ok » de l'aperçu, toutes ou aucune (une seule transaction), et
    les plans d'investissement cochés."""
    try:
        with db.connect() as conn:
            imp = _read_import(conn, body, client, create=True)
            ready = [p for p in imp.prepared if p.status == "ok"]
            # Ordre chronologique (un export peut lister du plus récent au plus ancien) : les
            # positions se vérifient à la fin dans l'ordre (date, id).
            ready.sort(key=lambda p: (p.csv.op_date, p.csv.seq or 0, p.csv.line))
            for p in ready:
                line = p.csv
                flow = _outflow(p, body, imp.account_ids)
                if flow is not None and flow["rest"] < 0:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Ligne {line.line} : la répartition dépasse le montant sorti.",
                    )
                parts = _outflow_parts(flow) if flow else [(None, None, None)]
                for i, (to, destination, value) in enumerate(parts):
                    # Retrait réparti : une opération par arrivée, les frais sur la première.
                    quantity, amount = line.quantity, line.amount
                    if value is not None and line.quantity is not None:
                        quantity = value
                        if amount:
                            amount = amount * value / flow["total"]
                    elif value is not None:
                        amount = value
                    repository.create_operation(
                        conn,
                        p.account.id,
                        p.asset.id if p.asset else None,
                        line.op_date,
                        line.op_type,
                        quantity,
                        line.unit_price,
                        amount,
                        line.fees if i == 0 else Decimal(0),
                        p.currency,
                        line.note,
                        source=preparation.SOURCE,
                        import_key=p.import_key
                        if i == 0 or not p.import_key
                        else f"{p.import_key}|{i + 1}",
                        check=False,
                        offered=line.offered,
                        reward_kind=line.reward_kind if line.offered else None,
                        tax_withheld=line.tax_withheld,
                        crypto_swap=line.crypto_swap,
                        to_account_id=to,
                        destination=destination,
                    )
            # Une seule vérification à la fin : vente de titres jamais achetés, etc.
            compute_positions(repository.list_operations(conn))
            created = _import_plans(conn, imp, set(body.plans or []))
    except PositionError as err:
        raise HTTPException(
            status_code=422, detail=f"Import annulé, rien n'a été enregistré : {err}"
        ) from err
    return {
        "imported": len(ready),
        "plans": created,
        "counts": _import_counts(imp.prepared),
        "auto_assets": imp.auto_assets,
    }


def _import_plans(conn, imp: _Import, chosen: set[str]) -> int:
    """Crée les plans cochés (dernière échéance traitée = dernière exécution du fichier) et
    avance les achats programmés existants jusqu'à la dernière exécution importée : une
    échéance déjà dans le relevé n'est pas reproposée dans « Gérer »."""
    created = 0
    assets = repository.list_assets(conn)
    for row, plan in zip(_plan_rows(conn, imp), imp.plans, strict=True):
        if row["key"] not in chosen or row["problem"]:
            continue
        account = imp.accounts[plan.part]
        asset = preparation.find_asset(plan.asset, assets)
        r = repository.save_recurring(
            conn,
            None,
            account.id,
            asset.id,
            plan.amount,
            plan.frequency,
            plan.day,
            plan.first,
            f"Plan {imp.profile.LABEL}",
        )
        repository.mark_recurring_due(conn, r["id"], plan.last)
        created += 1
    executed: dict[tuple[int, int], date] = {}
    for p in imp.prepared:
        if p.csv.plan and p.status in ("ok", "deja_importee") and p.asset and p.account:
            key = (p.account.id, p.asset.id)
            executed[key] = max(executed.get(key, p.csv.op_date), p.csv.op_date)
    for r in repository.list_recurring(conn):
        last = executed.get((r["account_id"], r["asset_id"]))
        if last and (r["last_due"] is None or last > r["last_due"]) and last >= r["start_date"]:
            repository.mark_recurring_due(conn, r["id"], last)
    return created


@app.get("/api/quote")
def check_quote(
    client: HttpClient, ticker: str | None = None, coingecko_id: str | None = None, currency="EUR"
) -> dict:
    """Teste un identifiant avant de créer ou modifier un actif : renvoie le cours trouvé.

    Rien n'est enregistré. Seuls l'identifiant et la devise partent sur le réseau.
    """
    try:
        if coingecko_id and coingecko_id.strip():
            coin = coingecko_id.strip().lower()
            # Même alphabet fermé que l'id enregistré sur un actif (`CoingeckoId`, D-138).
            if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,99}", coin):
                raise HTTPException(status_code=422, detail="Id CoinGecko invalide.")
            if not re.fullmatch(r"[A-Za-z]{3}", currency.strip()):
                raise HTTPException(status_code=422, detail="Devise invalide.")
            price = coingecko.get_price(client, coin, currency.strip(), config.coingecko_api_key())
        elif ticker and ticker.strip():
            price = yahoo.get_price(client, ticker.strip().upper())
        else:
            raise HTTPException(status_code=422, detail="Indique un ticker ou un id CoinGecko.")
    except providers.ProviderError as err:
        raise HTTPException(status_code=404, detail=f"Cours introuvable : {err}") from err
    return {"price": _price_text(price.price), "currency": price.currency, "source": price.source}


@app.get("/api/tickers")
def find_ticker(client: HttpClient, isin: str, currency: str = "EUR") -> dict:
    """Ticker Yahoo d'un ISIN (D-083) : table locale, sinon recherche chez Yahoo, demandée par
    un clic. Cotations dans la devise voulue d'abord. Seuls l'ISIN et le nom du titre partent."""
    isin, currency = isin.strip().upper(), currency.strip().upper()
    if not tickers.ISIN.match(isin) or not re.fullmatch(r"[A-Z]{3}", currency):
        raise HTTPException(status_code=422, detail="ISIN ou devise invalide.")
    if ticker := tickers.known(isin, currency):
        return {"source": "table", "quotes": [{"ticker": ticker, "currency": currency}]}
    try:
        quotes = tickers.search(client, isin, currency)
    except providers.ProviderError as err:
        raise HTTPException(status_code=502, detail=f"Recherche Yahoo impossible : {err}") from err
    return {"source": "yahoo", "quotes": quotes}


# --- Synthèse --------------------------------------------------------------


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


@app.get("/api/portfolio")
def portfolio(client: HttpClient, keep: Filter, force: bool = False) -> dict:
    """Positions et total en EUR, au cours en ligne (cache de PRICE_TTL_MINUTES).

    `keep` : lignes laissées de côté par le filtre de Vue d'ensemble (D-069) ; un compte
    entier exclu emporte ses espèces.

    `force=true` (bouton « Actualiser ») ignore le cache. Seuls les tickers, ids CoinGecko et
    codes devises partent sur le réseau, jamais les quantités ni les montants (règle 3).
    """
    now = datetime.now(UTC)  # horodatage du cache des prix, en UTC
    ttl = config.price_ttl()
    with db.connect() as conn:
        _update_defi(conn, client)
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
    conn.close()
    market = {i: (q.price.price, q.price.currency) for i, q in quotes.items() if q.price}
    valued = with_market_prices(positions, market, rates)
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
    with db.connect() as conn:
        debts_eur = _debts_total_eur(conn, rates, date.today())
    conn.close()

    return {
        "debts_eur": _money_or_none(debts_eur),
        "positions": [
            {
                "account": accounts[p.account_id].name,
                "asset": assets[p.asset_id].name,
                "ticker": assets[p.asset_id].ticker,
                "quantity": plain(p.quantity),
                "pru": money(p.pru, "0.0001"),
                "cost": money(p.cost),
                "last_price": _price_text(p.last_price),
                "value": money(p.value),
                "gain": money(p.gain),
                "gain_pct": _pct(ratio(p.gain, p.cost)),
                "gain_eur": _money_or_none(gain_eur(p)),
                "day_change": None if change is None else money(change),
                "day_change_pct": _pct(None if change is None else ratio(change, p.value - change)),
                "currency": p.currency,
                "asset_id": p.asset_id,
                "asset_class": class_of(p),
                "manual": assets[p.asset_id].manual,  # valeur saisie (D-056)
                "color": assets[p.asset_id].color,
                # Poids de la ligne dans le patrimoine total en EUR (espèces comprises).
                "value_eur": _money_or_none(value_eur(p)),
                "weight_pct": _pct(ratio(value_eur(p), grand_total)) if value_eur(p) else None,
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


def _value_series(
    client: httpx2.Client, keep: LineFilter = NO_FILTER, placed: bool = False
) -> dict:
    """Série quotidienne de la valeur des titres (voir /api/history), et ce qui a servi à la
    calculer. Complète au besoin les clôtures chez Yahoo (au plus une fois par actif et par
    jour) : seuls le ticker et deux dates partent sur le réseau (règle 3).

    `keep` : lignes laissées de côté (filtre de Vue d'ensemble, D-069).
    """
    # Jour local, comme la date des opérations saisies (D-039) : en UTC, une opération du
    # jour saisie entre minuit et 2 h serait absente de la courbe jusqu'à 2 h.
    today = date.today()
    with db.connect() as conn:
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
    conn.close()
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


def _series_flows(series: dict) -> tuple[dict[date, Decimal], dict[date, Decimal]]:
    """Apports (achats, ventes, titres déjà détenus à leur valeur de marché) et revenus
    (dividendes moins frais) de chaque jour, en EUR aux taux de leur jour (D-081)."""
    ops, rates, fx = series["operations"], series["rates"], series["fx"]
    return daily_flows(ops, rates, fx, series["closes"]), daily_income(ops, rates, fx)


@app.get("/api/history")
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
    series = _value_series(client, keep, placed=True)
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
                "invested_eroded": _money_or_none(e),
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
    series = _value_series(client, keep)
    out = {"assets": series["traded"], "periods": [], "summary": None}
    if not series["points"]:
        return {**out, "start": None, "end": None}
    start, end = _range(series, start, end)
    points = [(day, value) for day, value, _ in series["points"]]
    flows, income = _series_flows(series)
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
            "performance": _pct(r.performance),
            "cumulative_gain": money(r.cumulative_gain),
            "cumulative_performance": _pct(r.cumulative_factor - 1),
        }
        for r in results
    ]
    out["summary"] = {
        "start_value": money(results[0].start_value),
        "end_value": money(last.end_value),
        "flows": money(sum((r.flows for r in results), ZERO)),
        "income": money(sum((r.income for r in results), ZERO)),
        "gain": money(last.cumulative_gain),
        "performance": _pct(last.cumulative_factor - 1),
        "annualized": _pct(yearly),
        "irr": _pct(xirr(cashflows)) if days >= 30 else None,
        "volatility": _pct(vol),
        "risk_ratio": money(yearly / vol) if yearly is not None and vol else None,
        "max_drawdown": _pct(max_drawdown(daily)),
        "best": {"label": ranked[-1].label, "performance": _pct(ranked[-1].performance)},
        "worst": {"label": ranked[0].label, "performance": _pct(ranked[0].performance)},
        "positive_periods": sum(1 for r in results if r.factor > 1),
        "count": len(results),
    }
    return out


@app.get("/api/performance")
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


@app.get("/api/performance.csv")
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


@app.get("/api/benchmark")
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
    series = _value_series(client, keep)
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
    flows, income = _series_flows(series)
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
            "portfolio_performance": _pct(my_perf),
            "benchmark_performance": _pct(index_perf),
            "difference": _pct(my_perf - index_perf)
            if my_perf is not None and index_perf is not None
            else None,
            "portfolio_value": money(mine[rows[-1]]) if rows else None,
            "benchmark_value": money(simulated[rows[-1]]) if rows else None,
            # Écart en vrai % : valeur réelle comparée à la valeur si tout était dans l'indice.
            "value_difference": _pct(ratio(mine[rows[-1]], simulated[rows[-1]]) - 1)
            if rows and simulated[rows[-1]]
            else None,
        },
    }


# --- Calendrier des rendements mensuels ----------------------------------------------

CALENDAR_YEARS = 10  # profondeur d'un calendrier d'indice ou de livret


@app.get("/api/calendar")
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
    series = _value_series(client, keep)
    if not series["points"]:
        return _calendar_out({}, set(), "EUR")
    points = [(day, value) for day, value, _ in series["points"]]
    flows, income = _series_flows(series)
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
        "months": {k: _pct(v) for k, v in sorted(months.items())},
        "years": {k: _pct(v) for k, v in sorted(years.items())},
        "estimated": sorted(estimated),
        # Années dont le total est estimé : aucun mois réel.
        "estimated_years": sorted({k[:4] for k in estimated} - real_years),
    }


# Liste de comparaison (D-042) puis listes de suivi (D-072), dans app/api/.
app.include_router(benchmarks.router)
app.include_router(watchlists.router)


# Jusqu'à 5 taux annuels en %, séparés par « ; » (ex. « 2;5;8 » ou « 3,5;7 »).
RATE = r"-?\d{1,2}([.,]\d{1,2})?"
RATES_PATTERN = rf"^{RATE}(;{RATE}){{0,4}}$"


@app.get("/api/projection")
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
    series = _value_series(client, keep)
    with db.connect() as conn:
        accounts = repository.list_accounts(conn)
    conn.close()
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
                "rate": _pct(rate),
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


# Monté en dernier : les routes /api/... déclarées au-dessus restent prioritaires.
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
