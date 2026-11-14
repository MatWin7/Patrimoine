"""Allocation cible et rééquilibrage par les versements, fonctions pures (D-058).

Strictement de l'affichage : aucune vente proposée, aucun ordre passé (règle 1). On répartit
le prochain versement pour rapprocher chaque ligne de sa part visée.
"""

from dataclasses import dataclass
from decimal import Decimal

ZERO = Decimal("0")
HUNDRED = Decimal("100")


@dataclass(frozen=True)
class Line:
    key: int  # id de l'actif
    value: Decimal  # valeur actuelle en EUR
    target_pct: Decimal  # part visée, en %


@dataclass(frozen=True)
class Advice:
    key: int
    value: Decimal
    current_pct: Decimal
    target_pct: Decimal
    gap: Decimal  # valeur visée − valeur actuelle, en EUR (négatif : au-dessus de la cible)
    buy: Decimal  # montant du versement à mettre sur la ligne


def rebalance(lines: list[Line], contribution: Decimal = ZERO) -> list[Advice]:
    """Écart de chaque ligne à sa cible, et répartition d'un versement sans rien vendre.

    Les parts se calculent sur les lignes qui ont une cible. Le versement va d'abord aux lignes
    en dessous de leur cible, au prorata de ce qui leur manque une fois le versement fait ; s'il
    reste de l'argent (toutes les lignes à leur cible), il suit les parts visées.
    """
    current = sum((line.value for line in lines), ZERO)
    total_target = sum((line.target_pct for line in lines), ZERO)
    if not lines or total_target <= 0:
        return []
    after = current + contribution
    wanted = {line.key: after * line.target_pct / total_target for line in lines}
    missing = {line.key: max(ZERO, wanted[line.key] - line.value) for line in lines}
    needed = sum(missing.values(), ZERO)
    buys = {}
    for line in lines:
        if contribution <= 0:
            buys[line.key] = ZERO
        elif needed >= contribution:
            buys[line.key] = contribution * missing[line.key] / needed
        else:  # assez pour tout combler : le reste suit les parts visées
            rest = contribution - needed
            buys[line.key] = missing[line.key] + rest * line.target_pct / total_target
    return [
        Advice(
            key=line.key,
            value=line.value,
            current_pct=line.value / current * HUNDRED if current else ZERO,
            target_pct=line.target_pct / total_target * HUNDRED,
            gap=current * line.target_pct / total_target - line.value,
            buy=buys[line.key],
        )
        for line in lines
    ]
