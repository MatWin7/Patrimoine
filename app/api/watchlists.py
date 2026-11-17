"""Routes des listes de suivi (D-072). Seuls des noms et des tickers publics : ni montant ni
quantité (règle 3). Les clôtures viennent de Yahoo, gardées en mémoire et redemandées au plus
une fois par jour et par ticker."""

import sqlite3
from datetime import date

import httpx2
from fastapi import APIRouter, HTTPException

from app import config, crypto_tickers, db, prices, providers, repository
from app import watchlist as watch
from app.api.common import pct
from app.api.deps import HttpClient
from app.portfolio import compute_positions
from app.providers import coingecko
from app.schemas import OrderIn, WatchlistIn, WatchlistItemIn, money

router = APIRouter()


WATCH_MAX_ITEMS = 60


def _watchlist_or_404(conn: sqlite3.Connection, watchlist_id: int) -> dict:
    found = repository.get_watchlist(conn, watchlist_id)
    if found is None:
        raise HTTPException(status_code=404, detail="Liste inconnue.")
    return found


@router.get("/api/watchlists")
def list_watchlists() -> list[dict]:
    with db.connect() as conn:
        return repository.list_watchlists(conn)


@router.post("/api/watchlists", status_code=201)
def create_watchlist(body: WatchlistIn) -> dict:
    try:
        with db.connect() as conn:
            return repository.create_watchlist(conn, body.name)
    except sqlite3.IntegrityError as err:
        raise HTTPException(status_code=409, detail="Une liste porte déjà ce nom.") from err


@router.patch("/api/watchlists/{watchlist_id}")
def rename_watchlist(watchlist_id: int, body: WatchlistIn) -> dict:
    try:
        with db.connect() as conn:
            _watchlist_or_404(conn, watchlist_id)
            return repository.rename_watchlist(conn, watchlist_id, body.name)
    except sqlite3.IntegrityError as err:
        raise HTTPException(status_code=409, detail="Une liste porte déjà ce nom.") from err


@router.delete("/api/watchlists/{watchlist_id}", status_code=204)
def delete_watchlist(watchlist_id: int) -> None:
    with db.connect() as conn:
        if not repository.delete_watchlist(conn, watchlist_id):
            raise HTTPException(status_code=404, detail="Liste inconnue.")


@router.post("/api/watchlists/{watchlist_id}/items", status_code=201)
def add_watchlist_item(watchlist_id: int, body: WatchlistItemIn) -> dict:
    try:
        with db.connect() as conn:
            if len(_watchlist_or_404(conn, watchlist_id)["items"]) >= WATCH_MAX_ITEMS:
                raise HTTPException(
                    status_code=409, detail=f"{WATCH_MAX_ITEMS} actifs au plus par liste."
                )
            ticker = body.ticker
            if body.crypto:
                ticker = crypto_tickers.resolve_crypto(ticker)
                if not ticker:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Crypto « {body.ticker.lower()} » inconnue : saisis son ticker"
                        " Yahoo (ex. BTC-EUR, trouvé sur finance.yahoo.com).",
                    )
            return repository.add_watchlist_item(conn, watchlist_id, body.label, ticker)
    except sqlite3.IntegrityError as err:
        raise HTTPException(status_code=409, detail="Ce ticker est déjà dans la liste.") from err


@router.put("/api/watchlists/{watchlist_id}/items/order", status_code=204)
def reorder_watchlist_items(watchlist_id: int, body: OrderIn) -> None:
    """Range les actifs d'une liste de suivi dans l'ordre donné (glisser-déposer)."""
    with db.connect() as conn:
        _watchlist_or_404(conn, watchlist_id)
        if not repository.reorder_watchlist_items(conn, watchlist_id, body.ids):
            raise HTTPException(
                status_code=409, detail="La liste a changé entre-temps : recharge la page."
            )


@router.delete("/api/watchlists/{watchlist_id}/items/{item_id}", status_code=204)
def delete_watchlist_item(watchlist_id: int, item_id: int) -> None:
    with db.connect() as conn:
        if not repository.delete_watchlist_item(conn, watchlist_id, item_id):
            raise HTTPException(status_code=404, detail="Actif inconnu dans cette liste.")


def _coin_today_row(client: httpx2.Client, row: dict, coin: str, key: str | None) -> dict:
    """Ligne d'une crypto dont l'historique manque : cours du jour et variation sur 24 h."""
    try:
        price = coingecko.get_price(client, coin, "EUR", key)
    except providers.ProviderError as err:
        return row | {"error": f"CoinGecko : {err}"}
    if price.previous_close:
        row["1j"] = pct(price.price / price.previous_close - 1)
    return row | {
        "currency": "EUR",
        "last": money(price.price, "0.01" if price.price >= 10 else "0.0001"),
        "last_date": date.today().isoformat(),
        "note": "Historique indisponible pour l'instant (CoinGecko limite les appels sans clé) : "
        "cours du jour et variation sur 24 h seulement.",
        "error": None,
    }


def _watch_row(
    client: httpx2.Client,
    label: str,
    ticker: str | None,
    today: date,
    coin: str | None = None,
) -> dict:
    """Une ligne du tableau : dernier cours et rendement de chaque période (D-072).

    Ticker Yahoo d'abord, cryptos comprises (BTC-EUR, plusieurs années, D-111). Une crypto
    sans ticker Yahoo, ou que Yahoo ne renvoie pas, passe par son id CoinGecko (`coin`) :
    365 jours en euros, donc 3 et 5 ans vides. Seul le ticker ou l'id part (règle 3)."""
    row = {"label": label, "ticker": ticker, "currency": None, "last": None, "last_date": None}
    row |= dict.fromkeys(watch.PERIODS)
    if not ticker and not coin:
        return row | {"error": "pas de ticker Yahoo, donc pas d'historique"}
    currency, closes, error = "EUR", [], "aucune clôture chez Yahoo"
    if ticker:
        try:
            start = watch.years_before(today, watch.HISTORY_YEARS)
            currency, closes = prices.benchmark_history(client, ticker, start, today)
        except providers.ProviderError as err:
            error = str(err)
    if not closes and coin:
        row["ticker"] = None  # pas de bouton Calendrier sans clôtures Yahoo
        key = config.coingecko_api_key()
        currency, closes = "EUR", prices.coin_history(client, coin, today, key)
        if not closes:
            # L'historique est limité en débit sans clé (HTTP 429) : on se rabat sur le
            # cours du jour et sa variation sur 24 h, plutôt que « indisponible ».
            return _coin_today_row(client, row, coin, key)
    if not closes:
        return row | {"error": error}
    return _with_returns(row, currency, closes, today)


def _with_returns(row: dict, currency: str, closes, today: date) -> dict:
    returns = watch.period_returns(closes, today)
    row |= {k: pct(v) for k, v in returns.items()}
    last_day, last = closes[-1]
    return row | {
        "currency": currency,
        # Clôtures Yahoo en flottants : 173.58000183 -> 173.58 (4 décimales sous 10).
        "last": money(last, "0.01" if last >= 10 else "0.0001"),
        "last_date": last_day.isoformat(),
        "error": None,
    }


@router.get("/api/watchlists/lines/performance")
def lines_performance(client: HttpClient) -> dict:
    """« Mes lignes » : les actifs détenus aujourd'hui, avec un ticker Yahoo. Les valeurs
    saisies (sans cours public) sont laissées de côté ; seul le ticker part (règle 3)."""
    today = date.today()
    with db.connect() as conn:
        assets = {a.id: a for a in repository.list_assets(conn)}
        held = {p.asset_id for p in compute_positions(repository.list_operations(conn))}
        # Actifs des démos : leurs clôtures générées, rien en ligne (D-140).
        local = {a.id: prices.load_history(conn, a.id) for a in assets.values() if a.fictive}
    conn.close()
    rows = []
    for asset in assets.values():  # dans l'ordre choisi dans « Gérer » (D-070)
        if asset.id not in held or asset.manual:
            continue
        if asset.id in local:
            closes = [(day, close) for day, close, _ in local[asset.id]]
            row = {"label": asset.name, "ticker": None, "currency": None, "last": None,
                   "last_date": None} | dict.fromkeys(watch.PERIODS)  # fmt: skip
            row = _with_returns(row, asset.currency, closes, today) if closes else row
            rows.append({"id": asset.id} | row)
            continue
        # Crypto : ticker Yahoo saisi ou connu (plusieurs années, D-111), sinon CoinGecko.
        ticker = crypto_tickers.yahoo_ticker(asset.ticker, asset.coingecko_id)
        row = _watch_row(client, asset.name, ticker, today, asset.coingecko_id)
        rows.append({"id": asset.id} | row)
    return {"rows": rows, "periods": list(watch.PERIODS)}


@router.get("/api/watchlists/{watchlist_id}/performance")
def watchlist_performance(watchlist_id: int, client: HttpClient) -> dict:
    today = date.today()
    with db.connect() as conn:
        found = _watchlist_or_404(conn, watchlist_id)
    conn.close()
    rows = [
        {"id": item["id"]} | _watch_row(client, item["label"], item["ticker"], today)
        for item in found["items"]
    ]
    return {"rows": rows, "periods": list(watch.PERIODS)}
