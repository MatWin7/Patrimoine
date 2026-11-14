"""Ticker Yahoo d'une crypto (D-111) : Yahoo donne plusieurs années de clôtures, sans clé ni
compte, là où CoinGecko gratuit s'arrête à 365 jours et refuse vite les appels (HTTP 429).

Table vérifiée sur Yahoo le 2026-11-08 : cotation en euros quand elle existe, sinon en dollars.
Un symbole seul ne suffit pas : « ARB-USD » chez Yahoo est un autre jeton qu'Arbitrum
(« ARB11841-USD »), d'où une table par id CoinGecko plutôt qu'une devinette. Fonctions pures.
"""

import re

YAHOO_TICKERS = {
    "aave": "AAVE-EUR",
    "algorand": "ALGO-EUR",
    "aptos": "APT21794-USD",
    "arbitrum": "ARB11841-USD",
    "avalanche-2": "AVAX-EUR",
    "binance-staked-sol": "BNSOL-USD",
    "binancecoin": "BNB-EUR",
    "bitcoin": "BTC-EUR",
    "bitcoin-cash": "BCH-EUR",
    "bittensor": "TAO22974-USD",
    "cardano": "ADA-EUR",
    "chainlink": "LINK-EUR",
    "cosmos": "ATOM-EUR",
    "curve-dao-token": "CRV-EUR",
    "dai": "DAI-EUR",
    "decentraland": "MANA-EUR",
    "dogecoin": "DOGE-EUR",
    "elrond-erd-2": "EGLD-EUR",
    "ethereum": "ETH-EUR",
    "ethereum-classic": "ETC-EUR",
    "ethereum-pow-iou": "ETHW-USD",
    "euro-coin": "EURC-USD",
    "filecoin": "FIL-EUR",
    "first-digital-usd": "FDUSD-USD",
    "hedera-hashgraph": "HBAR-EUR",
    "internet-computer": "ICP-EUR",
    "kaspa": "KAS-USD",
    "litecoin": "LTC-EUR",
    "maker": "MKR-EUR",
    "monero": "XMR-EUR",
    "near": "NEAR-USD",
    "optimism": "OP-USD",
    "pepe": "PEPE24478-USD",
    "polkadot": "DOT-EUR",
    "polygon-ecosystem-token": "POL28321-EUR",
    "pyth-network": "PYTH-USD",
    "render-token": "RENDER-USD",
    "ripple": "XRP-EUR",
    "shiba-inu": "SHIB-EUR",
    "solana": "SOL-EUR",
    "stellar": "XLM-EUR",
    "sui": "SUI20947-USD",
    "tether": "USDT-EUR",
    "tezos": "XTZ-EUR",
    "the-sandbox": "SAND-EUR",
    "tron": "TRX-EUR",
    "uniswap": "UNI7083-EUR",
    "usd-coin": "USDC-EUR",
}

# Symbole courant -> id CoinGecko, pour taper « BTC » dans une liste de suivi.
_SYMBOLS = {t.split("-")[0].rstrip("0123456789"): coin for coin, t in YAHOO_TICKERS.items()}


def yahoo_ticker(ticker: str | None, coingecko_id: str | None) -> str | None:
    """Ticker Yahoo d'un actif : celui saisi dans « Gérer » d'abord, sinon celui de la table
    pour une crypto connue, sinon None (l'app se rabat alors sur CoinGecko, 365 jours)."""
    if ticker:
        return ticker
    return YAHOO_TICKERS.get(coingecko_id or "")


def resolve_crypto(text: str) -> str | None:
    """Crypto saisie dans une liste de suivi -> ticker Yahoo : un id CoinGecko (« bitcoin »),
    un symbole (« BTC ») ou déjà un ticker Yahoo (« BTC-EUR »). None si inconnue. Réservé au
    choix « Crypto » du formulaire : « SOL » en bourse est une action américaine."""
    key = text.strip()
    found = YAHOO_TICKERS.get(key.lower()) or YAHOO_TICKERS.get(_SYMBOLS.get(key.upper(), ""))
    if found:
        return found
    # Déjà un ticker Yahoo « JETON-DEVISE » ; « avalanche-2 » ou « jeton-inconnu » n'en sont pas.
    return key.upper() if re.fullmatch(r"[A-Za-z0-9]+-[A-Za-z]{3}", key) else None
