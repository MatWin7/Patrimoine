"""Garde d'un compte crypto et comptes à l'étranger (D-104) : fonctions pures.

Garde (custody) : sur une plateforme d'échange (exchange), la plateforme détient les clés et
doit les cryptos à son client ; dans un portefeuille personnel (wallet : Ledger, MetaMask...),
seul le détenteur a les clés. Un compte ouvert dans un établissement à l'étranger se déclare
chaque année avec la déclaration de revenus : formulaire 3916 (banque, courtier, assurance-vie)
ou 3916-bis (plateforme crypto). Un portefeuille personnel ne se déclare pas.
"""

from collections.abc import Iterable

from app.models import Account

CUSTODIES = ("exchange", "wallet")

# Noms reconnus dans le nom ou l'établissement du compte, en minuscules. Les portefeuilles
# d'abord : « Coinbase Wallet » est un portefeuille, « Coinbase » une plateforme.
WALLETS = (
    "ledger", "trezor", "metamask", "rabby", "phantom", "trust wallet", "exodus", "rainbow",
    "coinbase wallet", "tangem", "keystone", "bitbox", "electrum", "sparrow", "aave",
)  # fmt: skip
EXCHANGES = (
    "binance", "kraken", "coinbase", "bitpanda", "bitstamp", "bybit", "okx", "kucoin",
    "crypto.com", "gemini", "bitget", "bitvavo", "paymium", "coinhouse", "revolut",
    "trade republic",
)  # fmt: skip
# Établissements étrangers connus : case « à l'étranger » cochée d'office (modifiable). Les
# plateformes françaises (Paymium, Coinhouse) n'y sont pas. Binance y est : ses clients
# français passent par une filiale française, mais l'administration ne s'est pas prononcée
# clairement, et déclarer un compte de trop ne coûte rien (en oublier un : 750 € d'amende).
ABROAD = (
    "binance", "kraken", "coinbase", "bitpanda", "bitstamp", "bybit", "okx", "kucoin",
    "crypto.com", "gemini", "bitget", "bitvavo", "revolut", "trade republic", "degiro",
    "interactive brokers", "scalable", "n26", "saxo", "freedom24",
)  # fmt: skip


def _text(name: str, institution: str | None) -> str:
    return f"{name} {institution or ''}".lower()


def guess_custody(name: str, institution: str | None) -> str | None:
    """Garde devinée d'un compte crypto d'après son nom et son établissement ; None si rien
    n'est reconnu (« à préciser » : jamais de supposition)."""
    text = _text(name, institution)
    if any(w in text for w in WALLETS):
        return "wallet"
    if any(e in text for e in EXCHANGES):
        return "exchange"
    return None


def guess_abroad(name: str, institution: str | None, custody: str | None = None) -> bool:
    """Établissement étranger connu ? Toujours faux pour un portefeuille personnel."""
    if custody == "wallet":
        return False
    text = _text(name, institution)
    return any(a in text for a in ABROAD)


def form_of(account: Account) -> str:
    """Formulaire de déclaration d'un compte à l'étranger."""
    return "3916-bis" if account.kind == "crypto" else "3916"


def foreign_accounts(accounts: Iterable[Account]) -> list[dict]:
    """Comptes à déclarer chaque année : ceux cochés « à l'étranger », hors portefeuilles."""
    return [
        {
            "account_id": a.id,
            "account": a.name,
            "institution": a.institution,
            "kind": a.kind,
            "form": form_of(a),
            "opened_on": a.opened_on,
        }
        for a in accounts
        if a.abroad and a.custody != "wallet"
    ]
