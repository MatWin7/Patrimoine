"""Route des repères fiscaux, en estimation (D-059, PER D-064, crypto D-066). Calculés sur le
cache des cours et des taux, sans aucun appel réseau."""

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, HTTPException

from app import custody, db, prices, tax
from app.api.common import offline_state, pct
from app.portfolio import ZERO, convert, eur_costs, ratio
from app.schemas import money, plain

router = APIRouter()


@router.get("/api/tax")
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
    state = offline_state()
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
                    "used_pct": pct(ratio(st["deposits"], st["cap"])),
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
