"""Routes des dépôts Aave (D-095) et des emprunts Aave (D-108). Réseau : les taux publics d'un
marché (chaîne, pool, jeton), au plus une fois par jour, et les listes de chaînes et de marchés
à la demande. Jamais une adresse de portefeuille."""

import sqlite3
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Annotated

import httpx2
from fastapi import APIRouter, HTTPException, Query

from app import config, db, defi, defi_sync, prices, providers, repository
from app.api.common import backup_before_delete, money_or_none, offline_state, pct, quantity_text
from app.api.deps import HttpClient
from app.api.operations import save_operation
from app.models import Asset, Operation
from app.portfolio import ZERO, PositionError, RateHistory, compute_positions, convert
from app.schemas import (
    DefiBorrowIn,
    DefiBorrowMoveIn,
    DefiCheckIn,
    DefiLineIn,
    DefiMoveIn,
    TradeIn,
    money,
    plain,
)

router = APIRouter()


def update_defi(conn: sqlite3.Connection, client: httpx2.Client) -> dict[int, str]:
    """Taux du jour des marchés suivis puis intérêts des mois clos ; {ligne: avertissement}."""
    today = date.today()
    errors = defi_sync.refresh_rates(conn, client, today)
    conn.commit()
    return errors | defi_sync.safe_sync(conn, today)


@router.get("/api/aave/chains")
def aave_chains(client: HttpClient) -> list[dict]:
    try:
        rows = defi_sync.chains(client, date.today())
    except providers.ProviderError as err:
        raise HTTPException(status_code=502, detail=f"Aave injoignable : {err}") from err
    return [{"chain_id": c, "name": n} for c, n in rows]


@router.get("/api/aave/reserves")
def aave_reserves(client: HttpClient, chain_id: Annotated[int, Query(gt=0)]) -> list[dict]:
    """Jetons de chaque marché de la chaîne : taux de dépôt et d'emprunt du jour, seuil."""
    try:
        rows = defi_sync.reserves(client, chain_id, date.today())
    except providers.ProviderError as err:
        raise HTTPException(status_code=502, detail=f"Aave injoignable : {err}") from err
    return [
        r
        | {
            "apy_pct": pct(r["apy"]),
            "apy": plain(r["apy"]),
            "borrow_apy_pct": pct(r["borrow_apy"]),
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
        "apy_today_pct": pct(rates[max(rates)]) if rates else None,
        "apy_12m_pct": pct(defi.average_rate(rates, start, today)),
        "first_rate": min(rates).isoformat() if rates else None,
        "held": quantity_text(held),
        "value": eur(held),
        "interest": quantity_text(sum((o.quantity for o in recorded), ZERO)),
        "interest_value": money(
            sum(
                (state["fx"].to_eur(o.offered, o.currency, o.op_date) or ZERO for o in recorded),
                ZERO,
            )
        ),
        "current": quantity_text(est.current) if est else None,
        "current_value": eur(est.current) if est else None,
        "estimated": quantity_text(est.balance) if est else None,
        "extrapolated": bool(est and est.extrapolated),
        "unexplained": quantity_text(est.unexplained) if est and est.unexplained else None,
        "months": [
            {
                "month": f"{o.op_date:%Y-%m}",
                "quantity": quantity_text(o.quantity),
                "value": money_or_none(state["fx"].to_eur(o.offered, o.currency, o.op_date)),
                "verified": f"{o.op_date:%Y-%m}" in verified,
            }
            for o in sorted(recorded, key=lambda o: o.op_date, reverse=True)
        ],
        "checks": [
            {"date": d.isoformat(), "quantity": quantity_text(q)}
            for d, q in sorted(checks.items(), reverse=True)
        ],
        "warning": warning,
    }


def _defi_lines_out(conn, warnings: dict[int, str]) -> list[dict]:
    state = offline_state()
    today = date.today()
    return [
        _defi_line_out(conn, line, state, today, warnings.get(line.id))
        for line in defi_sync.list_lines(conn)
    ]


@router.get("/api/aave/lines")
def aave_lines(client: HttpClient) -> list[dict]:
    """Dépôts suivis : taux, intérêts enregistrés et estimés, soldes vérifiés."""
    with db.connect() as conn:
        warnings = update_defi(conn, client)
        out = _defi_lines_out(conn, warnings)
    conn.close()
    return out


@router.post("/api/aave/lines", status_code=201)
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
        warnings = update_defi(conn, client)
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
    if asset.coingecko_id and not asset.fictive and day < date.today():  # démo : D-140
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
    return save_operation(conn, trade)


@router.post("/api/aave/lines/{line_id}/moves", status_code=201)
def move_aave_line(line_id: int, body: DefiMoveIn, client: HttpClient) -> list[dict]:
    """Dépôt ou retrait sur une ligne suivie, puis intérêts recalculés."""
    try:
        with db.connect() as conn:
            line = defi_sync.get_line(conn, line_id)
            if line is None:
                raise HTTPException(status_code=404, detail="Ligne suivie inconnue.")
            _defi_move(conn, line.account_id, line.asset_id, body, client)
            conn.commit()
            out = _defi_lines_out(conn, update_defi(conn, client))
    except PositionError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    conn.close()
    return out


@router.delete("/api/aave/lines/{line_id}", status_code=204)
def delete_aave_line(line_id: int) -> None:
    """Arrête le suivi : les intérêts estimés de la ligne sont retirés (sauvegarde avant)."""
    with db.connect() as conn:
        if defi_sync.get_line(conn, line_id) is None:
            raise HTTPException(status_code=404, detail="Ligne suivie inconnue.")
    conn.close()
    backup_before_delete()
    try:
        with db.connect() as conn:
            defi_sync.delete_line(conn, line_id)
            compute_positions(repository.list_operations(conn))
    except PositionError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err


@router.post("/api/aave/lines/{line_id}/checks")
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
        "estimated": quantity_text(estimated) if estimated is not None else None,
        "gap": quantity_text(body.quantity - estimated) if estimated is not None else None,
        "lines": out,
    }


@router.delete("/api/aave/lines/{line_id}/checks/{check_date}", status_code=204)
def delete_aave_check(line_id: int, check_date: date) -> None:
    with db.connect() as conn:
        if not defi_sync.delete_check(conn, line_id, check_date):
            raise HTTPException(status_code=404, detail="Solde vérifié inconnu.")
        conn.commit()
        defi_sync.safe_sync(conn, date.today())
    conn.close()


# --- Emprunts et facteur de santé (D-108) ---------------------------------------------------
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


def borrows_report(conn: sqlite3.Connection, today: date) -> dict:
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
                "balance": quantity_text(balance),
                "value_eur": money_or_none(value),
                "apy_today_pct": pct(apy),
                "cost_year_eur": money_or_none(cost),
                "extrapolated": bool(est and est.extrapolated),
                "checks": [
                    {"date": d.isoformat(), "quantity": quantity_text(q)}
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
    report = borrows_report(conn, date.today())
    return {
        "borrows": report["borrows"],
        "markets": report["markets"],
        "debt_eur": money_or_none(report["debt_eur"]),
        "cost_eur": money(report["cost_eur"]),
    }


@router.get("/api/aave/borrows")
def aave_borrows(client: HttpClient) -> dict:
    """Emprunts suivis : dette estimée, taux d'emprunt du jour, coût annuel, facteur de santé."""
    with db.connect() as conn:
        update_defi(conn, client)
        conn.commit()
        out = _borrows_out(conn)
    conn.close()
    return out


@router.post("/api/aave/borrows", status_code=201)
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
        update_defi(conn, client)
        conn.commit()
        out = _borrows_out(conn)
    conn.close()
    return out


@router.post("/api/aave/borrows/{borrow_id}/moves", status_code=201)
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
                    detail=f"Remboursement supérieur à la dette estimée ({quantity_text(owed)}).",
                )
        defi_sync.add_borrow_flow(conn, borrow_id, body.op_date, quantity)
        conn.commit()
        out = _borrows_out(conn)
    conn.close()
    return out


@router.post("/api/aave/borrows/{borrow_id}/checks")
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
        "estimated": None if estimated is None else quantity_text(estimated),
        "gap": None if estimated is None else quantity_text(body.quantity - estimated),
        **out,
    }


@router.delete("/api/aave/borrows/{borrow_id}/checks/{check_date}", status_code=204)
def delete_aave_borrow_check(borrow_id: int, check_date: date) -> None:
    with db.connect() as conn:
        if not defi_sync.delete_borrow_check(conn, borrow_id, check_date):
            raise HTTPException(status_code=404, detail="Dette vérifiée inconnue.")
    conn.close()


@router.delete("/api/aave/borrows/{borrow_id}", status_code=204)
def delete_aave_borrow(borrow_id: int) -> None:
    """Arrête le suivi de l'emprunt, sauvegarde de la base juste avant (D-074)."""
    with db.connect() as conn:
        if defi_sync.get_borrow(conn, borrow_id) is None:
            raise HTTPException(status_code=404, detail="Emprunt inconnu.")
    conn.close()
    backup_before_delete()
    with db.connect() as conn:
        defi_sync.delete_borrow(conn, borrow_id)
    conn.close()
