"""Lecture et écriture des comptes, actifs et opérations (SQL écrit à la main, D-001)."""

import sqlite3
from dataclasses import replace
from datetime import date
from decimal import Decimal

from app import prices
from app.db import from_db, to_db
from app.debts import Debt
from app.models import CASH_ENTERED, Account, Asset, Operation
from app.portfolio import (
    RateHistory,
    compute_positions,
    current_cash,
    replay_cash,
    with_transfers,
)


def _account(
    row: sqlite3.Row, operations: list[Operation], fx: RateHistory | None = None
) -> Account:
    """Compte avec ses espèces et ses versements actuels.

    Livret : solde saisi + opérations enregistrées depuis (D-041). Autres comptes : espèces
    déduites des versements (champ « Versements » + opérations), achats, ventes... (D-062).
    Compte crypto : une crypto cotée dans une autre devise que les espèces (en $ sur un compte
    en €) les débite ou crédite au taux BCE du jour de l'opération (D-085) ; ailleurs, une
    opération en devise reste de côté (taux de la banque inconnu, D-041).
    """
    mine = [o for o in operations if o.account_id == row["id"]]
    if row["kind"] == "crypto" and fx is not None:
        mine = [_in_currency(o, row["currency"], fx) for o in mine]
    before = from_db(row["deposits_before"])
    entered = sum((o.amount for o in mine if o.op_type == "versement"), Decimal("0"))
    cash_date = date.fromisoformat(row["cash_date"]) if row["cash_date"] else None
    if row["kind"] == "livret":
        cash = current_cash(from_db(row["cash"]), row["cash_op_id"], row["currency"], mine)
        implied = Decimal("0")
    elif row["kind"] in CASH_ENTERED and cash_date is not None:
        # CTO, crypto, Autre (D-075) : solde saisi, puis les opérations suivantes rejouées ; un
        # achat sans assez d'espèces compte un versement supposé, comme D-062.
        after = [o for o in mine if o.id > row["cash_op_id"]]
        cash, implied = replay_cash(from_db(row["cash"]), row["currency"], after)
        before = Decimal("0")  # l'ancien champ « Versements » ne sert plus à ces comptes
    else:
        cash, implied = replay_cash(before, row["currency"], mine)
    return Account(
        row["id"],
        row["name"],
        row["kind"],
        row["institution"],
        row["currency"],
        cash,
        cash_date if row["kind"] in CASH_ENTERED else None,
        date.fromisoformat(row["opened_on"]) if row["opened_on"] else None,
        before,
        entered,
        implied,
        custody=row["custody"],
        abroad=bool(row["abroad"]),
    )


def _asset(row: sqlite3.Row) -> Asset:
    return Asset(
        row["id"],
        row["name"],
        row["asset_class"],
        row["isin"],
        row["ticker"],
        row["coingecko_id"],
        row["currency"],
        row["color"],
        from_db(row["ter"]),
        row["category"],
    )


def _operation(row: sqlite3.Row) -> Operation:
    return Operation(
        id=row["id"],
        account_id=row["account_id"],
        asset_id=row["asset_id"],
        op_date=date.fromisoformat(row["op_date"]),
        op_type=row["op_type"],
        quantity=from_db(row["quantity"]),
        unit_price=from_db(row["unit_price"]),
        amount=from_db(row["amount"]),
        fees=from_db(row["fees"]),
        currency=row["currency"],
        note=row["note"],
        crypto_swap=bool(row["crypto_swap"]),
        portfolio_value=from_db(row["portfolio_value"]),
        offered=from_db(row["offered"]),
        tax_withheld=from_db(row["tax_withheld"]),
        to_account_id=row["to_account_id"],
        destination=row["destination"],
        reward_kind=row["reward_kind"],
        source=row["source"],
    )


# --- Comptes ---------------------------------------------------------------


# --- Ordre choisi des comptes et actifs (D-070) -------------------------------

ORDERED_TABLES = ("account", "asset", "debt")


def _next_position(conn: sqlite3.Connection, table: str) -> int:
    """Un compte ou actif créé se range en fin de liste."""
    assert table in ORDERED_TABLES
    return conn.execute(f"SELECT COALESCE(MAX(position), 0) + 1 FROM {table}").fetchone()[0]


def reorder(conn: sqlite3.Connection, table: str, ids: list[int]) -> bool:
    """Range les comptes (ou actifs) dans l'ordre de `ids`, qui doit les citer tous une fois.

    Renvoie False, sans rien changer, si la liste ne correspond pas exactement à la table
    (un compte ajouté ou supprimé entre-temps dans un autre onglet).
    """
    assert table in ORDERED_TABLES  # nom de table jamais venu de l'utilisateur
    existing = {r[0] for r in conn.execute(f"SELECT id FROM {table}")}
    if len(ids) != len(set(ids)) or set(ids) != existing:
        return False
    conn.executemany(
        f"UPDATE {table} SET position = ? WHERE id = ?", [(n + 1, i) for n, i in enumerate(ids)]
    )
    return True


def _in_currency(op: Operation, currency: str, fx: RateHistory) -> Operation:
    """Opération ramenée dans la devise des espèces au taux BCE de son jour ; sans taux, telle
    quelle (laissée de côté par le calcul des espèces)."""
    if op.currency == currency:
        return op
    rates = fx.on(op.op_date)
    if op.currency not in rates or currency not in rates:
        return op
    factor = rates[currency] / rates[op.currency]
    amounts = {k: getattr(op, k) for k in CONVERTED}
    return replace(
        op, currency=currency, **{k: None if v is None else v * factor for k, v in amounts.items()}
    )


def _cash_fx(conn: sqlite3.Connection) -> RateHistory:
    """Taux stockés (BCE, jour par jour), sans appel réseau, pour les espèces des comptes."""
    currencies = {r[0] for r in conn.execute("SELECT DISTINCT currency FROM operation")}
    currencies -= {"EUR"}
    return RateHistory(prices.stored_rates(conn, currencies), prices.rate_series(conn, currencies))


def list_accounts(conn: sqlite3.Connection) -> list[Account]:
    operations = with_transfers(list_operations(conn))  # argent reçu d'un autre compte (D-085)
    fx = _cash_fx(conn)
    rows = conn.execute("SELECT * FROM account ORDER BY position, name")
    return [_account(r, operations, fx) for r in rows]


def get_account(conn: sqlite3.Connection, account_id: int) -> Account | None:
    row = conn.execute("SELECT * FROM account WHERE id = ?", (account_id,)).fetchone()
    if row is None:
        return None
    return _account(row, with_transfers(list_operations(conn)), _cash_fx(conn))


def _last_operation_id(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COALESCE(MAX(id), 0) FROM operation").fetchone()[0]


def create_account(
    conn: sqlite3.Connection,
    name: str,
    kind: str,
    institution: str | None,
    currency: str,
    cash: Decimal = Decimal("0"),
) -> Account:
    cur = conn.execute(
        "INSERT INTO account (name, kind, institution, currency, cash, cash_date, cash_op_id,"
        " position) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            name,
            kind,
            institution,
            currency,
            to_db(cash),
            date.today().isoformat() if cash or kind in CASH_ENTERED else None,
            _last_operation_id(conn),
            _next_position(conn, "account"),
        ),
    )
    return get_account(conn, cur.lastrowid)


def update_account(
    conn: sqlite3.Connection,
    account_id: int,
    name: str,
    kind: str,
    institution: str | None,
    currency: str,
    cash: Decimal,
) -> Account:
    """Modifie un compte. `cash` = espèces actuelles lues chez la banque : si elles diffèrent
    du calcul, elles deviennent le nouveau solde de référence (recalage), daté d'aujourd'hui,
    et seules les opérations enregistrées ensuite le feront bouger (D-041)."""
    old = get_account(conn, account_id)
    conn.execute(
        "UPDATE account SET name = ?, kind = ?, institution = ?, currency = ? WHERE id = ?",
        (name, kind, institution, currency, account_id),
    )
    # Espèces lues chez la banque : un nouveau solde de référence. Un CTO d'avant D-075 (sans
    # solde saisi) prend le chiffre affiché, confirmé en enregistrant.
    if cash != old.cash or (kind in CASH_ENTERED and old.cash_date is None):
        conn.execute(
            "UPDATE account SET cash = ?, cash_date = ?, cash_op_id = ? WHERE id = ?",
            (to_db(cash), date.today().isoformat(), _last_operation_id(conn), account_id),
        )
    return get_account(conn, account_id)


def set_account_tax_info(
    conn: sqlite3.Connection, account_id: int, opened_on: date | None, deposits_before: Decimal
) -> None:
    """Date d'ouverture et versements d'avant le suivi (repères fiscaux, D-059)."""
    conn.execute(
        "UPDATE account SET opened_on = ?, deposits_before = ? WHERE id = ?",
        (opened_on.isoformat() if opened_on else None, to_db(deposits_before), account_id),
    )


def set_account_custody(
    conn: sqlite3.Connection, account_id: int, custody: str | None, abroad: bool
) -> None:
    """Garde d'un compte crypto et établissement à l'étranger (D-104)."""
    conn.execute(
        "UPDATE account SET custody = ?, abroad = ? WHERE id = ?",
        (custody, int(abroad), account_id),
    )


def delete_account(conn: sqlite3.Connection, account_id: int) -> bool:
    """Supprime un compte sans opération (sinon IntegrityError, clé étrangère RESTRICT)."""
    return conn.execute("DELETE FROM account WHERE id = ?", (account_id,)).rowcount > 0


# --- Actifs ----------------------------------------------------------------


def list_assets(conn: sqlite3.Connection) -> list[Asset]:
    return [_asset(r) for r in conn.execute("SELECT * FROM asset ORDER BY position, name, id")]


def get_asset(conn: sqlite3.Connection, asset_id: int) -> Asset | None:
    row = conn.execute("SELECT * FROM asset WHERE id = ?", (asset_id,)).fetchone()
    return _asset(row) if row else None


def create_asset(
    conn: sqlite3.Connection,
    name: str,
    asset_class: str,
    isin: str | None,
    ticker: str | None,
    coingecko_id: str | None,
    currency: str,
    color: int | None = None,
) -> Asset:
    cur = conn.execute(
        "INSERT INTO asset (name, asset_class, isin, ticker, coingecko_id, currency, color,"
        " position) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            name,
            asset_class,
            isin,
            ticker,
            coingecko_id,
            currency,
            color,
            _next_position(conn, "asset"),
        ),
    )
    return get_asset(conn, cur.lastrowid)


def update_asset(
    conn: sqlite3.Connection,
    asset_id: int,
    name: str,
    asset_class: str,
    isin: str | None,
    ticker: str | None,
    coingecko_id: str | None,
    currency: str,
    color: int | None = None,
) -> Asset:
    """Modifie un actif. Si son identifiant de prix change, le cache de prix est vidé : les
    cours enregistrés venaient de l'ancien identifiant, peut-être un autre titre."""
    old = get_asset(conn, asset_id)
    conn.execute(
        "UPDATE asset SET name = ?, asset_class = ?, isin = ?, ticker = ?, coingecko_id = ?,"
        " currency = ?, color = ? WHERE id = ?",
        (name, asset_class, isin, ticker, coingecko_id, currency, color, asset_id),
    )
    if (old.ticker, old.coingecko_id) != (ticker, coingecko_id):
        conn.execute("DELETE FROM price WHERE asset_id = ?", (asset_id,))
        conn.execute("DELETE FROM price_history WHERE asset_id = ?", (asset_id,))
    return get_asset(conn, asset_id)


CONVERTED = ("unit_price", "amount", "fees", "offered", "tax_withheld")


def _converted(value: Decimal | None, rate: Decimal | None) -> Decimal | None:
    """Montant × taux, 10 décimales au plus, sans notation « 1E+3 »."""
    if value is None:
        return None
    if rate is None:
        raise LookupError
    return Decimal(format((value * rate).quantize(Decimal("1e-10")).normalize(), "f"))


def convert_asset_currency(conn: sqlite3.Connection, asset_id: int, new: str, fx) -> int:
    """Change la devise de cotation d'un actif qui a déjà des opérations (D-083). Prix, montants
    et frais saisis dans l'ancienne devise sont convertis au taux BCE du jour de chaque
    opération (`fx` : `RateHistory`), les achats programmés au dernier taux, les valeurs
    saisies au taux de leur date ; les cours en cache sont effacés (rechargés dans la nouvelle
    devise). Renvoie le nombre d'opérations converties ; `LookupError` si un taux manque."""
    old = get_asset(conn, asset_id).currency

    def rate(day: date) -> Decimal | None:
        rates = fx.on(day)
        return rates[new] / rates[old] if old in rates and new in rates else None

    rows = conn.execute(
        "SELECT * FROM operation WHERE asset_id = ? AND currency = ?", (asset_id, old)
    ).fetchall()
    for row in rows:
        r = rate(date.fromisoformat(row["op_date"]))
        values = [to_db(_converted(from_db(row[c]), r)) for c in CONVERTED]
        conn.execute(
            f"UPDATE operation SET {', '.join(f'{c} = ?' for c in CONVERTED)}, currency = ?"
            " WHERE id = ?",
            (*values, new, row["id"]),
        )
    for row in conn.execute("SELECT id, amount FROM recurring WHERE asset_id = ?", (asset_id,)):
        amount = to_db(_converted(from_db(row["amount"]), rate(date.today())))
        conn.execute("UPDATE recurring SET amount = ? WHERE id = ?", (amount, row["id"]))
    manual = conn.execute(
        "SELECT price_date, close FROM price_history WHERE asset_id = ? AND source = 'manuel'",
        (asset_id,),
    ).fetchall()
    for row in manual:
        day = date.fromisoformat(row["price_date"])
        close = to_db(_converted(from_db(row["close"]), rate(day)))
        conn.execute(
            "UPDATE price_history SET close = ?, currency = ?"
            " WHERE asset_id = ? AND price_date = ?",
            (close, new, asset_id, row["price_date"]),
        )
    conn.execute("DELETE FROM price WHERE asset_id = ?", (asset_id,))
    conn.execute("DELETE FROM price_history WHERE asset_id = ? AND source != 'manuel'", (asset_id,))
    return len(rows)


def set_asset_ter(conn: sqlite3.Connection, asset_id: int, ter: Decimal | None) -> None:
    conn.execute("UPDATE asset SET ter = ? WHERE id = ?", (to_db(ter), asset_id))


def set_asset_category(conn: sqlite3.Connection, asset_id: int, category: str | None) -> None:
    conn.execute("UPDATE asset SET category = ? WHERE id = ?", (category, asset_id))


# --- Allocation cible (D-058) --------------------------------------------------


def list_targets(conn: sqlite3.Connection) -> dict[int, Decimal]:
    return {r["asset_id"]: from_db(r["pct"]) for r in conn.execute("SELECT * FROM target")}


def save_targets(conn: sqlite3.Connection, targets: dict[int, Decimal]) -> None:
    """Remplace toute l'allocation cible (une part à 0 ou absente = pas de cible)."""
    conn.execute("DELETE FROM target")
    conn.executemany(
        "INSERT INTO target (asset_id, pct) VALUES (?, ?)",
        [(asset_id, to_db(pct)) for asset_id, pct in targets.items() if pct > 0],
    )


def list_category_targets(conn: sqlite3.Connection) -> dict[str, Decimal]:
    rows = conn.execute("SELECT * FROM category_target")
    return {r["category"]: from_db(r["pct"]) for r in rows}


def save_category_targets(conn: sqlite3.Connection, targets: dict[str, Decimal]) -> None:
    """Remplace toute l'allocation cible par catégorie (D-061)."""
    conn.execute("DELETE FROM category_target")
    conn.executemany(
        "INSERT INTO category_target (category, pct) VALUES (?, ?)",
        [(name, to_db(pct)) for name, pct in targets.items() if pct > 0],
    )


def delete_asset(conn: sqlite3.Connection, asset_id: int) -> bool:
    """Supprime un actif sans opération (sinon IntegrityError, clé étrangère RESTRICT).
    Ses prix en cache partent avec lui (ON DELETE CASCADE)."""
    return conn.execute("DELETE FROM asset WHERE id = ?", (asset_id,)).rowcount > 0


def remove_with_operations(
    conn: sqlite3.Connection, column: str, item_id: int, target_id: int | None
) -> int:
    """Compte (column = "account_id") ou actif ("asset_id") supprimé avec ses opérations
    (target_id None) ou après les avoir déplacées, avec ses achats programmés, vers target_id
    (D-074). Positions revérifiées : lève PositionError si le déplacement rend une vente
    impossible ; l'appelant annule alors la transaction. Renvoie le nombre d'opérations."""
    table = {"account_id": "account", "asset_id": "asset"}[column]  # jamais de texte libre
    if column == "account_id" and target_id is not None:
        # Transferts reçus (D-085) : ils arrivent désormais sur target_id. Un transfert entre
        # les deux comptes fusionnés n'a plus lieu d'être : titres ou argent restent sur place.
        conn.execute(
            "DELETE FROM operation WHERE (account_id = ? AND to_account_id = ?)"
            " OR (account_id = ? AND to_account_id = ?)",
            (target_id, item_id, item_id, target_id),
        )
        conn.execute(
            "UPDATE operation SET to_account_id = ? WHERE to_account_id = ?", (target_id, item_id)
        )
    # Sinon, ON DELETE SET NULL : les transferts reçus passent « Hors de l'app ».
    if target_id is None:
        count = conn.execute(f"DELETE FROM operation WHERE {column} = ?", (item_id,)).rowcount
    else:
        count = conn.execute(
            f"UPDATE operation SET {column} = ? WHERE {column} = ?", (target_id, item_id)
        ).rowcount
        conn.execute(f"UPDATE recurring SET {column} = ? WHERE {column} = ?", (target_id, item_id))
    conn.execute(f"DELETE FROM {table} WHERE id = ?", (item_id,))
    compute_positions(list_operations(conn))
    return count


def count_operations(conn: sqlite3.Connection, *, account_id=None, asset_id=None) -> int:
    """Opérations d'un compte, transferts reçus d'un autre compte compris (D-085), ou d'un
    actif."""
    if account_id is not None:
        sql = "SELECT COUNT(*) FROM operation WHERE account_id = ? OR to_account_id = ?"
        return conn.execute(sql, (account_id, account_id)).fetchone()[0]
    return conn.execute(
        "SELECT COUNT(*) FROM operation WHERE asset_id = ?", (asset_id,)
    ).fetchone()[0]


# --- Opérations ------------------------------------------------------------


def list_operations(conn: sqlite3.Connection) -> list[Operation]:
    rows = conn.execute("SELECT * FROM operation ORDER BY op_date, id")
    return [_operation(r) for r in rows]


def create_trade(
    conn: sqlite3.Connection,
    account_id: int,
    asset_id: int,
    op_date: date,
    op_type: str,
    quantity: Decimal,
    unit_price: Decimal,
    fees: Decimal,
    currency: str,
    note: str | None,
) -> Operation:
    """Enregistre un achat, une vente ou un solde initial, puis vérifie que les positions
    restent cohérentes. En cas d'erreur, l'appelant annule la transaction SQL (rollback)."""
    cur = conn.execute(
        "INSERT INTO operation (account_id, asset_id, op_date, op_type, quantity, unit_price,"
        " fees, currency, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            account_id,
            asset_id,
            op_date.isoformat(),
            op_type,
            to_db(quantity),
            to_db(unit_price),
            to_db(fees),
            currency,
            note,
        ),
    )
    compute_positions(list_operations(conn))  # lève PositionError si incohérent
    row = conn.execute("SELECT * FROM operation WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _operation(row)


def get_operation(conn: sqlite3.Connection, operation_id: int) -> Operation | None:
    row = conn.execute("SELECT * FROM operation WHERE id = ?", (operation_id,)).fetchone()
    return _operation(row) if row else None


def create_operation(
    conn: sqlite3.Connection,
    account_id: int,
    asset_id: int | None,
    op_date: date,
    op_type: str,
    quantity: Decimal | None,
    unit_price: Decimal | None,
    amount: Decimal | None,
    fees: Decimal,
    currency: str,
    note: str | None,
    *,
    source: str = "manuel",
    import_key: str | None = None,
    check: bool = True,
    offered: Decimal | None = None,
    tax_withheld: Decimal | None = None,
    crypto_swap: bool = False,
    to_account_id: int | None = None,
    destination: str | None = None,
    reward_kind: str | None = None,
) -> Operation:
    """Enregistre une opération de tout type (achat, vente, versement, retrait...), puis vérifie
    que les positions restent cohérentes ; en cas d'erreur, l'appelant annule (rollback).

    `check=False` : l'appelant vérifie une seule fois après une série d'ajouts (import CSV).
    """
    cur = conn.execute(
        "INSERT INTO operation (account_id, asset_id, op_date, op_type, quantity, unit_price,"
        " amount, fees, currency, note, source, import_key, offered, tax_withheld, crypto_swap,"
        " to_account_id, destination, reward_kind)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            account_id,
            asset_id,
            op_date.isoformat(),
            op_type,
            to_db(quantity),
            to_db(unit_price),
            to_db(amount),
            to_db(fees),
            currency,
            note,
            source,
            import_key,
            to_db(offered),
            to_db(tax_withheld),
            int(crypto_swap),
            to_account_id,
            destination,
            reward_kind,
        ),
    )
    if check:
        compute_positions(list_operations(conn))  # lève PositionError si incohérent
    return get_operation(conn, cur.lastrowid)


def update_operation(
    conn: sqlite3.Connection,
    operation_id: int,
    account_id: int,
    asset_id: int | None,
    op_date: date,
    op_type: str,
    quantity: Decimal | None,
    unit_price: Decimal | None,
    amount: Decimal | None,
    fees: Decimal,
    currency: str,
    note: str | None,
) -> Operation:
    """Corrige une opération en gardant son id : si elle comptait déjà dans les espèces saisies,
    elle y reste ; sinon sa correction s'y répercute. Positions revérifiées comme à l'ajout."""
    conn.execute(
        "UPDATE operation SET account_id = ?, asset_id = ?, op_date = ?, op_type = ?,"
        " quantity = ?, unit_price = ?, amount = ?, fees = ?, currency = ?, note = ?"
        " WHERE id = ?",
        (
            account_id,
            asset_id,
            op_date.isoformat(),
            op_type,
            to_db(quantity),
            to_db(unit_price),
            to_db(amount),
            to_db(fees),
            currency,
            note,
            operation_id,
        ),
    )
    compute_positions(list_operations(conn))  # lève PositionError si incohérent
    return get_operation(conn, operation_id)


def set_sale_tax_info(
    conn: sqlite3.Connection,
    operation_id: int,
    crypto_swap: bool,
    portfolio_value: Decimal | None,
) -> Operation:
    """Renseignements fiscaux d'une vente sur un compte crypto (D-066) : échange contre une
    autre crypto, valeur globale du portefeuille ce jour-là (EUR). Remis à zéro ailleurs."""
    conn.execute(
        "UPDATE operation SET crypto_swap = ?, portfolio_value = ? WHERE id = ?",
        (int(crypto_swap), to_db(portfolio_value), operation_id),
    )
    return get_operation(conn, operation_id)


def set_offered_withheld(
    conn: sqlite3.Connection,
    operation_id: int,
    offered: Decimal | None,
    tax_withheld: Decimal | None,
    reward_kind: str | None = None,
) -> Operation:
    """Part offerte d'un achat et son type de récompense (D-092), impôt retenu d'un dividende
    (D-076) ; vides ailleurs."""
    conn.execute(
        "UPDATE operation SET offered = ?, tax_withheld = ?, reward_kind = ? WHERE id = ?",
        (to_db(offered), to_db(tax_withheld), reward_kind, operation_id),
    )
    return get_operation(conn, operation_id)


def set_transfer(
    conn: sqlite3.Connection,
    operation_id: int,
    to_account_id: int | None,
    destination: str | None,
) -> Operation:
    """Destination d'un transfert sortant ou d'un retrait (D-085) : un compte de l'app, ou un
    texte libre hors de l'app ; vides ailleurs. Positions revérifiées (titres reçus)."""
    conn.execute(
        "UPDATE operation SET to_account_id = ?, destination = ? WHERE id = ?",
        (to_account_id, destination, operation_id),
    )
    compute_positions(list_operations(conn))
    return get_operation(conn, operation_id)


def delete_operation(conn: sqlite3.Connection, operation_id: int) -> bool:
    """Supprime une opération (correction d'une erreur de saisie). Même contrôle qu'à l'ajout :
    supprimer un achat ne doit pas rendre une vente ultérieure impossible."""
    cur = conn.execute("DELETE FROM operation WHERE id = ?", (operation_id,))
    if cur.rowcount == 0:
        return False
    compute_positions(list_operations(conn))
    return True


# --- Liste de comparaison (indices et livrets, D-042) ------------------------------


def _benchmark(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "label": row["label"],
        "grp": row["grp"],
        "ticker": row["ticker"],
        "rate": row["rate"],  # déjà en texte (Decimal), jamais de float
        "position": row["position"],
    }


def list_benchmarks(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM benchmark ORDER BY position, id")
    return [_benchmark(r) for r in rows]


def get_benchmark(conn: sqlite3.Connection, benchmark_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM benchmark WHERE id = ?", (benchmark_id,)).fetchone()
    return _benchmark(row) if row else None


def create_benchmark(
    conn: sqlite3.Connection,
    kind: str,
    label: str,
    grp: str | None,
    ticker: str | None,
    rate: Decimal | None,
) -> dict:
    """Ajoute une entrée en fin de liste."""
    position = conn.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM benchmark").fetchone()[0]
    cur = conn.execute(
        "INSERT INTO benchmark (kind, label, grp, ticker, rate, position)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (kind, label, grp, ticker, to_db(rate), position),
    )
    return get_benchmark(conn, cur.lastrowid)


def update_benchmark(
    conn: sqlite3.Connection,
    benchmark_id: int,
    kind: str,
    label: str,
    grp: str | None,
    ticker: str | None,
    rate: Decimal | None,
) -> dict:
    conn.execute(
        "UPDATE benchmark SET kind = ?, label = ?, grp = ?, ticker = ?, rate = ? WHERE id = ?",
        (kind, label, grp, ticker, to_db(rate), benchmark_id),
    )
    return get_benchmark(conn, benchmark_id)


def move_benchmark(conn: sqlite3.Connection, benchmark_id: int, step: int) -> None:
    """Monte (step = -1) ou descend (step = +1) une entrée d'un cran dans la liste."""
    ids = [b["id"] for b in list_benchmarks(conn)]
    i = ids.index(benchmark_id)
    j = min(max(i + step, 0), len(ids) - 1)
    ids[i], ids[j] = ids[j], ids[i]
    conn.executemany(
        "UPDATE benchmark SET position = ? WHERE id = ?", [(n + 1, b) for n, b in enumerate(ids)]
    )


def delete_benchmark(conn: sqlite3.Connection, benchmark_id: int) -> bool:
    return conn.execute("DELETE FROM benchmark WHERE id = ?", (benchmark_id,)).rowcount > 0


# --- Achats programmés (D-055) ------------------------------------------------


def _recurring(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "account_id": row["account_id"],
        "asset_id": row["asset_id"],
        "amount": from_db(row["amount"]),
        "frequency": row["frequency"],
        "day": row["day"],
        "start_date": date.fromisoformat(row["start_date"]),
        "last_due": date.fromisoformat(row["last_due"]) if row["last_due"] else None,
        "note": row["note"],
    }


def list_recurring(conn: sqlite3.Connection) -> list[dict]:
    return [_recurring(r) for r in conn.execute("SELECT * FROM recurring ORDER BY day, id")]


def get_recurring(conn: sqlite3.Connection, recurring_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM recurring WHERE id = ?", (recurring_id,)).fetchone()
    return _recurring(row) if row else None


def save_recurring(
    conn: sqlite3.Connection,
    recurring_id: int | None,
    account_id: int,
    asset_id: int,
    amount: Decimal,
    frequency: str,
    day: int,
    start_date: date,
    note: str | None,
) -> dict:
    """Crée (recurring_id vide) ou modifie un achat programmé ; les échéances déjà traitées
    restent."""
    values = (account_id, asset_id, to_db(amount), frequency, day, start_date.isoformat(), note)
    if recurring_id is None:
        cur = conn.execute(
            "INSERT INTO recurring (account_id, asset_id, amount, frequency, day, start_date,"
            " note) VALUES (?, ?, ?, ?, ?, ?, ?)",
            values,
        )
        recurring_id = cur.lastrowid
    else:
        conn.execute(
            "UPDATE recurring SET account_id = ?, asset_id = ?, amount = ?, frequency = ?,"
            " day = ?, start_date = ?, note = ? WHERE id = ?",
            (*values, recurring_id),
        )
    return get_recurring(conn, recurring_id)


def mark_recurring_due(conn: sqlite3.Connection, recurring_id: int, due: date) -> None:
    conn.execute("UPDATE recurring SET last_due = ? WHERE id = ?", (due.isoformat(), recurring_id))


def delete_recurring(conn: sqlite3.Connection, recurring_id: int) -> bool:
    return conn.execute("DELETE FROM recurring WHERE id = ?", (recurring_id,)).rowcount > 0


# --- Listes de suivi (D-072) -------------------------------------------------


def list_watchlists(conn: sqlite3.Connection) -> list[dict]:
    """Listes dans l'ordre de création, chacune avec ses actifs suivis."""
    items: dict[int, list[dict]] = {}
    for r in conn.execute("SELECT * FROM watchlist_item ORDER BY position, id"):
        items.setdefault(r["watchlist_id"], []).append(
            {"id": r["id"], "label": r["label"], "ticker": r["ticker"]}
        )
    rows = conn.execute("SELECT * FROM watchlist ORDER BY position, id")
    return [{"id": r["id"], "name": r["name"], "items": items.get(r["id"], [])} for r in rows]


def reorder_watchlist_items(conn: sqlite3.Connection, watchlist_id: int, ids: list[int]) -> bool:
    """Range les actifs d'une liste dans l'ordre de `ids` (tous, une fois chacun)."""
    existing = {
        r[0]
        for r in conn.execute(
            "SELECT id FROM watchlist_item WHERE watchlist_id = ?", (watchlist_id,)
        )
    }
    if len(ids) != len(set(ids)) or set(ids) != existing:
        return False
    conn.executemany(
        "UPDATE watchlist_item SET position = ? WHERE id = ? AND watchlist_id = ?",
        [(n + 1, i, watchlist_id) for n, i in enumerate(ids)],
    )
    return True


def get_watchlist(conn: sqlite3.Connection, watchlist_id: int) -> dict | None:
    return next((w for w in list_watchlists(conn) if w["id"] == watchlist_id), None)


def create_watchlist(conn: sqlite3.Connection, name: str) -> dict:
    position = conn.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM watchlist").fetchone()[0]
    cur = conn.execute("INSERT INTO watchlist (name, position) VALUES (?, ?)", (name, position))
    return get_watchlist(conn, cur.lastrowid)


def rename_watchlist(conn: sqlite3.Connection, watchlist_id: int, name: str) -> dict:
    conn.execute("UPDATE watchlist SET name = ? WHERE id = ?", (name, watchlist_id))
    return get_watchlist(conn, watchlist_id)


def delete_watchlist(conn: sqlite3.Connection, watchlist_id: int) -> bool:
    return conn.execute("DELETE FROM watchlist WHERE id = ?", (watchlist_id,)).rowcount > 0


def add_watchlist_item(
    conn: sqlite3.Connection, watchlist_id: int, label: str, ticker: str
) -> dict:
    """Ajoute un actif en fin de liste."""
    position = conn.execute(
        "SELECT COALESCE(MAX(position), 0) + 1 FROM watchlist_item WHERE watchlist_id = ?",
        (watchlist_id,),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO watchlist_item (watchlist_id, label, ticker, position) VALUES (?, ?, ?, ?)",
        (watchlist_id, label, ticker, position),
    )
    return get_watchlist(conn, watchlist_id)


def delete_watchlist_item(conn: sqlite3.Connection, watchlist_id: int, item_id: int) -> bool:
    return (
        conn.execute(
            "DELETE FROM watchlist_item WHERE id = ? AND watchlist_id = ?", (item_id, watchlist_id)
        ).rowcount
        > 0
    )


# --- Dettes (D-107) ------------------------------------------------------------------------
DEBT_FIELDS = "name, kind, institution, currency, principal, rate, start_date, months"


def _debt(row: sqlite3.Row) -> Debt:
    return Debt(
        row["id"],
        row["name"],
        row["kind"],
        row["institution"],
        row["currency"],
        from_db(row["principal"]),
        from_db(row["rate"]),
        date.fromisoformat(row["start_date"]),
        row["months"],
        row["position"],
    )


def list_debts(conn: sqlite3.Connection) -> list[Debt]:
    rows = conn.execute("SELECT * FROM debt ORDER BY position, id").fetchall()
    return [_debt(r) for r in rows]


def get_debt(conn: sqlite3.Connection, debt_id: int) -> Debt | None:
    row = conn.execute("SELECT * FROM debt WHERE id = ?", (debt_id,)).fetchone()
    return _debt(row) if row else None


def _debt_values(values: dict) -> tuple:
    return (
        values["name"],
        values["kind"],
        values.get("institution"),
        values.get("currency") or "EUR",
        to_db(values["principal"]),
        to_db(values.get("rate")),
        values["start_date"].isoformat(),
        values.get("months"),
    )


def create_debt(conn: sqlite3.Connection, values: dict) -> Debt:
    cur = conn.execute(
        f"INSERT INTO debt ({DEBT_FIELDS}, position) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (*_debt_values(values), _next_position(conn, "debt")),
    )
    return get_debt(conn, cur.lastrowid)


def update_debt(conn: sqlite3.Connection, debt_id: int, values: dict) -> Debt | None:
    sets = ", ".join(f"{f.strip()} = ?" for f in DEBT_FIELDS.split(","))
    conn.execute(f"UPDATE debt SET {sets} WHERE id = ?", (*_debt_values(values), debt_id))
    return get_debt(conn, debt_id)


def delete_debt(conn: sqlite3.Connection, debt_id: int) -> bool:
    return conn.execute("DELETE FROM debt WHERE id = ?", (debt_id,)).rowcount > 0


def debt_balances(conn: sqlite3.Connection) -> dict[int, dict[date, Decimal]]:
    """Capitaux restant dus saisis, par dette puis par date."""
    out: dict[int, dict[date, Decimal]] = {}
    for r in conn.execute("SELECT debt_id, balance_date, balance FROM debt_balance"):
        out.setdefault(r["debt_id"], {})[date.fromisoformat(r["balance_date"])] = from_db(
            r["balance"]
        )
    return out


def save_debt_balance(conn: sqlite3.Connection, debt_id: int, day: date, balance: Decimal) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO debt_balance (debt_id, balance_date, balance) VALUES (?, ?, ?)",
        (debt_id, day.isoformat(), to_db(balance)),
    )


def delete_debt_balance(conn: sqlite3.Connection, debt_id: int, day: date) -> bool:
    return (
        conn.execute(
            "DELETE FROM debt_balance WHERE debt_id = ? AND balance_date = ?",
            (debt_id, day.isoformat()),
        ).rowcount
        > 0
    )
