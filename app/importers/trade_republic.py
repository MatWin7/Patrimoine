"""Profil Trade Republic : export « Exportation de transactions » (D-071), fonctions pures.

L'export mélange tout le compte : dépenses par carte, virements, intérêts et investissements.
Seules les opérations d'investissement sont gardées ; le reste est ignoré avec sa raison.
Les colonnes de contrepartie (nom, IBAN, référence, code commerçant) ne sont jamais lues.

Chaque ligne gardée reçoit une « partie » (PEA, compte-titres, obligations, non coté, crypto),
que l'utilisateur relie à un compte de l'app, et l'identifiant `transaction_id` de TR, qui sert
d'empreinte anti-doublon.
"""

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal
from statistics import median

from app.importers.csv_generique import CsvLine, _plain, parse_date, parse_decimal

ID = "trade_republic"
LABEL = "Trade Republic"
INSTITUTION = "Trade Republic"
# Colonnes indispensables : leur présence suffit à reconnaître l'export.
REQUIRED = (
    "date",
    "account_type",
    "category",
    "type",
    "asset_class",
    "name",
    "symbol",
    "shares",
    "price",
    "amount",
    "fee",
    "currency",
    "transaction_id",
)

# Parties du relevé : libellé, type de compte de l'app proposé, nom proposé à la création.
PARTS = {
    "pea": ("PEA", "pea", "Trade Republic PEA"),
    "cto": ("Compte-titres (actions, ETF)", "cto", "Trade Republic CTO"),
    "obligations": ("Obligations", "cto", "Trade Republic CTO"),
    "non_cote": ("Titres non cotés", "cto", "Trade Republic CTO"),
    "crypto": ("Crypto", "crypto", "Trade Republic Crypto"),
}
# Classe d'actif TR -> classe proposée à la création de l'actif.
CLASSES = {
    "FUND": "etf",
    "STOCK": "action",
    "BOND": "obligation",
    "PRIVATE_FUND": "autre",
    "CRYPTO": "crypto",
}

CARD = "dépense par carte"
TRANSFER = "virement du compte espèces"
# Types ignorés d'office (vie du compte espèces, primes) et leur raison.
IGNORED = {
    "CARD_TRANSACTION": CARD,
    "CARD_TRANSACTION_INTERNATIONAL": CARD,
    "CARD_ORDERING_FEE": "frais de carte",
    "TRANSFER_INSTANT_INBOUND": TRANSFER,
    "TRANSFER_INSTANT_OUTBOUND": TRANSFER,
    "TRANSFER_INBOUND": TRANSFER,
    "TRANSFER_OUTBOUND": TRANSFER,
    "CUSTOMER_INBOUND": TRANSFER,
    "CUSTOMER_INPAYMENT": TRANSFER,
    "CUSTOMER_OUTBOUND_REQUEST": TRANSFER,
    "TAX_OPTIMIZATION": "régularisation d'impôt du compte espèces",
    "PRIVATE_MARKET_BUY": "prépaiement du non coté (l'achat a sa propre ligne)",
    "BONUS": "prime Trade Republic",
    "PEA_MARKETING": "prime Trade Republic",
}
# Argent offert par TR et investi aussitôt dans le titre (D-076) : la prime n'est pas importée,
# son montant devient la part offerte de l'achat du même titre le même jour.
REWARDS = {"BENEFITS_SAVEBACK": "Saveback", "STOCKPERK": "Stockperk", "GIFT": "action offerte"}
# Cadeau (« Gift lottery ») sans titre : TR verse le prix de l'action tirée au sort, frais
# compris, puis l'achète aussitôt. Écart toléré entre le cadeau et le total de l'achat.
GIFT_TOLERANCE = (Decimal("2"), Decimal("0.10"))
MATURITY = ("FINAL_MATURITY", "FINAL_MATURITY_CANCELLED")
PLAN_WORDS = re.compile(r"savings plan|plan d (epargne|investissement)|sparplan")


def detect(header: list[str]) -> bool:
    names = {h.strip().lower() for h in header}
    return all(c in names for c in REQUIRED)


def part_of(row: dict[str, str]) -> str:
    if row["account_type"].upper() == "PEA":
        return "pea"
    return {"CRYPTO": "crypto", "BOND": "obligations", "PRIVATE_FUND": "non_cote"}.get(
        row["asset_class"].upper(), "cto"
    )


def _num(row: dict[str, str], column: str) -> Decimal | None:
    return parse_decimal(row.get(column, ""))


def _base(number: int, row: dict[str, str]) -> CsvLine:
    """Champs communs : date locale (la colonne `datetime` est en UTC), actif, partie, clé."""
    symbol = row["symbol"].strip() or None
    return CsvLine(
        number,
        op_date=parse_date(row["date"]),
        asset=symbol,
        asset_name=(row["name"].strip() or None) if symbol else None,
        currency=row["currency"].strip().upper() or None,
        part=part_of(row),
        key=row["transaction_id"].strip() or None,
        asset_class=CLASSES.get(row["asset_class"].upper()),
    )


def _skip(number: int, reason: str) -> CsvLine:
    return CsvLine(number, skip=reason)


def _trade(number: int, row: dict[str, str], op_type: str) -> CsvLine:
    line = _base(number, row)
    quantity, price = _num(row, "shares"), _num(row, "price")
    if line.asset is None:
        raise ValueError("ISIN manquant")
    if not quantity:
        raise ValueError("quantité manquante ou nulle")
    if price is None:
        amount = _num(row, "amount")
        if not amount:
            raise ValueError("prix manquant")
        price = abs(amount) / abs(quantity)
    plan = op_type == "achat" and bool(PLAN_WORDS.search(_plain(row.get("description", ""))))
    return replace(
        line,
        op_type=op_type,
        quantity=abs(quantity),
        unit_price=abs(price),
        fees=abs(_num(row, "fee") or Decimal("0")),
        plan=plan,
        note="Plan d'investissement" if plan else None,
    )


def _income(number: int, row: dict[str, str], note: str | None = None) -> CsvLine:
    line = _base(number, row)
    amount = _num(row, "amount")
    if not amount:
        raise ValueError("montant manquant ou nul")
    tax = _num(row, "tax")
    withheld = -tax if tax and tax < 0 else None  # positif = remboursement, pas une retenue
    return replace(line, op_type="dividende", amount=abs(amount), note=note, tax_withheld=withheld)


def _cash(number: int, row: dict[str, str], op_type: str) -> CsvLine:
    line = _base(number, row)
    amount = _num(row, "amount")
    if not amount:
        raise ValueError("montant manquant ou nul")
    return replace(line, op_type=op_type, amount=abs(amount), asset=None, asset_name=None)


def _one(number: int, row: dict[str, str]) -> CsvLine:
    """Une ligne seule (les types qui vont par groupes sont traités dans `parse`)."""
    kind = row["type"].strip().upper()
    has_asset = bool(row["symbol"].strip())
    pea = row["account_type"].upper() == "PEA"
    if kind in IGNORED:
        return _skip(number, IGNORED[kind])
    if kind == "BUY":
        return _trade(number, row, "achat")
    if kind == "SELL":
        return _trade(number, row, "vente")
    if kind == "DIVIDEND":
        return _income(number, row)
    if kind == "INTEREST_PAYMENT":
        return (
            _income(number, row, "Coupon")
            if has_asset
            else _skip(number, "intérêts du compte espèces")
        )
    if kind in REWARDS and not has_asset:
        return _skip(number, "prime Trade Republic sans titre associé")
    if kind in ("TRANSFER_IN", "TRANSFER_OUT"):
        if pea:
            return _cash(number, row, "versement" if kind == "TRANSFER_IN" else "retrait")
        return _skip(number, "virement entre le compte espèces et le PEA (compté côté PEA)")
    if not has_asset and row["category"].upper() == "CASH":
        return _skip(number, f"opération du compte espèces ({kind})")
    raise ValueError(f"type Trade Republic inconnu « {kind} » : à saisir à la main")


def _grouped(rows: list[tuple[int, dict[str, str]]]) -> list[CsvLine]:
    """Réceptions de titres, fusions et remboursements d'obligations : plusieurs lignes TR
    pour un seul évènement."""
    out: list[CsvLine] = []
    by_kind: dict[str, list[tuple[int, dict[str, str]]]] = defaultdict(list)
    for number, row in rows:
        kind = row["type"].strip().upper()
        by_kind["MATURITY" if kind in MATURITY else kind].append((number, row))

    # Réceptions de titres : une entrée et une sortie du même titre le même jour s'annulent
    # (transfert interne chez TR). Le reste n'a pas de prix : à saisir à la main.
    receipts: dict[tuple, list] = defaultdict(list)
    for number, row in by_kind["FREE_RECEIPT"]:
        receipts[(row["date"], row["symbol"], row["account_type"])].append((number, row))
    for group in receipts.values():
        net = sum((_num(r, "shares") or Decimal("0") for _, r in group), Decimal("0"))
        for number, _ in group:
            if net == 0:
                out.append(_skip(number, "transfert interne de titres (entrée et sortie)"))
            elif net > 0:
                message = "titres reçus sans prix : à saisir comme « Titres déjà détenus »"
                out.append(CsvLine(number, error=message))
            else:
                out.append(CsvLine(number, error="titres sortis du compte : à saisir à la main"))

    # Fusion de fonds : l'ancien titre sort, le nouveau entre, sans montant. Vente et achat liés,
    # au prix de revient (fixé à la préparation, D-071) : une fusion ne crée pas de plus-value.
    mergers: dict[tuple, list] = defaultdict(list)
    for number, row in by_kind["MERGER"]:
        mergers[(row["date"], row["account_type"])].append((number, row))
    for group in mergers.values():
        out_rows = [(n, r) for n, r in group if (_num(r, "shares") or 0) < 0]
        in_rows = [(n, r) for n, r in group if (_num(r, "shares") or 0) > 0]
        if len(out_rows) != 1 or len(in_rows) != 1:
            out += [CsvLine(n, error="fusion de titres à saisir à la main") for n, _ in group]
            continue
        (n_old, old), (n_new, new) = out_rows[0], in_rows[0]
        link = f"{old['transaction_id']}>{new['transaction_id']}"
        sale, buy = _base(n_old, old), _base(n_new, new)
        out.append(
            replace(
                sale,
                op_type="vente",
                quantity=-_num(old, "shares"),
                merger=link,
                note="Fusion : ancien titre",
            )
        )
        out.append(
            replace(
                buy,
                op_type="achat",
                quantity=_num(new, "shares"),
                merger=link,
                note="Fusion : nouveau titre",
            )
        )

    # Remboursement d'une obligation : une ligne de titres (quantité sortie) et une ligne
    # d'argent (montant reçu), parfois annulées puis refaites. On garde le solde par titre.
    maturities: dict[str, list] = defaultdict(list)
    for number, row in by_kind["MATURITY"]:
        maturities[row["symbol"]].append((number, row))
    for group in maturities.values():
        group.sort()
        shares = sum((_num(r, "shares") or Decimal("0") for _, r in group), Decimal("0"))
        cash = sum((_num(r, "amount") or Decimal("0") for _, r in group), Decimal("0"))
        first, rest = group[0], group[1:]
        if shares == 0:
            out += [_skip(n, "remboursement d'obligation annulé") for n, _ in group]
            continue
        if shares > 0 or cash <= 0:
            out += [
                CsvLine(n, error="remboursement d'obligation à saisir à la main") for n, _ in group
            ]
            continue
        last_day = max(parse_date(r["date"]) for _, r in group)
        line = _base(first[0], first[1])
        out.append(
            replace(
                line,
                op_date=last_day,
                op_type="vente",
                quantity=-shares,
                unit_price=cash / -shares,
                key="+".join(sorted(r["transaction_id"] for _, r in group)),
                note="Remboursement à l'échéance",
            )
        )
        out += [_skip(n, f"regroupée avec la ligne {first[0]}") for n, _ in rest]
    return out


def _rewards(lines: list[CsvLine], rewards: list[tuple[int, dict[str, str]]]) -> list[CsvLine]:
    """Saveback, Stockperk et cadeaux (D-076, D-078) : TR verse la prime puis achète le titre le
    même jour. La prime devient la part offerte de cet achat (au plus son total, frais compris) ;
    s'il y a plusieurs achats, celui dont le montant est le plus proche. Un cadeau ne nomme pas
    le titre : achat du même jour sur la même partie du relevé, au total proche du cadeau. Sans
    achat trouvé, la prime est ignorée."""
    out = []
    by_line = {line.line: i for i, line in enumerate(lines)}
    for number, row in rewards:
        label = REWARDS[row["type"].strip().upper()]
        amount = abs(_num(row, "amount") or Decimal("0"))
        if not amount:
            out.append(CsvLine(number, error="montant manquant ou nul"))
            continue
        day, symbol, part = parse_date(row["date"]), row["symbol"].strip(), part_of(row)
        buys = [
            b
            for b in lines
            if b.op_type == "achat" and not b.error and b.offered is None and not b.merger
            if b.op_date == day and (b.asset == symbol or not symbol)
            if b.part == part or (not symbol and part != "pea" and b.part != "pea")
        ]
        # Prime d'un titre : comparée au prix des titres ; cadeau : le montant versé couvre
        # aussi les frais.
        gaps = {
            b.line: abs(b.quantity * b.unit_price + (0 if symbol else b.fees) - amount)
            for b in buys
        }
        if not symbol:
            limit = max(GIFT_TOLERANCE[0], amount * GIFT_TOLERANCE[1])
            buys = [b for b in buys if gaps[b.line] <= limit]
        if not buys:
            out.append(_skip(number, "prime Trade Republic sans achat du même titre ce jour-là"))
            continue
        buy = min(buys, key=lambda b: (gaps[b.line], b.line))
        total = buy.quantity * buy.unit_price + buy.fees
        lines[by_line[buy.line]] = replace(
            buy, offered=min(amount, total), plan=False, note=f"Offert par Trade Republic ({label})"
        )
        out.append(_skip(number, "prime Trade Republic reportée sur l'achat offert du même jour"))
    return out


GROUPED = ("FREE_RECEIPT", "MERGER", *MATURITY)


def parse(header: list[str], data: list[list[str]], first: int = 2) -> list[CsvLine]:
    """Lignes de l'export, dans l'ordre du fichier ; `first` = numéro de la 1re ligne de données."""
    names = [h.strip().lower() for h in header]
    lines: list[CsvLine] = []
    grouped: list[tuple[int, dict[str, str]]] = []
    rewards: list[tuple[int, dict[str, str]]] = []
    for i, cells in enumerate(data):
        number = first + i
        row = defaultdict(str, zip(names, (c.strip() for c in cells), strict=False))
        try:
            kind = row["type"].strip().upper()
            if kind in GROUPED:
                grouped.append((number, row))
                continue
            if kind in REWARDS and (row["symbol"].strip() or kind == "GIFT"):
                rewards.append((number, row))
                continue
            lines.append(_one(number, row))
        except ValueError as err:
            lines.append(CsvLine(number, error=str(err)))
    try:
        lines += _grouped(grouped)
    except ValueError as err:
        lines += [CsvLine(n, error=str(err)) for n, _ in grouped]
    lines += _rewards(lines, rewards)
    return sorted((_checked(line) for line in lines), key=lambda line: line.line)


def _checked(line: CsvLine) -> CsvLine:
    if line.op_date and line.op_date > date.today():
        return CsvLine(line.line, error="date dans le futur")
    if line.op_type and line.currency and not re.fullmatch(r"[A-Z]{3}", line.currency):
        return CsvLine(line.line, error=f"devise illisible « {line.currency} »")
    return line


# --- Plans d'investissement -------------------------------------------------------------
# Les exécutions d'un même plan (même partie, même titre) donnent sa fréquence, son montant et
# son jour habituels. Le plan est proposé à l'utilisateur, jamais créé seul (D-055, D-071).


@dataclass(frozen=True)
class Plan:
    key: str  # partie|titre, pour que l'utilisateur coche les plans à créer
    part: str
    asset: str
    asset_name: str | None
    frequency: str | None  # None : exécutions trop irrégulières ou trop peu nombreuses
    day: int | None
    amount: Decimal
    first: date
    last: date
    count: int
    active: bool


# Écart médian entre deux exécutions (jours) -> fréquence ; durée au-delà de laquelle un plan
# sans exécution est considéré comme arrêté.
FREQUENCY_GAPS = (
    ("hebdomadaire", 4, 10, 21),
    ("bimensuel", 11, 20, 35),
    ("mensuel", 21, 45, 62),
    ("trimestriel", 70, 110, 190),
)


def _mode(values):
    """Valeur la plus fréquente ; à égalité, la plus récente (dernière de la liste)."""
    counts = Counter(values)
    best = max(counts.values())
    return next(v for v in reversed(values) if counts[v] == best)


def detect_plans(lines: list[CsvLine], today: date | None = None) -> list[Plan]:
    today = today or date.today()
    groups: dict[tuple[str, str], list[CsvLine]] = defaultdict(list)
    for line in lines:
        if line.plan and not line.error and line.op_type == "achat":
            groups[(line.part, line.asset)].append(line)
    plans = []
    for (part, asset), runs in groups.items():
        runs.sort(key=lambda x: x.op_date)
        days = [r.op_date for r in runs]
        gaps = [(b - a).days for a, b in zip(days, days[1:], strict=False) if b != a]
        frequency, day, active = None, None, False
        if gaps:
            gap = median(gaps)
            for name, low, high, stale in FREQUENCY_GAPS:
                if low <= gap <= high:
                    frequency = name
                    active = today - days[-1] <= timedelta(days=stale)
                    break
        if frequency in ("hebdomadaire", "bimensuel"):
            day = _mode([d.isoweekday() for d in days])
        elif frequency:
            day = min(_mode([d.day for d in days]), 28)
        amounts = [(r.quantity * r.unit_price).quantize(Decimal("0.01")) for r in runs]
        plans.append(
            Plan(
                key=f"{part}|{asset}",
                part=part,
                asset=asset,
                asset_name=runs[-1].asset_name,
                frequency=frequency,
                day=day,
                amount=_mode(amounts),
                first=days[0],
                last=days[-1],
                count=len(runs),
                active=active,
            )
        )
    return sorted(plans, key=lambda p: (not p.active, p.part, p.asset_name or p.asset))
