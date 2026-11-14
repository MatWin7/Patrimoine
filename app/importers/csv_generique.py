"""Lecture d'un CSV quelconque (export de courtier, tableur), fonctions pures (D-053).

L'utilisateur dit quelle colonne contient quoi (« correspondance des colonnes ») ; une
correspondance est proposée d'après les noms des en-têtes. Formats français et anglais
acceptés : séparateur `;`, `,` ou tabulation, virgule ou point décimal, dates JJ/MM/AAAA ou
AAAA-MM-JJ, fichier en UTF-8 ou Windows-1252 (Excel sous Windows).
"""

import csv
import io
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

# Champs d'une opération qu'une colonne peut remplir.
FIELDS = (
    "date",
    "type",
    "asset",
    "asset_name",
    "quantity",
    "unit_price",
    "amount",
    "fees",
    "currency",
    "note",
)
MAX_ROWS = 5000


class CsvError(ValueError):
    """Fichier illisible dans son ensemble (vide, trop long, sans colonnes)."""


def decode(data: bytes) -> str:
    """UTF-8 (avec ou sans BOM), sinon Windows-1252, l'encodage des CSV d'Excel français."""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def read_rows(text: str) -> list[list[str]]:
    """Lignes du fichier, séparateur deviné sur le début du texte ; lignes vides ignorées."""
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t")
        delimiter = dialect.delimiter
    except csv.Error:
        # Un seul champ par ligne, ou échantillon ambigu : le plus fréquent l'emporte.
        delimiter = max(";,\t", key=sample.count)
    rows = [
        [cell.strip() for cell in row]
        for row in csv.reader(io.StringIO(text), delimiter=delimiter)
        if any(cell.strip() for cell in row)
    ]
    if not rows:
        raise CsvError("Fichier vide.")
    # Chaque ligne entière entre guillemets (CSV ré-enregistré par Excel) : une seule cellule
    # par ligne, qui contient la vraie ligne ; on la relit.
    if all(len(row) == 1 for row in rows) and any(sep in rows[0][0] for sep in ";,\t"):
        return read_rows("\n".join(row[0] for row in rows))
    if len(rows) > MAX_ROWS + 1:
        raise CsvError(f"Fichier trop long : {MAX_ROWS} lignes au plus par import.")
    return rows


def _plain(text: str) -> str:
    """Minuscules sans accents ni ponctuation : « Date d'opération » -> "date d operation"."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


# Mots des en-têtes, du plus sûr au moins sûr, pour proposer une correspondance.
HEADER_WORDS = {
    "date": ["date operation", "date d execution", "date execution", "date valeur", "date"],
    "type": ["type", "sens", "operation", "nature", "transaction", "action"],
    # Identifiant de l'actif : ISIN, ticker ou symbole. À défaut, le nom (voir guess_mapping).
    "asset": ["isin", "code isin", "ticker", "symbole", "symbol"],
    # Nom affiché (sert à reconnaître l'actif par son nom, et à pré-remplir sa création).
    "asset_name": ["libelle", "nom", "name", "titre", "valeur", "produit", "instrument", "actif"],
    "quantity": ["quantite", "qte", "qty", "quantity", "nombre", "nb", "parts", "shares"],
    "unit_price": ["prix unitaire", "cours", "prix", "price", "cours execute"],
    "amount": ["montant net", "montant", "amount", "total", "montant brut"],
    "fees": ["frais", "commission", "fees", "courtage"],
    "currency": ["devise", "currency", "monnaie"],
    "note": ["note", "commentaire", "comment", "description"],
}


def guess_mapping(header: list[str]) -> dict[str, int | None]:
    """Correspondance proposée {champ: numéro de colonne}, d'après les en-têtes.

    Un en-tête identique au mot l'emporte sur un en-tête qui le contient ; une colonne ne sert
    qu'à un champ.
    """
    names = [_plain(h) for h in header]
    mapping: dict[str, int | None] = dict.fromkeys(FIELDS)
    used: set[int] = set()
    for exact in (True, False):
        for field in FIELDS:
            if mapping[field] is not None:
                continue
            for word in HEADER_WORDS[field]:
                found = next(
                    (
                        i
                        for i, name in enumerate(names)
                        if i not in used
                        and (name == word if exact else re.search(rf"\b{word}\b", name))
                    ),
                    None,
                )
                if found is not None:
                    mapping[field] = found
                    used.add(found)
                    break
    # Pas de colonne ISIN ni ticker : l'actif est reconnu par son nom.
    if mapping["asset"] is None:
        mapping["asset"] = mapping["asset_name"]
    return mapping


def parse_decimal(text: str) -> Decimal | None:
    """« 1 234,56 € », « 1,234.56 », « -12,5 » -> Decimal ; vide -> None ; illisible -> erreur.

    Avec virgule et point, le dernier des deux est le séparateur décimal. Le signe est gardé :
    c'est l'appelant qui décide (certains courtiers notent les achats en négatif).
    """
    cleaned = re.sub(r"[\s  '€$£]|[A-Z]{3}$", "", text.strip())
    if not cleaned or cleaned in {"-", "--"}:
        return None
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    else:
        cleaned = cleaned.replace(",", ".")
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        raise ValueError(f"nombre illisible « {text} »") from None
    if not value.is_finite():
        raise ValueError(f"nombre illisible « {text} »")
    return value


DATE_FORMATS = ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%Y/%m/%d")


def parse_date(text: str) -> date:
    """Date seule ou date et heure (« 02/01/2025 », « 2025-01-02T10:30:00 »)."""
    head = text.strip().split(" ")[0].split("T")[0]
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(head, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"date illisible « {text} »")


# Mots qui désignent un type d'opération (texte simplifié par _plain), dans cet ordre.
TYPE_WORDS = [
    ("solde_initial", ["solde initial", "titres deja detenus", "transfert entrant de titres"]),
    ("transfert_sortant", ["transfert sortant", "titres envoyes", "transfert de titres sortant"]),
    ("dividende", ["dividende", "dividend", "coupon", "distribution"]),
    ("frais", ["frais", "fee", "commission", "droits de garde", "taxe", "tax"]),
    ("vente", ["vente", "sell", "sale", "cession"]),
    ("achat", ["achat", "buy", "purchase", "souscription"]),
    ("retrait", ["retrait", "withdraw", "virement sortant"]),
    ("versement", ["versement", "depot", "deposit", "apport", "virement entrant", "virement"]),
]


def parse_type(text: str) -> str:
    plain = _plain(text)
    for op_type, words in TYPE_WORDS:
        if any(re.search(rf"\b{w}", plain) for w in words):
            return op_type
    raise ValueError(f"type d'opération inconnu « {text} »")


@dataclass(frozen=True)
class CsvLine:
    """Une ligne lue, valeurs converties ; `error` explique pourquoi elle est inutilisable."""

    line: int  # numéro dans le fichier (1 = première ligne)
    op_date: date | None = None
    op_type: str | None = None
    asset: str | None = None  # texte de la colonne « actif » : ISIN, ticker ou nom
    asset_name: str | None = None  # nom de l'actif, s'il est dans une autre colonne
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    amount: Decimal | None = None
    fees: Decimal = Decimal("0")
    currency: str | None = None
    note: str | None = None
    error: str | None = None
    # Remplis par un profil de plateforme (D-071) ; vides pour l'import générique.
    skip: str | None = None  # ligne volontairement ignorée (dépense, virement...), et pourquoi
    part: str | None = None  # partie du relevé (PEA, compte-titres...) -> compte de l'app
    key: str | None = None  # identifiant de l'opération chez la plateforme (anti-doublon)
    plan: bool = False  # exécution d'un plan d'investissement (achat programmé)
    asset_class: str | None = None  # classe proposée pour créer l'actif s'il est inconnu
    merger: str | None = None  # fusion : vente et achat liés, prix fixés au prix de revient
    offered: Decimal | None = None  # achat : part payée par la plateforme (D-076)
    reward_kind: str | None = None  # achat offert d'une crypto : type de récompense (D-092)
    tax_withheld: Decimal | None = None  # dividende : impôt retenu à la source (D-076)
    crypto_swap: bool = False  # vente crypto contre une autre crypto, non imposable (D-066)
    seq: int | None = None  # ordre chronologique dans le fichier, s'il diffère de `line` (D-082)
    # Montants convertibles dans la devise de l'actif au taux BCE du jour (D-085) : valeurs
    # estimées par l'app (crypto Binance), pas le prix d'une cotation précise.
    convertible: bool = False


TRADES = ("achat", "vente", "solde_initial", "transfert_sortant")


def parse_line(number: int, row: list[str], mapping: dict[str, int | None]) -> CsvLine:
    """Convertit une ligne selon la correspondance des colonnes.

    Nombres pris en valeur absolue : le sens vient du type. Prix unitaire absent d'un achat ou
    d'une vente : déduit de montant ÷ quantité (le montant est alors compris hors frais).
    """

    def cell(field: str) -> str:
        i = mapping.get(field)
        return row[i] if i is not None and i < len(row) else ""

    try:
        if not cell("date"):
            raise ValueError("date manquante")
        op_date = parse_date(cell("date"))
        if not cell("type"):
            raise ValueError("type d'opération manquant")
        op_type = parse_type(cell("type"))
        numbers = {f: parse_decimal(cell(f)) for f in ("quantity", "unit_price", "amount", "fees")}
        quantity, unit_price, amount, fees = (
            abs(numbers[f]) if numbers[f] is not None else None
            for f in ("quantity", "unit_price", "amount", "fees")
        )
        asset = cell("asset") or cell("asset_name") or None
        asset_name = cell("asset_name") or None
        if op_type in TRADES:
            if asset is None:
                raise ValueError("actif manquant")
            if not quantity:
                raise ValueError("quantité manquante ou nulle")
            if unit_price is None and amount:
                unit_price = amount / quantity
            if unit_price is None:
                raise ValueError("prix unitaire (ou montant) manquant")
            amount = None
        else:
            if not amount:
                raise ValueError("montant manquant ou nul")
            if op_type != "dividende":
                asset = asset_name = None  # versement, retrait, frais : pas d'actif
            elif asset is None:
                raise ValueError("actif du dividende manquant")
            quantity = unit_price = None
        if op_date > date.today():
            raise ValueError("date dans le futur")
        currency = cell("currency").upper() or None
        if currency is not None and not re.fullmatch(r"[A-Z]{3}", currency):
            raise ValueError(f"devise illisible « {cell('currency')} »")
        return CsvLine(
            number,
            op_date,
            op_type,
            asset,
            asset_name,
            quantity,
            unit_price,
            amount,
            fees or Decimal("0"),
            currency,
            cell("note")[:200] or None,
        )
    except ValueError as err:
        return CsvLine(number, error=str(err))
