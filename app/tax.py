"""Repères fiscaux français, en ESTIMATION et jamais en conseil (D-059). Fonctions pures.

Les règles changent avec les lois de finances : chaque chiffre affiché dit la règle utilisée
et sa date. Rien n'est déclaré ni envoyé nulle part.
"""

from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.models import Account, Operation
from app.portfolio import TRADES, RateHistory, convert, implied_deposits

ZERO = Decimal("0")
HUNDRED = Decimal("100")
RULES_DATE = "2026"  # année des règles ci-dessous, affichée avec chaque estimation
PEA_CAP = Decimal("150000")  # plafond des versements d'un PEA (hors PEA-PME)
PEA_YEARS = 5  # après 5 ans, un retrait ne clôture plus le plan ; gains exonérés d'impôt
AV_YEARS = 8  # après 8 ans, abattement annuel sur les gains des rachats d'une assurance-vie
PFU_PCT = Decimal("30")  # prélèvement forfaitaire unique : 12,8 % d'impôt + 17,2 % sociaux
PFU_2026_PCT = Decimal("31.4")  # dès 2026 : CSG + 1,4 point, prélèvements sociaux à 18,6 %
PFU_IR_PCT = Decimal("12.8")  # part impôt du PFU ; le reste = prélèvements sociaux
TMI_PCTS = (Decimal("0"), Decimal("11"), Decimal("30"), Decimal("41"), Decimal("45"))  # barème
PER_LOW_TMI_PCT = Decimal("11")  # à cette tranche ou moins, déduire un versement rapporte peu
CRYPTO_EXEMPT = Decimal("305")  # cessions crypto de l'année jusqu'à ce total : pas d'impôt


def pfu_default(year: int, gains: bool = False) -> Decimal:
    """Taux du prélèvement forfaitaire proposé pour une année (le taux saisi l'emporte).

    `gains` : plus-values de titres (comptes-titres) et de cryptos, « revenus du patrimoine »
    déclarés l'année suivante : la hausse de la CSG de la LFSS 2026 les touche dès les revenus
    2025 (D-081). Dividendes, intérêts, retraits de PEA (« produits de placement ») : dès 2026.
    """
    return PFU_2026_PCT if year >= (2025 if gains else 2026) else PFU_PCT


def anniversary(start: date, years: int) -> date:
    try:
        return start.replace(year=start.year + years)
    except ValueError:  # 29 février
        return start.replace(year=start.year + years, day=28)


def opened_on(account: Account, operations: Iterable[Operation]) -> date | None:
    """Date d'ouverture saisie, sinon date de la première opération du compte (approchée)."""
    if account.opened_on:
        return account.opened_on
    days = [o.op_date for o in operations if o.account_id == account.id]
    return min(days) if days else None


def pea_status(account: Account, operations: list[Operation], today: date) -> dict:
    """Versements cumulés face au plafond, et date des 5 ans.

    Versements = champ « Versements » du compte + opérations « Versement » + ceux que les achats
    supposent (rejeu des espèces, D-062) : on ne peut pas acheter sans avoir versé avant.
    """
    deposits = account.deposits
    return {
        **_deposits(account),
        "cap": PEA_CAP,
        "remaining": max(ZERO, PEA_CAP - deposits),
        **_milestone(account, operations, today, PEA_YEARS),
    }


def av_status(account: Account, operations: list[Operation], today: date) -> dict:
    return {**_deposits(account), **_milestone(account, operations, today, AV_YEARS)}


def _deposits(account: Account) -> dict:
    return {
        "deposits": account.deposits,
        "deposits_before": account.deposits_before,
        "deposits_entered": account.deposits_entered,
        "deposits_implied": account.deposits_implied,
    }


def _milestone(account: Account, operations: list[Operation], today: date, years: int) -> dict:
    start = opened_on(account, operations)
    milestone = anniversary(start, years) if start else None
    return {
        "opened_on": start,
        "opened_on_entered": account.opened_on is not None,
        "milestone": milestone,
        "milestone_reached": milestone is not None and today >= milestone,
    }


def pea_exit(value: Decimal, deposits: Decimal, five_years: bool, pfu_pct: Decimal) -> dict:
    """Impôt si tout le PEA était retiré aujourd'hui. Vendre dans le plan n'est pas imposé : seul
    le retrait l'est, sur sa part de gains (valeur − versements). Avant 5 ans : prélèvement
    forfaitaire (et le plan est clôturé) ; après : prélèvements sociaux seuls, c'est-à-dire le
    prélèvement forfaitaire moins sa part d'impôt sur le revenu (D-065)."""
    gain = max(ZERO, value - deposits)
    rate = max(ZERO, pfu_pct - PFU_IR_PCT) if five_years else pfu_pct
    return {"gain": gain, "rate_pct": rate, "tax": gain * rate / HUNDRED}


def per_deposits_in_year(account: Account, operations: list[Operation], year: int) -> Decimal:
    """Versements d'un PER faits pendant `year` : opérations « Versement » datées de l'année et
    versements que les achats de l'année supposent (D-062). Le champ « Versements » du compte,
    sans date, compte pour les années passées (D-064)."""
    mine = [o for o in operations if o.account_id == account.id]
    entered = sum(
        (o.amount for o in mine if o.op_type == "versement" and o.op_date.year == year), ZERO
    )
    implied = implied_deposits(account.deposits_before, account.currency, mine)
    return entered + sum((amount for day, amount in implied if day.year == year), ZERO)


def per_year(deposits_year: Decimal, ceiling: Decimal | None, tmi_pct: Decimal) -> dict:
    """Versements de l'année de tous les PER face au plafond épargne retraite (celui de l'avis
    d'impôt, reports compris) et impôt économisé si on les déduit : part dans le plafond × TMI.
    Sans plafond saisi, tout est supposé déductible."""
    deductible = deposits_year if ceiling is None else min(deposits_year, ceiling)
    return {
        "deposits": deposits_year,
        "ceiling": ceiling,
        "remaining": None if ceiling is None else max(ZERO, ceiling - deposits_year),
        "over": ZERO if ceiling is None else max(ZERO, deposits_year - ceiling),
        "deductible": deductible,
        "saving": deductible * tmi_pct / HUNDRED,
        "low_tmi": tmi_pct <= PER_LOW_TMI_PCT,
    }


def per_exit(value: Decimal, deposits: Decimal, tmi_pct: Decimal, pfu_pct: Decimal) -> dict:
    """Impôt si tout le PER sortait en capital aujourd'hui (versements supposés tous déduits).

    La part versée est imposée au barème (TMI, sans prélèvements sociaux), les gains au
    prélèvement forfaitaire. Si la valeur est sous les versements, tout ce qui sort est de la
    part versée et il n'y a pas de gain.
    """
    paid_in = min(value, deposits) if value > 0 else ZERO
    gains = max(ZERO, value - deposits)
    tax_paid_in = paid_in * tmi_pct / HUNDRED
    tax_gains = gains * pfu_pct / HUNDRED
    return {
        "value": value,
        "gains": value - deposits,
        "tax_paid_in": tax_paid_in,
        "tax_gains": tax_gains,
        "tax": tax_paid_in + tax_gains,
        "net": value - tax_paid_in - tax_gains,
    }


@dataclass(frozen=True)
class Sale:
    op_id: int
    account_id: int
    asset_id: int
    op_date: date
    proceeds: Decimal  # quantité × prix − frais
    cost: Decimal  # prix de revient des parts vendues (prix moyen pondéré, frais d'achat inclus)
    currency: str
    # Plus-value en EUR, achats et vente convertis au taux de leur jour (D-081) ; None sans
    # historique des taux.
    gain_eur: Decimal | None = None

    @property
    def gain(self) -> Decimal:
        return self.proceeds - self.cost


def realized_sales(
    operations: Iterable[Operation],
    history: RateHistory | None = None,
    pooled: bool = False,
    pool: Collection[int] | None = None,
) -> list[Sale]:
    """Ventes avec leur plus-value réalisée, au prix moyen pondéré (méthode fiscale française).

    `history` : prix moyen tenu aussi en euros, chaque achat au taux de son jour et la vente au
    sien, comme le retient l'impôt pour un titre en devise (D-081). `pooled` : un seul prix
    moyen par titre pour tous les comptes donnés (tous les comptes-titres du foyer, D-081) ;
    sinon un par compte (PEA). `pool` : les comptes qui partagent ce prix moyen quand la liste
    contient aussi les opérations d'autres comptes (chacun garde alors le sien, D-138)."""
    state: dict[tuple[int, int], list] = {}
    sales = []
    trades = sorted(
        (o for o in operations if o.op_type in TRADES and o.op_type != "transfert_entrant"),
        key=lambda o: (o.op_date, o.id),
    )

    def key_of(account_id: int, asset_id: int) -> tuple[int, int]:
        shared = pooled and (pool is None or account_id in pool)
        return (0 if shared else account_id, asset_id)  # aucun compte ne porte l'id 0

    for op in trades:
        key = key_of(op.account_id, op.asset_id)
        qty, cost, cost_eur = state.setdefault(key, [ZERO, ZERO, ZERO])
        if op.op_type == "transfert_sortant":
            # Titres envoyés ailleurs : ils emportent leur part du prix de revient, sans cession ;
            # vers un compte de l'app, elle le suit (D-085).
            share = op.quantity / qty if qty else ZERO
            moved = [op.quantity, cost * share, None if cost_eur is None else cost_eur * share]
            state[key] = [
                qty - op.quantity,
                cost - moved[1],
                None if cost_eur is None else cost_eur - moved[2],
            ]
            if op.to_account_id:
                dest = key_of(op.to_account_id, op.asset_id)
                d_qty, d_cost, d_eur = state.setdefault(dest, [ZERO, ZERO, ZERO])
                state[dest] = [
                    d_qty + moved[0],
                    d_cost + moved[1],
                    None if d_eur is None or moved[2] is None else d_eur + moved[2],
                ]
        elif op.op_type == "vente":
            share = op.quantity / qty if qty else ZERO
            proceeds = op.quantity * op.unit_price - op.fees
            gain_eur = None
            if history is not None and cost_eur is not None:
                proceeds_eur = history.to_eur(proceeds, op.currency, op.op_date)
                if proceeds_eur is not None:
                    gain_eur = proceeds_eur - cost_eur * share
            sales.append(
                Sale(
                    op.id,
                    op.account_id,
                    op.asset_id,
                    op.op_date,
                    proceeds,
                    cost * share,
                    op.currency,
                    gain_eur,
                )  # fmt: skip
            )
            state[key] = [
                qty - op.quantity,
                cost - cost * share,
                None if cost_eur is None else cost_eur - cost_eur * share,
            ]
        else:
            paid = op.quantity * op.unit_price + op.fees
            paid_eur = history.to_eur(paid, op.currency, op.op_date) if history else None
            state[key] = [
                qty + op.quantity,
                cost + paid,
                None if cost_eur is None or paid_eur is None else cost_eur + paid_eur,
            ]
    return sales


def cto_estimate(
    operations: list[Operation],
    account_ids: set[int],
    year: int,
    rates: dict[str, Decimal],
    rate_pct: Decimal = PFU_PCT,
    history: RateHistory | None = None,
    pooled: bool = False,
    only: int | None = None,
) -> dict:
    """Plus-values réalisées et dividendes de l'année sur les comptes-titres, et impôt estimé
    sur les plus-values au prélèvement forfaitaire unique. Moins-values reportables des années
    passées ignorées. `history`, `pooled` : voir realized_sales ; `only` : ventes et dividendes
    d'un seul de ces comptes (prix moyen toujours calculé sur tous)."""
    history = history or RateHistory(rates)
    gains = losses = dividends = ZERO
    sales = []
    mine = [o for o in operations if o.account_id in account_ids]
    # Toutes les opérations, pas seulement celles de ces comptes : des titres transférés depuis
    # un autre compte de l'app arrivent avec leur prix de revient (D-085). Limité à `mine`, le
    # calcul les faisait entrer pour zéro et comptait tout le prix de vente en plus-value (D-138).
    for sale in realized_sales(operations, history, pooled, account_ids):
        if sale.account_id not in account_ids:
            continue
        if sale.op_date.year != year or (only is not None and sale.account_id != only):
            continue
        gain = sale.gain_eur
        if gain is None:
            gain = convert(sale.gain, sale.currency, "EUR", rates)
        if gain is None:
            continue
        sales.append((sale, gain))
        if gain >= 0:
            gains += gain
        else:
            losses -= gain
    for o in mine:
        if o.op_type == "dividende" and o.op_date.year == year and only in (None, o.account_id):
            dividends += history.to_eur(o.amount, o.currency, o.op_date) or ZERO
    net = gains - losses
    # Dividendes affichés mais hors impôt estimé : un courtier français prélève déjà le PFU à
    # la source, et le montant saisi est le net reçu ; les compter doublerait l'impôt.
    base = max(ZERO, net)
    return {
        "year": year,
        "sales": sales,
        "gains": gains,
        "losses": losses,
        "net": net,
        "dividends": dividends,
        "rate_pct": rate_pct,
        "tax": base * rate_pct / HUNDRED,
    }


# --- Cryptos (D-066) --------------------------------------------------------------------
# Régime des particuliers (art. 150 VH bis du CGI) : seule une cession contre des euros (ou un
# bien) est imposable ; un échange contre une autre crypto ne l'est pas. La plus-value se
# calcule sur le portefeuille global, comme dans le formulaire 2086 (numéros de cases ci-dessous).


@dataclass(frozen=True)
class CryptoSale:
    op_id: int
    asset_id: int
    op_date: date
    portfolio_value: Decimal  # 212 : valeur globale du portefeuille juste avant la cession
    estimated: bool  # valeur 212 estimée par l'app (pas saisie)
    gross: Decimal  # 213 : prix de cession (quantité × prix)
    fees: Decimal  # 214 : frais de cession
    purchases: Decimal  # 220 : prix total d'acquisition (achats payés en euros, frais compris)
    consumed: Decimal  # 221 : fractions de ce prix déjà imputées sur les cessions passées

    @property
    def net(self) -> Decimal:  # 215 (218 sans soulte)
        return self.gross - self.fees

    @property
    def acquisition(self) -> Decimal:  # 223 : prix total d'acquisition net
        return self.purchases - self.consumed

    @property
    def fraction(self) -> Decimal:
        """Part du prix d'acquisition imputée sur cette cession : 223 × 213 ÷ 212."""
        if self.portfolio_value <= 0:
            return self.acquisition
        return self.acquisition * self.gross / self.portfolio_value

    @property
    def gain(self) -> Decimal:  # 224 : plus ou moins-value
        return self.net - self.fraction


def crypto_sales(
    operations: Iterable[Operation],
    rates: dict[str, Decimal],
    price_on: Callable[[int, date], Decimal | None],
    current: dict[int, Decimal],
    history: RateHistory | None = None,
    internal: Iterable[int] = (),
) -> tuple[list[CryptoSale], Decimal]:
    """Cessions imposables des comptes crypto (formule du portefeuille global) et prix
    d'acquisition net restant après la dernière opération, en EUR.

    Valeur globale à une cession : celle saisie sur la vente, sinon estimée avec la crypto
    vendue au prix de la vente et les autres au cours EUR stocké à cette date (`price_on`),
    sinon au prix de leur dernière opération, sinon au cours actuel (`current`). Un échange
    (vente marquée `crypto_swap`) n'est pas imposable : son produit paie les achats suivants,
    qui n'augmentent donc pas le prix d'acquisition (seul l'argent sorti de la poche compte).
    Montants en devise au taux du jour de l'opération (`history`, D-081).
    """
    history = history or RateHistory(rates)
    internal = set(internal)
    day = date.min

    def eur(amount: Decimal, currency: str) -> Decimal:
        return history.to_eur(amount, currency, day) or ZERO

    # Le même jour, l'échange passe avant l'achat qu'il paie.
    def order(o: Operation):
        return (o.op_date, 0 if o.op_type == "vente" and o.crypto_swap else 1, o.id)

    purchases = consumed = swap_credit = ZERO
    held: dict[int, Decimal] = {}
    last: dict[int, Decimal] = {}  # prix EUR de la dernière opération sur chaque crypto
    sales = []

    def portfolio_value(op: Operation, price: Decimal) -> Decimal:
        """Valeur estimée des cryptos détenues le jour de `op`, celle de `op` à son prix."""
        value = ZERO
        for asset_id, qty in held.items():
            if qty <= 0:
                continue
            if asset_id == op.asset_id:
                unit = price
            else:
                unit = price_on(asset_id, op.op_date)
                if unit is None:
                    unit = last.get(asset_id, current.get(asset_id, ZERO))
            value += qty * unit
        return value

    # Transfert entre deux comptes crypto de l'app (D-085) : la crypto reste dans le portefeuille
    # global, rien ne change.
    trades = [
        o
        for o in operations
        if o.op_type in TRADES
        and o.op_type != "transfert_entrant"
        and not (o.op_type == "transfert_sortant" and o.to_account_id in internal)
    ]
    for op in sorted(trades, key=order):
        day = op.op_date
        price = eur(op.unit_price, op.currency)
        if op.op_type == "vente" and not op.crypto_swap:
            if op.portfolio_value is not None:
                value, estimated = op.portfolio_value, False
            else:
                value, estimated = portfolio_value(op, price), True
            gross = op.quantity * price
            sale = CryptoSale(
                op.id,
                op.asset_id,
                op.op_date,
                max(value, gross),  # le portefeuille vaut au moins ce qu'on en vend
                estimated,
                gross,
                eur(op.fees, op.currency),
                purchases,
                consumed,
            )
            sales.append(sale)
            consumed += sale.fraction
        elif op.op_type == "vente":
            swap_credit += eur(op.quantity * op.unit_price - op.fees, op.currency)
        elif op.op_type == "transfert_sortant":
            # Envoyée ailleurs : rien n'est cédé, mais elle quitte le portefeuille suivi par l'app
            # et emporte sa part du prix d'acquisition, au prorata de sa valeur (D-082).
            out = op.quantity * price
            whole = max(portfolio_value(op, price), out)
            if whole > 0:
                consumed += (purchases - consumed) * out / whole
        else:
            # Crypto reçue gratuitement (Earn, airdrop, part offerte) : prix d'acquisition nul
            # pour cette part, seul l'argent sorti de la poche compte (D-082).
            cost = eur(op.quantity * op.unit_price + op.fees - (op.offered or ZERO), op.currency)
            paid = min(swap_credit, cost)
            swap_credit -= paid
            purchases += cost - paid
        sign = -1 if op.op_type in ("vente", "transfert_sortant") else 1
        held[op.asset_id] = held.get(op.asset_id, ZERO) + sign * op.quantity
        last[op.asset_id] = price
    return sales, purchases - consumed


def crypto_estimate(sales: list[CryptoSale], year: int, rate_pct: Decimal) -> dict:
    """Cessions d'une année : total, plus-value nette (les moins-values ne se compensent qu'avec
    les plus-values crypto de la même année, jamais reportées) et impôt au prélèvement
    forfaitaire, nul si le total des cessions ne dépasse pas 305 €."""
    in_year = [s for s in sales if s.op_date.year == year]
    total = sum((s.gross for s in in_year), ZERO)
    gains = sum((s.gain for s in in_year if s.gain > 0), ZERO)
    losses = -sum((s.gain for s in in_year if s.gain < 0), ZERO)
    net = gains - losses
    exempt = total <= CRYPTO_EXEMPT
    return {
        "sales": in_year,
        "total": total,
        "exempt": exempt,
        "gains": gains,
        "losses": losses,
        "net": net,
        "tax": ZERO if exempt else max(ZERO, net) * rate_pct / HUNDRED,
    }


def crypto_exit(value: Decimal, acquisition: Decimal, rate_pct: Decimal) -> dict:
    """Tout vendre en euros aujourd'hui : plus-value = valeur − prix d'acquisition net restant
    (la cession de tout le portefeuille impute tout ce prix), impôt nul jusqu'à 305 €."""
    gain = value - acquisition
    taxed = gain > 0 and value > CRYPTO_EXEMPT
    return {"gain": gain, "tax": gain * rate_pct / HUNDRED if taxed else ZERO}
