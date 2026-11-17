"""Routes des comptes et des actifs : liste, ajout, modification, ordre (D-070), suppression
avec ses opérations (D-074), comparaison au relevé de la banque (D-063), valeurs saisies d'un
actif sans cours public (D-056 : la valeur totale de la ligne à une date, rangée comme un prix,
rien ne part sur le réseau)."""

import sqlite3
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query

from app import custody, db, prices, reconcile, repository, valuations
from app.api.common import account_out, asset_out, backup_before_delete, conflict, reorder
from app.api.deps import HttpClient
from app.models import CASH_ENTERED, Account, Asset
from app.portfolio import TRADES, PositionError, RateHistory, compute_positions, with_transfers
from app.schemas import AccountIn, AssetIn, OrderIn, ReconcileIn, ValuationIn, money

router = APIRouter()


@router.get("/api/accounts")
def list_accounts() -> list[dict]:
    with db.connect() as conn:
        return [account_out(a) for a in repository.list_accounts(conn)]


@router.put("/api/accounts/order", status_code=204)
def reorder_accounts(body: OrderIn) -> None:
    """Range les comptes dans l'ordre donné (glisser-déposer de « Gérer », D-070)."""
    reorder("account", body)


def _set_custody(conn, account: Account, body: AccountIn, created: bool) -> None:
    """Garde et « à l'étranger » (D-104). Un champ absent est deviné à la création (nom,
    établissement) et gardé à la modification, sauf une garde à préciser, devinée à nouveau.
    La garde n'existe que pour un compte crypto ; un portefeuille personnel n'est jamais « à
    l'étranger » (rien à déclarer)."""
    fields = body.model_fields_set
    if account.kind != "crypto":
        custody_ = None
    elif "custody" in fields:
        custody_ = body.custody
    else:
        custody_ = account.custody or custody.guess_custody(body.name, body.institution)
    if custody_ == "wallet":
        abroad = False
    elif body.abroad is not None:
        abroad = body.abroad
    elif created:
        abroad = custody.guess_abroad(body.name, body.institution, custody_)
    else:
        abroad = account.abroad
    repository.set_account_custody(conn, account.id, custody_, abroad)


@router.post("/api/accounts", status_code=201)
def create_account(body: AccountIn) -> dict:
    try:
        with db.connect() as conn:
            account = repository.create_account(
                conn, body.name, body.kind, body.institution, body.currency, body.cash
            )
            repository.set_account_tax_info(conn, account.id, body.opened_on, body.deposits_before)
            _set_custody(conn, account, body, created=True)
            account = repository.get_account(conn, account.id)
    except sqlite3.IntegrityError as err:
        raise conflict(err) from err
    return account_out(account)


@router.patch("/api/accounts/{account_id}")
def update_account(account_id: int, body: AccountIn) -> dict:
    try:
        with db.connect() as conn:
            if repository.get_account(conn, account_id) is None:
                raise HTTPException(status_code=404, detail="Compte inconnu.")
            account = repository.update_account(
                conn, account_id, body.name, body.kind, body.institution, body.currency, body.cash
            )
            repository.set_account_tax_info(conn, account_id, body.opened_on, body.deposits_before)
            _set_custody(conn, account, body, created=False)
            account = repository.get_account(conn, account_id)
    except sqlite3.IntegrityError as err:
        raise conflict(err) from err
    return account_out(account)


# Suppression d'un compte ou d'un actif qui a des opérations (D-074) : `operations` dit quoi
# en faire, les supprimer aussi ou les déplacer (`vers` = compte ou actif d'arrivée, même
# devise). Sans ce choix, refus (409) : rien ne disparaît par accident. Une sauvegarde de la
# base est faite juste avant, dans le dossier des sauvegardes.
OpsChoice = Annotated[Literal["supprimer", "deplacer"] | None, Query()]


def _remove(kind: str, item_id: int, operations: str | None, vers: int | None) -> None:
    noun = "Compte" if kind == "account" else "Actif"
    get = repository.get_account if kind == "account" else repository.get_asset
    with db.connect() as conn:
        item = get(conn, item_id)
        if item is None:
            raise HTTPException(status_code=404, detail=f"{noun} inconnu.")
        used = repository.count_operations(conn, **{f"{kind}_id": item_id})
    conn.close()
    if not used:
        with db.connect() as conn:
            (repository.delete_account if kind == "account" else repository.delete_asset)(
                conn, item_id
            )
        return
    if operations is None:
        raise HTTPException(
            status_code=409,
            detail=f"{noun} utilisé par {used} opération(s) : choisis de les supprimer ou de les "
            "déplacer.",
        )
    target = None
    if operations == "deplacer":
        with db.connect() as conn:
            target = get(conn, vers) if vers is not None and vers != item_id else None
        conn.close()
        if target is None:
            raise HTTPException(status_code=422, detail=f"{noun} d'arrivée manquant ou inconnu.")
        if target.currency != item.currency:
            raise HTTPException(
                status_code=422,
                detail=f"Déplacement impossible : {item.currency} vers {target.currency}, "
                "les montants des opérations ne seraient plus dans la bonne devise.",
            )
    backup_before_delete()
    try:
        with db.connect() as conn:
            repository.remove_with_operations(
                conn, f"{kind}_id", item_id, target.id if target else None
            )
    except PositionError as err:
        raise HTTPException(status_code=422, detail=f"Déplacement refusé : {err}") from err


@router.delete("/api/accounts/{account_id}", status_code=204)
def delete_account(account_id: int, operations: OpsChoice = None, vers: int | None = None) -> None:
    _remove("account", account_id, operations, vers)


@router.post("/api/accounts/{account_id}/reconcile")
def reconcile_account(account_id: int, body: ReconcileIn) -> dict:
    """Compare le compte au relevé de la banque (D-063). Lecture seule : rien n'est enregistré.

    Chaque ligne cotée du compte est renvoyée, vérifiée si sa quantité est fournie ; une ligne à
    valeur saisie (D-056) n'a pas de quantité à comparer, ses « parts » sont internes à l'app."""
    with db.connect() as conn:
        account = repository.get_account(conn, account_id)
        if account is None:
            raise HTTPException(status_code=404, detail="Compte inconnu.")
        assets = {a.id: a for a in repository.list_assets(conn)}
        # Arrivées des transferts comprises (D-085) : des titres reçus d'un autre compte de
        # l'app sont bien détenus ici, et leur vente n'est pas une vente à découvert (D-138).
        ops = [
            o
            for o in with_transfers(repository.list_operations(conn))
            if o.account_id == account_id
        ]
    conn.close()
    try:
        positions = compute_positions(ops)
    except PositionError as err:
        raise HTTPException(status_code=409, detail=str(err)) from err

    def qty(d: Decimal) -> str:
        return format(d.normalize(), "f") if d else "0"

    cash = None
    if body.cash is not None:
        check = reconcile.cash_check(
            account.cash, body.cash, account.kind == "livret", account.kind in CASH_ENTERED
        )
        fix = check["correction"]
        cash = {
            **{k: money(check[k]) for k in ("app", "bank", "gap")},
            "ok": check["ok"],
            "causes": check["causes"],
            "correction": fix and {"kind": fix["kind"], "amount": money(fix["amount"])},
        }
    lines = []
    for p in sorted(positions, key=lambda p: list(assets).index(p.asset_id)):
        asset = assets[p.asset_id]
        line = {"asset_id": asset.id, "asset": asset.name, "manual": asset.manual}
        line["app"] = qty(p.quantity)
        bank = body.quantities.get(asset.id)
        if bank is not None and not asset.manual:
            check = reconcile.line_check(p.quantity, bank)
            line |= {
                "bank": qty(check["bank"]),
                "gap": qty(check["gap"]),
                "ok": check["ok"],
                "causes": check["causes"],
            }
        lines.append(line)
    checked = [x for x in lines if "ok" in x] + ([cash] if cash else [])
    return {
        "account": account.name,
        "kind": account.kind,
        "currency": account.currency,
        "cash": cash,
        "lines": lines,
        "ok": bool(checked) and all(x["ok"] for x in checked),
    }


@router.get("/api/assets")
def list_assets() -> list[dict]:
    """Actifs dans l'ordre choisi. `sold_out` : des opérations, mais plus rien de détenu nulle
    part (revendu, transféré hors de l'app) ; « Gérer » les range à part (D-085)."""
    with db.connect() as conn:
        operations = repository.list_operations(conn)
        try:
            held = {p.asset_id for p in compute_positions(operations)}
        except PositionError:
            held = {o.asset_id for o in operations}  # opérations incohérentes : rien de masqué
        traded = {o.asset_id for o in operations if o.op_type in TRADES}
        return [
            {**asset_out(a), "sold_out": a.id in traded and a.id not in held}
            for a in repository.list_assets(conn)
        ]


@router.put("/api/assets/order", status_code=204)
def reorder_assets(body: OrderIn) -> None:
    """Range les actifs dans l'ordre donné (glisser-déposer de « Gérer », D-070)."""
    reorder("asset", body)


@router.post("/api/assets", status_code=201)
def create_asset(body: AssetIn) -> dict:
    try:
        with db.connect() as conn:
            asset = repository.create_asset(
                conn,
                body.name,
                body.asset_class,
                body.isin,
                body.ticker,
                body.coingecko_id,
                body.currency,
                body.color,
            )
            repository.set_asset_ter(conn, asset.id, body.ter)
            repository.set_asset_category(conn, asset.id, body.category)
            asset = repository.get_asset(conn, asset.id)
    except sqlite3.IntegrityError as err:
        raise conflict(err) from err
    return asset_out(asset)


@router.patch("/api/assets/{asset_id}")
def update_asset(asset_id: int, body: AssetIn, client: HttpClient) -> dict:
    """Corrige un actif (nom, classe, ISIN, ticker...). Nouvelle devise de cotation alors que
    des opérations sont saisies : elles sont converties au taux BCE de leur jour (D-083)."""
    try:
        with db.connect() as conn:
            asset = repository.get_asset(conn, asset_id)
            if asset is None:
                raise HTTPException(status_code=404, detail="Actif inconnu.")
            changed = body.currency != asset.currency
            if changed:
                _convert_asset(conn, asset, body.currency, client)
            asset = repository.update_asset(
                conn,
                asset_id,
                body.name,
                body.asset_class,
                body.isin,
                body.ticker,
                body.coingecko_id,
                body.currency,
                body.color,
            )
            repository.set_asset_ter(conn, asset_id, body.ter)
            repository.set_asset_category(conn, asset_id, body.category)
            asset = repository.get_asset(conn, asset_id)
            if changed and not asset.ticker and not asset.coingecko_id:
                valuations.refresh_latest(conn, asset)  # valeur saisie, dans la nouvelle devise
    except sqlite3.IntegrityError as err:
        raise conflict(err) from err
    return asset_out(asset)


def _convert_asset(conn, asset: Asset, currency: str, client) -> None:
    """Passe les opérations, achats programmés et valeurs saisies d'un actif dans sa nouvelle
    devise (D-083). Taux BCE de Frankfurter : seuls les codes devise partent sur le réseau."""
    first = conn.execute(
        "SELECT min(op_date) FROM operation WHERE asset_id = ?", (asset.id,)
    ).fetchone()[0]
    if first is None:
        conn.execute("DELETE FROM price WHERE asset_id = ? AND source != 'manuel'", (asset.id,))
        conn.execute(
            "DELETE FROM price_history WHERE asset_id = ? AND source != 'manuel'", (asset.id,)
        )
        return
    currencies = {asset.currency, currency} - {"EUR"}
    start = date.fromisoformat(first)
    prices.update_fx_history(conn, dict.fromkeys(currencies, start), client, today=date.today())
    fx = RateHistory(prices.stored_rates(conn, currencies), prices.rate_series(conn, currencies))
    try:
        repository.convert_asset_currency(conn, asset.id, currency, fx)
    except LookupError:
        raise HTTPException(
            status_code=503,
            detail=f"Taux de change {asset.currency} → {currency} indisponible : réessaie plus "
            "tard (connexion à la BCE nécessaire pour convertir les opérations).",
        ) from None


@router.delete("/api/assets/{asset_id}", status_code=204)
def delete_asset(asset_id: int, operations: OpsChoice = None, vers: int | None = None) -> None:
    _remove("asset", asset_id, operations, vers)


def _manual_asset(conn, asset_id: int):
    asset = repository.get_asset(conn, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Actif inconnu.")
    if not asset.manual:
        raise HTTPException(
            status_code=422, detail="Cet actif a un cours en ligne : sa valeur n'est pas saisie."
        )
    return asset


def _valuations_out(conn, asset) -> list[dict]:
    return [
        {"date": day.isoformat(), "value": money(value), "currency": asset.currency}
        for day, value in valuations.list_valuations(conn, asset)
    ]


@router.get("/api/assets/{asset_id}/valuations")
def list_asset_valuations(asset_id: int) -> list[dict]:
    with db.connect() as conn:
        out = _valuations_out(conn, _manual_asset(conn, asset_id))
    conn.close()
    return out


@router.post("/api/assets/{asset_id}/valuations", status_code=201)
def save_asset_valuation(asset_id: int, body: ValuationIn) -> list[dict]:
    """Enregistre la valeur de la ligne ce jour-là (remplace celle du même jour)."""
    try:
        with db.connect() as conn:
            asset = _manual_asset(conn, asset_id)
            valuations.save_valuation(conn, asset, body.value_date, body.value)
            out = _valuations_out(conn, asset)
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    conn.close()
    return out


@router.delete("/api/assets/{asset_id}/valuations/{value_date}", status_code=204)
def delete_asset_valuation(asset_id: int, value_date: date) -> None:
    with db.connect() as conn:
        if not valuations.delete_valuation(conn, _manual_asset(conn, asset_id), value_date):
            raise HTTPException(status_code=404, detail="Aucune valeur saisie ce jour-là.")
