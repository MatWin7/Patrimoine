"""Routes de l'import CSV (D-053). Le texte du fichier arrive du navigateur (127.0.0.1), est lu
en mémoire et n'est ni enregistré ni journalisé : seules les opérations validées entrent dans
la base."""

import contextvars
import json
import queue
import threading
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import httpx2
from fastapi import APIRouter, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse

from app import config, db, prices, repository, tickers
from app.api.deps import HttpClient
from app.importers import binance, csv_generique, preparation, trade_republic
from app.models import Account, Asset
from app.portfolio import TRADES, ZERO, PositionError, RateHistory, compute_positions
from app.schemas import ImportIn, SplitIn

router = APIRouter()


PROFILES = {trade_republic.ID: trade_republic, binance.ID: binance}


@dataclass
class _Import:
    header: list[str]
    mapping: dict[str, int | None]
    profile: object | None  # module du profil de plateforme reconnu, ou None (générique)
    prepared: list[preparation.Prepared]
    parts: list[dict]
    plans: list[trade_republic.Plan]
    accounts: dict[str | None, Account]
    account_ids: set[int]  # tous les comptes de l'app (destinations possibles, D-085)
    auto_assets: list[str]  # actifs déjà revendus créés sans demander (D-085)


def _part_accounts(profile, parts: list[str], accounts: list[Account], body: ImportIn):
    """Compte de chaque partie du relevé : celui choisi, sinon l'unique compte de ce type à la
    plateforme (établissement ou nom qui la contient), de préférence celui du formulaire. Sans
    compte à la plateforme, aucun choix : l'aperçu propose de le créer (D-083), pour ne pas
    importer dans le compte d'une autre banque. Obligations et non coté suivent le compte-titres."""
    by_id = {a.id: a for a in accounts}
    chosen = body.accounts or {}
    platform = profile.INSTITUTION.lower()
    out: dict[str | None, Account] = {}
    for part in sorted(parts, key=lambda p: p in ("obligations", "non_cote")):
        kind = profile.PARTS[part][1]
        account = by_id.get(chosen.get(part))
        if account is None and part not in chosen:
            here = [
                a
                for a in accounts
                if a.kind == kind
                and (platform in (a.institution or "").lower() or platform in a.name.lower())
            ]
            if part in ("obligations", "non_cote") and "cto" in out:
                account = out["cto"]
            elif body.account_id in {a.id for a in here}:
                account = by_id[body.account_id]
            elif len(here) == 1:
                account = here[0]
        if account is not None:
            out[part] = account
    return out


Progress = Callable[[int, str], None]  # (pourcentage 0 à 100, étape en cours) : D-089


def _no_progress(percent: int, label: str) -> None:
    """Progression ignorée (aperçu sans suivi, import)."""


def _binance_pricer(
    conn,
    client,
    header: list[str],
    data: list[list[str]],
    progress: Progress = _no_progress,
) -> binance.Pricer:
    """Cours utiles à l'import Binance (D-082) : taux BCE du dollar pour les stablecoins, puis
    cours CoinGecko des 365 derniers jours des seules cryptos dont la valeur ne se déduit pas
    du fichier (gains Earn, frais en BNB, poussière). Seuls un code devise et des id CoinGecko
    partent sur le réseau, jamais une quantité ni une date d'achat (règle 3)."""
    found = binance.coins(header, data)
    if not found:
        return binance.Pricer()
    today = date.today()
    progress(20, "Taux de change")
    prices.update_fx_history(conn, {"USD": min(found.values())}, client, today=today)
    usd = prices.rate_series(conn, {"USD"}).get("USD") or []
    if not usd and "USD" in (stored := prices.stored_rates(conn, {"USD"})):
        usd = [(date.min, stored["USD"])]
    # Premier passage sans CoinGecko : les jetons qui manquent d'un cours du jour.
    probe = binance.Pricer(usd)
    progress(30, "Lecture des opérations")
    binance.parse(header, data, pricer=probe)
    assets = repository.list_assets(conn)
    closes = {}
    asked = sorted(probe.asked)
    for i, symbol in enumerate(asked):
        progress(35 + 50 * i // len(asked), f"Cours de {symbol}")
        asset = preparation.find_asset(symbol, assets)
        coin = binance.COINGECKO_IDS.get(symbol) or (asset.coingecko_id if asset else None)
        if coin:
            closes[symbol] = prices.coin_history(client, coin, today, config.coingecko_api_key())
    return binance.Pricer(usd, closes)


def _read_import(
    conn,
    body: ImportIn,
    client: httpx2.Client,
    create: bool = False,
    progress: Progress = _no_progress,
) -> _Import:
    progress(5, "Lecture du fichier")
    try:
        rows = csv_generique.read_rows(body.text)
    except csv_generique.CsvError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    header = rows[0] if body.header else [f"Colonne {i + 1}" for i in range(len(rows[0]))]
    data = rows[1:] if body.header else rows
    width = max(len(r) for r in rows)
    header += [f"Colonne {i + 1}" for i in range(len(header), width)]
    first = 2 if body.header else 1
    mapping = dict.fromkeys(csv_generique.FIELDS)
    profile = None
    if body.profile in PROFILES:
        profile = PROFILES[body.profile]
    elif body.profile is None and body.header:
        profile = next((p for p in PROFILES.values() if p.detect(header)), None)
    all_accounts = repository.list_accounts(conn)
    if profile is not None:
        if not profile.detect(header):
            raise HTTPException(
                status_code=422, detail=f"Ce fichier n'est pas un export {profile.LABEL}."
            )
        if profile is binance:
            pricer = _binance_pricer(conn, client, header, data, progress)
            progress(88, "Lecture des opérations")
            lines = binance.parse(header, data, first, pricer=pricer)
        else:
            progress(40, "Lecture des opérations")
            lines = profile.parse(header, data, first)
        found = Counter(
            line.part for line in lines if line.part and not line.skip and not line.error
        )
        accounts = _part_accounts(profile, list(found), all_accounts, body)
        parts = [
            {
                "part": part,
                "label": profile.PARTS[part][0],
                "kind": profile.PARTS[part][1],
                "suggested_name": profile.PARTS[part][2],
                "institution": profile.INSTITUTION,
                "count": found[part],
                "account_id": accounts[part].id if part in accounts else None,
            }
            for part in profile.PARTS
            if part in found
        ]
        plans = profile.detect_plans(lines)
    else:
        account = next((a for a in all_accounts if a.id == body.account_id), None)
        if account is None:
            raise HTTPException(status_code=422, detail="Choisis le compte où importer ce fichier.")
        if body.mapping is None:
            mapping.update(csv_generique.guess_mapping(header))
        else:
            for field, column in body.mapping.items():
                if field in mapping and column is not None and 0 <= column < width:
                    mapping[field] = column
        lines = [csv_generique.parse_line(first + i, r, mapping) for i, r in enumerate(data)]
        accounts, parts, plans = {None: account}, [], []
    assets = repository.list_assets(conn)
    fx = None
    auto: list[str] = []
    if profile is binance:
        # Actifs déjà revendus (D-085) : créés sans demander, dans la devise par défaut des
        # cryptos ; à l'aperçu, seulement simulés.
        found_auto = _sold_coins(lines, assets)
        for i, (symbol, coin) in enumerate(found_auto):
            if create:
                asset = repository.create_asset(
                    conn, symbol, "crypto", None, None, coin, CRYPTO_CURRENCY
                )
            else:
                asset = Asset(-1 - i, symbol, "crypto", None, None, coin, CRYPTO_CURRENCY)
            assets.append(asset)
            auto.append(symbol)
        currencies = {a.currency for a in assets} - {"EUR"}
        fx = RateHistory(
            prices.stored_rates(conn, currencies), prices.rate_series(conn, currencies)
        )
    progress(95, "Vérification des doublons")
    prepared = preparation.prepare(
        conn, accounts, lines, assets, repository.list_operations(conn), fx
    )
    account_ids = {a.id for a in all_accounts}
    return _Import(header, mapping, profile, prepared, parts, plans, accounts, account_ids, auto)


CRYPTO_CURRENCY = "USD"  # cotation par défaut d'une crypto (D-085), modifiable par actif


def _sold_coins(lines, assets: list[Asset]) -> list[tuple[str, str]]:
    """Cryptos du relevé à l'id CoinGecko connu, encore absentes de l'app et qui ne valent plus
    rien à la fin du fichier (moins d'une unité de devise) : (symbole, id), dans l'ordre."""
    net: dict[tuple[str, str], Decimal] = {}
    price: dict[tuple[str, str], Decimal] = {}
    for line in sorted(
        (x for x in lines if x.op_type in TRADES and not x.skip and not x.error),
        key=lambda x: (x.op_date, x.seq or 0, x.line),
    ):
        if not line.asset or not line.asset_name or line.asset == line.asset_name:
            continue  # id CoinGecko inconnu : l'utilisateur le saisit en créant l'actif
        if preparation.find_asset(line.asset, assets) or preparation.find_asset(
            line.asset_name, assets
        ):
            continue
        key = (line.asset_name, line.asset)
        sign = -1 if line.op_type in ("vente", "transfert_sortant") else 1
        net[key] = net.get(key, ZERO) + sign * line.quantity
        price[key] = line.unit_price or ZERO
    return [key for key, qty in net.items() if qty * price[key] < 1]


def _plan_rows(conn, imp: _Import) -> list[dict]:
    """Plans détectés, avec ce qui empêche de les créer (actif inconnu, compte, déjà là)."""
    assets = repository.list_assets(conn)
    existing = {(r["account_id"], r["asset_id"]) for r in repository.list_recurring(conn)}
    out = []
    for plan in imp.plans:
        account = imp.accounts.get(plan.part)
        asset = preparation.find_asset(plan.asset, assets)
        problem = None
        if plan.frequency is None:
            problem = "exécutions trop irrégulières : à créer à la main si besoin"
        elif asset is None:
            problem = "crée d'abord l'actif"
        elif account is None:
            problem = "choisis d'abord le compte"
        elif (account.id, asset.id) in existing:
            problem = "déjà dans tes achats programmés"
        elif asset.currency != "EUR":
            problem = "actif coté hors euro : à créer à la main"
        out.append(
            {
                "key": plan.key,
                "part": plan.part,
                "asset": plan.asset,
                "asset_name": asset.name if asset else plan.asset_name,
                "account": account.name if account else None,
                "frequency": plan.frequency,
                "day": plan.day,
                "amount": plan.amount,
                "first": plan.first,
                "last": plan.last,
                "count": plan.count,
                "active": plan.active,
                "problem": problem,
            }
        )
    return out


def _import_row_with_outflow(p, body: ImportIn, accounts: set[int]) -> dict:
    flow = _outflow(p, body, accounts)
    row = _import_row(p)
    row["outflow"] = _outflow_json(flow)
    single = flow is not None and len(flow["legs"]) == 1 and flow["rest"] == 0
    row["to_account_id"] = flow["legs"][0][0] if single else None
    row["destination"] = flow["text"] if flow else None
    return row


def _import_row(p: preparation.Prepared) -> dict:
    line = p.csv

    def text(value: Decimal | None) -> str | None:
        return None if value is None else format(value.normalize(), "f")

    return {
        "line": line.line,
        "status": p.status,
        "message": p.message,
        "op_date": line.op_date.isoformat() if line.op_date else None,
        "op_type": line.op_type,
        "asset": line.asset,
        "asset_name": p.asset.name if p.asset else line.asset_name,
        "asset_class": line.asset_class,
        "known": p.asset is not None,
        "asset_id": p.asset.id if p.asset else None,
        "account": p.account.name if p.account else None,
        "part": line.part,
        "plan": line.plan,
        "quantity": text(line.quantity),
        "unit_price": text(line.unit_price),
        "amount": text(line.amount),
        "fees": text(line.fees) if line.op_type else None,
        "currency": p.currency or line.currency,
        # Actif inconnu repéré par son ISIN : ticker Yahoo de la table locale (D-083).
        "ticker": (
            tickers.known(line.asset, line.currency or "EUR")
            if p.status == "actif_inconnu" and line.asset and line.asset_class != "crypto"
            else None
        ),
    }


def _import_counts(prepared) -> dict[str, int]:
    counts: dict[str, int] = {}
    for p in prepared:
        counts[p.status] = counts.get(p.status, 0) + 1
    return counts


def _preview_data(body: ImportIn, client: httpx2.Client, progress: Progress = _no_progress) -> dict:
    """Aperçu d'un import : profil reconnu ou correspondance des colonnes, parties du relevé et
    leur compte, lignes avec leur statut, plans d'investissement détectés. Rien n'est enregistré.
    Les lignes ignorées sont résumées par raison, sans leur contenu."""
    with db.connect() as conn:
        imp = _read_import(conn, body, client, progress=progress)
        plans = _plan_rows(conn, imp)
    conn.close()
    ignored = Counter(p.message for p in imp.prepared if p.status == "ignoree")
    return {
        "profile": {"id": imp.profile.ID, "label": imp.profile.LABEL} if imp.profile else None,
        "columns": imp.header,
        "mapping": imp.mapping,
        "parts": imp.parts,
        "rows": [
            _import_row_with_outflow(p, body, imp.account_ids)
            for p in imp.prepared
            if p.status != "ignoree"
        ],
        "ignored": [{"reason": r, "count": n} for r, n in ignored.most_common()],
        "counts": _import_counts(imp.prepared),
        "plans": plans,
        "auto_assets": imp.auto_assets,
    }


@router.post("/api/import/preview")
def import_preview(body: ImportIn, client: HttpClient) -> dict:
    """Aperçu d'un import, d'un seul bloc."""
    return _preview_data(body, client)


@router.post("/api/import/preview/stream")
def import_preview_stream(body: ImportIn, client: HttpClient) -> StreamingResponse:
    """Même aperçu, avec sa progression (D-089) : une ligne JSON par événement,
    `{"progress": 0 à 100, "label": étape}` pendant la lecture, puis `{"result": aperçu}` ou
    `{"error": message}`. La lecture tourne dans un fil pour pouvoir rendre la main entre deux
    étapes ; rien ne sort de la machine de plus que dans l'aperçu ordinaire."""
    events: queue.Queue = queue.Queue()

    def work() -> None:
        try:
            result = _preview_data(
                body, client, lambda f, label: events.put({"progress": f, "label": label})
            )
            # Decimal en texte, comme la route ordinaire (D-017) : jamais en nombre flottant.
            encoded = jsonable_encoder(result, custom_encoder={Decimal: str})
            events.put({"result": encoded})
        except HTTPException as err:
            events.put({"error": err.detail})
        except Exception:  # noqa: BLE001 - le message reste générique, rien de privé n'en sort
            events.put({"error": "Aperçu impossible pour l'instant."})
        finally:
            events.put(None)

    # Le fil reçoit une copie du contexte : sans elle, il ne saurait pas quelle base de profil
    # ouvrir (une ContextVar ne passe pas d'elle-même à un nouveau fil, D-098).
    context = contextvars.copy_context()
    threading.Thread(target=context.run, args=(work,), daemon=True).start()

    def lines() -> Iterator[str]:
        while (event := events.get()) is not None:
            yield json.dumps(event) + "\n"

    return StreamingResponse(lines(), media_type="application/x-ndjson")


def _outflow(p, body: ImportIn, accounts: set[int]) -> dict | None:
    """Où part un transfert sortant ou un retrait (D-085, D-087) : parts envoyées à des comptes
    de l'app (`splits`, ou `destinations` pour tout le retrait), le reste « hors de l'app » avec
    son texte libre. Un compte pas encore créé (première utilisation) n'a pas d'identifiant à
    exclure."""
    line = p.csv
    if line.op_type not in TRANSFERS:
        return None
    total = line.quantity if line.quantity is not None else line.amount
    if not total:
        return None
    own = p.account.id if p.account else None
    asked = (body.splits or {}).get(line.line)
    if asked is None:
        to = (body.destinations or {}).get(line.line)
        asked = [SplitIn(account_id=to, value=total)] if to else []
    legs = [
        (s.account_id, s.value) for s in asked if s.account_id in accounts and s.account_id != own
    ]
    rest = total - sum((v for _, v in legs), Decimal(0))
    text = (body.destination_texts or {}).get(line.line)
    return {
        "total": total,
        "legs": legs,
        "rest": rest,
        "text": text if rest > 0 and text else None,
    }


def _outflow_json(flow: dict | None) -> dict | None:
    if flow is None:
        return None
    return {
        "total": format(flow["total"].normalize(), "f"),
        "legs": [{"account_id": a, "value": format(v.normalize(), "f")} for a, v in flow["legs"]],
        "rest": format(max(flow["rest"], Decimal(0)).normalize(), "f"),
        "over": flow["rest"] < 0,
        "text": flow["text"],
    }


def _outflow_parts(flow: dict) -> list[tuple[int | None, str | None, Decimal]]:
    """Opérations à créer pour un retrait : une par compte d'arrivée, puis le reste."""
    parts = [(a, None, v) for a, v in flow["legs"]]
    if flow["rest"] > 0:
        parts.append((None, flow["text"], flow["rest"]))
    return parts


TRANSFERS = ("transfert_sortant", "retrait")


@router.post("/api/import", status_code=201)
def import_operations(body: ImportIn, client: HttpClient) -> dict:
    """Enregistre les lignes « ok » de l'aperçu, toutes ou aucune (une seule transaction), et
    les plans d'investissement cochés."""
    try:
        with db.connect() as conn:
            imp = _read_import(conn, body, client, create=True)
            ready = [p for p in imp.prepared if p.status == "ok"]
            # Ordre chronologique (un export peut lister du plus récent au plus ancien) : les
            # positions se vérifient à la fin dans l'ordre (date, id).
            ready.sort(key=lambda p: (p.csv.op_date, p.csv.seq or 0, p.csv.line))
            for p in ready:
                line = p.csv
                flow = _outflow(p, body, imp.account_ids)
                if flow is not None and flow["rest"] < 0:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Ligne {line.line} : la répartition dépasse le montant sorti.",
                    )
                parts = _outflow_parts(flow) if flow else [(None, None, None)]
                for i, (to, destination, value) in enumerate(parts):
                    # Retrait réparti : une opération par arrivée, les frais sur la première.
                    quantity, amount = line.quantity, line.amount
                    if value is not None and line.quantity is not None:
                        quantity = value
                        if amount:
                            amount = amount * value / flow["total"]
                    elif value is not None:
                        amount = value
                    repository.create_operation(
                        conn,
                        p.account.id,
                        p.asset.id if p.asset else None,
                        line.op_date,
                        line.op_type,
                        quantity,
                        line.unit_price,
                        amount,
                        line.fees if i == 0 else Decimal(0),
                        p.currency,
                        line.note,
                        source=preparation.SOURCE,
                        import_key=p.import_key
                        if i == 0 or not p.import_key
                        else f"{p.import_key}|{i + 1}",
                        check=False,
                        offered=line.offered,
                        reward_kind=line.reward_kind if line.offered else None,
                        tax_withheld=line.tax_withheld,
                        crypto_swap=line.crypto_swap,
                        to_account_id=to,
                        destination=destination,
                    )
            # Une seule vérification à la fin : vente de titres jamais achetés, etc.
            compute_positions(repository.list_operations(conn))
            created = _import_plans(conn, imp, set(body.plans or []))
    except PositionError as err:
        raise HTTPException(
            status_code=422, detail=f"Import annulé, rien n'a été enregistré : {err}"
        ) from err
    return {
        "imported": len(ready),
        "plans": created,
        "counts": _import_counts(imp.prepared),
        "auto_assets": imp.auto_assets,
    }


def _import_plans(conn, imp: _Import, chosen: set[str]) -> int:
    """Crée les plans cochés (dernière échéance traitée = dernière exécution du fichier) et
    avance les achats programmés existants jusqu'à la dernière exécution importée : une
    échéance déjà dans le relevé n'est pas reproposée dans « Gérer »."""
    created = 0
    assets = repository.list_assets(conn)
    for row, plan in zip(_plan_rows(conn, imp), imp.plans, strict=True):
        if row["key"] not in chosen or row["problem"]:
            continue
        account = imp.accounts[plan.part]
        asset = preparation.find_asset(plan.asset, assets)
        r = repository.save_recurring(
            conn,
            None,
            account.id,
            asset.id,
            plan.amount,
            plan.frequency,
            plan.day,
            plan.first,
            f"Plan {imp.profile.LABEL}",
        )
        repository.mark_recurring_due(conn, r["id"], plan.last)
        created += 1
    executed: dict[tuple[int, int], date] = {}
    for p in imp.prepared:
        if p.csv.plan and p.status in ("ok", "deja_importee") and p.asset and p.account:
            key = (p.account.id, p.asset.id)
            executed[key] = max(executed.get(key, p.csv.op_date), p.csv.op_date)
    for r in repository.list_recurring(conn):
        last = executed.get((r["account_id"], r["asset_id"]))
        if last and (r["last_due"] is None or last > r["last_due"]) and last >= r["start_date"]:
            repository.mark_recurring_due(conn, r["id"], last)
    return created
