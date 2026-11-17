"""Routes des achats programmés (D-055). Rien n'est enregistré tout seul : chaque échéance
attend que l'utilisateur la valide avec le prix et la quantité réellement exécutés, ou
l'ignore. Aucune requête réseau ici."""

from datetime import date

from fastapi import APIRouter, HTTPException

from app import db, prices, repository
from app import recurring as dca
from app.api.common import operation_out, price_text
from app.portfolio import PositionError
from app.schemas import RecurringConfirmIn, RecurringIn, RecurringSkipIn, plain

router = APIRouter()


def _recurring_out(r: dict, accounts, assets) -> dict:
    asset = assets[r["asset_id"]]
    return {
        **r,
        "amount": plain(r["amount"]),
        "start_date": r["start_date"].isoformat(),
        "last_due": r["last_due"].isoformat() if r["last_due"] else None,
        "account": accounts[r["account_id"]].name,
        "asset": asset.name,
        "currency": asset.currency,
    }


def _pending(r: dict, today: date) -> list[date]:
    return dca.pending_dates(r["start_date"], r["frequency"], r["day"], r["last_due"], today)


@router.get("/api/recurring")
def list_recurring() -> dict:
    """Achats programmés et échéances à valider. Prix proposé : clôture du jour de l'échéance
    (ou d'avant) si l'historique est en cache, sinon dernier cours connu ; sans appel réseau."""
    today = date.today()
    with db.connect() as conn:
        accounts = {a.id: a for a in repository.list_accounts(conn)}
        assets = {a.id: a for a in repository.list_assets(conn)}
        items = repository.list_recurring(conn)
        last = {r["asset_id"]: prices.latest_price(conn, r["asset_id"]) for r in items}
        due_dates = {r["id"]: _pending(r, today) for r in items}
        closes = {
            (r["asset_id"], due): prices.close_on(conn, r["asset_id"], due)
            for r in items
            for due in due_dates[r["id"]]
        }
    conn.close()
    pending = []
    for r in items:
        asset = assets[r["asset_id"]]
        price = last[r["asset_id"]]
        latest = price.price if price and price.currency == asset.currency else None
        for due in due_dates[r["id"]]:
            close = closes[(r["asset_id"], due)]
            known = close[0] if close and close[1] == asset.currency else latest
            quantity = dca.suggested_quantity(r["amount"], known, asset.asset_class)
            pending.append(
                {
                    "recurring_id": r["id"],
                    "frequency": r["frequency"],
                    "due_date": due.isoformat(),
                    "account": accounts[r["account_id"]].name,
                    "asset": asset.name,
                    "amount": plain(r["amount"]),
                    "currency": asset.currency,
                    "price": None if known is None else price_text(known),
                    "quantity": None if quantity is None else plain(quantity),
                }
            )
    pending.sort(key=lambda p: (p["due_date"], p["recurring_id"]))
    return {"items": [_recurring_out(r, accounts, assets) for r in items], "pending": pending}


def _save_recurring(body: RecurringIn, recurring_id: int | None = None) -> dict:
    with db.connect() as conn:
        if recurring_id is not None and repository.get_recurring(conn, recurring_id) is None:
            raise HTTPException(status_code=404, detail="Achat programmé inconnu.")
        if repository.get_account(conn, body.account_id) is None:
            raise HTTPException(status_code=404, detail="Compte inconnu.")
        if repository.get_asset(conn, body.asset_id) is None:
            raise HTTPException(status_code=404, detail="Actif inconnu.")
        r = repository.save_recurring(
            conn,
            recurring_id,
            body.account_id,
            body.asset_id,
            body.amount,
            body.frequency,
            body.day,
            body.start_date,
            body.note,
        )
        accounts = {a.id: a for a in repository.list_accounts(conn)}
        assets = {a.id: a for a in repository.list_assets(conn)}
    return _recurring_out(r, accounts, assets)


@router.post("/api/recurring", status_code=201)
def create_recurring(body: RecurringIn) -> dict:
    return _save_recurring(body)


@router.patch("/api/recurring/{recurring_id}")
def update_recurring(recurring_id: int, body: RecurringIn) -> dict:
    return _save_recurring(body, recurring_id)


@router.delete("/api/recurring/{recurring_id}", status_code=204)
def delete_recurring(recurring_id: int) -> None:
    """Supprime le modèle ; les achats déjà validés restent des opérations ordinaires."""
    with db.connect() as conn:
        if not repository.delete_recurring(conn, recurring_id):
            raise HTTPException(status_code=404, detail="Achat programmé inconnu.")


def _oldest_due(conn, recurring_id: int, due_date: date) -> dict:
    """Le modèle, si `due_date` est bien sa plus ancienne échéance en attente (traitées dans
    l'ordre, pour qu'aucune ne soit sautée sans être vue)."""
    r = repository.get_recurring(conn, recurring_id)
    if r is None:
        raise HTTPException(status_code=404, detail="Achat programmé inconnu.")
    due = _pending(r, date.today())
    if not due or due[0] != due_date:
        expected = due[0].strftime("%d/%m/%Y") if due else "aucune"
        raise HTTPException(
            status_code=409,
            detail=f"Échéance à traiter d'abord : {expected}.",
        )
    return r


@router.post("/api/recurring/{recurring_id}/confirm", status_code=201)
def confirm_recurring(recurring_id: int, body: RecurringConfirmIn) -> dict:
    """Enregistre l'achat de l'échéance, avec le prix et la quantité réellement exécutés."""
    try:
        with db.connect() as conn:
            r = _oldest_due(conn, recurring_id, body.due_date)
            asset = repository.get_asset(conn, r["asset_id"])
            op = repository.create_operation(
                conn,
                r["account_id"],
                r["asset_id"],
                body.op_date,
                "achat",
                body.quantity,
                body.unit_price,
                None,
                body.fees,
                asset.currency,
                r["note"] or "Achat programmé",
            )
            repository.mark_recurring_due(conn, recurring_id, body.due_date)
    except PositionError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    return operation_out(op)


@router.post("/api/recurring/{recurring_id}/skip", status_code=204)
def skip_recurring(recurring_id: int, body: RecurringSkipIn) -> None:
    """Échéance ignorée (achat non passé cette fois-là, ou déjà saisi autrement)."""
    with db.connect() as conn:
        _oldest_due(conn, recurring_id, body.due_date)
        repository.mark_recurring_due(conn, recurring_id, body.due_date)
