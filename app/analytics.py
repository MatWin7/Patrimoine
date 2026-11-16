"""Analyses du portefeuille (fonctions pures, en Decimal) : rendement personnel (TRI),
volatilité, comparaison à un indice, projection vers un objectif.

Les flux suivent la convention de daily_flows : positif = argent apporté par l'investisseur.
"""

from collections.abc import Iterable
from datetime import date
from decimal import Decimal, localcontext

ZERO = Decimal("0")
ONE = Decimal("1")
YEAR_DAYS = Decimal("365")
TRADING_DAYS = Decimal("252")  # jours de bourse par an, pour annualiser la volatilité


def xirr(cashflows: Iterable[tuple[date, Decimal]]) -> Decimal | None:
    """Taux de rendement interne annualisé (TRI, « XIRR » des tableurs).

    `cashflows` du point de vue de l'investisseur : négatif quand il verse, positif quand il
    récupère (la valeur finale compte comme un retrait fictif). C'est le taux r tel que la
    somme des flux actualisés, flux / (1 + r) ^ (jours / 365), vaut zéro. On le trouve par
    dichotomie : la somme baisse quand r monte, on resserre l'intervalle jusqu'à zéro.
    None s'il n'y a pas à la fois des versements et un retour, ou moins d'un jour d'écart.
    """
    flows = [(d, a) for d, a in cashflows if a]
    if not any(a < 0 for _, a in flows) or not any(a > 0 for _, a in flows):
        return None
    origin = min(d for d, _ in flows)
    if max(d for d, _ in flows) == origin:
        return None
    years = [(Decimal((d - origin).days) / YEAR_DAYS, a) for d, a in flows]

    def npv(rate: Decimal) -> Decimal:
        return sum((a / (ONE + rate) ** t for t, a in years), ZERO)

    low, high = Decimal("-0.99"), Decimal("10")
    at_low = npv(low)
    if at_low * npv(high) > 0:
        return None  # pas de solution dans [-99 %, +1000 %] par an
    if at_low == 0:
        return low  # la borne basse est la solution (presque tout perdu en un an)
    with localcontext() as ctx:
        ctx.prec = 30
        for _ in range(80):
            mid = (low + high) / 2
            # On garde la moitié où la somme change de signe. Cas courant (versements d'abord) :
            # elle est positive en bas, donc positive au milieu = le taux est plus haut. Si
            # l'argent est sorti avant d'entrer, elle monte avec le taux : le sens est lu sur
            # la borne basse au lieu d'être supposé (D-138).
            if (npv(mid) > 0) == (at_low > 0):
                low = mid
            else:
                high = mid
    return (low + high) / 2


def volatility(daily_factors: list[Decimal], per_year: Decimal = TRADING_DAYS) -> Decimal | None:
    """Volatilité annualisée : écart type des rendements quotidiens × racine(points par an).

    Mesure à quel point la valeur « secoue » d'un jour à l'autre (20 % : une année sur
    trois environ, l'écart à la tendance dépasse 20 %). None avec moins de 2 jours.
    `per_year` : nombre de points de la série par an, 252 jours de bourse, mais 365 avec une
    crypto, cotée aussi le week-end (`points_per_year`, D-081).
    """
    returns = [f - ONE for f in daily_factors]
    if len(returns) < 2:
        return None
    mean = sum(returns, ZERO) / len(returns)
    variance = sum(((r - mean) ** 2 for r in returns), ZERO) / (len(returns) - 1)
    return variance.sqrt() * per_year.sqrt()


def points_per_year(count: int, first: date, last: date) -> Decimal:
    """Rythme d'une série quotidienne : points par an, 252 si elle dure moins d'un mois (trop
    court pour mesurer un rythme), plafonné à 365."""
    days = (last - first).days + 1
    if days < 30:
        return TRADING_DAYS
    return min(Decimal(count) * YEAR_DAYS / Decimal(days), YEAR_DAYS)


def simulate_benchmark(
    days: list[date],
    flows: dict[date, Decimal],
    closes: list[tuple[date, Decimal]],
    start_value: Decimal,
    start: date,
) -> list[tuple[date, Decimal]]:
    """Valeur qu'aurait eue le même argent placé sur un indice, aux mêmes dates.

    La valeur de départ et chaque apport achètent des « parts » de l'indice à sa dernière
    clôture connue ; un retrait en revend pour le même montant. `closes` en EUR, triées.
    Les jours d'avant la première clôture de l'indice sont ignorés.
    """
    if not closes:
        return []
    index = 0
    close = None
    units = ZERO
    invested_start = False
    previous = start
    points = []
    for day in days:
        while index < len(closes) and closes[index][0] <= day:
            close = closes[index][1]
            index += 1
        if close is None:
            continue
        if not invested_start:
            units = start_value / close
            invested_start = True
            flow = sum((f for d, f in flows.items() if start <= d <= day), ZERO)
        else:
            flow = sum((f for d, f in flows.items() if previous < d <= day), ZERO)
        units = max(units + flow / close, ZERO)
        points.append((day, units * close))
        previous = day
    return points


def project(
    start_value: Decimal, monthly: Decimal, annual_rate: Decimal, months: int
) -> list[Decimal]:
    """Valeur à la fin de chaque mois : versement mensuel et rendement constant.

    Taux mensuel équivalent : (1 + taux annuel) ^ (1/12) - 1, pour que 12 mois composés
    redonnent exactement le taux annuel. Une simulation, pas une prévision.
    """
    monthly_rate = (ONE + annual_rate) ** (ONE / 12) - ONE
    value = start_value
    values = []
    for _ in range(months):
        value = value * (ONE + monthly_rate) + monthly
        values.append(value)
    return values


# --- Calendrier et livrets -------------------------------------------------------------


def monthly_returns(closes: Iterable[tuple[date, Decimal]]) -> dict[str, Decimal]:
    """Rendement de chaque mois (« 2026-03 ») : dernière clôture du mois ÷ dernière clôture du
    mois précédent − 1. Le premier mois de la série n'a pas de point de départ : il est omis."""
    last: dict[str, Decimal] = {}
    for day, close in sorted(closes):
        last[f"{day.year}-{day.month:02d}"] = close
    keys = sorted(last)
    return {k: last[k] / last[p] - ONE for p, k in zip(keys, keys[1:], strict=False) if last[p]}


def yearly_returns(months: dict[str, Decimal]) -> dict[str, Decimal]:
    """Rendement de chaque année : les mois connus enchaînés, (1 + r1) × (1 + r2) × … − 1."""
    factors: dict[str, Decimal] = {}
    for label, value in months.items():
        year = label[:4]
        factors[year] = factors.get(year, ONE) * (ONE + value)
    return {year: factor - ONE for year, factor in factors.items()}


def blend(series: list[tuple[Decimal, dict[str, Decimal]]]) -> dict[str, Decimal]:
    """Moyenne pondérée de plusieurs séries de rendements mensuels ((poids, {mois: r}), ...).
    Un mois où une série manque est calculé sur les autres, poids remis à l'échelle."""
    labels = sorted({label for _, months in series for label in months})
    out = {}
    for label in labels:
        known = [(w, months[label]) for w, months in series if label in months]
        total = sum((w for w, _ in known), ZERO)
        if total:
            out[label] = sum((w * r for w, r in known), ZERO) / total
    return out


def livret_closes(rate_pct: Decimal, start: date, end: date) -> list[tuple[date, Decimal]]:
    """« Cours » fictif d'un livret à taux annuel fixe : 1 au départ, (1 + taux)^(jours / 365)
    ensuite. Les vrais livrets comptent par quinzaine et versent les intérêts une fois par an ;
    une capitalisation quotidienne au même taux en est très proche (D-044)."""
    with localcontext() as ctx:
        ctx.prec = 28
        base = ONE + rate_pct / 100
        return [
            (date.fromordinal(n), base ** (Decimal(n - start.toordinal()) / YEAR_DAYS))
            for n in range(start.toordinal(), end.toordinal() + 1)
        ]


def index_on(monthly: dict[date, Decimal], day: date) -> Decimal | None:
    """Indice de prix d'un jour : interpolé entre le 1er du mois et le 1er du mois suivant.

    Avant la série : premier indice ; après : dernier indice (voir `extend_index`, qui
    prolonge la série au taux saisi). Interpoler évite des marches à chaque début de mois.
    """
    if not monthly:
        return None
    months = sorted(monthly)
    first = months[0]
    if day <= first:
        return monthly[first]
    start = date(day.year, day.month, 1)
    nxt = _next_month(start)
    if start not in monthly or nxt not in monthly:
        return monthly[max(m for m in months if m <= day)]
    weight = Decimal((day - start).days) / Decimal((nxt - start).days)
    return monthly[start] + (monthly[nxt] - monthly[start]) * weight


def _next_month(day: date) -> date:
    return date(day.year + day.month // 12, day.month % 12 + 1, 1)


def smooth_index(monthly: dict[date, Decimal]) -> dict[date, Decimal]:
    """Indice lissé : la hausse des prix de chaque année civile, étalée à vitesse constante (D-110).

    L'IPCH n'est pas désaisonnalisé : il baisse 4 à 5 mois par an (soldes de janvier et juin,
    fin des tarifs d'été en septembre), ce qui ferait remonter « investi moins l'inflation »
    sans versement. Ici, l'indice reste exact au premier mois et à chaque décembre, et suit
    entre deux une progression géométrique (même pourcentage chaque mois). Après le dernier
    décembre, même progression jusqu'au dernier mois publié, exact lui aussi ; si les prix
    ont baissé depuis décembre (soldes de janvier), la pente est le glissement sur 12 mois
    du dernier mois publié (le taux d'inflation annoncé). Ne baisse donc que si les prix
    baissent sur une année entière.
    """
    months = sorted(monthly)
    if len(months) < 2:
        return dict(monthly)
    first, last = months[0], months[-1]
    anchors = [first] + [m for m in months if m.month == 12 and m > first]
    out: dict[date, Decimal] = {}
    with localcontext() as ctx:
        ctx.prec = 28
        for a, b in zip(anchors, anchors[1:], strict=False):
            span = _months_between(a, b)
            ratio = monthly[b] / monthly[a]
            for m in months:
                if a <= m <= b:
                    out[m] = monthly[a] * ratio ** (Decimal(_months_between(a, m)) / span)
        start = anchors[-1]
        out[start] = monthly[start]
        if start < last:
            step = (monthly[last] / monthly[start]) ** (ONE / _months_between(start, last))
            year_ago = date(last.year - 1, last.month, 1)
            if step < ONE and year_ago in monthly:  # en janvier, souvent : soldes
                step = (monthly[last] / monthly[year_ago]) ** (ONE / 12)
            for m in months:
                if m > start:
                    out[m] = monthly[start] * step ** _months_between(start, m)
    return out


def _months_between(a: date, b: date) -> int:
    return (b.year - a.year) * 12 + b.month - a.month


def extend_index(
    monthly: dict[date, Decimal], rate_pct: Decimal, start: date, end: date
) -> dict[date, Decimal]:
    """Indice mensuel prolongé au taux annuel saisi après le dernier mois publié (D-067).

    Sans indice publié (Eurostat injoignable), la série part de 100 au mois de `start`.
    Prolongée jusqu'au mois suivant `end` pour que l'interpolation du mois en cours marche.
    """
    out = dict(monthly) if monthly else {date(start.year, start.month, 1): Decimal(100)}
    with localcontext() as ctx:
        ctx.prec = 28
        monthly_factor = (ONE + rate_pct / 100) ** (ONE / 12)
        month = max(out)
        value = out[month]
        last = _next_month(date(end.year, end.month, 1))
        while month < last:
            month = _next_month(month)
            value *= monthly_factor
            out[month] = value
    return out


def yearly_inflation(monthly: dict[date, Decimal], day: date, fallback: Decimal) -> Decimal:
    """Taux annuel, en %, auquel la courbe « placé moins l'inflation » baisse le jour `day` (D-112).

    `monthly` est l'indice qui sert à la courbe (lissé, prolongé) : `index_on` interpole entre le
    1er du mois de `day` et le 1er du mois suivant, donc le taux est la pente de ce segment
    ramenée à l'année. Lissé par année civile, c'est la hausse des prix de l'année (D-110) ; au
    taux saisi après le dernier mois publié. Sans l'un des deux mois : `fallback`.
    """
    month = date(day.year, day.month, 1)
    nxt = _next_month(month)
    if month not in monthly or nxt not in monthly:
        return fallback
    with localcontext() as ctx:
        ctx.prec = 28
        return ((monthly[nxt] / monthly[month]) ** 12 - 1) * 100


def eroded_invested(
    points: list[tuple[date, Decimal]], monthly: dict[date, Decimal], flows: bool = False
) -> list[Decimal | None]:
    """Montant investi moins l'inflation, jour par jour : son pouvoir d'achat réel.

    Chaque somme investie perd ce que les prix ont pris depuis son jour d'investissement :
    100 € investis, puis 2 % d'inflation, n'achètent plus que l'équivalent de 98,04 € d'alors.
    Une vente (baisse de l'investi) retire la même part du montant érodé. Sans indice : None.
    `flows` (argent net placé, D-110) : chaque variation est un apport ou un retrait d'euros du
    jour, ajouté ou retiré tel quel ; la série peut devenir négative (plus retiré que placé).
    """
    if not monthly or not points:
        return [None] * len(points)
    out: list[Decimal | None] = []
    real = Decimal(0)
    previous_index = previous_invested = None
    for day, invested in points:
        index = index_on(monthly, day)
        if previous_index is not None:
            real = real * previous_index / index
        if flows or previous_invested is None or invested >= previous_invested:
            real += invested - (previous_invested or 0)
        elif previous_invested > 0:
            real = real * invested / previous_invested
        out.append(real)
        previous_index, previous_invested = index, invested
    return out
