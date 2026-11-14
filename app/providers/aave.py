"""Aave v3 : taux de dépôt et d'emprunt publics de chaque marché, via l'API officielle,
sans clé (GraphQL).

Règle 3 élargie (D-095) : une requête ne contient qu'un numéro de chaîne, l'adresse publique
d'un marché et celle d'un jeton (des contrats d'Aave, pas un portefeuille), jamais une adresse
de portefeuille, un montant ni une quantité. L'API accepte un champ `user` (adresse d'un
portefeuille) : il n'est jamais envoyé, et les seules requêtes possibles sont les trois lectures
écrites ci-dessous (aucune mutation ; les requêtes `borrow`, `supply`, `repay` de l'API, qui
préparent une transaction, ne sont jamais appelées).

Notion : sur Aave, un « marché » est un pool de prêt (une chaîne peut en avoir plusieurs :
Core, Lido, EtherFi...). Chaque jeton déposé y rapporte un taux variable, renvoyé en APY
(rendement annuel composé, 0,03 pour 3 %).
"""

import re
from datetime import date, datetime
from decimal import Decimal

import httpx2

from app.providers import ProviderError

URL = "https://api.v3.aave.com/graphql"
ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")

CHAINS_QUERY = "query { chains { chainId name } }"
MARKETS_QUERY = (
    "query($c: [ChainId!]!) { markets(request: {chainIds: $c}) { name address"
    " reserves { underlyingToken { symbol address }"
    " supplyInfo { apy { value } liquidationThreshold { value } }"
    " borrowInfo { apy { value } borrowingState } isFrozen isPaused } } }"
)
HISTORY_QUERIES = {
    "supply": (
        "supplyAPYHistory",
        "query($r: SupplyAPYHistoryRequest!) { supplyAPYHistory(request: $r) {"
        " avgRate { value } date } }",
    ),
    "borrow": (
        "borrowAPYHistory",
        "query($r: BorrowAPYHistoryRequest!) { borrowAPYHistory(request: $r) {"
        " avgRate { value } date } }",
    ),
}


def check_address(value: str) -> str:
    if not isinstance(value, str) or not ADDRESS.match(value):
        raise ProviderError(f"adresse de contrat invalide : {value!r}")
    return value


def _post(client: httpx2.Client, query: str, variables: dict | None = None) -> dict:
    """Une lecture GraphQL ; nombres lus en Decimal (jamais de float)."""
    payload = {"query": query, "variables": variables or {}}
    try:
        response = client.post(URL, json=payload, headers={"Accept": "application/json"})
        response.raise_for_status()
        data = response.json(parse_float=Decimal, parse_int=Decimal)
    except httpx2.HTTPStatusError as err:
        raise ProviderError(f"réponse HTTP {err.response.status_code}") from err
    except httpx2.HTTPError as err:
        raise ProviderError(f"source injoignable ({type(err).__name__})") from err
    except ValueError as err:
        raise ProviderError("réponse illisible") from err
    if not isinstance(data, dict) or data.get("errors") or not isinstance(data.get("data"), dict):
        raise ProviderError("réponse Aave inattendue")
    return data["data"]


def _rate(value: object) -> Decimal:
    if isinstance(value, str):
        try:
            value = Decimal(value)
        except ArithmeticError as err:
            raise ProviderError(f"taux invalide : {value!r}") from err
    if not isinstance(value, Decimal) or not value.is_finite() or not 0 <= value < 10:
        raise ProviderError(f"taux invalide : {value!r}")
    return value


def get_chains(client: httpx2.Client) -> list[tuple[int, str]]:
    """Chaînes où Aave v3 est déployé : (numéro de chaîne, nom)."""
    data = _post(client, CHAINS_QUERY)
    try:
        return [(int(c["chainId"]), str(c["name"])) for c in data["chains"]]
    except (KeyError, TypeError, ValueError) as err:
        raise ProviderError("liste des chaînes Aave illisible") from err


def get_reserves(client: httpx2.Client, chain_id: int) -> list[dict]:
    """Jetons de chaque marché d'une chaîne : taux de dépôt et d'emprunt du jour, emprunt
    ouvert ou non, seuil de liquidation (part de la valeur d'un dépôt qui couvre une dette)."""
    data = _post(client, MARKETS_QUERY, {"c": [int(chain_id)]})
    out = []
    try:
        for market in data["markets"]:
            for r in market["reserves"]:
                borrow = r.get("borrowInfo")
                out.append(
                    {
                        "market": check_address(market["address"]),
                        "market_name": str(market["name"]),
                        "token": check_address(r["underlyingToken"]["address"]),
                        "symbol": str(r["underlyingToken"]["symbol"]),
                        "apy": _rate(r["supplyInfo"]["apy"]["value"]),
                        "liquidation_threshold": _threshold(r["supplyInfo"]),
                        "borrow_apy": _rate(borrow["apy"]["value"]) if borrow else None,
                        "borrowable": bool(borrow and borrow.get("borrowingState") == "ENABLED"),
                        "frozen": bool(r["isFrozen"] or r["isPaused"]),
                    }
                )
    except (KeyError, TypeError) as err:
        raise ProviderError("liste des marchés Aave illisible") from err
    return out


def _threshold(supply: dict) -> Decimal:
    """Seuil de liquidation entre 0 et 1 (0 : le jeton ne couvre aucune dette)."""
    value = (supply.get("liquidationThreshold") or {}).get("value", "0")
    try:
        value = Decimal(str(value))
    except ArithmeticError as err:
        raise ProviderError(f"seuil invalide : {value!r}") from err
    if not value.is_finite() or not 0 <= value <= 1:
        raise ProviderError(f"seuil invalide : {value!r}")
    return value


def get_rate_history(
    client: httpx2.Client, chain_id: int, market: str, token: str, side: str = "supply"
) -> dict[date, Decimal]:
    """Taux moyen de chaque jour des 365 derniers jours {jour: APY}, de dépôt (`supply`) ou
    d'emprunt (`borrow`). Le jour en cours est une moyenne partielle, remplacée le lendemain."""
    field, query = HISTORY_QUERIES[side]
    request = {
        "market": check_address(market),
        "underlyingToken": check_address(token),
        "chainId": int(chain_id),
        "window": "LAST_YEAR",
    }
    data = _post(client, query, {"r": request})
    out: dict[date, Decimal] = {}
    try:
        for sample in data[field]:
            day = datetime.fromisoformat(sample["date"]).date()
            out[day] = _rate(sample["avgRate"]["value"])
    except (KeyError, TypeError, ValueError) as err:
        raise ProviderError("historique des taux Aave illisible") from err
    return dict(sorted(out.items()))
