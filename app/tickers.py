"""Ticker Yahoo d'un titre d'après son ISIN (D-083) : table locale des titres courants, puis
recherche chez Yahoo sur demande. Fonctions pures, sauf `search` qui reçoit le client HTTP.

Un même ISIN a un ticker par Bourse et par devise (IWDA.AS en euros à Amsterdam, IWDA.L en
dollars à Londres) : il faut celui de la devise du relevé, sinon ses lignes sont refusées.
"""

import re

import httpx2

from app.providers import ProviderError, yahoo

# ISIN -> {devise: ticker}. Vérifiés chez Yahoo le 2026-10-23 (recherche de l'ISIN, devise du
# cours). À compléter au fil des demandes ; le reste passe par la recherche.
KNOWN: dict[str, dict[str, str]] = {
    # ETF éligibles au PEA
    "LU1681043599": {"EUR": "CW8.PA"},  # Amundi MSCI World Swap
    "FR001400U5Q4": {"EUR": "DCAM.PA"},  # Amundi PEA Monde (MSCI World)
    "IE0002XZSHO1": {"EUR": "WPEA.PA"},  # iShares MSCI World Swap PEA
    "FR0011871128": {"EUR": "PSP5.PA"},  # Amundi PEA S&P 500
    "FR0011550185": {"EUR": "ESE.PA"},  # BNP Paribas Easy S&P 500
    "FR0011871110": {"EUR": "PUST.PA"},  # Amundi PEA Nasdaq-100
    "FR0013412020": {"EUR": "PAEEM.PA"},  # Amundi PEA Émergents
    # ETF de compte-titres courants
    "IE00B4L5Y983": {"EUR": "IWDA.AS", "USD": "IWDA.L"},  # iShares Core MSCI World
    "IE00BK5BQT80": {"EUR": "VWCE.DE", "USD": "VWRA.L"},  # Vanguard FTSE All-World
    "IE00B5BMR087": {"EUR": "SXR8.DE"},  # iShares Core S&P 500
    "IE00BKM4GZ66": {"EUR": "IS3N.DE", "USD": "EIMI.L"},  # iShares Core MSCI EM IMI
    # Actions françaises
    "FR0000120271": {"EUR": "TTE.PA"},  # TotalEnergies
    "FR0000121014": {"EUR": "MC.PA"},  # LVMH
    "FR0000120578": {"EUR": "SAN.PA"},  # Sanofi
    "FR0000131104": {"EUR": "BNP.PA"},  # BNP Paribas
    "FR0000120073": {"EUR": "AI.PA"},  # Air Liquide
    "FR0000121972": {"EUR": "SU.PA"},  # Schneider Electric
    "FR0000120321": {"EUR": "OR.PA"},  # L'Oréal
    "NL0000235190": {"EUR": "AIR.PA"},  # Airbus
    # Actions américaines : cotation en euros à Francfort (Xetra), en dollars à New York
    "US0378331005": {"EUR": "APC.DE", "USD": "AAPL"},  # Apple
    "US5949181045": {"EUR": "MSF.DE", "USD": "MSFT"},  # Microsoft
    "US0231351067": {"EUR": "AMZ.DE", "USD": "AMZN"},  # Amazon
    "US67066G1040": {"EUR": "NVD.DE", "USD": "NVDA"},  # NVIDIA
    "US88160R1014": {"EUR": "TL0.DE", "USD": "TSLA"},  # Tesla
    "US30303M1027": {"EUR": "FB2A.DE", "USD": "META"},  # Meta
}

ISIN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
# Places préférées à devise égale : Paris, Amsterdam, Xetra, Milan, puis le reste. Les cotations
# de Stuttgart nommées par l'ISIN (« FR0011871128.SG ») sont peu échangées : en dernier.
PREFERRED = (".PA", ".AS", ".DE", ".MI", ".BR", ".F")
CHECKED = 6  # cotations dont on demande la devise, au plus


def known(isin: str, currency: str) -> str | None:
    """Ticker de la table locale pour cet ISIN dans cette devise, sans appel réseau."""
    return KNOWN.get(isin.strip().upper(), {}).get(currency.strip().upper())


def _rank(symbol: str, isin: str) -> tuple[int, int]:
    if symbol.startswith(isin):
        return (2, 0)
    for i, suffix in enumerate(PREFERRED):
        if symbol.endswith(suffix):
            return (0, i)
    return (1, 0)


def search(client: httpx2.Client, isin: str, currency: str) -> list[dict]:
    """Cotations Yahoo d'un ISIN, celles dans `currency` d'abord. Yahoo ne renvoie souvent
    qu'une cotation par ISIN : une seconde recherche par son nom trouve les autres places.
    Seuls l'ISIN et le nom public du titre partent sur le réseau (règle 3)."""
    isin, currency = isin.strip().upper(), currency.strip().upper()
    found = yahoo.search(client, isin)
    names = {q["name"] for q in found if q["name"]}
    for name in sorted(names)[:1]:
        found += [q for q in yahoo.search(client, name) if q["name"] in names]
    symbols = sorted({q["symbol"] for q in found}, key=lambda s: _rank(s, isin))
    names_by = {q["symbol"]: q["name"] for q in found}
    out = []
    for symbol in symbols[:CHECKED]:
        try:
            price = yahoo.get_price(client, symbol)
        except ProviderError:
            continue
        out.append(
            {
                "ticker": symbol,
                "name": names_by.get(symbol),
                "currency": price.currency,
                "price": format(price.price.normalize(), "f"),
            }
        )
    out.sort(key=lambda q: q["currency"] != currency)
    return out
