"""Listes de suivi (D-072) : performance d'un cours sur plusieurs périodes. Fonctions pures.

Chaque période compare la dernière clôture à la dernière clôture connue à la date de départ
(ou juste avant) : « 1 mois » au 22 octobre part du 22 septembre. « Depuis janvier » part de
la dernière clôture de l'année précédente. Les rendements de 3 et 5 ans sont cumulés, pas
annualisés. Une période plus longue que l'historique donne None.
"""

from bisect import bisect_right
from datetime import date, timedelta
from decimal import Decimal

PERIODS = ("1j", "1s", "1m", "ytd", "1a", "3a", "5a")
HISTORY_YEARS = 5  # profondeur demandée à Yahoo, une fois par jour et par ticker


def years_before(day: date, years: int) -> date:
    """Même jour `years` ans plus tôt (29 février -> 28 février)."""
    try:
        return day.replace(year=day.year - years)
    except ValueError:
        return day.replace(year=day.year - years, day=28)


def month_before(day: date) -> date:
    """Même jour le mois précédent, ramené au dernier jour du mois s'il n'existe pas (31 -> 30)."""
    first = day.replace(day=1)
    last_of_previous = first - timedelta(days=1)
    return last_of_previous.replace(day=min(day.day, last_of_previous.day))


def start_dates(today: date) -> dict[str, date]:
    """Date de départ de chaque période, sauf « 1j » (veille de cotation)."""
    return {
        "1s": today - timedelta(days=7),
        "1m": month_before(today),
        "ytd": date(today.year - 1, 12, 31),
        "1a": years_before(today, 1),
        "3a": years_before(today, 3),
        "5a": years_before(today, 5),
    }


def period_returns(closes: list[tuple[date, Decimal]], today: date) -> dict[str, Decimal | None]:
    """Rendement (en rapport : 0.05 = +5 %) de chaque période, à partir de clôtures triées."""
    out: dict[str, Decimal | None] = dict.fromkeys(PERIODS)
    if not closes:
        return out
    days = [d for d, _ in closes]
    last = closes[-1][1]
    if len(closes) >= 2:
        out["1j"] = last / closes[-2][1] - 1
    for key, start in start_dates(today).items():
        i = bisect_right(days, start)  # clôtures à `start` ou avant : indices < i
        if i == 0:
            continue  # historique trop court
        ref = closes[i - 1][1]
        out[key] = last / ref - 1 if ref > 0 else None
    return out
