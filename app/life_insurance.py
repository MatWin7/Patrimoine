"""Assurance-vie : part de gains d'un rachat, abattement après 8 ans et taux selon la date des
versements, en ESTIMATION (D-144). Fonctions pures.

Règles (CGI art. 125-0 A et 200 A ; BOFiP BOI-RPPM-RCM-20-10-20-50 et BOI-RPPM-RCM-20-15 ;
impots.gouv.fr, page mise à jour le 20/03/2026) :
- part de gains d'un rachat partiel = rachat − primes restantes × rachat / valeur du contrat ;
  les primes ainsi remboursées sortent des primes restantes ; la part de primes est plafonnée au
  rachat quand le contrat vaut moins que ses primes (tolérance de l'administration) ;
- primes versées jusqu'au 26/09/2017 : 35 % avant 4 ans, 15 % de 4 à 8 ans, 7,5 % après ;
- primes versées depuis le 27/09/2017 : 12,8 % avant 8 ans ; après, 7,5 % tant que les primes
  restantes de tous les contrats au 31/12 de l'année précédente ne dépassent pas 150 000 €,
  sinon 7,5 % sur la fraction (150 000 − primes d'avant 2017) / primes depuis 2017 ;
- après 8 ans, abattement annuel de 4 600 € (9 200 € en imposition commune), imputé d'abord sur
  les gains des primes d'avant 2017, puis sur la part à 7,5 %, puis sur celle à 12,8 % ;
- prélèvements sociaux : 17,2 % (l'assurance-vie est hors de la hausse de CSG de la LFSS 2026),
  déjà prélevés chaque année sur les intérêts du fonds euros.

Les gains d'un rachat sont répartis entre primes d'avant et d'après 2017 au prorata de ces
primes (l'assureur, lui, suit chaque prime) ; option pour le barème non simulée.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.tax import anniversary

ZERO = Decimal("0")
HUNDRED = Decimal("100")
REFORM_DAY = date(2017, 9, 27)  # primes versées à partir de ce jour : régime du PFU
YEARS_REDUCED = 8  # après 8 ans : abattement annuel et taux de 7,5 %
YEARS_MIDDLE = 4  # primes d'avant 2017 : 35 % avant 4 ans, 15 % de 4 à 8 ans
ALLOWANCE_SINGLE = Decimal("4600")
ALLOWANCE_COUPLE = Decimal("9200")  # couple soumis à imposition commune
CAP = Decimal("150000")  # primes restantes de tous les contrats, au 31/12 de l'année passée
REDUCED_PCT = Decimal("7.5")
PFU_IR_PCT = Decimal("12.8")
PRE_SHORT_PCT = Decimal("35")
PRE_MIDDLE_PCT = Decimal("15")
SOCIAL_PCT = Decimal("17.2")
# Transmission (repère seulement, D-144) : primes versées avant 70 ans, 152 500 € par
# bénéficiaire (art. 990 I) ; après 70 ans, 30 500 € pour tous les bénéficiaires (art. 757 B).
HEIR_ALLOWANCE = Decimal("152500")
AFTER_70_ALLOWANCE = Decimal("30500")


def years_reached(opened_on: date | None, day: date, years: int) -> bool:
    return opened_on is not None and day >= anniversary(opened_on, years)


@dataclass(frozen=True)
class Premium:
    """Versement sur le contrat. `day` None : champ « Versements » du compte, sans date."""

    day: date | None
    amount: Decimal


@dataclass(frozen=True)
class Buyback:
    """Rachat (retrait) : montant et valeur du contrat juste avant, None si inconnue."""

    day: date
    amount: Decimal
    value_before: Decimal | None


@dataclass(frozen=True)
class BuybackGain:
    day: date
    amount: Decimal
    gain: Decimal  # part de gains du rachat
    gain_pre: Decimal  # dont attachée aux primes d'avant le 27/09/2017
    estimated: bool  # valeur du contrat inconnue ce jour-là : tout le rachat compté en primes
    eight_years: bool  # contrat de 8 ans ou plus le jour du rachat (abattement)


@dataclass(frozen=True)
class Remaining:
    """Primes pas encore remboursées par un rachat, d'avant et d'après le 27/09/2017."""

    pre: Decimal
    post: Decimal

    @property
    def total(self) -> Decimal:
        return self.pre + self.post


def _is_pre(day: date | None, opened_on: date | None) -> bool:
    """Un versement sans date est d'avant 2017 si le contrat a été ouvert avant le 27/09/2017."""
    if day is None:
        return opened_on is not None and opened_on < REFORM_DAY
    return day < REFORM_DAY


def replay(
    premiums: Iterable[Premium],
    buybacks: Iterable[Buyback],
    opened_on: date | None,
    until: date | None = None,
) -> tuple[Remaining, list[BuybackGain]]:
    """Primes restantes et part de gains de chaque rachat, dans l'ordre des dates (le même jour,
    les versements avant les rachats ; les versements sans date en premier). `until` : seulement
    ce qui est daté de ce jour ou avant (primes restantes au 31/12 de l'année passée)."""
    events: list[tuple[date, int, Premium | Buyback]] = []
    pre = post = ZERO
    for p in premiums:
        if p.day is None:
            if _is_pre(None, opened_on):
                pre += p.amount
            else:
                post += p.amount
        elif until is None or p.day <= until:
            events.append((p.day, 0, p))
    for b in buybacks:
        if until is None or b.day <= until:
            events.append((b.day, 1, b))
    gains = []
    for day, _, event in sorted(events, key=lambda e: (e[0], e[1])):
        if isinstance(event, Premium):
            if _is_pre(day, opened_on):
                pre += event.amount
            else:
                post += event.amount
            continue
        total = pre + post
        amount = event.amount
        value = event.value_before
        estimated = value is None or value <= 0
        if total <= 0:
            capital = ZERO
        elif estimated:
            capital = min(amount, total)
        else:
            capital = min(amount, total * amount / value, total)
        share_pre = pre_share(Remaining(pre, post), opened_on)
        gain = amount - capital
        pre -= capital * share_pre
        post -= capital - capital * share_pre
        gains.append(
            BuybackGain(
                day,
                amount,
                gain,
                gain * share_pre,
                estimated,
                years_reached(opened_on, day, YEARS_REDUCED),
            )
        )
    return Remaining(max(ZERO, pre), max(ZERO, post)), gains


def pre_share(remaining: Remaining, opened_on: date | None) -> Decimal:
    """Part des gains attachée aux primes d'avant le 27/09/2017 : au prorata des primes restantes
    (sans prime restante : toute ou rien selon la date d'ouverture)."""
    if remaining.total > 0:
        return remaining.pre / remaining.total
    return Decimal(1) if _is_pre(None, opened_on) else ZERO


def gain_share(value: Decimal, remaining: Decimal) -> Decimal:
    """Part de gains d'un rachat : 1 − primes restantes / valeur, 0 si le contrat est en perte."""
    if value <= 0 or value <= remaining:
        return ZERO
    return (value - remaining) / value


def reduced_fraction(encours: Remaining) -> Decimal:
    """Part des gains des primes depuis 2017 taxée à 7,5 % après 8 ans (seuil de 150 000 €)."""
    if encours.total <= CAP:
        return Decimal(1)
    if encours.post <= 0:
        return ZERO
    return min(Decimal(1), max(ZERO, CAP - encours.pre) / encours.post)


def income_tax(
    gain: Decimal,
    gain_pre: Decimal,
    opened_on: date | None,
    day: date,
    allowance_left: Decimal,
    encours: Remaining,
) -> dict:
    """Impôt sur le revenu d'une part de gains `gain` (dont `gain_pre` d'avant 2017) rachetée le
    jour `day` : taux selon l'âge du contrat, abattement après 8 ans. Renvoie l'impôt, la part
    d'abattement utilisée et le détail par taux."""
    gain_pre = min(max(ZERO, gain_pre), max(ZERO, gain))
    gain_post = max(ZERO, gain) - gain_pre
    if not years_reached(opened_on, day, YEARS_REDUCED):
        pre_pct = PRE_MIDDLE_PCT if years_reached(opened_on, day, YEARS_MIDDLE) else PRE_SHORT_PCT
        tax = (gain_pre * pre_pct + gain_post * PFU_IR_PCT) / HUNDRED
        return {"tax": tax, "allowance_used": ZERO, "at_reduced": ZERO, "at_pfu": gain_post}
    at_reduced = gain_post * reduced_fraction(encours)
    at_pfu = gain_post - at_reduced
    left = max(ZERO, allowance_left)
    taxed = []
    for part in (gain_pre, at_reduced, at_pfu):  # ordre d'imputation de l'abattement (BOFiP)
        used = min(left, part)
        left -= used
        taxed.append(part - used)
    tax = ((taxed[0] + taxed[1]) * REDUCED_PCT + taxed[2] * PFU_IR_PCT) / HUNDRED
    return {
        "tax": tax,
        "allowance_used": max(ZERO, allowance_left) - left,
        "at_reduced": gain_pre + at_reduced,
        "at_pfu": at_pfu,
    }


def allowance(couple: bool) -> Decimal:
    return ALLOWANCE_COUPLE if couple else ALLOWANCE_SINGLE


def tax_free_buyback(
    value: Decimal, remaining: Decimal, eight_years: bool, allowance_left: Decimal
) -> Decimal:
    """Rachat le plus grand sans impôt sur le revenu : tout si le contrat est en perte ; après 8
    ans, celui dont la part de gains égale l'abattement restant ; sinon rien."""
    if value <= 0:
        return ZERO
    share = gain_share(value, remaining)
    if share == 0:
        return value
    if not eight_years or allowance_left <= 0:
        return ZERO
    return min(value, allowance_left / share)
