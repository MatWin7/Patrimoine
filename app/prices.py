"""Cache des prix et des taux de change dans SQLite, rafraîchis depuis les sources publiques.

Un prix récupéré il y a moins de PRICE_TTL_MINUTES est réutilisé tel quel (D-007). Si la
source ne répond pas, on garde le dernier prix connu et on remonte l'erreur pour l'afficher.
"""

import sqlite3
import time
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import httpx2

from app import crypto_tickers
from app.db import conn_key, from_db, to_db
from app.models import Asset
from app.providers import (
    Price,
    ProviderError,
    Unreachable,
    coingecko,
    eurostat,
    frankfurter,
    yahoo,
)

ONE = Decimal("1")
TIMESTAMP = "%Y-%m-%dT%H:%M:%SZ"

# Clôture de la veille par actif, gardée en mémoire seulement (pas de colonne en base) : elle
# est perdue au redémarrage et revient au premier cours récupéré en ligne.
_previous_close: dict[tuple[str, int], Decimal] = {}  # (base du profil, actif), D-098


@dataclass(frozen=True)
class Quote:
    """Dernier prix connu d'un actif, et l'erreur éventuelle du dernier essai en ligne."""

    price: Price | None
    fetched: bool = False  # True si le prix vient d'être récupéré pendant cet appel
    error: str | None = None


def fetch_price(client: httpx2.Client, asset: Asset, coingecko_key: str | None) -> Price:
    """Choisit la source selon l'actif. Seul l'identifiant public part sur le réseau."""
    if asset.coingecko_id:
        try:
            return coingecko.get_price(client, asset.coingecko_id, asset.currency, coingecko_key)
        except ProviderError as err:
            # CoinGecko refuse (429) ou en panne : cours Yahoo de la crypto s'il est coté dans
            # la devise de l'actif (D-111). Sinon l'erreur d'origine, le dernier prix sert.
            ticker = crypto_tickers.yahoo_ticker(asset.ticker, asset.coingecko_id)
            if not ticker:
                raise
            try:
                price = yahoo.get_price(client, ticker)
            except ProviderError:
                raise err from None
            if price.currency != asset.currency:
                raise err from None
            return price
    if asset.ticker:
        return yahoo.get_price(client, asset.ticker)
    raise ProviderError("ni ticker ni id CoinGecko")


# --- Prix ------------------------------------------------------------------


def latest_price(conn: sqlite3.Connection, asset_id: int) -> Price | None:
    row = conn.execute(
        "SELECT * FROM price WHERE asset_id = ? ORDER BY fetched_at DESC LIMIT 1", (asset_id,)
    ).fetchone()
    if row is None:
        return None
    fetched_at = datetime.strptime(row["fetched_at"], TIMESTAMP).replace(tzinfo=UTC)
    return Price(from_db(row["price"]), row["currency"], row["source"], fetched_at)


def save_price(conn: sqlite3.Connection, asset_id: int, price: Price) -> None:
    """Range le dernier cours d'un actif et efface les cours en ligne plus anciens (D-138).

    Seul le dernier sert (`latest_price`) ; l'historique jour par jour est dans `price_history`.
    Sans ce ménage, une ligne s'ajoutait par actif à chaque rafraîchissement (toutes les
    5 minutes, page ouverte) : la base et chacune de ses sauvegardes grossissaient sans fin.
    Une valeur saisie à la main (source « manuel », D-056) n'est jamais effacée ici."""
    stamp = price.fetched_at.strftime(TIMESTAMP)
    conn.execute(
        "DELETE FROM price WHERE asset_id = ? AND source != 'manuel' AND fetched_at < ?",
        (asset_id, stamp),
    )
    conn.execute(
        "INSERT OR REPLACE INTO price (asset_id, fetched_at, price, currency, source)"
        " VALUES (?, ?, ?, ?, ?)",
        (asset_id, stamp, to_db(price.price), price.currency, price.source),
    )


def get_quotes(
    conn: sqlite3.Connection,
    assets: list[Asset],
    client: httpx2.Client,
    *,
    ttl: timedelta,
    now: datetime,
    force: bool = False,
    coingecko_key: str | None = None,
) -> dict[int, Quote]:
    """Prix de chaque actif : le cache s'il a moins de `ttl`, sinon un appel à la source."""
    quotes = {}
    scope = conn_key(conn)
    down = None  # Yahoo n'a pas répondu : les titres suivants gardent leur dernier cours (D-138)
    for asset in assets:
        cached = latest_price(conn, asset.id)
        if cached:
            cached = replace(cached, previous_close=_previous_close.get((scope, asset.id)))
        if asset.manual:  # valeur saisie (D-056) : rien à demander en ligne
            quotes[asset.id] = Quote(cached)
            continue
        if cached and not force and now - cached.fetched_at < ttl:
            quotes[asset.id] = Quote(cached)
            continue
        if down and not asset.coingecko_id:
            quotes[asset.id] = Quote(cached, error=down)
            continue
        try:
            price = fetch_price(client, asset, coingecko_key)
        except ProviderError as err:
            quotes[asset.id] = Quote(cached, error=str(err))
            if isinstance(err, Unreachable) and not asset.coingecko_id:
                down = str(err)
            continue
        save_price(conn, asset.id, price)
        if price.previous_close is not None:
            _previous_close[(scope, asset.id)] = price.previous_close
        quotes[asset.id] = Quote(price, fetched=True)
    return quotes


# --- Taux de change --------------------------------------------------------


def latest_rates(conn: sqlite3.Connection, currencies: set[str]) -> dict[str, tuple[date, Decimal]]:
    rates = {}
    for cur in currencies:
        row = conn.execute(
            "SELECT rate_date, rate FROM fx_rate WHERE currency = ?"
            " ORDER BY rate_date DESC LIMIT 1",
            (cur,),
        ).fetchone()
        if row:
            rates[cur] = (date.fromisoformat(row["rate_date"]), from_db(row["rate"]))
    return rates


def get_rates(
    conn: sqlite3.Connection,
    currencies: set[str],
    client: httpx2.Client,
    *,
    today: date,
    may_fetch: bool,
) -> tuple[dict[str, Decimal], str | None]:
    """Taux « 1 EUR = rate devise » pour chaque devise (EUR = 1), et l'erreur éventuelle.

    On n'interroge Frankfurter que si un taux manque ou n'est pas celui du jour, et seulement
    quand `may_fetch` (les prix viennent d'être rafraîchis) : au plus un appel par TTL.
    """
    foreign = {c for c in currencies if c != "EUR"}
    stored = latest_rates(conn, foreign)
    error = None
    missing = {c for c in foreign if c not in stored}
    outdated = {c for c, (d, _) in stored.items() if d < today}
    if missing or (outdated and may_fetch):
        try:
            rate_date, fresh = frankfurter.get_rates(client, foreign)
        except ProviderError as err:
            error = f"taux de change : {err}"
        else:
            for cur, rate in fresh.items():
                conn.execute(
                    "INSERT OR REPLACE INTO fx_rate (rate_date, currency, rate) VALUES (?, ?, ?)",
                    (rate_date.isoformat(), cur, to_db(rate)),
                )
            stored = latest_rates(conn, foreign)
    rates = {cur: rate for cur, (_, rate) in stored.items()}
    rates["EUR"] = ONE
    return rates, error


# Devises dont l'historique des taux a été complété aujourd'hui (mémoire du processus).
_fx_checked: dict[tuple[str, str], date] = {}  # (base du profil, devise), D-098


def update_fx_history(
    conn: sqlite3.Connection, starts: dict[str, date], client: httpx2.Client, *, today: date
) -> str | None:
    """Complète l'historique des taux de chaque devise depuis `starts[devise]` (première
    opération), au plus une fois par jour (D-081). Début demandé : le 1er janvier de l'année,
    comme pour Yahoo (la date du premier achat est une information personnelle). Renvoie
    l'erreur éventuelle ; les taux déjà stockés restent utilisables."""
    by_begin: dict[date, set[str]] = {}
    scope = conn_key(conn)
    for cur, start in starts.items():
        if cur == "EUR" or _fx_checked.get((scope, cur)) == today:
            continue
        first, last = (
            date.fromisoformat(d) if d else None
            for d in conn.execute(
                "SELECT min(rate_date), max(rate_date) FROM fx_rate WHERE currency = ?", (cur,)
            ).fetchone()
        )
        if first is not None and first <= start and last is not None and last >= today:
            _fx_checked[(scope, cur)] = today
            continue
        begin = history_begin(start) if first is None or first > start else last - OVERLAP
        by_begin.setdefault(begin, set()).add(cur)
    for begin, currencies in sorted(by_begin.items()):
        try:
            series = frankfurter.get_history(client, currencies, begin, today)
        except ProviderError as err:
            return f"historique des taux de change : {err}"
        conn.executemany(
            "INSERT OR REPLACE INTO fx_rate (rate_date, currency, rate) VALUES (?, ?, ?)",
            [(d.isoformat(), cur, to_db(r)) for cur, s in series.items() for d, r in s],
        )
        for cur in currencies:
            _fx_checked[(scope, cur)] = today
    return None


def rate_series(
    conn: sqlite3.Connection, currencies: set[str]
) -> dict[str, list[tuple[date, Decimal]]]:
    """Taux stockés de chaque devise, du plus ancien au plus récent, sans appel réseau."""
    return {
        cur: [
            (date.fromisoformat(r["rate_date"]), from_db(r["rate"]))
            for r in conn.execute(
                "SELECT rate_date, rate FROM fx_rate WHERE currency = ? ORDER BY rate_date",
                (cur,),
            )
        ]
        for cur in currencies - {"EUR"}
    }


def stored_rates(conn: sqlite3.Connection, currencies: set[str]) -> dict[str, Decimal]:
    """Derniers taux enregistrés, sans appel réseau (EUR = 1)."""
    rates = {cur: rate for cur, (_, rate) in latest_rates(conn, currencies - {"EUR"}).items()}
    rates["EUR"] = ONE
    return rates


# --- Historique des clôtures (courbe d'évolution) ------------------------------

# Actifs dont l'historique a déjà été complété aujourd'hui (mémoire du processus) : au plus
# un appel par actif et par jour, même si la page se recharge toutes les 5 minutes.
_history_checked: dict[tuple[str, int], date] = {}  # (base du profil, actif), D-098
OVERLAP = timedelta(days=5)  # jours redemandés pour corriger la « clôture » provisoire du jour
MARGIN = timedelta(days=7)  # avant le premier achat : une clôture existe même après un week-end


def history_begin(start: date) -> date:
    """Début demandé à Yahoo : le 1er janvier de l'année, marge comprise, et non la date du
    premier achat, qui est une information personnelle (audit, point 8). Quelques clôtures de
    plus, rien de moins."""
    return date((start - MARGIN).year, 1, 1)


def load_history(conn: sqlite3.Connection, asset_id: int) -> list[tuple[date, Decimal, str]]:
    rows = conn.execute(
        "SELECT price_date, close, currency FROM price_history WHERE asset_id = ?"
        " ORDER BY price_date",
        (asset_id,),
    ).fetchall()
    return [(date.fromisoformat(r["price_date"]), from_db(r["close"]), r["currency"]) for r in rows]


def close_on(conn: sqlite3.Connection, asset_id: int, day: date) -> tuple[Decimal, str] | None:
    """Dernière clôture en cache à `day` ou avant (cours et devise), sans appel réseau."""
    row = conn.execute(
        "SELECT close, currency FROM price_history WHERE asset_id = ? AND price_date <= ?"
        " ORDER BY price_date DESC LIMIT 1",
        (asset_id, day.isoformat()),
    ).fetchone()
    return (from_db(row["close"]), row["currency"]) if row else None


def history_ticker(asset: Asset) -> str | None:
    """Ticker Yahoo dont viennent les clôtures d'un actif : le sien ; pour une crypto, celui
    saisi dans « Gérer », sinon celui de la table des cryptos courantes (D-111). None : pas
    d'historique (valeur saisie, crypto inconnue de Yahoo)."""
    return crypto_tickers.yahoo_ticker(asset.ticker, asset.coingecko_id)


def _stored_in_hundredths(conn: sqlite3.Connection, asset_id: int, day: date, close: Decimal):
    """La clôture en base de ce jour vaut-elle cent fois celle que Yahoo donne maintenant ?
    C'est la trace d'un historique lu en pence avant D-138 (place de Londres) : il est alors
    redemandé en entier."""
    row = conn.execute(
        "SELECT close FROM price_history WHERE asset_id = ? AND price_date = ?"
        " AND source = 'yahoo'",
        (asset_id, day.isoformat()),
    ).fetchone()
    return row is not None and from_db(row["close"]) == close * 100


def update_history(
    conn: sqlite3.Connection,
    assets: list[tuple[Asset, date]],
    client: httpx2.Client,
    *,
    today: date,
) -> dict[int, str]:
    """Complète les clôtures de chaque (actif, date du premier achat) ; renvoie les erreurs.

    Seuls le ticker et deux dates partent sur le réseau (règle 3). On ne redemande que les
    derniers jours, sauf si une opération plus ancienne que l'historique stocké est apparue.
    Cryptos comprises depuis D-138, par leur ticker Yahoo (`history_ticker`) : sans clôtures,
    leur courbe restait au prix des opérations et tout le gain tombait sur le dernier jour.
    Si Yahoo ne répond pas du tout, les actifs suivants ne sont pas demandés : chacun attendrait
    le même délai de 10 s avant d'échouer (D-138).
    """
    errors = {}
    scope = conn_key(conn)
    down = None  # Yahoo n'a pas répondu : on ne lui redemande pas les actifs suivants
    for asset, start in assets:
        ticker = history_ticker(asset)
        key = (scope, asset.id)
        if not ticker or _history_checked.get(key) == today:
            continue
        if down:
            errors[asset.id] = down
            continue
        span = conn.execute(
            "SELECT min(price_date), max(price_date) FROM price_history"
            " WHERE asset_id = ? AND source = 'yahoo'",  # jamais la date d'une valeur saisie
            (asset.id,),
        ).fetchone()
        first, last = (date.fromisoformat(d) if d else None for d in span)
        whole = first is None or first > start
        begin = history_begin(start) if whole else last - OVERLAP
        try:
            currency, closes = yahoo.get_history(client, ticker, begin, today)
            if closes and not whole and _stored_in_hundredths(conn, asset.id, *closes[0]):
                currency, closes = yahoo.get_history(client, ticker, history_begin(start), today)
        except ProviderError as err:
            errors[asset.id] = str(err)
            if isinstance(err, Unreachable):
                down = str(err)
            continue
        conn.executemany(
            "INSERT OR REPLACE INTO price_history (asset_id, price_date, close, currency, source)"
            " VALUES (?, ?, ?, ?, 'yahoo')",
            [(asset.id, day.isoformat(), to_db(close), currency) for day, close in closes],
        )
        _history_checked[key] = today
    return errors


# --- Indices de comparaison -------------------------------------------------------

# Clôtures des indices, gardées en mémoire seulement (données publiques, faciles à
# redemander) : ticker -> (jour de récupération, devise, clôtures). Un appel par jour.
_benchmarks: dict[str, tuple[date, str, list[tuple[date, Decimal]]]] = {}


def benchmark_history(
    client: httpx2.Client, ticker: str, start: date, today: date
) -> tuple[str, list[tuple[date, Decimal]]]:
    """Clôtures quotidiennes d'un indice depuis `start` ; seul le ticker part (règle 3)."""
    cached = _benchmarks.get(ticker)
    if cached and cached[0] == today and (not cached[2] or cached[2][0][0] <= start + MARGIN):
        return cached[1], cached[2]
    currency, closes = yahoo.get_history(client, ticker, history_begin(start), today)
    _benchmarks[ticker] = (today, currency, closes)
    return currency, closes


# --- Cours passés des cryptos (import Binance, D-082) ------------------------------

# Gardés en mémoire seulement, un appel par crypto et par jour : id -> (jour, cours EUR).
_coin_history: dict[str, tuple[date, list[tuple[date, Decimal]]]] = {}
COIN_RETRIES = (2, 5)  # secondes d'attente avant de redemander après un refus (429)
# Crypto restée sans réponse : pas de nouvel essai avant 10 min (sinon chaque aperçu d'import
# rattendrait les mêmes refus) ; l'import se rabat entre-temps sur les prix du fichier.
_coin_failed: dict[str, datetime] = {}
COIN_RETRY_AFTER = timedelta(minutes=10)


def coin_history(
    client: httpx2.Client, coin_id: str, today: date, api_key: str | None = None, sleep=None
) -> list[tuple[date, Decimal]]:
    """Cours quotidiens EUR des 365 derniers jours d'une crypto ; liste vide si CoinGecko ne
    répond pas (l'import se rabat alors sur les prix du fichier). L'API gratuite limite le
    nombre d'appels par minute : on attend un peu et on redemande."""
    cached = _coin_history.get(coin_id)
    if cached and cached[0] == today:
        return cached[1]
    now = datetime.now(UTC)
    if now - _coin_failed.get(coin_id, datetime.min.replace(tzinfo=UTC)) < COIN_RETRY_AFTER:
        return []
    sleep = sleep or time.sleep
    for wait in (*COIN_RETRIES, None):
        try:
            series = coingecko.get_history(client, coin_id, "EUR", api_key)
        except ProviderError as err:
            if wait is None or "429" not in str(err):
                break
            sleep(wait)
            continue
        _coin_history[coin_id] = (today, series)
        _coin_failed.pop(coin_id, None)
        return series
    _coin_failed[coin_id] = now
    return []


# --- Inflation (indice des prix Eurostat) ---------------------------------------

# Indice mensuel gardé en mémoire seulement (série publique, D-045) : un appel par jour au
# plus ; après une panne, nouvel essai au bout d'une heure, en gardant l'ancienne série.
_inflation: dict[str, object] = {}
RETRY_AFTER = timedelta(hours=1)


def inflation_index(
    client: httpx2.Client, now: datetime | None = None
) -> tuple[dict[date, Decimal], str | None]:
    """IPCH France {1er du mois: indice} et l'erreur éventuelle du dernier essai."""
    now = now or datetime.now(UTC)
    values = _inflation.get("values", {})
    fetched = _inflation.get("fetched")
    failed = _inflation.get("failed")
    if fetched and fetched.date() == now.date():
        return values, None
    if failed and now - failed < RETRY_AFTER:
        return values, _inflation.get("error")
    try:
        values = eurostat.get_hicp(client)
    except ProviderError as err:
        _inflation.update(failed=now, error=str(err))
        return values, str(err)
    _inflation.clear()
    _inflation.update(values=values, fetched=now)
    return values, None
