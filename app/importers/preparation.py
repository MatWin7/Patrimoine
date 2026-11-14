"""Lignes d'un CSV prêtes à enregistrer : actif reconnu, devise, doublons (D-053).

Statuts d'une ligne :
- "ok" : sera importée ;
- "erreur" : illisible ou incohérente (message à l'appui) ;
- "actif_inconnu" : aucun actif enregistré ne correspond (le créer dans « Gérer » d'abord) ;
- "deja_importee" : la même ligne vient d'un import précédent (empreinte identique) ;
- "deja_saisie" : une opération saisie à la main a la même date, le même type, le même actif et
  la même quantité ou le même montant ; ignorée pour ne pas compter deux fois la même chose ;
- "ignoree" : écartée par un profil de plateforme (dépense par carte, virement...), D-071 ;
- "sans_compte" : partie du relevé (PEA, compte-titres...) reliée à aucun compte de l'app.
"""

import hashlib
import sqlite3
from dataclasses import dataclass, replace
from decimal import Decimal

from app.importers.csv_generique import CsvLine
from app.models import Account, Asset, Operation
from app.portfolio import PositionError, RateHistory, compute_positions

SOURCE = "csv"


@dataclass(frozen=True)
class Prepared:
    csv: CsvLine
    status: str
    message: str | None = None
    asset: Asset | None = None
    currency: str | None = None
    import_key: str | None = None
    account: Account | None = None


def find_asset(text: str, assets: list[Asset]) -> Asset | None:
    """ISIN, puis ticker Yahoo ou id CoinGecko, puis nom exact (sans tenir compte de la casse)."""
    key = text.strip()
    for match in (
        lambda a: a.isin and a.isin.upper() == key.upper(),
        lambda a: a.ticker and a.ticker.upper() == key.upper(),
        lambda a: a.coingecko_id and a.coingecko_id.lower() == key.lower(),
        lambda a: a.name.casefold() == key.casefold(),
    ):
        found = [a for a in assets if match(a)]
        if len(found) == 1:
            return found[0]
    return None


def _text(value: Decimal | None) -> str:
    """Écriture canonique d'un nombre : « 10 », « 10.0 » et « 10,00 » donnent la même empreinte."""
    return "" if value is None else format(value.normalize(), "f")


def platform_key(key: str) -> str:
    """Empreinte d'une ligne qui porte l'identifiant de l'opération chez la plateforme : stable
    quel que soit le compte choisi, et sans rien révéler de l'identifiant lui-même."""
    return hashlib.sha256(f"id|{key}".encode()).hexdigest()[:32]


def fingerprint(account_id: int, line: CsvLine, asset_id: int | None, occurrence: int) -> str:
    """Empreinte d'une ligne : la réimporter (ou un relevé qui la contient aussi) ne la double
    pas. `occurrence` distingue deux lignes identiques d'un même fichier (deux achats pareils
    le même jour) : la 2e garde sa place à chaque import du fichier."""
    parts = [
        str(account_id),
        line.op_date.isoformat(),
        line.op_type,
        str(asset_id or ""),
        _text(line.quantity),
        _text(line.unit_price),
        _text(line.amount),
        _text(line.fees),
        str(occurrence),
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]


def _same_manual(op: Operation, line: CsvLine, account_id: int, asset_id: int | None) -> bool:
    if (op.account_id, op.op_date, op.op_type, op.asset_id) != (
        account_id,
        line.op_date,
        line.op_type,
        asset_id,
    ):
        return False
    if line.quantity is not None:
        return op.quantity == line.quantity
    return op.amount == line.amount


def prepare(
    conn: sqlite3.Connection,
    accounts: dict[str | None, Account],
    lines: list[CsvLine],
    assets: list[Asset],
    operations: list[Operation],
    fx: RateHistory | None = None,
) -> list[Prepared]:
    """`accounts` : compte de chaque partie du relevé (clé None pour l'import générique).
    `fx` : taux pour convertir une ligne `convertible` dans la devise de son actif (D-085)."""
    imported = {
        r[0]
        for r in conn.execute(
            "SELECT import_key FROM operation WHERE source = ? AND import_key IS NOT NULL",
            (SOURCE,),
        )
    }
    manual_ids = {r[0] for r in conn.execute("SELECT id FROM operation WHERE source = 'manuel'")}
    manual = [op for op in operations if op.id in manual_ids]
    seen: dict[tuple, int] = {}
    out = []
    for line in lines:
        if line.error:
            out.append(Prepared(line, "erreur", line.error))
            continue
        if line.skip:
            out.append(Prepared(line, "ignoree", line.skip))
            continue
        # Actif d'abord : à la première utilisation, comptes et actifs se créent en même temps.
        account = accounts.get(line.part)
        asset = None
        if line.asset is not None:
            asset = find_asset(line.asset, assets)
            if asset is None and line.asset_name:
                asset = find_asset(line.asset_name, assets)
            if asset is None:
                out.append(
                    Prepared(
                        line, "actif_inconnu", f"actif inconnu « {line.asset} »", account=account
                    )
                )
                continue
        if account is None:
            out.append(Prepared(line, "sans_compte", "choisis le compte de cette partie"))
            continue
        # Devise : celle de l'actif pour un achat, une vente ou un dividende ; du compte sinon.
        currency = asset.currency if asset else account.currency
        if line.currency and line.currency != currency and line.convertible and asset and fx:
            line = convert_line(line, currency, fx)
        if line.currency and line.currency != currency:
            message = f"devise {line.currency} différente de celle attendue ({currency})"
            if asset and asset.asset_class in ("action", "etf"):
                message += f" : choisis pour cet actif une cotation en {line.currency}"
            elif asset:
                message += (
                    f" : passe la devise de « {asset.name} » à {line.currency} dans « Gérer »"
                    " (ses opérations seront converties)"
                )
            out.append(Prepared(line, "erreur", message, account=account, asset=asset))
            continue
        if line.key:
            key = platform_key(line.key)
        else:
            base = (line.op_date, line.op_type, asset.id if asset else None, _text(line.quantity))
            base += (_text(line.unit_price), _text(line.amount), _text(line.fees))
            occurrence = seen.get((account.id, *base), 0)
            seen[(account.id, *base)] = occurrence + 1
            key = fingerprint(account.id, line, asset.id if asset else None, occurrence)
        prepared = Prepared(line, "ok", None, asset, currency, key, account)
        if key in imported:
            prepared = replace(prepared, status="deja_importee", message="déjà importée")
        elif any(_same_manual(op, line, account.id, asset.id if asset else None) for op in manual):
            message = "une opération identique a déjà été saisie à la main"
            prepared = replace(prepared, status="deja_saisie", message=message)
        out.append(prepared)
    return _price_mergers(out, operations)


AMOUNTS = ("unit_price", "amount", "fees", "offered", "tax_withheld")


def convert_line(line: CsvLine, currency: str, fx: RateHistory) -> CsvLine:
    """Montants de la ligne dans `currency`, au taux BCE de son jour (D-085). Sans taux, la
    ligne reste dans sa devise : elle sera refusée avec le message habituel."""
    rates = fx.on(line.op_date)
    if line.currency not in rates or currency not in rates:
        return line
    factor = rates[currency] / rates[line.currency]

    def conv(value: Decimal | None) -> Decimal | None:
        if value is None:
            return None
        return Decimal(format((value * factor).quantize(Decimal("1e-12")).normalize(), "f"))

    return replace(line, currency=currency, **{k: conv(getattr(line, k)) for k in AMOUNTS})


def as_operation(p: Prepared, op_id: int) -> Operation:
    line = p.csv
    return Operation(
        op_id,
        p.account.id,
        p.asset.id if p.asset else None,
        line.op_date,
        line.op_type,
        line.quantity,
        line.unit_price,
        line.amount,
        line.fees,
        p.currency,
        line.note,
    )


def _price_mergers(prepared: list[Prepared], operations: list[Operation]) -> list[Prepared]:
    """Fusion : l'ancien titre est vendu à son prix de revient du jour, le nouveau acheté pour
    ce même total. Ni plus-value ni mouvement d'espèces (D-071)."""
    links = {p.csv.merger for p in prepared if p.csv.merger}
    for link in sorted(
        links, key=lambda k: min(p.csv.op_date for p in prepared if p.csv.merger == k)
    ):
        pair = [i for i, p in enumerate(prepared) if p.csv.merger == link]
        if any(prepared[i].status != "ok" for i in pair):
            for i in pair:
                if prepared[i].status == "ok":
                    message = "fusion incomplète : l'autre ligne n'est pas importable"
                    prepared[i] = replace(prepared[i], status="erreur", message=message)
            continue
        sale = next(i for i in pair if prepared[i].csv.op_type == "vente")
        buy = next(i for i in pair if prepared[i].csv.op_type == "achat")
        day = prepared[sale].csv.op_date
        # Positions la veille de la fusion : base existante + lignes du fichier antérieures.
        before = [o for o in operations if o.op_date <= day]
        before += [
            as_operation(p, 10**9 + n)
            for n, p in enumerate(prepared)
            if p.status == "ok"
            and p.csv.op_date <= day
            and p.csv.unit_price is not None
            and not p.csv.merger
            and p.csv.op_type in ("achat", "vente", "solde_initial", "transfert_sortant")
        ]
        held = None
        try:
            held = next(
                (
                    pos
                    for pos in compute_positions(before)
                    if (pos.account_id, pos.asset_id)
                    == (prepared[sale].account.id, prepared[sale].asset.id)
                ),
                None,
            )
        except PositionError:
            pass
        quantity = prepared[sale].csv.quantity
        if held is None or held.quantity < quantity:
            for i in pair:
                message = "fusion : ancien titre pas (assez) détenu à cette date"
                prepared[i] = replace(prepared[i], status="erreur", message=message)
            continue
        cost = held.cost * quantity / held.quantity
        new_qty = prepared[buy].csv.quantity
        prepared[sale] = replace(
            prepared[sale], csv=replace(prepared[sale].csv, unit_price=cost / quantity)
        )
        prepared[buy] = replace(
            prepared[buy], csv=replace(prepared[buy].csv, unit_price=cost / new_qty)
        )
    return prepared
