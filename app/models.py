"""Dataclasses du domaine. Montants, prix et quantités toujours en Decimal (D-002)."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

# Comptes dont on saisit les espèces actuelles, lues chez la banque (D-041, D-075) : un livret,
# et les comptes où les versements ne servent à aucun calcul fiscal (CTO, crypto, Autre). Les
# autres (PEA, assurance-vie, PER, immobilier) déduisent leurs espèces des versements (D-062).
CASH_ENTERED = ("livret", "cto", "crypto", "autre")


@dataclass(frozen=True)
class Account:
    id: int
    name: str
    kind: str  # pea, cto, crypto, assurance_vie, per, livret, immobilier, autre (D-056)
    institution: str | None
    currency: str
    # Espèces actuelles. Livret : dernier solde saisi + opérations depuis (D-041). CTO, crypto,
    # Autre : solde saisi + opérations depuis, rejouées comme D-062 (D-075). Autres comptes :
    # versements − achats + ventes... rejoués dans l'ordre (D-062).
    cash: Decimal = Decimal("0")
    # Date de la dernière saisie du solde (CASH_ENTERED) ; None pour un CTO créé avant D-075 :
    # espèces encore déduites de l'ancien champ « Versements », à vérifier.
    cash_date: date | None = None
    opened_on: date | None = None  # date d'ouverture (repères fiscaux, D-059)
    deposits_before: Decimal = Decimal("0")  # champ « Versements » : total saisi pour le compte
    deposits_entered: Decimal = Decimal("0")  # opérations « Versement » saisies
    deposits_implied: Decimal = Decimal("0")  # versements que les achats supposent (D-062)
    # Garde d'un compte crypto (D-104) : 'exchange' (plateforme), 'wallet' (portefeuille
    # personnel) ou None (à préciser ; toujours None hors crypto). `abroad` : établissement à
    # l'étranger, à déclarer (3916 ou 3916-bis).
    custody: str | None = None
    abroad: bool = False

    @property
    def deposits(self) -> Decimal:
        return self.deposits_before + self.deposits_entered + self.deposits_implied


@dataclass(frozen=True)
class Asset:
    id: int
    name: str
    asset_class: (
        str  # action, etf, fonds, crypto, obligation, fonds_euros, scpi, or, immobilier, autre
    )
    isin: str | None
    ticker: str | None
    coingecko_id: str | None
    currency: str
    color: int | None = None  # couleur choisie (1 à 7), None = automatique (D-052)
    ter: Decimal | None = None  # frais courants en % par an (D-060)
    category: str | None = None  # catégorie personnelle ; None = la classe (D-061)

    @property
    def manual(self) -> bool:
        """Sans identifiant de prix : valeur saisie à la main (D-056), aucun appel réseau."""
        return self.ticker is None and self.coingecko_id is None


@dataclass(frozen=True)
class Operation:
    id: int
    account_id: int
    asset_id: int | None
    op_date: date
    op_type: str  # solde_initial, achat, vente, dividende, frais, versement, retrait
    quantity: Decimal | None
    unit_price: Decimal | None
    amount: Decimal | None
    fees: Decimal
    currency: str
    note: str | None
    # Vente d'un compte crypto (D-066) : échange contre une autre crypto, et valeur globale du
    # portefeuille crypto saisie (EUR) ; sans objet ailleurs.
    crypto_swap: bool = False
    portfolio_value: Decimal | None = None
    # Achat : part payée par la plateforme (Saveback, Stockperk), dans la devise de l'opération ;
    # dividende : impôt retenu à la source (D-076). None = rien.
    offered: Decimal | None = None
    tax_withheld: Decimal | None = None
    # Transfert sortant ou retrait (D-085) : compte de l'app qui reçoit les titres ou l'argent,
    # sinon destination hors de l'app en texte libre.
    to_account_id: int | None = None
    destination: str | None = None
    # Achat offert d'une crypto (D-092) : earn, staking, airdrop, cashback, parrainage, autre.
    reward_kind: str | None = None
    # Origine : 'manuel', nom de l'importeur, ou 'aave' (intérêts estimés par l'app, D-095).
    source: str = "manuel"


@dataclass(frozen=True)
class Position:
    """Ligne détenue sur un compte, calculée à partir des opérations (jamais stockée)."""

    account_id: int
    asset_id: int
    quantity: Decimal
    cost: Decimal  # prix de revient total, frais d'achat inclus
    last_price: Decimal  # cours en ligne si connu, sinon prix de la dernière opération
    currency: str

    @property
    def pru(self) -> Decimal:
        """Prix de revient unitaire : coût total / quantité."""
        return self.cost / self.quantity

    @property
    def value(self) -> Decimal:
        return self.quantity * self.last_price

    @property
    def gain(self) -> Decimal:
        """Plus-value latente (valeur - prix de revient)."""
        return self.value - self.cost
