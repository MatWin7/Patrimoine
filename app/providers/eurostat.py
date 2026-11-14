"""Eurostat : indice des prix à la consommation harmonisé (IPCH) mensuel, sans clé d'API.

Règle 3 : la requête ne contient que le nom de la série publique, le pays et l'unité de
l'indice, jamais un montant (D-045). Réponse au format JSON-stat : les valeurs sont rangées
par position, et la dimension « time » donne la position de chaque mois (« 2026-03 »).
"""

from datetime import date
from decimal import Decimal

import httpx2

from app.providers import ProviderError, get_json, positive_decimal

BASE = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/"
# Depuis 2026, Eurostat publie l'IPCH en nomenclature ECOICOP 2 (base 2025 = 100) dans de
# nouvelles tables ; les anciennes (prc_hicp_midx) sont figées à décembre 2025 (D-067). On
# essaie la nouvelle table d'abord, puis l'ancienne, qui donne au moins le passé : le taux
# saisi prend le relais après le dernier mois publié. Le rapport entre deux mois, seul
# utilisé, ne dépend pas de la base.
SOURCES = (
    # ECOICOP 2 : la dimension s'appelle « coicop18 » et l'ensemble des produits « TOTAL ».
    ("prc_hicp_minr", {"unit": "I25", "coicop18": "TOTAL"}),
    # Repli : variations mensuelles en %, enchaînées en indice (même rapport entre deux mois).
    ("prc_hicp_minr", {"unit": "RCH_M", "coicop18": "TOTAL"}),
    ("prc_hicp_midx", {"unit": "I25", "coicop": "CP00"}),
    ("prc_hicp_midx", {"unit": "I15", "coicop": "CP00"}),
)


def get_hicp(client: httpx2.Client, geo: str = "FR") -> dict[date, Decimal]:
    """Indice mensuel {premier jour du mois: valeur}, tous ensemble (ensemble des produits)."""
    errors = []
    for dataset, series in SOURCES:
        label = " ".join([dataset, *series.values()])
        try:
            data = get_json(client, BASE + dataset, params={"format": "JSON", "geo": geo, **series})
            values = _parse(data, rates=series["unit"] == "RCH_M")
        except ProviderError as err:
            errors.append(f"{label} : {err}")
            continue
        if values:
            return values
        errors.append(f"{label} : série vide")
    raise ProviderError("IPCH indisponible (" + " ; ".join(errors) + ")")


def _parse(data: object, rates: bool = False) -> dict[date, Decimal]:
    """Indice {1er du mois: valeur} ; `rates` : la série donne des variations mensuelles en %,
    enchaînées à partir de 100 (seul le rapport entre deux mois sert)."""
    try:
        months = data["dimension"]["time"]["category"]["index"]
        raw = data["value"]
    except (KeyError, TypeError) as err:
        raise ProviderError("réponse Eurostat inattendue") from err
    out = {}
    for label, position in months.items():
        # JSON-stat autorise un objet {position: valeur} (Eurostat) ou une simple liste.
        if isinstance(raw, dict):
            value = raw.get(str(position))
        else:
            value = raw[position] if isinstance(raw, list) and position < len(raw) else None
        if value is None:
            continue  # mois pas encore publié
        year, month = label.replace("M", "-").split("-")
        out[date(int(year), int(month), 1)] = value if rates else positive_decimal(value)
    out = dict(sorted(out.items()))
    if rates:
        index, chained = Decimal(100), {}
        for month, rate in out.items():
            if not isinstance(rate, Decimal) or not rate.is_finite() or rate <= -100:
                raise ProviderError(f"variation invalide : {rate!r}")
            index *= 1 + rate / 100
            chained[month] = index
        return chained
    return out
