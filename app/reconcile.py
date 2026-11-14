"""Réconciliation d'un compte avec le relevé de la banque (D-063), fonctions pures.

On compare ce que l'app calcule (espèces, quantité de chaque ligne) à ce que la banque
affiche, saisi pour l'occasion. Rien n'est enregistré : l'écart et ses causes probables sont
renvoyés, et une correction n'est proposée qu'en opération à valider par l'utilisateur.
"""

from decimal import Decimal

ZERO = Decimal("0")
CENT = Decimal("0.01")

CASH_UP = [
    "un versement non saisi",
    "un dividende ou des intérêts non saisis",
    "une vente non saisie",
]
CASH_DOWN = [
    "des frais (droits de garde, gestion) non saisis",
    "un achat non saisi",
    "un retrait non saisi",
]
LIVRET_UP = ["des intérêts versés", "un versement non saisi"]
LIVRET_DOWN = ["un retrait non saisi"]
QTY_UP = ["un achat non saisi", "un dividende réinvesti en titres", "une division du titre"]
QTY_DOWN = ["une vente non saisie", "un regroupement de titres"]


def cash_check(app_cash: Decimal, bank_cash: Decimal, livret: bool, entered: bool = False) -> dict:
    """Écart d'espèces (banque − app), causes probables et correction proposée.

    Livret : on propose de prendre le solde de la banque comme nouveau solde (D-041), car les
    intérêts ne sont pas des opérations. Autres comptes : un versement si la banque a plus,
    des frais si elle a moins (D-062 : les espèces se déduisent des opérations). CTO, crypto,
    Autre (`entered`, D-075) : solde saisi lui aussi, donc même correction que le livret.
    """
    gap = bank_cash - app_cash
    out = {"app": app_cash, "bank": bank_cash, "gap": gap, "ok": abs(gap) < CENT}
    if out["ok"]:
        return out | {"causes": [], "correction": None}
    if livret:
        causes = LIVRET_UP if gap > 0 else LIVRET_DOWN
        return out | {"causes": causes, "correction": {"kind": "solde", "amount": bank_cash}}
    causes = CASH_UP if gap > 0 else CASH_DOWN
    if entered:
        return out | {"causes": causes, "correction": {"kind": "solde", "amount": bank_cash}}
    op_type = "versement" if gap > 0 else "frais"
    return out | {"causes": causes, "correction": {"kind": op_type, "amount": abs(gap)}}


def line_check(app_qty: Decimal, bank_qty: Decimal) -> dict:
    """Écart de quantité d'une ligne (banque − app) et causes probables. Pas de correction
    automatique : il faudrait le prix, que seul le relevé de l'opération donne."""
    gap = bank_qty - app_qty
    ok = gap == 0
    causes = [] if ok else QTY_UP if gap > 0 else QTY_DOWN
    return {"app": app_qty, "bank": bank_qty, "gap": gap, "ok": ok, "causes": causes}
