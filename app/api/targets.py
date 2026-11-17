"""Routes de l'allocation cible (D-058), par ligne ou par catégorie (D-061). Calculée sur le
cache des cours et des taux, sans aucun appel réseau."""

from decimal import Decimal

from fastapi import APIRouter, HTTPException

from app import db, rebalance, repository
from app.api.common import by_asset_eur, category_of, offline_state, pct
from app.portfolio import ZERO, ratio
from app.schemas import CategoryTargetsIn, TargetsIn, money, plain

router = APIRouter()


def _advice_out(a, whole: Decimal, value: Decimal, target) -> dict:
    return {
        "value": money(value),
        "weight_pct": pct(ratio(value, whole)) if whole else None,
        "target": None if target is None else plain(target),
        # Déjà en % (rebalance travaille en parts sur 100).
        "current_pct": money(a.current_pct) if a else None,
        "target_pct": money(a.target_pct) if a else None,
        "gap": money(a.gap) if a else None,
        "buy": money(a.buy) if a else None,
    }


@router.get("/api/targets")
def targets_report(contribution: Decimal = ZERO, level: str = "ligne") -> dict:
    """Allocation cible par ligne (D-058) ou par catégorie (D-061) : part actuelle et visée,
    écart en EUR, et répartition du prochain versement (`contribution`) sans rien vendre.
    Affichage seulement (règle 1)."""
    if not contribution.is_finite() or contribution < 0:
        raise HTTPException(status_code=422, detail="Versement invalide.")
    if level not in ("ligne", "categorie"):
        raise HTTPException(status_code=422, detail="Niveau inconnu.")
    state = offline_state()
    if level == "categorie":
        return _category_targets(state, contribution)
    values = by_asset_eur(state, "value")
    targets = state["targets"]
    assets = state["assets"]
    lines = [
        rebalance.Line(asset_id, values.get(asset_id, ZERO), pct)
        for asset_id, pct in targets.items()
    ]
    advice = {a.key: a for a in rebalance.rebalance(lines, contribution)}
    whole = sum(values.values(), ZERO)
    rows = []
    # Lignes avec une cible d'abord : le tableau défile, elles restent visibles.
    order = sorted(
        set(values) | set(targets), key=lambda i: (i not in targets, -values.get(i, ZERO))
    )
    for asset_id in order:
        value = values.get(asset_id, ZERO)
        out = _advice_out(advice.get(asset_id), whole, value, targets.get(asset_id))
        rows.append({"asset_id": asset_id, "asset": assets[asset_id].name, **out})
    # Actifs créés mais ni détenus ni visés : on peut leur donner une cible avant d'acheter.
    addable = sorted(
        ({"asset_id": i, "asset": a.name} for i, a in assets.items() if i not in order),
        key=lambda r: r["asset"].lower(),
    )
    return {
        "level": "ligne",
        "rows": rows,
        "addable": addable,
        "targets_total": plain(sum(targets.values(), ZERO)),
        "contribution": money(contribution),
    }


def _category_targets(state: dict, contribution: Decimal) -> dict:
    """Cibles par catégorie : le montant à acheter d'une catégorie va sur sa plus grosse ligne
    (un seul ordre, moins de frais), choix retenu pour D-061."""
    values = by_asset_eur(state, "value")
    assets = state["assets"]
    targets = state["category_targets"]
    members: dict[str, list[int]] = {}
    for asset_id in values:
        members.setdefault(category_of(assets[asset_id]), []).append(asset_id)
    totals = {c: sum((values[i] for i in ids), ZERO) for c, ids in members.items()}
    lines = [rebalance.Line(c, totals.get(c, ZERO), pct) for c, pct in targets.items()]
    advice = {a.key: a for a in rebalance.rebalance(lines, contribution)}
    whole = sum(totals.values(), ZERO)
    rows = []
    order = sorted(
        set(totals) | set(targets), key=lambda c: (c not in targets, -totals.get(c, ZERO))
    )
    # Actifs créés par catégorie, détenus ou non : de quoi acheter dans une catégorie vide.
    created: dict[str, list[str]] = {}
    for asset in sorted(assets.values(), key=lambda a: a.name.lower()):
        created.setdefault(category_of(asset), []).append(asset.name)
    for category in order:
        ids = sorted(members.get(category, []), key=lambda i: -values[i])
        a = advice.get(category)
        # Ligne sur laquelle acheter : la plus grosse ; sinon le premier actif créé de la
        # catégorie (cible posée avant tout achat) ; aucune si la catégorie n'a aucun actif.
        candidates = [assets[ids[0]].name] if ids else created.get(category, [])[:1]
        out = _advice_out(a, whole, totals.get(category, ZERO), targets.get(category))
        rows.append(
            {
                "category": category,
                **out,
                "lines": [assets[i].name for i in ids],
                "buy_asset": candidates[0] if candidates and a and a.buy > 0 else None,
            }
        )
    return {
        "level": "categorie",
        "rows": rows,
        "addable": sorted((c for c in created if c not in order), key=str.lower),
        "targets_total": plain(sum(targets.values(), ZERO)),
        "contribution": money(contribution),
    }


@router.put("/api/targets")
def save_targets(body: TargetsIn) -> dict:
    with db.connect() as conn:
        known = {a.id for a in repository.list_assets(conn)}
        if set(body.targets) - known:
            raise HTTPException(status_code=404, detail="Actif inconnu.")
        repository.save_targets(conn, body.targets)
    conn.close()
    return targets_report()


@router.put("/api/targets/categories")
def save_category_targets(body: CategoryTargetsIn) -> dict:
    """Une catégorie sans ligne détenue est acceptée : on peut viser une catégorie avant d'y
    avoir acheté quoi que ce soit (rien à acheter n'est alors proposé)."""
    with db.connect() as conn:
        repository.save_category_targets(conn, body.targets)
    conn.close()
    return targets_report(level="categorie")
