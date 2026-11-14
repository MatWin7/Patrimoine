"""Achats programmés (investissement régulier), fonctions pures (D-055).

Un modèle dit « tel montant de tel actif, chaque mois (ou trimestre, ou semaine), tel jour, à
partir de telle date ». Chaque échéance passée est proposée à l'utilisateur, qui saisit le prix
et la quantité réellement exécutés : le cours connu de l'app n'est jamais celui de la banque.
"""

from collections.abc import Iterator
from datetime import date, timedelta
from decimal import ROUND_DOWN, Decimal

# `day` : jour du mois (1 à 28) en mensuel et trimestriel, jour de la semaine (1 = lundi à
# 7 = dimanche) en hebdomadaire et toutes les deux semaines (« bimensuel », D-071).
FREQUENCIES = ("mensuel", "trimestriel", "hebdomadaire", "bimensuel")
WEEKLY = ("hebdomadaire", "bimensuel")
# Les quinzaines sont comptées à partir d'un lundi fixe : une échéance tous les 14 jours tombe
# toujours dans une quinzaine différente, quel que soit le jour choisi.
FORTNIGHT_ORIGIN = date(2001, 1, 1)
MAX_PENDING = 12  # au-delà, les échéances les plus anciennes ne sont plus proposées


def period_key(due: date, frequency: str) -> str:
    """Période d'une échéance : « 2025-03 » (mois), « 2025-W11 » (semaine ISO) ou « Q00626 »
    (quinzaine).

    Comparer des périodes plutôt que des dates évite de reproposer une échéance déjà traitée
    quand on change le jour (du 5 au 20 du mois, du lundi au vendredi).
    """
    if frequency == "bimensuel":
        return f"Q{(due - FORTNIGHT_ORIGIN).days // 14:05d}"
    if frequency == "hebdomadaire":
        year, week, _ = due.isocalendar()
        return f"{year:04d}-W{week:02d}"
    return f"{due.year:04d}-{due.month:02d}"


def _due_dates(start: date, frequency: str, day: int, today: date) -> Iterator[date]:
    if frequency in WEEKLY:
        due = start + timedelta(days=(day - 1 - start.weekday()) % 7)
        step = timedelta(days=14 if frequency == "bimensuel" else 7)
        while due <= today:
            yield due
            due += step
        return
    step = 3 if frequency == "trimestriel" else 1
    index = start.year * 12 + start.month - 1
    while (index // 12, index % 12 + 1) <= (today.year, today.month):
        due = date(index // 12, index % 12 + 1, day)
        if start <= due <= today:
            yield due
        index += step


def pending_dates(
    start: date, frequency: str, day: int, last_due: date | None, today: date
) -> list[date]:
    """Échéances à valider ou ignorer, de la plus ancienne à la plus récente : au plus tôt
    `start`, au plus tard aujourd'hui, et d'une période postérieure à la dernière traitée."""
    done = period_key(last_due, frequency) if last_due else None
    out = [
        due
        for due in _due_dates(start, frequency, day, today)
        if done is None or period_key(due, frequency) > done
    ]
    return out[-MAX_PENDING:]


def suggested_quantity(amount: Decimal, price: Decimal | None, asset_class: str) -> Decimal | None:
    """Quantité proposée : montant ÷ cours connu, arrondie vers le bas. Parts entières pour une
    action ou un ETF (cas d'un PEA), 8 décimales pour une crypto. Corrigée ensuite à la main."""
    if not price or price <= 0:
        return None
    raw = amount / price
    if asset_class == "crypto":
        return raw.quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
    whole = raw.quantize(Decimal("1"), rounding=ROUND_DOWN)
    return whole if whole >= 1 else raw.quantize(Decimal("0.0001"), rounding=ROUND_DOWN)
