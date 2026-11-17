"""Routes des opérations : liste, ajout, correction, suppression."""

from decimal import Decimal

from fastapi import APIRouter, HTTPException

from app import db, defi_sync, repository, valuations
from app.api.common import operation_out
from app.models import Operation
from app.portfolio import ZERO, PositionError
from app.schemas import CashIn, OperationIn, TradeIn

router = APIRouter()


@router.get("/api/operations")
def list_operations() -> list[dict]:
    with db.connect() as conn:
        return [operation_out(op) for op in repository.list_operations(conn)]


def save_operation(conn, body: TradeIn | CashIn, operation_id: int | None = None):
    """Ajoute (operation_id vide) ou corrige une opération après les vérifications communes.
    La devise est celle de l'actif pour un achat ou une vente, celle du compte sinon."""
    account = repository.get_account(conn, body.account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Compte inconnu.")
    if isinstance(body, CashIn):
        currency = account.currency
        if body.asset_id is not None:  # dividende : dans la devise de l'actif, comme l'import
            asset = repository.get_asset(conn, body.asset_id)
            if asset is None:
                raise HTTPException(status_code=404, detail="Actif inconnu.")
            currency = asset.currency
        fields = (body.account_id, body.asset_id, body.op_date, body.op_type, None, None)
        fields += (body.amount, ZERO, currency, body.note)
    else:
        asset = repository.get_asset(conn, body.asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="Actif inconnu.")
        quantity, unit_price = body.quantity, body.unit_price
        if quantity is None:
            # Valeur saisie (D-056) : le montant devient des parts au dernier prix connu.
            if not asset.manual:
                raise HTTPException(status_code=422, detail="Indique la quantité.")
            unit_price = valuations.unit_on(conn, asset.id, body.op_date)
            quantity = (body.amount / unit_price).quantize(Decimal("0.00000001"))
        fields = (body.account_id, body.asset_id, body.op_date, body.op_type, quantity)
        fields += (unit_price, None, body.fees, asset.currency, body.note)
    if operation_id is None:
        op = repository.create_operation(conn, *fields)
    else:
        op = repository.update_operation(conn, operation_id, *fields)
    # Seule une vente d'un compte crypto garde ses renseignements fiscaux (D-066).
    crypto_sale = isinstance(body, TradeIn) and account.kind == "crypto" and body.op_type == "vente"
    swap = crypto_sale and body.crypto_swap
    value = body.portfolio_value if crypto_sale else None
    if (swap, value) != (op.crypto_swap, op.portfolio_value):
        op = repository.set_sale_tax_info(conn, op.id, swap, value)
    offered = body.offered if isinstance(body, TradeIn) else None
    withheld = body.tax_withheld if isinstance(body, CashIn) else None
    if offered is not None and offered > op.quantity * op.unit_price + op.fees:
        raise HTTPException(
            status_code=422, detail="La part offerte dépasse le montant de l'achat."
        )
    kind = body.reward_kind if offered is not None else None
    if (offered, withheld, kind) != (op.offered, op.tax_withheld, op.reward_kind):
        op = repository.set_offered_withheld(conn, op.id, offered, withheld, kind)
    if body.to_account_id is not None and repository.get_account(conn, body.to_account_id) is None:
        raise HTTPException(status_code=404, detail="Compte de destination inconnu.")
    if (body.to_account_id, body.destination) != (op.to_account_id, op.destination):
        op = repository.set_transfer(conn, op.id, body.to_account_id, body.destination)
    return op


@router.post("/api/operations", status_code=201)
def create_operation(body: OperationIn) -> dict:
    # Le "with" valide la transaction SQL si tout va bien, l'annule (rollback) sinon.
    try:
        with db.connect() as conn:
            op = save_operation(conn, body)
    except PositionError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    return operation_out(op)


def _editable_or_raise(op: Operation | None) -> None:
    """404 si l'opération n'existe pas ; 409 pour des intérêts Aave estimés par l'app (D-095),
    recalculés à chaque passage : on les corrige avec « Vérifier » dans l'onglet Rendements
    crypto."""
    if op is None:
        raise HTTPException(status_code=404, detail="Opération inconnue.")
    if op.source == defi_sync.SOURCE:
        raise HTTPException(
            status_code=409,
            detail="Intérêts Aave calculés par l'app : corrige-les avec « Vérifier » dans "
            "l'onglet Rendements crypto.",
        )


@router.patch("/api/operations/{operation_id}")
def update_operation(operation_id: int, body: OperationIn) -> dict:
    """Corrige une opération (bouton « Modifier »), positions revérifiées comme à l'ajout."""
    try:
        with db.connect() as conn:
            _editable_or_raise(repository.get_operation(conn, operation_id))
            op = save_operation(conn, body, operation_id)
    except PositionError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    return operation_out(op)


@router.delete("/api/operations/{operation_id}", status_code=204)
def delete_operation(operation_id: int) -> None:
    try:
        with db.connect() as conn:
            _editable_or_raise(repository.get_operation(conn, operation_id))
            repository.delete_operation(conn, operation_id)
    except PositionError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
