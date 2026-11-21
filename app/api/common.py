"""Mises en forme et gestes communs à plusieurs familles de routes : compte, actif et opération
tels que l'API les renvoie, texte d'un prix ou d'un pourcentage, ordre choisi par
glisser-déposer, sauvegarde avant une suppression, catégorie d'un actif."""

import sqlite3
from dataclasses import asdict
from datetime import date
from decimal import Decimal

from fastapi import HTTPException

from app import backup, db, prices, profiles, repository
from app.models import Account, Asset, Operation
from app.portfolio import (
    TRADES,
    ZERO,
    RateHistory,
    cash_movement,
    compute_positions,
    convert,
    with_market_prices,
)
from app.schemas import OrderIn, money, plain

CLASS_LABELS = {
    "etf": "ETF",
    "fonds": "Fonds",
    "action": "Actions",
    "crypto": "Crypto",
    "obligation": "Obligations",
    "fonds_euros": "Fonds euros",
    "scpi": "SCPI",
    "or": "Or",
    "immobilier": "Immobilier",
    "autre": "Divers",  # pas « Autres », déjà le nom de la part qui regroupe les petites
}


LIVRETS = "Livrets"  # espèces d'un compte de type livret : une classe à part entière (D-056)


def category_of(asset: Asset) -> str:
    """Catégorie personnelle de l'actif, sinon sa classe (« ETF », « Actions »...), D-061."""
    return asset.category or CLASS_LABELS.get(asset.asset_class, "Autres")


def conflict(err: sqlite3.IntegrityError) -> HTTPException:
    """Contrainte SQL violée (nom déjà pris, ISIN ou ticker en double...) -> 409."""
    return HTTPException(status_code=409, detail=f"Refusé par la base : {err}")


def account_out(account: Account) -> dict:
    out = asdict(account)
    out["cash"] = plain(account.cash)  # jamais de float en JSON
    out["cash_date"] = account.cash_date.isoformat() if account.cash_date else None
    out["opened_on"] = account.opened_on.isoformat() if account.opened_on else None
    out["deposits_before"] = plain(account.deposits_before)
    out["deposits_entered"] = plain(account.deposits_entered)
    out["deposits_implied"] = plain(account.deposits_implied)
    out["deposits"] = plain(account.deposits)  # total : champ + opérations + supposés (D-062)
    return out


def asset_out(asset: Asset) -> dict:
    out = asdict(asset)
    out["ter"] = None if asset.ter is None else plain(asset.ter)  # jamais de float en JSON
    out["manual"] = asset.manual
    out["category_label"] = category_of(asset)  # catégorie affichée (la classe à défaut)
    return out


def operation_out(op: Operation) -> dict:
    out = asdict(op)
    out["op_date"] = op.op_date.isoformat()
    for key in (
        "quantity",
        "unit_price",
        "amount",
        "fees",
        "portfolio_value",
        "offered",
        "tax_withheld",
    ):
        out[key] = None if out[key] is None else plain(out[key])  # jamais de float en JSON
    # Montant de l'opération : titres achetés frais compris, vente nette de frais, ou montant.
    if op.op_type in TRADES:
        out["total"] = money(
            abs(cash_movement(op))
            if op.op_type == "vente"
            else op.quantity * op.unit_price + op.fees
        )
        out["gross"] = money(op.quantity * op.unit_price)  # montant hors frais (valeur saisie)
    else:
        out["total"] = money(op.amount)
    return out


def price_text(value: Decimal) -> str:
    """Cours en chaîne ; un cours converti d'une autre devise est limité à 8 décimales."""
    if value.as_tuple().exponent < -8:
        value = value.quantize(Decimal("0.00000001"))
    return plain(value)


def pct(value: Decimal | None) -> str | None:
    """Rapport -> pourcentage arrondi au centième, en chaîne ("8.04" pour 8,04 %)."""
    return None if value is None else money(value * 100)


def money_or_none(value: Decimal | None) -> str | None:
    return None if value is None else money(value)


def reorder(table: str, body: OrderIn) -> None:
    with db.connection() as conn:
        if not repository.reorder(conn, table, body.ids):
            raise HTTPException(
                status_code=409, detail="La liste a changé entre-temps : recharge la page."
            )


def backup_before_delete() -> None:
    path = db.db_path()
    # sauvegardes/ du dossier des données pour toutes les bases, profils compris (D-098, D-119).
    folder = path.parent.parent if path.parent.name == profiles.PROFILES_SUBDIR else path.parent
    try:
        backup.backup(path, folder / "sauvegardes", label="avant-suppression")
    except (OSError, sqlite3.Error) as err:
        raise HTTPException(
            status_code=500, detail=f"Sauvegarde impossible, rien n'est supprimé : {err}"
        ) from err


def offline_state() -> dict:
    """Comptes, actifs, opérations et positions valorisées au dernier cours en cache."""
    with db.connection() as conn:
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


def by_asset_eur(state: dict, attr: str) -> dict[int, Decimal]:
    """Somme par actif (tous comptes) de la valeur ou du coût des positions, en EUR."""
    out: dict[int, Decimal] = {}
    for p in state["valued"]:
        amount = convert(getattr(p, attr), p.currency, "EUR", state["rates"])
        if amount is not None:
            out[p.asset_id] = out.get(p.asset_id, ZERO) + amount
    return out


def quantity_text(value: Decimal) -> str:
    """Quantité de jetons, 8 décimales au plus, sans notation « 1E+1 »."""
    return format(value.quantize(Decimal("1e-8")).normalize(), "f")


def last_months(today: date, count: int) -> list[date]:
    index = today.year * 12 + today.month - 1
    return [date((index - k) // 12, (index - k) % 12 + 1, 1) for k in range(count - 1, -1, -1)]
