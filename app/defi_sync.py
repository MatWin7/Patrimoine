"""Dépôts Aave suivis (D-095) : lignes reliées à un marché, taux publics en cache, intérêts
estimés enregistrés chaque mois clos en achats offerts (type « earn », source 'aave').

Réseau (`refresh_rates`) : au plus un appel par marché et par jour, qui ne contient que la
chaîne, le marché et le jeton (règle 3 élargie). Tout le reste (`sync`) est hors ligne.

Intérêts enregistrés : une opération par ligne et par mois clos, datée du dernier jour du
mois, empreinte `aave:<ligne>:<AAAA-MM>`. Elle est recalculée à chaque passage (un dépôt ajouté
après coup, un solde vérifié) ; on ne la modifie pas à la main : on passe par « Vérifier ».
"""

import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import httpx2

from app import defi, prices, repository
from app.db import conn_key, from_db, to_db
from app.portfolio import OUTGOING, TRADES, PositionError, compute_positions, with_transfers
from app.providers import ProviderError, aave

SOURCE = "aave"
ZERO = Decimal("0")
RETRY_AFTER = timedelta(hours=1)

# Mémoire du processus : dernier essai réseau par marché (au plus un appel par jour, nouvel
# essai une heure après une panne), et listes de chaînes et de marchés du jour.
_rates_checked: dict[tuple, date] = {}
_rates_failed: dict[tuple, datetime] = {}
_catalog: dict[object, tuple[date, object]] = {}


@dataclass(frozen=True)
class Line:
    id: int
    account_id: int
    asset_id: int
    chain_id: int
    chain_name: str
    market: str
    market_name: str
    token: str
    symbol: str

    @property
    def key(self) -> tuple[int, str, str]:
        return (self.chain_id, self.market.lower(), self.token.lower())


def reset_memory() -> None:
    _rates_checked.clear()
    _rates_failed.clear()
    _catalog.clear()


# --- Lignes suivies, taux et soldes vérifiés ------------------------------------------


def list_lines(conn: sqlite3.Connection) -> list[Line]:
    rows = conn.execute(
        "SELECT id, account_id, asset_id, chain_id, chain_name, market, market_name, token,"
        " symbol FROM defi_line ORDER BY id"
    ).fetchall()
    return [Line(*tuple(r)) for r in rows]


def get_line(conn: sqlite3.Connection, line_id: int) -> Line | None:
    return next((x for x in list_lines(conn) if x.id == line_id), None)


def create_line(conn: sqlite3.Connection, account_id: int, asset_id: int, reserve: dict) -> Line:
    cur = conn.execute(
        "INSERT INTO defi_line (account_id, asset_id, chain_id, chain_name, market, market_name,"
        " token, symbol) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            account_id,
            asset_id,
            int(reserve["chain_id"]),
            reserve["chain_name"],
            aave.check_address(reserve["market"]),
            reserve["market_name"],
            aave.check_address(reserve["token"]),
            reserve["symbol"],
        ),
    )
    return get_line(conn, cur.lastrowid)


def delete_line(conn: sqlite3.Connection, line_id: int) -> bool:
    """Retire le suivi et les intérêts estimés de la ligne (soldes vérifiés en cascade)."""
    conn.execute(
        "DELETE FROM operation WHERE source = ? AND import_key LIKE ?",
        (SOURCE, f"aave:{line_id}:%"),
    )
    return conn.execute("DELETE FROM defi_line WHERE id = ?", (line_id,)).rowcount > 0


def load_rates(conn: sqlite3.Connection, line, side: str = "supply") -> dict[date, Decimal]:
    rows = conn.execute(
        "SELECT rate_date, apy FROM defi_rate WHERE chain_id = ? AND market = ? AND token = ?"
        " AND side = ? ORDER BY rate_date",
        (*line.key, side),
    ).fetchall()
    return {date.fromisoformat(r["rate_date"]): from_db(r["apy"]) for r in rows}


def save_rates(
    conn: sqlite3.Connection, line, rates: dict[date, Decimal], side: str = "supply"
) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO defi_rate (chain_id, market, token, side, rate_date, apy)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        [(*line.key, side, d.isoformat(), to_db(v)) for d, v in rates.items()],
    )


def load_checks(conn: sqlite3.Connection, line_id: int) -> dict[date, Decimal]:
    rows = conn.execute(
        "SELECT check_date, quantity FROM defi_check WHERE line_id = ? ORDER BY check_date",
        (line_id,),
    ).fetchall()
    return {date.fromisoformat(r["check_date"]): from_db(r["quantity"]) for r in rows}


def save_check(conn: sqlite3.Connection, line_id: int, day: date, quantity: Decimal) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO defi_check (line_id, check_date, quantity) VALUES (?, ?, ?)",
        (line_id, day.isoformat(), to_db(quantity)),
    )


def delete_check(conn: sqlite3.Connection, line_id: int, day: date) -> bool:
    return (
        conn.execute(
            "DELETE FROM defi_check WHERE line_id = ? AND check_date = ?",
            (line_id, day.isoformat()),
        ).rowcount
        > 0
    )


# --- Emprunts (D-108) : dette en jetons, au taux d'emprunt public -------------------------


@dataclass(frozen=True)
class Borrow:
    id: int
    asset_id: int
    chain_id: int
    chain_name: str
    market: str
    market_name: str
    token: str
    symbol: str

    @property
    def key(self) -> tuple[int, str, str]:
        return (self.chain_id, self.market.lower(), self.token.lower())


def list_borrows(conn: sqlite3.Connection) -> list[Borrow]:
    rows = conn.execute(
        "SELECT id, asset_id, chain_id, chain_name, market, market_name, token, symbol"
        " FROM defi_borrow ORDER BY id"
    ).fetchall()
    return [Borrow(*tuple(r)) for r in rows]


def get_borrow(conn: sqlite3.Connection, borrow_id: int) -> Borrow | None:
    return next((b for b in list_borrows(conn) if b.id == borrow_id), None)


def create_borrow(conn: sqlite3.Connection, asset_id: int, reserve: dict) -> Borrow:
    cur = conn.execute(
        "INSERT INTO defi_borrow (asset_id, chain_id, chain_name, market, market_name, token,"
        " symbol) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            asset_id,
            int(reserve["chain_id"]),
            reserve["chain_name"],
            aave.check_address(reserve["market"]),
            reserve["market_name"],
            aave.check_address(reserve["token"]),
            reserve["symbol"],
        ),
    )
    return get_borrow(conn, cur.lastrowid)


def delete_borrow(conn: sqlite3.Connection, borrow_id: int) -> bool:
    """Retire l'emprunt, ses mouvements et ses dettes vérifiées (en cascade)."""
    return conn.execute("DELETE FROM defi_borrow WHERE id = ?", (borrow_id,)).rowcount > 0


def add_borrow_flow(conn: sqlite3.Connection, borrow_id: int, day: date, quantity: Decimal) -> None:
    """Emprunt (quantité positive) ou remboursement (négative) d'un jour."""
    conn.execute(
        "INSERT INTO defi_borrow_flow (borrow_id, flow_date, quantity) VALUES (?, ?, ?)",
        (borrow_id, day.isoformat(), to_db(quantity)),
    )


def borrow_flows(conn: sqlite3.Connection, borrow_id: int) -> dict[date, Decimal]:
    out: dict[date, Decimal] = {}
    for r in conn.execute(
        "SELECT flow_date, quantity FROM defi_borrow_flow WHERE borrow_id = ?", (borrow_id,)
    ):
        day = date.fromisoformat(r["flow_date"])
        out[day] = out.get(day, ZERO) + from_db(r["quantity"])
    return out


def borrow_checks(conn: sqlite3.Connection, borrow_id: int) -> dict[date, Decimal]:
    rows = conn.execute(
        "SELECT check_date, quantity FROM defi_borrow_check WHERE borrow_id = ?"
        " ORDER BY check_date",
        (borrow_id,),
    ).fetchall()
    return {date.fromisoformat(r["check_date"]): from_db(r["quantity"]) for r in rows}


def save_borrow_check(conn: sqlite3.Connection, borrow_id: int, day: date, qty: Decimal) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO defi_borrow_check (borrow_id, check_date, quantity)"
        " VALUES (?, ?, ?)",
        (borrow_id, day.isoformat(), to_db(qty)),
    )


def delete_borrow_check(conn: sqlite3.Connection, borrow_id: int, day: date) -> bool:
    return (
        conn.execute(
            "DELETE FROM defi_borrow_check WHERE borrow_id = ? AND check_date = ?",
            (borrow_id, day.isoformat()),
        ).rowcount
        > 0
    )


def estimate_borrow(conn: sqlite3.Connection, borrow: Borrow, today: date):
    """Dette estimée : mêmes calculs que les dépôts (D-095), au taux d'emprunt du jour. Les
    intérêts s'ajoutent à la dette au lieu du solde."""
    return defi.estimate(
        borrow_flows(conn, borrow.id),
        load_rates(conn, borrow, "borrow"),
        borrow_checks(conn, borrow.id),
        today,
    )


# --- Réseau : taux publics, au plus une fois par jour --------------------------------------


def refresh_rates(
    conn: sqlite3.Connection, client: httpx2.Client, today: date, now: datetime | None = None
) -> dict[int, str]:
    """Complète les taux de dépôt des lignes suivies et d'emprunt des emprunts suivis, puis
    les seuils de liquidation de leurs marchés (D-108) ; renvoie {ligne: erreur} des échecs
    de dépôt (un emprunt en échec garde ses derniers taux)."""
    now = now or datetime.now(UTC)
    errors: dict[int, str] = {}
    done: dict[tuple, str | None] = {}
    scope = conn_key(conn)  # taux rangés dans la base du profil : repères par base (D-098)
    items = [(line, "supply") for line in list_lines(conn)]
    items += [(b, "borrow") for b in list_borrows(conn)]
    for line, side in items:
        key = (*line.key, side)
        if key in done:
            if done[key] and side == "supply":
                errors[line.id] = done[key]
            continue
        done[key] = None
        if _rates_checked.get((scope, key)) == today:
            continue
        failed = _rates_failed.get((scope, key))
        if failed and now - failed < RETRY_AFTER:
            continue
        try:
            rates = aave.get_rate_history(client, line.chain_id, line.market, line.token, side)
        except ProviderError as err:
            _rates_failed[(scope, key)] = now
            done[key] = str(err)
            if side == "supply":
                errors[line.id] = str(err)
            continue
        save_rates(conn, line, rates, side)
        _rates_checked[(scope, key)] = today
        _rates_failed.pop((scope, key), None)
    if any(side == "borrow" for _, side in items):
        refresh_thresholds(conn, client, today, [line for line, _ in items])
    return errors


def refresh_thresholds(conn: sqlite3.Connection, client: httpx2.Client, today: date, lines) -> None:
    """Seuils de liquidation des marchés des lignes données, relus une fois par jour (la liste
    des marchés d'une chaîne, déjà publique, sans aucun montant). Panne : derniers seuils."""
    stored = {
        (r["chain_id"], r["market"], r["token"]): r["fetched_on"]
        for r in conn.execute("SELECT chain_id, market, token, fetched_on FROM defi_reserve")
    }
    for chain_id in sorted({line.chain_id for line in lines}):
        keys = {line.key for line in lines if line.chain_id == chain_id}
        if all(stored.get(k) == today.isoformat() for k in keys):
            continue
        try:
            found = reserves(client, chain_id, today)
        except ProviderError:
            continue
        conn.executemany(
            "INSERT OR REPLACE INTO defi_reserve (chain_id, market, token, liquidation_threshold,"
            " fetched_on) VALUES (?, ?, ?, ?, ?)",
            [
                (
                    chain_id,
                    r["market"].lower(),
                    r["token"].lower(),
                    to_db(r["liquidation_threshold"]),
                    today.isoformat(),
                )
                for r in found
            ],
        )


def load_thresholds(conn: sqlite3.Connection) -> dict[tuple[int, str, str], Decimal]:
    """Seuils de liquidation connus {(chaîne, marché, jeton) en minuscules: seuil}."""
    rows = conn.execute("SELECT chain_id, market, token, liquidation_threshold FROM defi_reserve")
    return {(r[0], r[1], r[2]): from_db(r[3]) for r in rows}


def chains(client: httpx2.Client, today: date) -> list[tuple[int, str]]:
    cached = _catalog.get("chains")
    if cached and cached[0] == today:
        return cached[1]
    out = aave.get_chains(client)
    _catalog["chains"] = (today, out)
    return out


def reserves(client: httpx2.Client, chain_id: int, today: date) -> list[dict]:
    cached = _catalog.get(chain_id)
    if cached and cached[0] == today:
        return cached[1]
    out = aave.get_reserves(client, chain_id)
    _catalog[chain_id] = (today, out)
    return out


# --- Hors ligne : intérêts estimés ----------------------------------------------------------


def flows_of(operations, line: Line) -> dict[date, Decimal]:
    """Variation de quantité de chaque jour de la ligne, hors intérêts estimés par l'app."""
    out: dict[date, Decimal] = {}
    for op in with_transfers(operations):
        if (op.account_id, op.asset_id) != (line.account_id, line.asset_id):
            continue
        if op.op_type not in TRADES or op.source == SOURCE:
            continue
        delta = -op.quantity if op.op_type in OUTGOING else op.quantity
        out[op.op_date] = out.get(op.op_date, ZERO) + delta
    return out


def estimate_line(conn: sqlite3.Connection, line: Line, operations, today: date):
    return defi.estimate(
        flows_of(operations, line), load_rates(conn, line), load_checks(conn, line.id), today
    )


def _unit_price(conn: sqlite3.Connection, asset, day: date, operations, line: Line):
    """Cours du jeton au jour donné (historique en cache, sinon dernier cours connu, sinon prix
    du dernier achat de la ligne) et sa devise."""
    close = prices.close_on(conn, asset.id, day)
    if close and close[1] == asset.currency:
        return close
    latest = prices.latest_price(conn, asset.id)
    if latest and latest.currency == asset.currency:
        return latest.price, latest.currency
    buys = [
        o
        for o in operations
        if (o.account_id, o.asset_id) == (line.account_id, line.asset_id)
        and o.op_type in ("achat", "solde_initial")
        and o.source != SOURCE
        and o.unit_price
    ]
    return (buys[-1].unit_price, buys[-1].currency) if buys else None


def sync(conn: sqlite3.Connection, today: date) -> dict[int, str]:
    """Enregistre ou recalcule les intérêts des mois clos de chaque ligne suivie. Renvoie
    {ligne: avertissement}. Une incohérence (intérêts déjà vendus...) annule tout le passage :
    l'appelant fait le rollback, rien n'est changé."""
    lines = list_lines(conn)
    known = {line.id for line in lines}
    # Intérêts d'une ligne dont le suivi a disparu (compte ou actif supprimé) : ils deviennent
    # des opérations ordinaires, modifiables à la main.
    for row in conn.execute(
        "SELECT id, import_key FROM operation WHERE source = ?", (SOURCE,)
    ).fetchall():
        parts = (row["import_key"] or "").split(":")
        if len(parts) != 3 or not parts[1].isdigit() or int(parts[1]) not in known:
            conn.execute(
                "UPDATE operation SET source = 'manuel', import_key = NULL WHERE id = ?",
                (row["id"],),
            )
    if not lines:
        return {}
    assets = {a.id: a for a in repository.list_assets(conn)}
    operations = repository.list_operations(conn)
    warnings: dict[int, str] = {}
    changed = False
    for line in lines:
        result = estimate_line(conn, line, operations, today)
        existing = {
            o.op_date.strftime("%Y-%m"): o
            for o in operations
            if o.source == SOURCE and (o.account_id, o.asset_id) == (line.account_id, line.asset_id)
        }
        wanted = {} if result is None else result.months
        if result is None:
            warnings[line.id] = "aucun taux connu pour ce marché"
        for month, op in existing.items():
            if month not in wanted:
                conn.execute("DELETE FROM operation WHERE id = ?", (op.id,))
                changed = True
        asset = assets[line.asset_id]
        for month, quantity in wanted.items():
            op = existing.get(month)
            if op is not None and op.quantity == quantity:
                continue
            day = defi.month_end(month)
            priced = _unit_price(conn, asset, day, operations, line)
            if priced is None:
                warnings[line.id] = "cours du jeton inconnu : intérêts non enregistrés"
                continue
            price, currency = priced
            offered = quantity * price
            if op is None:
                repository.create_operation(
                    conn,
                    line.account_id,
                    line.asset_id,
                    day,
                    "achat",
                    quantity,
                    price,
                    None,
                    ZERO,
                    currency,
                    f"Intérêts Aave estimés ({line.symbol}, {line.chain_name})",
                    source=SOURCE,
                    import_key=f"aave:{line.id}:{month}",
                    check=False,
                    offered=offered,
                    reward_kind="earn",
                )
            else:
                conn.execute(
                    "UPDATE operation SET quantity = ?, unit_price = ?, offered = ?, currency = ?"
                    " WHERE id = ?",
                    (to_db(quantity), to_db(price), to_db(offered), currency, op.id),
                )
            changed = True
    if changed:
        compute_positions(repository.list_operations(conn))  # lève PositionError
    return warnings


def safe_sync(conn: sqlite3.Connection, today: date) -> dict[int, str]:
    """`sync` dans sa propre transaction ; une incohérence laisse la base intacte."""
    try:
        with conn:
            return sync(conn, today)
    except PositionError as err:
        message = f"intérêts non mis à jour : {err}"
        return {line.id: message for line in list_lines(conn)}
