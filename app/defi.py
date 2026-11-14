"""Dépôts DeFi (Aave, D-095) : intérêts estimés en local à partir des taux publics du jour,
fonctions pures.

Notion : un dépôt sur Aave est représenté par un « aToken » dont le solde grossit tout seul,
sans transaction. Les intérêts ne figurent donc dans aucun export : on les estime jour par jour
avec le taux de dépôt public du marché (une moyenne par jour), puis le solde réel saisi par
l'utilisateur (« Vérifier ») recale l'estimation.

Jour `d` : on applique les dépôts et retraits du jour, puis les intérêts du jour,
solde × ((1 + APY)^(1/365) − 1), qui s'ajoutent au solde (intérêts composés). Un solde vérifié
ce jour-là remplace ensuite le solde estimé, et l'écart compte dans les intérêts du mois.
Un mois dont le total devient négatif (estimation trop haute, corrigée par un solde vérifié)
est ramené à zéro et l'écart réduit les mois précédents ; ce qui reste après le premier mois
est un écart inexpliqué (il manque sans doute un retrait).
"""

from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Decimal

ZERO = Decimal("0")
ONE = Decimal("1")
DAYS_PER_YEAR = Decimal("365")
QUANTUM = Decimal("1E-10")  # précision des intérêts enregistrés (USDC : 6 décimales)


@dataclass
class Estimate:
    months: dict[str, Decimal]  # mois clos « AAAA-MM » -> intérêts (jetons), ≥ 0
    current: Decimal  # intérêts du mois en cours jusqu'à `today` inclus, pas encore enregistrés
    balance: Decimal  # solde estimé à `today` (dépôts + intérêts)
    extrapolated: bool  # des jours avant le premier taux connu : taux le plus ancien appliqué
    unexplained: Decimal  # écart négatif qu'aucun intérêt ne couvre (0 si aucun)
    verified: set[str] = field(default_factory=set)  # mois qui contiennent un solde vérifié


def daily_factor(apy: Decimal) -> Decimal:
    """Taux d'un jour équivalent à un APY : (1 + APY)^(1/365) − 1."""
    return (ONE + apy) ** (ONE / DAYS_PER_YEAR) - ONE


def rate_on(rates: dict[date, Decimal], days: list[date], day: date) -> Decimal:
    """Taux connu le jour donné, sinon le dernier avant, sinon le plus ancien (après)."""
    i = bisect_right(days, day)
    return rates[days[i - 1]] if i else rates[days[0]]


def month_end(month: str) -> date:
    year, m = (int(x) for x in month.split("-"))
    first_next = date(year + m // 12, m % 12 + 1, 1)
    return first_next - timedelta(days=1)


def estimate(
    flows: dict[date, Decimal],
    rates: dict[date, Decimal],
    checks: dict[date, Decimal],
    today: date,
) -> Estimate | None:
    """Intérêts par mois d'une ligne suivie. `flows` : variation de quantité de chaque jour
    (dépôts, retraits, transferts), hors intérêts déjà estimés ; `checks` : soldes vérifiés.
    None sans aucun taux connu."""
    if not rates:
        return None
    days = sorted(rates)
    starts = [d for d in (*flows, *checks) if d <= today]
    if not starts:
        return Estimate({}, ZERO, ZERO, False, ZERO)
    factors: dict[Decimal, Decimal] = {}
    by_month: dict[str, Decimal] = {}
    verified: set[str] = set()
    balance, extrapolated = ZERO, False
    day = min(starts)
    while day <= today:
        key = f"{day:%Y-%m}"
        by_month.setdefault(key, ZERO)
        balance += flows.get(day, ZERO)
        if balance > 0:
            apy = rate_on(rates, days, day)
            extrapolated |= day < days[0]
            if apy not in factors:
                factors[apy] = daily_factor(apy)
            accrued = balance * factors[apy]
            balance += accrued
            by_month[key] += accrued
        if day in checks:
            by_month[key] += checks[day] - balance
            balance = checks[day]
            verified.add(key)
        day += timedelta(days=1)

    # Mois négatifs : ramenés à zéro, l'écart réduit les mois précédents.
    keys = sorted(by_month)
    carry = ZERO
    for key in reversed(keys):
        value = by_month[key] + carry
        carry = min(value, ZERO)
        by_month[key] = max(value, ZERO)
    current_key = f"{today:%Y-%m}"
    current = by_month.pop(current_key, ZERO)
    months = {k: v.quantize(QUANTUM, ROUND_HALF_EVEN) for k, v in by_month.items()}
    return Estimate(
        {k: v for k, v in months.items() if v > 0},
        current,
        balance,
        extrapolated,
        -carry,
        verified,
    )


def average_rate(rates: dict[date, Decimal], start: date, end: date) -> Decimal | None:
    """Moyenne simple des taux du jour entre deux dates incluses (None si aucun)."""
    values = [v for d, v in rates.items() if start <= d <= end]
    return sum(values, ZERO) / len(values) if values else None


# --- Facteur de santé (D-108) -------------------------------------------------------------
# Notion : Aave liquide une partie des dépôts quand la dette dépasse ce qu'ils couvrent. Chaque
# jeton déposé couvre une part de sa valeur, son seuil de liquidation (0,78 pour 78 %).
# Facteur de santé = Σ(valeur des dépôts × seuil) ÷ valeur des dettes, sur un même marché ;
# sous 1, la liquidation peut commencer. L'app l'estime en local avec les lignes suivies.
HEALTH_WARNING = Decimal("1.5")


def health_factor(
    collateral: list[tuple[Decimal, Decimal]], debts: list[Decimal]
) -> Decimal | None:
    """`collateral` : (valeur, seuil de liquidation) des dépôts ; `debts` : valeurs des
    dettes, dans une même devise. None sans dette."""
    owed = sum(debts, ZERO)
    if owed <= 0:
        return None
    return sum((value * threshold for value, threshold in collateral), ZERO) / owed
