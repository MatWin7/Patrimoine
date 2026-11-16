"""Formats d'entrée de l'API, validés par Pydantic avant d'atteindre la base.

En sortie, les Decimal sont renvoyés en chaînes ("1234.56") pour ne jamais passer par un
float côté JSON : c'est le navigateur qui les formate pour l'affichage.
"""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    StringConstraints,
    field_validator,
    model_validator,
)

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


# Nettoyage avant validation : Pydantic applique le motif (pattern) au texte brut, donc
# " fr0000000001 " serait refusé sans ce passage préalable en majuscules.
Upper = BeforeValidator(lambda v: v.strip().upper() if isinstance(v, str) else v)
Currency = Annotated[str, Upper, StringConstraints(pattern=r"^[A-Z]{3}$")]
Isin = Annotated[str, Upper, StringConstraints(pattern=r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")]
Ticker = Annotated[str, Upper, StringConstraints(min_length=1, max_length=20)]
CoingeckoId = Annotated[str, StringConstraints(strip_whitespace=True, to_lower=True, min_length=1)]
# Catégorie personnelle (D-061) : texte libre court ; vide = aucune (la classe sert alors).
Blank = BeforeValidator(lambda v: (v.strip() or None) if isinstance(v, str) else v)
Category = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]


class SettingsIn(BaseModel):
    """Champs changés dans la carte Réglages (D-130) ; un champ absent ne bouge pas, null remet
    la valeur par défaut. Valeurs vérifiées par app/settings.py, dont les messages ne
    reprennent jamais la clé reçue."""

    model_config = ConfigDict(extra="forbid")

    price_ttl_minutes: StrictInt | None = None
    backup_keep: StrictInt | None = None
    profile_lock_minutes: StrictInt | None = None
    backup_copy_dir: StrictStr | None = None
    coingecko_api_key: StrictStr | None = None


class StartPageIn(BaseModel):
    """Page de démarrage (D-134) : fond et mot d'accueil ; un champ absent ne bouge pas, un
    texte vide ou null remet celui d'origine. Valeurs vérifiées par app/startpage.py."""

    model_config = ConfigDict(extra="forbid")

    fond: StrictStr | None = Field(default=None, max_length=20)
    texte: StrictStr | None = Field(default=None, max_length=400)


class UpdatesIn(BaseModel):
    """Réponse à « Prévenir des nouvelles versions ? » (D-129)."""

    enabled: StrictBool


class OrderIn(BaseModel):
    """Ordre choisi des comptes ou des actifs (D-070) : tous leurs id, du premier au dernier."""

    ids: list[int] = Field(max_length=10_000)


class AccountIn(BaseModel):
    name: Text
    kind: Literal["pea", "cto", "crypto", "assurance_vie", "per", "livret", "immobilier", "autre"]
    institution: Text | None = None
    currency: Currency = "EUR"
    cash: Decimal = Field(default=Decimal("0"), ge=0)  # solde espèces, dans la devise du compte
    # Repères fiscaux (D-059) : date d'ouverture, versements faits avant le suivi (PEA).
    opened_on: date | None = None
    deposits_before: Decimal = Field(default=Decimal("0"), ge=0)
    # Garde d'un compte crypto et établissement à l'étranger (D-104). Absents : devinés d'après
    # le nom et l'établissement à la création, gardés tels quels à la modification.
    custody: Literal["exchange", "wallet"] | None = None
    abroad: bool | None = None

    @field_validator("cash", "deposits_before")
    @classmethod
    def nombre_fini(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("Nombre invalide.")
        return value


class AssetIn(BaseModel):
    name: Text
    asset_class: Literal[
        "action",
        "etf",
        "fonds",
        "crypto",
        "obligation",
        "fonds_euros",
        "scpi",
        "or",
        "immobilier",
        "autre",
    ]
    isin: Isin | None = None
    ticker: Ticker | None = None
    coingecko_id: CoingeckoId | None = None
    currency: Currency = "EUR"
    color: int | None = Field(default=None, ge=1, le=7)  # None = automatique (D-052)
    ter: Decimal | None = Field(default=None, ge=0, le=10, allow_inf_nan=False)  # % par an
    category: Annotated[Category | None, Blank] = None

    @model_validator(mode="after")
    def identifiant_de_prix(self):
        # Sans identifiant, la valeur est saisie à la main (D-056) : réservé aux classes qui
        # n'ont souvent pas de cours public (fonds euros, immobilier, SCPI...).
        priced = self.asset_class in ("action", "etf", "crypto")
        if priced and self.ticker is None and self.coingecko_id is None:
            raise ValueError("Un ticker (ex. CW8.PA) ou un id CoinGecko est obligatoire.")
        if self.asset_class == "crypto" and self.coingecko_id is None:
            raise ValueError("Une crypto a besoin de son id CoinGecko (ex. bitcoin).")
        return self


# PRU déduit d'un montant : 8 décimales, l'écart quantité × PRU − montant reste sous le centime.
PRU_PLACES = Decimal("0.00000001")


Destination = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
RewardKind = Literal["earn", "staking", "airdrop", "cashback", "parrainage", "autre"]


def _keep_transfer(body, op_type: str) -> None:
    """Destination (D-085) : seulement sur un transfert sortant ou un retrait ; un compte de
    l'app l'emporte sur le texte libre."""
    if body.op_type != op_type:
        body.to_account_id = body.destination = None
    elif body.to_account_id is not None:
        if body.to_account_id == body.account_id:
            raise ValueError("Choisis un autre compte que celui d'où part le transfert.")
        body.destination = None


class TradeIn(BaseModel):
    """Achat, vente, titres déjà détenus (solde_initial) ou envoyés ailleurs (transfert_sortant,
    D-082). Devise : celle de cotation de l'actif.

    Pour des titres déjà détenus, `amount` (montant actuel de la ligne) peut remplacer le prix
    unitaire : le PRU devient montant ÷ quantité (D-049). Le montant n'est pas enregistré.
    """

    account_id: int
    asset_id: int
    op_date: date
    op_type: Literal["achat", "vente", "solde_initial", "transfert_sortant"]
    # Quantité vide : actif à valeur saisie (D-056), seul le montant compte ; le serveur en
    # déduit des parts au dernier prix unitaire connu.
    quantity: Decimal | None = Field(default=None, gt=0)
    unit_price: Decimal | None = Field(default=None, ge=0)
    amount: Decimal | None = Field(default=None, gt=0)
    fees: Decimal = Field(default=Decimal("0"), ge=0)
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] | None = None
    # Vente sur un compte crypto (D-066) : échange contre une autre crypto (non imposable) et
    # valeur globale du portefeuille crypto ce jour-là, en EUR (vide = estimée par l'app).
    crypto_swap: bool = False
    portfolio_value: Decimal | None = Field(default=None, gt=0, allow_inf_nan=False)
    # Achat : part payée par la plateforme (Saveback, Stockperk, D-076), au plus le total.
    offered: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    # Achat offert d'une crypto (D-092) : type de récompense, affiché dans l'onglet Crypto.
    reward_kind: RewardKind | None = None
    # Transfert sortant (D-085) : compte de l'app qui reçoit les titres, sinon texte libre.
    to_account_id: int | None = None
    destination: Destination | None = None

    @model_validator(mode="after")
    def prix_ou_montant(self):
        _keep_transfer(self, "transfert_sortant")
        if self.op_type != "vente" or self.crypto_swap:
            self.portfolio_value = None  # sans objet : pas de cession imposable
        if self.op_type != "vente":
            self.crypto_swap = False
        if self.op_type != "achat" or not self.offered:
            self.offered = self.reward_kind = None
        if self.quantity is None:
            if self.amount is None:
                raise ValueError("Indique la quantité, ou le montant pour une valeur saisie.")
            self.unit_price = None
            return self
        if self.unit_price is None:
            if self.op_type != "solde_initial" or self.amount is None:
                raise ValueError("Indique le prix unitaire (ou le montant actuel de la ligne).")
            self.unit_price = (self.amount / self.quantity).quantize(PRU_PLACES, ROUND_HALF_UP)
        self.amount = None  # le PRU saisi, s'il y en a un, l'emporte
        return self

    @field_validator("op_date")
    @classmethod
    def pas_dans_le_futur(cls, value: date) -> date:
        if value > date.today():
            raise ValueError("La date ne peut pas être dans le futur.")
        return value

    @field_validator("quantity", "unit_price", "amount", "fees")
    @classmethod
    def nombre_fini(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and not value.is_finite():
            raise ValueError("Nombre invalide.")
        return value


class CashIn(BaseModel):
    """Montant seul : versement ou retrait (argent qui entre sur le compte ou en sort), frais
    (droits de garde...) ou dividende d'une ligne (D-057), crédité aux espèces du compte."""

    account_id: int
    op_date: date
    op_type: Literal["versement", "retrait", "frais", "dividende"]
    asset_id: int | None = None  # obligatoire pour un dividende, ignoré sinon
    amount: Decimal = Field(gt=0)
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] | None = None
    # Dividende : impôt retenu à la source (D-076), le montant restant le net reçu.
    tax_withheld: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    # Retrait (D-085) : compte de l'app qui reçoit l'argent (versement), sinon texte libre.
    to_account_id: int | None = None
    destination: Destination | None = None

    @model_validator(mode="after")
    def actif_du_dividende(self):
        _keep_transfer(self, "retrait")
        if self.op_type == "dividende" and self.asset_id is None:
            raise ValueError("Un dividende se rattache à une ligne : choisis l'actif.")
        if self.op_type != "dividende":
            self.asset_id = None
        if self.op_type != "dividende" or not self.tax_withheld:
            self.tax_withheld = None
        return self

    @field_validator("op_date")
    @classmethod
    def pas_dans_le_futur(cls, value: date) -> date:
        return TradeIn.pas_dans_le_futur(value)

    @field_validator("amount")
    @classmethod
    def nombre_fini(cls, value: Decimal) -> Decimal:
        return TradeIn.nombre_fini(value)


class TargetsIn(BaseModel):
    """Allocation cible (D-058) : {id d'actif: part visée en %} ; total de 100 % au plus."""

    targets: dict[int, Annotated[Decimal, Field(ge=0, le=100, allow_inf_nan=False)]]

    @model_validator(mode="after")
    def total(self):
        if sum(self.targets.values(), Decimal("0")) > 100:
            raise ValueError("Le total des parts visées dépasse 100 %.")
        return self


class CategoryTargetsIn(BaseModel):
    """Allocation cible par catégorie (D-061) : {catégorie: part visée en %}, 100 % au plus."""

    targets: dict[Category, Annotated[Decimal, Field(ge=0, le=100, allow_inf_nan=False)]]

    @model_validator(mode="after")
    def total(self):
        return TargetsIn.total(self)


class RecurringIn(BaseModel):
    """Achat programmé (D-055) : montant visé (devise de l'actif), fréquence et jour."""

    account_id: int
    asset_id: int
    amount: Decimal = Field(gt=0, allow_inf_nan=False)
    frequency: Literal["mensuel", "trimestriel", "hebdomadaire", "bimensuel"] = "mensuel"
    # Jour du mois (29 à 31 n'existent pas tous les mois) ; en hebdomadaire ou toutes les deux
    # semaines, 1 = lundi à 7.
    day: int = Field(ge=1, le=28)
    start_date: date
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] | None = None

    @model_validator(mode="after")
    def jour_de_la_semaine(self) -> "RecurringIn":
        if self.frequency in ("hebdomadaire", "bimensuel") and self.day > 7:
            raise ValueError("jour de la semaine : 1 (lundi) à 7 (dimanche)")
        return self


class RecurringConfirmIn(BaseModel):
    """Échéance validée avec le prix et la quantité réellement exécutés."""

    due_date: date  # échéance traitée (la plus ancienne en attente)
    op_date: date
    quantity: Decimal = Field(gt=0, allow_inf_nan=False)
    unit_price: Decimal = Field(gt=0, allow_inf_nan=False)
    fees: Decimal = Field(default=Decimal("0"), ge=0, allow_inf_nan=False)

    @field_validator("op_date")
    @classmethod
    def pas_dans_le_futur(cls, value: date) -> date:
        return TradeIn.pas_dans_le_futur(value)


class ValuationIn(BaseModel):
    """Valeur totale d'une ligne à valeur saisie (D-056), à une date."""

    value_date: date
    value: Decimal = Field(gt=0, allow_inf_nan=False)

    @field_validator("value_date")
    @classmethod
    def pas_dans_le_futur(cls, value: date) -> date:
        return TradeIn.pas_dans_le_futur(value)


class RecurringSkipIn(BaseModel):
    due_date: date


class SplitIn(BaseModel):
    """Part d'un retrait envoyée à un compte de l'app (D-087) : quantité du jeton, ou montant
    en euros pour un retrait d'espèces."""

    account_id: int
    value: Annotated[Decimal, Field(gt=0, max_digits=40, allow_inf_nan=False)]


class ImportIn(BaseModel):
    """Fichier CSV à lire (D-053) : son texte, le compte visé et la correspondance des colonnes
    ({champ: numéro de colonne}, proposée par le serveur si absente). Rien n'est gardé."""

    account_id: int | None = None
    text: Annotated[str, StringConstraints(max_length=2_000_000)]
    mapping: dict[str, int | None] | None = None
    header: bool = True  # la première ligne contient les noms des colonnes
    # Profil de plateforme (D-071) : None = reconnu d'après les en-têtes, "generique" = aucun.
    profile: Literal["generique", "trade_republic", "binance"] | None = None
    # Compte choisi pour chaque partie du relevé (PEA, compte-titres...) ; sinon proposé.
    accounts: dict[str, int | None] | None = None
    # Plans d'investissement détectés à créer en achats programmés (clés de l'aperçu).
    plans: list[str] | None = None
    # Transfert sortant ou retrait : {numéro de ligne: compte de l'app qui reçoit} (D-085).
    destinations: dict[int, int] | None = None
    # Sinon, un texte libre « vers où » (Ledger, banque...), par numéro de ligne (D-086).
    destination_texts: dict[int, Destination] | None = None
    # Retrait réparti entre plusieurs comptes de l'app : {numéro de ligne: parts} ; le reste va
    # « hors de l'app » (avec son texte). Prime sur `destinations` pour la ligne (D-087).
    splits: dict[int, Annotated[list[SplitIn], Field(max_length=12)]] | None = None


class BenchmarkIn(BaseModel):
    """Entrée de la liste de comparaison : un indice (ticker Yahoo) ou un livret (taux en %)."""

    kind: Literal["indice", "livret"]
    label: Text
    grp: Text | None = None
    ticker: Annotated[Ticker, StringConstraints(pattern=r"^[A-Z0-9.^=\-]{1,20}$")] | None = None
    rate: Decimal | None = Field(default=None, ge=-5, le=30)

    @model_validator(mode="after")
    def identifiant(self):
        if self.kind == "indice" and self.ticker is None:
            raise ValueError("Un indice a besoin de son ticker Yahoo (ex. ^FCHI).")
        if self.kind == "livret" and (self.rate is None or not self.rate.is_finite()):
            raise ValueError("Un livret a besoin de son taux annuel en % (ex. 1,7).")
        if self.kind == "livret":
            self.ticker = None
        else:
            self.rate = None
        return self


# Le type d'opération choisit le format : achat/vente/solde initial ou versement/retrait.
OperationIn = Annotated[TradeIn | CashIn, Field(discriminator="op_type")]


def money(value: Decimal, places: str = "0.01") -> str:
    """Arrondi d'affichage (au centime par défaut), renvoyé en chaîne."""
    return str(value.quantize(Decimal(places), rounding=ROUND_HALF_UP))


class ReconcileIn(BaseModel):
    """Chiffres du relevé de la banque, saisis pour comparer (D-063) ; jamais enregistrés.
    Un champ laissé vide n'est pas vérifié."""

    cash: Decimal | None = None
    quantities: dict[int, Decimal] = {}

    @field_validator("cash")
    @classmethod
    def nombre_fini(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and not value.is_finite():
            raise ValueError("Nombre invalide.")
        return value

    @field_validator("quantities")
    @classmethod
    def quantites_valides(cls, value: dict[int, Decimal]) -> dict[int, Decimal]:
        if any(not q.is_finite() or q < 0 for q in value.values()):
            raise ValueError("Quantité invalide.")
        return value


class WatchlistIn(BaseModel):
    """Liste de suivi (D-072) : seulement un nom."""

    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]


class WatchlistItemIn(BaseModel):
    """Actif suivi : un nom et son ticker Yahoo public (action, ETF, indice, crypto « BTC-EUR »).

    `crypto` (D-111) : le texte peut aussi être un id CoinGecko (« bitcoin ») ou un symbole
    (« BTC ») ; l'API le traduit en ticker Yahoo avant de l'enregistrer."""

    label: Text
    ticker: Annotated[str, Upper, StringConstraints(pattern=r"^[A-Z0-9.^=\-]{1,30}$")]
    crypto: bool = False


# --- Dépôts Aave suivis (D-095) ---------------------------------------------------------

EvmAddress = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r"^0x[0-9a-fA-F]{40}$")
]


class DefiMoveIn(BaseModel):
    """Dépôt sur Aave ou retrait (D-096). `other_account_id` : compte de l'app d'où viennent
    les jetons (dépôt) ou où ils vont (retrait), transfert au prix de revient ; vide = hors de
    l'app. `unit_price` vide : cours du jeton ce jour-là, sinon dernier cours connu."""

    direction: Literal["depot", "retrait"] = "depot"
    quantity: Decimal = Field(gt=0, allow_inf_nan=False)
    op_date: date
    other_account_id: int | None = None
    unit_price: Decimal | None = Field(default=None, gt=0, allow_inf_nan=False)

    @field_validator("op_date")
    @classmethod
    def pas_dans_le_futur(cls, value: date) -> date:
        if value > date.today():
            raise ValueError("La date ne peut pas être dans le futur.")
        return value


class DefiLineIn(BaseModel):
    """Ligne (compte, jeton) reliée à un marché Aave public : chaîne, pool et jeton choisis
    dans la liste renvoyée par /api/aave/reserves. Aucune adresse de portefeuille."""

    account_id: int | None = None  # vide : nouveau compte crypto « Aave <chaîne> » (D-096)
    asset_id: int
    chain_id: int = Field(gt=0)
    market: EvmAddress
    token: EvmAddress
    deposit: DefiMoveIn | None = None  # premier dépôt, facultatif


class DefiCheckIn(BaseModel):
    """Solde lu dans Aave à une date (« Vérifier ») : il recale l'estimation des intérêts."""

    check_date: date
    quantity: Decimal = Field(ge=0)

    @field_validator("quantity")
    @classmethod
    def nombre_fini(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("Nombre invalide.")
        return value


ProfileName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
ProfileCode = Annotated[str, StringConstraints(max_length=64)]


class ProfileIn(BaseModel):
    """Nouveau profil (D-098) : un nom, et un code facultatif."""

    name: ProfileName
    code: ProfileCode | None = None
    # Tutoriel de premier lancement : une base encore vide devient ce profil (pas de « Principal »).
    adopt_empty: bool = False


class ProfileOpenIn(BaseModel):
    code: ProfileCode = ""


class ProfilePatchIn(BaseModel):
    name: ProfileName | None = None
    tutorial_done: bool | None = None


class ProfilePhotoIn(BaseModel):
    """Photo du profil en data URL (vignette réduite par le navigateur) ; vide pour la retirer."""

    photo: str | None = Field(default=None, max_length=70_000)


class ProfileCodeIn(BaseModel):
    """Poser, changer (`current` = code actuel) ou retirer (`new` vide) le code de son profil."""

    current: ProfileCode = ""
    new: ProfileCode | None = None


class ProfileDeleteIn(BaseModel):
    """Supprimer un profil (D-098) : son nom retapé, et son code s'il en a un."""

    confirm: ProfileName
    code: ProfileCode = ""


class DebtIn(BaseModel):
    """Dette (D-107) : montant emprunté au départ ; taux annuel en % et durée en mois
    facultatifs (sans durée, la dette ne baisse qu'avec un capital restant dû saisi)."""

    name: Text
    kind: Literal["immobilier", "conso", "etudiant", "autre"]
    institution: Text | None = None
    currency: Currency = "EUR"
    principal: Decimal = Field(gt=0)
    rate_pct: Decimal | None = Field(default=None, ge=0, le=100)
    start_date: date
    months: int | None = Field(default=None, ge=1, le=600)

    @field_validator("principal", "rate_pct")
    @classmethod
    def nombre_fini(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and not value.is_finite():
            raise ValueError("Nombre invalide.")
        return value


class DebtBalanceIn(BaseModel):
    """Capital restant dû lu sur un relevé (D-107) : il recale le calcul à cette date."""

    balance_date: date
    balance: Decimal = Field(ge=0)

    @field_validator("balance")
    @classmethod
    def nombre_fini(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("Nombre invalide.")
        return value


class DefiBorrowIn(BaseModel):
    """Emprunt Aave suivi (D-108) : marché et jeton choisis dans la liste publique, premier
    emprunt en jetons. Aucune adresse de portefeuille."""

    asset_id: int
    chain_id: int = Field(gt=0)
    market: EvmAddress
    token: EvmAddress
    quantity: Decimal = Field(gt=0, allow_inf_nan=False)
    op_date: date

    @field_validator("op_date")
    @classmethod
    def pas_dans_le_futur(cls, value: date) -> date:
        if value > date.today():
            raise ValueError("La date ne peut pas être dans le futur.")
        return value


class DefiBorrowMoveIn(BaseModel):
    """Nouvel emprunt ou remboursement, en jetons (D-108)."""

    direction: Literal["emprunt", "remboursement"]
    quantity: Decimal = Field(gt=0, allow_inf_nan=False)
    op_date: date

    @field_validator("op_date")
    @classmethod
    def pas_dans_le_futur(cls, value: date) -> date:
        if value > date.today():
            raise ValueError("La date ne peut pas être dans le futur.")
        return value
