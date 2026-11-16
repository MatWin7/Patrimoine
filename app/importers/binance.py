"""Profil Binance : export « Historique des transactions » (D-082), fonctions pures.

Une ligne par mouvement de solde d'une crypto : un achat tient donc sur plusieurs lignes à la
même seconde (crypto reçue, crypto ou euros dépensés, frais). Le prix n'est pas écrit, il se
déduit du montant dépensé. Les colonnes « Identifiant utilisateur » et « Remarque » ne sont
jamais lues (identifiant personnel, références Binance Pay).

Choix (D-082) :
- USDT, USDC... sont des actifs crypto : BTC acheté en USDT = vente d'USDT marquée « échange »
  (non imposable, D-066) + achat de BTC ; seule une vente contre des euros est une cession.
- Valeur en euros d'un échange : la jambe en euros si elle existe, sinon le stablecoin
  (1 USDT = 1 $ au taux BCE du jour), sinon le cours estimé (`Pricer`).
- Frais en BNB : la quantité de BNB baisse (vente marquée échange) et leur valeur s'ajoute aux
  frais de l'opération ; valeur au cours du BNB, sinon 0,075 % du montant (tarif payé en BNB).
- Intérêts Earn, staking, airdrops, cashback : achat offert (D-078), regroupé par crypto et
  par mois ; le mois du dernier mouvement du fichier attend l'export suivant.
- Jeton reçu gratuitement puis converti en poussière : pas d'actif à créer, ce qu'il est
  devenu (BNB, USDC) entre comme offert.
- Crypto envoyée hors de Binance : transfert sortant (ni vente ni espèces) ; reçue d'ailleurs :
  titres déjà détenus au cours estimé.
- Vente plus grande que ce que le fichier montre détenu (intérêts Earn absents de l'export) :
  le manque entre comme reçu offert juste avant.
"""

import hashlib
import re
from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from itertools import permutations

from app.importers.csv_generique import TRADES, CsvLine, _plain, parse_decimal
from app.importers.trade_republic import detect_plans  # noqa: F401 (même détection des plans)

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    PARIS = ZoneInfo("Europe/Paris")
except (ImportError, ZoneInfoNotFoundError):  # pragma: no cover (Windows sans tzdata)
    PARIS = UTC

ID = "binance"
LABEL = "Binance"
INSTITUTION = "Binance"
PARTS = {"crypto": ("Crypto", "crypto", "Binance")}

# Colonnes utiles et leurs noms (anglais, français), simplifiés par _plain.
COLUMNS = {
    "time": ("utc time", "duree", "date", "time"),
    "operation": ("operation",),
    "coin": ("coin", "jeton"),
    "change": ("change", "variation"),
}

ZERO = Decimal("0")
STABLE_USD = {"USDT", "USDC", "BUSD", "FDUSD", "TUSD", "USDP", "DAI"}  # 1 jeton ≈ 1 $
STABLE_EUR = {"EURC", "EURI"}  # 1 jeton ≈ 1 €
MIN_YEAR = 2009  # première crypto : aucune opération possible avant
FEE_RATE = Decimal("0.00075")  # frais Binance payés en BNB : 0,1 % moins 25 %
CLUSTER_SECONDS = 2  # lignes d'un même échange : à la même seconde, ou presque (Convert)
NEAR_DAYS = 3  # cours jugé « du jour » s'il date d'au plus 3 jours

# Symbole Binance -> id CoinGecko, pour les plus courants (aucune recherche en ligne, D-054).
COINGECKO_IDS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "BNB": "binancecoin",
    "USDT": "tether",
    "USDC": "usd-coin",
    "FDUSD": "first-digital-usd",
    "DAI": "dai",
    "EURC": "euro-coin",
    "SOL": "solana",
    "BNSOL": "binance-staked-sol",
    "XRP": "ripple",
    "ADA": "cardano",
    "DOGE": "dogecoin",
    "DOT": "polkadot",
    "AVAX": "avalanche-2",
    "LINK": "chainlink",
    "LTC": "litecoin",
    "TRX": "tron",
    "SHIB": "shiba-inu",
    "ATOM": "cosmos",
    "UNI": "uniswap",
    "NEAR": "near",
    "ARB": "arbitrum",
    "OP": "optimism",
    "SUI": "sui",
    "TON": "the-open-network",
    "PEPE": "pepe",
    "MANA": "decentraland",
    "SAND": "the-sandbox",
    "PYTH": "pyth-network",
    "ETHW": "ethereum-pow-iou",
    "XLM": "stellar",
    "POL": "polygon-ecosystem-token",
}

INTERNAL = re.compile(
    r"subscription|redemption|transfer between|main and funding|account transfer"
    r"|savings purchase|savings principal|pos savings|isolated margin|cross margin"
)
REWARD = re.compile(
    r"interest|reward|airdrop|distribution|crypto box|cashback|bonus|rebate|referral"
    r"|kickback|megadrop|launchpool|mining|dividend"
)
FIAT_BUY = re.compile(r"buy crypto|fiat purchase|credit card")
PAYMENT = re.compile(r"merchant acquiring")
TRADE = re.compile(
    r"\bbuy\b|\bsell\b|\bfee\b|transaction|convert|small assets|exchange|auto invest|otc"
    r"|staking purchase|swap"
)
# Type de récompense d'un libellé de gain (D-092), le premier qui correspond l'emporte : un
# « Launchpool Interest » est un airdrop, pas un intérêt Earn. Même ordre que la migration 017.
REWARD_KINDS = (
    ("staking", re.compile(r"staking|eth 2\.0|vault")),
    ("airdrop", re.compile(r"airdrop|distribution|launchpool|megadrop|hodler")),
    ("earn", re.compile(r"earn|interest|savings")),
    ("cashback", re.compile(r"cashback")),
    ("parrainage", re.compile(r"referral|kickback|commission|rebate")),
)
REWARDS_FIRST = 1_000_000
IGNORED_INTERNAL = "mouvement interne à Binance (Earn, Funding...) : la crypto reste à toi"


def _columns(header: list[str]) -> dict[str, int] | None:
    names = [_plain(h) for h in header]
    found = {}
    for role, words in COLUMNS.items():
        index = next((names.index(w) for w in words if w in names), None)
        if index is None:
            return None
        found[role] = index
    return found


def detect(header: list[str]) -> bool:
    names = {_plain(h) for h in header}
    # « Change » et « Coin » ou « Jeton » ensemble : propre à Binance.
    return _columns(header) is not None and (
        "user id" in names or "identifiant utilisateur" in names
    )


def reward_kind(operation: str) -> str:
    """Type de récompense (D-092) d'un libellé Binance de gain : earn, staking, airdrop..."""
    label = operation.lower()
    return next((kind for kind, pattern in REWARD_KINDS if pattern.search(label)), "autre")


def coin_id(symbol: str) -> str:
    """Identifiant cherché parmi les actifs : id CoinGecko connu, sinon le symbole (nom)."""
    return COINGECKO_IDS.get(symbol, symbol)


def _time(text: str) -> datetime:
    """« 2025-01-31 18:02:03 » (ou « 25-01-31 ... » dans les anciens exports), en UTC."""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            moment = datetime.strptime(text.strip()[:19], fmt)
        except ValueError:
            continue
        if moment.year < MIN_YEAR:  # jamais une date d'avant Binance : faute de frappe (D-138)
            break
        return moment
    raise ValueError(f"date illisible « {text} »")


def _day(moment: datetime) -> date:
    """Jour à Paris : un achat à 23 h 30 UTC en hiver tombe le lendemain."""
    return moment.replace(tzinfo=UTC).astimezone(PARIS).date()


# --- Cours estimés ------------------------------------------------------------------


class Pricer:
    """Cours EUR d'un jeton à une date, sans appel réseau : stablecoins au taux BCE du dollar
    (`usd` : 1 EUR = rate USD), puis clôtures connues (`closes` : CoinGecko des 365 derniers
    jours, prix déduits des échanges du fichier), la plus proche dans le temps."""

    def __init__(
        self,
        usd: list[tuple[date, Decimal]] | None = None,
        closes: dict[str, list[tuple[date, Decimal]]] | None = None,
    ):
        self.usd = sorted(usd or [])
        self._usd_days = [d for d, _ in self.usd]
        self.closes: dict[str, list[tuple[date, Decimal]]] = {
            s: sorted(c) for s, c in (closes or {}).items()
        }
        self.asked: set[str] = set()  # jetons sans cours du jour : à demander à CoinGecko

    def learn(self, symbol: str, day: date, price: Decimal) -> None:
        series = self.closes.setdefault(symbol, [])
        if day not in (d for d, _ in series):
            series.append((day, price))
            series.sort()

    def usd_rate(self, day: date) -> Decimal | None:
        if not self.usd:
            return None
        i = bisect_right(self._usd_days, day)
        return self.usd[max(i - 1, 0)][1]

    def price(self, symbol: str, day: date) -> tuple[Decimal, int] | None:
        """(cours EUR, écart en jours avec la clôture utilisée), ou None si rien n'est connu."""
        if symbol == "EUR" or symbol in STABLE_EUR:
            return Decimal("1"), 0
        if symbol in STABLE_USD:
            rate = self.usd_rate(day)
            return (Decimal("1") / rate, 0) if rate else None
        series = self.closes.get(symbol)
        if not series:
            self.asked.add(symbol)
            return None
        days = [d for d, _ in series]
        i = bisect_left(days, day)
        near = [j for j in (i - 1, i) if 0 <= j < len(series)]
        j = min(near, key=lambda k: abs((series[k][0] - day).days))
        gap = abs((series[j][0] - day).days)
        if gap > NEAR_DAYS:
            self.asked.add(symbol)
        return series[j][1], gap

    def exact(self, symbol: str, day: date) -> Decimal | None:
        """Cours sûr : euro ou stablecoin (sinon None)."""
        if symbol == "EUR" or symbol in STABLE_EUR or symbol in STABLE_USD:
            found = self.price(symbol, day)
            return found[0] if found else None
        return None


# --- Lecture ------------------------------------------------------------------------


@dataclass(frozen=True)
class Row:
    number: int
    time: datetime
    operation: str
    plain: str
    coin: str
    change: Decimal

    @property
    def day(self) -> date:
        return _day(self.time)

    @property
    def raw(self) -> str:
        return f"{self.time.isoformat()}|{self.plain}|{self.coin}|{format(self.change, 'f')}"


def _kind(row: Row) -> str:
    p = row.plain
    if INTERNAL.search(p):
        return "internal"
    if re.search(r"\bdeposit\b|\bfunds transfer\b", p):
        return "deposit"
    if re.search(r"\bwithdraw", p):
        return "withdraw"
    if REWARD.search(p):
        return "reward"
    if FIAT_BUY.search(p):
        return "fiat_buy"
    if PAYMENT.search(p):
        return "payment"
    if TRADE.search(p):
        return "fee" if re.search(r"\bfee\b", p) else "trade"
    return "unknown"


def coins(header: list[str], data: list[list[str]]) -> dict[str, date]:
    """Jetons du fichier (hors euro) et le jour de leur premier mouvement : ce qu'il faut de
    cours pour l'aperçu. Aucun montant."""
    cols = _columns(header)
    out: dict[str, date] = {}
    if cols is None:
        return out
    for cells in data:
        try:
            symbol = cells[cols["coin"]].strip().upper()
            day = _day(_time(cells[cols["time"]]))
        except (IndexError, ValueError):
            continue
        if symbol and symbol != "EUR":
            out[symbol] = min(out.get(symbol, day), day)
    return out


# --- Construction des opérations ----------------------------------------------------


def _key(rows: list[Row]) -> str:
    return hashlib.sha256("\n".join(sorted(r.raw for r in rows)).encode()).hexdigest()[:24]


def _clean(value: Decimal | None) -> Decimal | None:
    """12 décimales au plus, sans notation « 2E+3 » (prix déduits d'une division)."""
    if value is None:
        return None
    return Decimal(format(value.quantize(Decimal("1e-12")).normalize(), "f"))


def _trade_line(number, day, op_type, symbol, qty, unit, *, fees=ZERO, **extra) -> CsvLine:
    if extra.get("offered") is not None:
        extra["offered"] = _clean(extra["offered"])
    return CsvLine(
        number,
        op_date=day,
        op_type=op_type,
        asset=coin_id(symbol),
        asset_name=symbol,
        quantity=qty,
        unit_price=_clean(unit),
        fees=_clean(fees),
        currency="EUR",
        part="crypto",
        asset_class="crypto",
        convertible=True,  # valeur estimée : convertie si la crypto est cotée en dollars (D-085)
        **extra,
    )


def _cash_line(number, day, op_type, amount, key, seq, note=None) -> CsvLine:
    return CsvLine(
        number,
        op_date=day,
        op_type=op_type,
        amount=_clean(amount),
        currency="EUR",
        part="crypto",
        key=key,
        seq=seq,
        note=note,
    )


@dataclass
class _Leg:
    symbol: str
    qty: Decimal
    value: Decimal
    number: int
    fees: Decimal = ZERO  # frais payés dans ce jeton ou en euros, ajoutés à l'opération
    extra_fees: Decimal = ZERO  # frais payés dans un 3e jeton (BNB), sans changer le prix
    offered: Decimal = ZERO
    notes: list[str] = field(default_factory=list)


def _estimate(pricer: Pricer, symbol: str, qty: Decimal, day: date) -> tuple[Decimal, bool] | None:
    """Valeur EUR d'une quantité et si elle est approchée (cours d'un autre jour)."""
    found = pricer.price(symbol, day)
    if found is None:
        return None
    price, gap = found
    return qty * price, gap > NEAR_DAYS


class _Builder:
    """Lignes produites au fil de la lecture, numérotées dans l'ordre chronologique (`seq`)."""

    def __init__(self, pricer: Pricer):
        self.pricer = pricer
        self.lines: list[CsvLine] = []
        self.seq = 0

    def next_seq(self) -> int:
        self.seq += 2
        return self.seq

    def skip(self, row: Row, reason: str) -> None:
        self.lines.append(CsvLine(row.number, skip=reason))

    def error(self, row: Row, message: str) -> None:
        self.lines.append(CsvLine(row.number, error=message))


def _passage(rows: list[Row], kinds: dict[int, str]) -> set[str]:
    """Jetons reçus seulement gratuitement (airdrop, Earn...) puis convertis (poussière) : il ne
    reste rien, inutile de les créer comme actifs."""
    by_coin: dict[str, list[Row]] = defaultdict(list)
    for row in rows:
        if kinds[row.number] != "internal":
            by_coin[row.coin].append(row)
    out = set()
    for symbol, coin_rows in by_coin.items():
        if symbol == "EUR" or symbol in STABLE_USD or symbol in STABLE_EUR:
            continue
        ins = [r for r in coin_rows if r.change > 0]
        outs = [r for r in coin_rows if r.change < 0]
        if (
            outs
            and all(kinds[r.number] == "reward" for r in ins)
            and all(kinds[r.number] == "trade" for r in outs)
            and sum((r.change for r in coin_rows), ZERO) <= 0
        ):
            out.add(symbol)
    return out


def _clusters(rows: list[Row]) -> list[list[Row]]:
    """Lignes d'échange regroupées : même seconde, ou à 2 s près (les deux jambes d'un
    Convert)."""
    out: list[list[Row]] = []
    for row in sorted(rows, key=lambda r: (r.time, r.number)):
        if out and (row.time - out[-1][-1].time).total_seconds() <= CLUSTER_SECONDS:
            out[-1].append(row)
        else:
            out.append([row])
    return out


def _value_side(pricer, side: dict[str, Decimal], day) -> tuple[dict[str, Decimal], bool] | None:
    """Valeur EUR de chaque jeton d'un côté de l'échange, et si l'une est approchée."""
    values, rough = {}, False
    for symbol, qty in side.items():
        found = _estimate(pricer, symbol, qty, day)
        if found is None:
            return None
        values[symbol], approx = found
        rough = rough or approx
    return values, rough


def _split(total: Decimal, side: dict[str, Decimal], pricer, day) -> dict[str, Decimal] | None:
    """Répartit `total` entre les jetons d'un côté, au prorata de leur valeur estimée."""
    if len(side) == 1:
        return {next(iter(side)): total}
    estimated = _value_side(pricer, side, day)
    if estimated is None:
        return None
    weights = estimated[0]
    whole = sum(weights.values(), ZERO)
    if not whole:
        return None
    return {s: total * w / whole for s, w in weights.items()}


def _paired(moves, ins, outs, pricer, day) -> dict[str, Decimal] | None:
    """Deux achats à la même seconde payés dans le même stablecoin (BTC et ETH contre USDT) :
    chaque ligne dépensée va à l'achat dont elle donne le prix le plus proche du cours
    estimé. None si le cas ne se présente pas."""
    if len(ins) < 2 or len(outs) != 1 or len(ins) > 4:
        return None
    (paid,) = outs
    rate = pricer.exact(paid, day)
    spends = [-r.change for r in moves if r.coin == paid and r.change < 0]
    if rate is None or len(spends) != len(ins) or any(r.change < 0 for r in moves if r.coin in ins):
        return None
    if any(sum(1 for r in moves if r.coin == s) != 1 for s in ins):
        return None
    guess = {}
    for s, q in ins.items():
        found = pricer.price(s, day)
        if found is None:
            return None
        guess[s] = q * found[0]
    coins_in = list(ins)

    def miss(order):
        return sum(
            abs(spends[i] * rate - guess[s]) / guess[s]
            for i, s in zip(order, coins_in, strict=True)
        )

    best = min(permutations(range(len(spends))), key=miss)
    return {s: spends[i] * rate for i, s in zip(best, coins_in, strict=True)}


def _cluster(b: _Builder, cluster: list[Row], kinds: dict[int, str], passage: set[str]) -> None:
    """Un échange (achat, vente, Convert, poussière -> BNB, achat par carte) : ventes et achats
    en euros, frais répartis."""
    p = b.pricer
    day = cluster[0].day
    key = _key(cluster)
    fee_rows = [r for r in cluster if kinds[r.number] == "fee"]
    moves = [r for r in cluster if kinds[r.number] != "fee"]
    net: dict[str, Decimal] = defaultdict(lambda: ZERO)
    first: dict[str, int] = {}
    for r in moves:
        net[r.coin] += r.change
        first.setdefault(r.coin, r.number)
    ins = {s: q for s, q in net.items() if q > 0}
    outs = {s: -q for s, q in net.items() if q < 0}
    fiat_buy = any(kinds[r.number] == "fiat_buy" for r in moves)
    if not ins or (not outs and not fiat_buy):
        for r in cluster:
            b.error(r, "échange incomplet dans l'export : à saisir à la main")
        return
    gone = {s: q for s, q in outs.items() if s in passage}  # jetons gratuits convertis
    real_outs = {s: q for s, q in outs.items() if s not in passage}
    plan = any("auto invest" in r.plain for r in moves)
    notes: list[str] = []

    # Valeur totale de l'échange : euros, sinon stablecoins, sinon cours estimés.
    def exact(side):
        values = {s: p.exact(s, day) for s in side}
        if not side or any(v is None for v in values.values()):
            return None
        return sum((side[s] * values[s] for s in side), ZERO)

    total = None
    if real_outs and not gone:
        total = exact(real_outs)
    if total is None:
        total = exact(ins)
    if total is None:
        side = real_outs if real_outs and not gone else ins
        estimated = _value_side(p, side, day)
        if estimated is None:
            for r in cluster:
                b.error(r, f"cours introuvable pour {', '.join(sorted(side))} : à saisir à la main")
            return
        total = sum(estimated[0].values(), ZERO)
        notes.append("cours estimé" + (" (autre jour)" if estimated[1] else ""))
    if fiat_buy and not outs:
        notes.append("montant payé par carte non indiqué par Binance : estimé")

    in_values = _paired(moves, ins, real_outs, p, day) or _split(total, ins, p, day)
    out_values: dict[str, Decimal] | None = {}
    if real_outs:
        if gone:
            # Une part vient de jetons gratuits : les autres jetons à leur cours, le reste offert.
            # Sans cours : part égale par ligne convertie (montants de poussière, minimes).
            per_line = total / len([r for r in moves if r.change < 0])
            out_values = {}
            for s, q in real_outs.items():
                found = _estimate(p, s, q, day)
                lines_of = sum(1 for r in moves if r.coin == s and r.change < 0)
                out_values[s] = found[0] if found else per_line * lines_of
                if not found:
                    notes.append("poussière sans cours : part égale par jeton")
        else:
            out_values = _split(total, real_outs, p, day)
    if in_values is None or out_values is None:
        for r in cluster:
            b.error(
                r, "plusieurs cryptos dans le même échange sans cours connu : à saisir à la main"
            )
        return
    free = max(ZERO, total - sum(out_values.values(), ZERO)) if gone else ZERO

    legs_in = {s: _Leg(s, q, in_values[s], first[s]) for s, q in ins.items() if s != "EUR"}
    legs_out = {s: _Leg(s, q, out_values[s], first[s]) for s, q in real_outs.items() if s != "EUR"}
    if free:
        for s, leg in legs_in.items():
            leg.offered = free * in_values[s] / total if total else ZERO
    to_eur = "EUR" in ins  # vente contre des euros : cession imposable
    main = next(iter(legs_in.values()), None) or next(iter(legs_out.values()), None)
    fee_legs: dict[str, _Leg] = {}  # frais payés dans un 3e jeton, une vente par jeton
    for r in fee_rows:
        qty = -r.change if r.change < 0 else r.change
        if r.coin in legs_in:
            leg = legs_in[r.coin]
            value = qty * leg.value / leg.qty
            leg.qty -= qty
            leg.fees += value
        elif r.coin in legs_out:
            leg = legs_out[r.coin]
            value = qty * leg.value / leg.qty
            leg.qty += qty
            leg.value += value
            leg.fees += value
        elif r.coin == "EUR":
            main.extra_fees += qty
        else:
            near = p.price(r.coin, day)
            if near is not None and near[1] <= NEAR_DAYS:
                value = qty * near[0]
            else:
                value = total * FEE_RATE
                notes.append("frais estimés à 0,075 %")
            main.extra_fees += value
            if r.coin in fee_legs:
                fee_legs[r.coin].qty += qty
                fee_legs[r.coin].value += value
            else:
                fee_legs[r.coin] = _Leg(r.coin, qty, value, r.number)

    note = (
        "Binance : " + " + ".join(sorted(real_outs) or ["achat"]) + " → " + " + ".join(sorted(ins))
    )
    if gone:
        note += f" (jetons reçus gratuitement : {', '.join(sorted(gone))})"
    if notes:
        note += " · " + ", ".join(dict.fromkeys(notes))
    made: list[int] = []
    for leg in [*legs_out.values(), *fee_legs.values()]:
        seq = b.next_seq()
        price = leg.value / leg.qty if leg.qty else ZERO
        b.lines.append(
            _trade_line(
                leg.number,
                day,
                "vente",
                leg.symbol,
                leg.qty,
                price,
                fees=leg.fees + (leg.extra_fees if leg is main else ZERO),
                crypto_swap=not (to_eur and leg in legs_out.values()),
                key=f"{key}|vente|{leg.symbol}|{'jambe' if leg in legs_out.values() else 'frais'}",
                seq=seq,
                note=note if leg in legs_out.values() else "Binance : frais payés en " + leg.symbol,
            )
        )
        made.append(leg.number)
    for leg in legs_in.values():
        if leg.qty <= 0:
            continue
        seq = b.next_seq()
        price = (leg.value - leg.fees) / leg.qty
        offered = min(leg.offered, leg.value + leg.extra_fees) if leg.offered else None
        b.lines.append(
            _trade_line(
                leg.number,
                day,
                "achat",
                leg.symbol,
                leg.qty,
                price,
                fees=leg.fees + (leg.extra_fees if leg is main else ZERO),
                key=f"{key}|achat|{leg.symbol}",
                seq=seq,
                note=note,
                offered=offered,
                reward_kind="autre" if offered else None,  # jetons reçus gratuitement, convertis
                plan=plan,
            )
        )
        made.append(leg.number)
    for r in cluster:
        if r.number not in made:
            reason = (
                "jeton reçu gratuitement puis converti : compté dans l'achat offert"
                if r.coin in gone
                else "regroupée avec les autres lignes du même échange"
            )
            b.skip(r, reason)


def _rewards(b: _Builder, rows: list[Row], passage: set[str], last_month: str) -> None:
    """Gains gratuits (intérêts Earn, staking, airdrops, cashback) : un achat offert par jeton,
    par mois et par type de récompense (D-092), au cours estimé de chaque jour (D-078)."""
    groups: dict[tuple[str, str, str], list[Row]] = defaultdict(list)
    for row in rows:
        if row.coin in passage:
            b.skip(row, "jeton reçu gratuitement puis converti : compté dans l'achat offert")
        elif row.change <= 0:
            b.error(row, "gain négatif inattendu : à saisir à la main")
        elif row.day.strftime("%Y-%m") >= last_month:
            b.skip(row, "gains du dernier mois de l'export : importés avec le prochain export")
        else:
            groups[(row.coin, row.day.strftime("%Y-%m"), reward_kind(row.operation))].append(row)
    # Un seul type dans le mois : la clé d'avant D-092, sans doublon au réimport.
    kinds = defaultdict(int)
    for symbol, month, _ in groups:
        kinds[(symbol, month)] += 1
    for (symbol, month, kind), group in sorted(groups.items(), key=lambda g: (g[0][1], g[0][0])):
        group.sort(key=lambda r: (r.time, r.number))
        qty = value = ZERO
        rough = False
        for r in group:
            found = _estimate(b.pricer, symbol, r.change, r.day)
            if found is None:
                break
            qty += r.change
            value += found[0]
            rough = rough or found[1]
        else:
            last = group[-1]
            labels = sorted({r.operation for r in group})
            note = f"Binance : {', '.join(labels)} ({len(group)} lignes)"
            if rough:
                note += " · cours estimé (autre jour)"
            b.lines.append(
                _trade_line(
                    last.number,
                    last.day,
                    "achat",
                    symbol,
                    qty,
                    value / qty,
                    key=f"gains|{symbol}|{month}"
                    + (f"|{kind}" if kinds[(symbol, month)] > 1 else ""),
                    # Avant les autres lignes de son jour : les gains du mois sont déjà reçus
                    # quand une vente ou un retrait du même jour les utilise.
                    seq=b.next_seq() - REWARDS_FIRST,
                    note=note,
                    offered=value,
                    reward_kind=kind,
                )
            )
            for r in group[:-1]:
                b.skip(r, "regroupée avec les gains du même mois")
            continue
        for r in group:
            b.error(r, f"cours de {symbol} introuvable : à saisir à la main")


def _shortfalls(lines: list[CsvLine]) -> list[CsvLine]:
    """Une vente plus grande que la quantité que le fichier montre détenue : le manque (souvent
    des intérêts Earn absents de l'export) entre comme reçu offert juste avant. Avant cela, les
    gains du même mois et du même jeton, regroupés à la date du dernier, sont avancés avant la
    vente pour combler le manque : sinon le manque les compterait une seconde fois. `lines`
    est modifiée sur place (gains raccourcis) ; les lignes à ajouter sont renvoyées."""
    held: dict[str, Decimal] = defaultdict(lambda: ZERO)
    extra = []
    order = sorted(
        (i for i, x in enumerate(lines) if x.op_type in TRADES and not x.error),
        key=lambda i: (lines[i].op_date, lines[i].seq or 0),
    )
    for i in order:
        x = lines[i]
        if x.op_type in ("vente", "transfert_sortant"):
            gap = x.quantity - held[x.asset]
            if gap > 0:
                gap -= _advance_gains(lines, x, gap, extra, held)
            if gap > 0:
                value = gap * x.unit_price
                extra.append(
                    replace(
                        x,
                        op_type="achat",
                        quantity=gap,
                        fees=ZERO,
                        crypto_swap=False,
                        offered=_clean(value),
                        key=f"manque|{x.key}",
                        seq=(x.seq or 0) - 1,
                        note="Binance : reçu absent de l'export (Earn, airdrop...), compté offert",
                        reward_kind="autre",
                    )
                )
                held[x.asset] += gap
            held[x.asset] -= x.quantity
        else:
            held[x.asset] += x.quantity
    return extra


def _advance_gains(
    lines: list[CsvLine], sale: CsvLine, gap: Decimal, extra: list[CsvLine], held
) -> Decimal:
    """Avance avant `sale` la part du gain mensuel (même jeton, même mois, daté après elle) qui
    comble le manque ; le reste du gain garde sa date. Renvoie la quantité avancée."""
    # Clé des gains : symbole Binance (asset_name), pas l'id CoinGecko (asset) ; un suffixe
    # « |type » quand le mois a plusieurs types de récompense (D-092).
    base = f"gains|{sale.asset_name}|{sale.op_date.strftime('%Y-%m')}"
    advanced = ZERO
    for i, line in enumerate(lines):
        key = line.key or ""
        if key != base and not (key.startswith(base + "|") and "|avance" not in key):
            continue
        if line.error or line.skip or line.op_date <= sale.op_date:
            continue
        taken = min(gap - advanced, line.quantity)
        extra.append(
            replace(
                line,
                op_date=sale.op_date,
                seq=(sale.seq or 0) - 1,
                quantity=taken,
                offered=_clean(taken * line.unit_price),
                key=f"{key}|avance|{sale.key}",  # une part par retrait du mois
                note=f"{line.note} · avancé avant un retrait du mois",
            )
        )
        held[sale.asset] += taken
        advanced += taken
        rest = line.quantity - taken
        lines[i] = replace(line, quantity=rest, offered=_clean(rest * line.unit_price))
        if rest <= 0:
            lines[i] = replace(lines[i], skip="gain avancé avant un retrait du même mois")
        if advanced >= gap:
            break
    return advanced


def parse(
    header: list[str], data: list[list[str]], first: int = 2, pricer: Pricer | None = None
) -> list[CsvLine]:
    """Lignes de l'export, dans l'ordre du fichier ; `pricer` : taux du dollar et cours connus
    (sans lui, seuls les échanges contre des euros ont une valeur)."""
    cols = _columns(header)
    if cols is None:
        raise ValueError("Ce fichier n'est pas un export Binance.")
    b = _Builder(pricer or Pricer())
    rows: list[Row] = []
    for i, cells in enumerate(data):
        number = first + i
        values = {role: cells[c].strip() if c < len(cells) else "" for role, c in cols.items()}
        cell = values.__getitem__
        try:
            change = parse_decimal(cell("change"))
            if not change:
                b.lines.append(CsvLine(number, skip="mouvement nul"))
                continue
            symbol = cell("coin").upper()
            if not re.fullmatch(r"[A-Z0-9]{1,20}", symbol):
                raise ValueError(f"jeton illisible « {cell('coin')} »")
            operation = cell("operation")
            rows.append(
                Row(number, _time(cell("time")), operation, _plain(operation), symbol, change)
            )
        except ValueError as err:
            b.lines.append(CsvLine(number, error=str(err)))
    if not rows:
        return sorted(b.lines, key=lambda x: x.line)
    kinds = {r.number: _kind(r) for r in rows}
    passage = _passage(rows, kinds)
    last_month = max(r.day for r in rows).strftime("%Y-%m")

    # Prix appris des échanges contre euros ou stablecoins : ils servent aux autres lignes.
    trade_rows = [r for r in rows if kinds[r.number] in ("trade", "fee", "fiat_buy")]
    clusters = _clusters(trade_rows)
    for cluster in clusters:
        moves = [r for r in cluster if kinds[r.number] != "fee"]
        net: dict[str, Decimal] = defaultdict(lambda: ZERO)
        for r in moves:
            net[r.coin] += r.change
        sure = [s for s in net if b.pricer.exact(s, cluster[0].day) is not None]
        other = [s for s in net if s not in sure]
        if len(sure) >= 1 and len(other) == 1 and net[other[0]]:
            reference = sum((abs(net[s]) * b.pricer.exact(s, cluster[0].day) for s in sure), ZERO)
            b.pricer.learn(other[0], cluster[0].day, reference / abs(net[other[0]]))

    rewards = []
    for cluster in clusters:
        _cluster(b, cluster, kinds, passage)
    for row in sorted(rows, key=lambda r: (r.time, r.number)):
        kind = kinds[row.number]
        if kind in ("trade", "fee", "fiat_buy"):
            continue
        seq = b.next_seq()
        key = _key([row])
        if kind == "internal":
            b.skip(row, IGNORED_INTERNAL)
        elif kind == "reward":
            rewards.append(row)
        elif kind == "deposit" and row.change > 0:
            if row.coin == "EUR":
                b.lines.append(_cash_line(row.number, row.day, "versement", row.change, key, seq))
                continue
            qty = row.change
            found = _estimate(b.pricer, row.coin, qty, row.day)
            if found is None:
                b.error(row, f"crypto reçue, cours de {row.coin} introuvable : à saisir à la main")
                continue
            note = "Binance : crypto reçue d'un autre wallet (titres déjà détenus, cours estimé)"
            b.lines.append(
                _trade_line(
                    row.number,
                    row.day,
                    "solde_initial",
                    row.coin,
                    qty,
                    found[0] / qty,
                    key=key,
                    seq=seq,
                    note=note,
                )
            )
        elif kind == "withdraw" and row.change < 0:
            if row.coin == "EUR":
                b.lines.append(_cash_line(row.number, row.day, "retrait", -row.change, key, seq))
                continue
            # Crypto envoyée ailleurs (wallet, autre plateforme) : transfert sortant, ni vente
            # ni espèces ; sa valeur estimée sort de la performance. Revenue plus tard, elle
            # rentre en titres déjà détenus.
            qty = -row.change
            found = _estimate(b.pricer, row.coin, qty, row.day)
            if found is None:
                b.error(
                    row, f"crypto envoyée, cours de {row.coin} introuvable : à saisir à la main"
                )
                continue
            b.lines.append(
                _trade_line(
                    row.number,
                    row.day,
                    "transfert_sortant",
                    row.coin,
                    qty,
                    found[0] / qty,
                    key=key,
                    seq=seq,
                    note="Binance : crypto envoyée hors de Binance (cours estimé)",
                )
            )
        elif kind == "payment" and row.change < 0 and row.coin != "EUR":
            qty = -row.change
            found = _estimate(b.pricer, row.coin, qty, row.day)
            if found is None:
                b.error(row, f"paiement, cours de {row.coin} introuvable : à saisir à la main")
                continue
            value = found[0]
            note = "Binance Pay : paiement chez un commerçant (cession, cours estimé)"
            b.lines.append(
                _trade_line(
                    row.number,
                    row.day,
                    "vente",
                    row.coin,
                    qty,
                    value / qty,
                    key=key,
                    seq=seq,
                    note=note,
                )
            )
            b.lines.append(
                _cash_line(row.number, row.day, "retrait", value, f"{key}|paiement", seq + 1, note)
            )
        else:
            b.error(row, f"opération Binance « {row.operation} » non reconnue : à saisir à la main")
    _rewards(b, rewards, passage, last_month)
    b.lines += _shortfalls(b.lines)
    return sorted(b.lines, key=lambda x: (x.line, x.seq or 0))
