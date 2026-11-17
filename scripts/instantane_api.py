"""Instantané de l'API sur les bases de démo, pour vérifier qu'une refonte ne change rien.

    uv run python scripts/instantane_api.py avant.json     # avant la refonte
    uv run python scripts/instantane_api.py apres.json     # après
    diff avant.json apres.json                              # doit être vide

Écrire ces fichiers hors du dépôt (2 Mo chacun, à ne pas valider dans git).

Le fichier contient la table des routes (dans l'ordre où elles sont déclarées), la description
OpenAPI de chaque route (paramètres, formats d'entrée) et la réponse de chaque route de lecture
sur `data/demo.db` et `data/demo-grand.db` (données fictives). Réseau coupé, travail sur une
copie des bases dans un dossier temporaire : rien n'est lu ni écrit ailleurs.
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))
BASES = ("demo.db", "demo-grand.db")

# Routes de lecture appelées telles quelles, puis avec quelques paramètres qui changent le calcul.
VARIANTES = [
    "/api/performance?period=day",
    "/api/performance?period=week",
    "/api/performance?period=year",
    "/api/performance?period=month&exclude=1",
    "/api/performance?period=month&exclude_account=1",
    "/api/performance.csv?period=month",
    "/api/portfolio?exclude=1",
    "/api/portfolio?exclude_account=1",
    "/api/portfolio?exclude_line=1-1",
    "/api/history?exclude=1",
    "/api/history?inflation_rate=3",
    "/api/benchmark?rate=3",
    "/api/benchmark?ticker=%5EGSPC",
    "/api/calendar?rate=3",
    "/api/calendar?ticker=IWDA.AS",
    "/api/targets?level=categorie",
    "/api/targets?contribution=1000",
    "/api/targets?contribution=1000&level=categorie",
    "/api/tax?year=2025",
    "/api/tax?year=2024&tmi=41&per_ceiling=5000",
    "/api/tax?pfu=30",
    "/api/projection?monthly=500&years=20&target=100000&rates=3;6",
    "/api/projection?exclude_account=1",
    "/api/quote?ticker=IWDA.AS",
    "/api/tickers?isin=IE00B4L5Y983",
    "/api/aave/reserves?chain_id=1",
]


def _a_plat(routes) -> list:
    """Routes dans l'ordre de déclaration. FastAPI garde un routeur inclus (`include_router`) en
    un seul bloc, à sa place : ses routes sont dépliées ici, pour comparer avec ou sans routeur."""
    a_plat = []
    for route in routes:
        inclus = getattr(route, "original_router", None)
        a_plat.extend(_a_plat(inclus.routes) if inclus is not None else [route])
    return a_plat


def _routes(app) -> list[list]:
    """Chemin, méthodes et fonction de chaque route, dans l'ordre de déclaration : c'est cet
    ordre qui décide quelle route répond quand deux chemins se ressemblent."""
    lignes = []
    for route in _a_plat(app.routes):
        methodes = sorted(getattr(route, "methods", None) or [])
        lignes.append([route.path, methodes, route.name])
    return lignes


def _reponse(client, url: str) -> dict:
    r = client.get(url)
    genre = r.headers.get("content-type", "")
    if genre.startswith("application/json"):
        corps = r.json()
    elif genre.startswith("text/"):
        corps = r.text
    else:
        corps = f"<{len(r.content)} octets, {genre}>"
    return {"status": r.status_code, "body": corps}


def _lectures(client, app) -> dict:
    """Toutes les routes GET sans paramètre de chemin, les variantes, puis les routes à
    identifiant pour chaque actif et chaque liste de suivi de la base."""
    sorties = {}
    for route in _a_plat(app.routes):
        chemin = route.path
        if "GET" in (getattr(route, "methods", None) or ()) and "{" not in chemin:
            if chemin.startswith("/api/") and chemin != "/api/diagnostic":
                sorties[chemin] = _reponse(client, chemin)
    for url in VARIANTES:
        sorties[url] = _reponse(client, url)
    for asset in sorties["/api/assets"]["body"]:
        url = f"/api/assets/{asset['id']}/valuations"
        sorties[url] = _reponse(client, url)
    for liste in sorties["/api/watchlists"]["body"]:
        url = f"/api/watchlists/{liste['id']}/performance"
        sorties[url] = _reponse(client, url)
    return sorties


def _sans_chemins(valeur, dossier: str):
    """Le dossier temporaire change à chaque passage : il est remplacé par un nom fixe."""
    if isinstance(valeur, dict):
        return {k: _sans_chemins(v, dossier) for k, v in valeur.items()}
    if isinstance(valeur, list):
        return [_sans_chemins(v, dossier) for v in valeur]
    if isinstance(valeur, str):
        return valeur.replace(dossier, "<dossier>")
    return valeur


def main(sortie: Path) -> None:
    manquantes = [b for b in BASES if not (RACINE / "data" / b).exists()]
    if manquantes:
        raise SystemExit(
            f"Base(s) de démo absente(s) : {', '.join(manquantes)}. "
            "Lancer `uv run python -m app.demo` et `uv run python -m app.demo --grand`."
        )
    with tempfile.TemporaryDirectory() as dossier:
        # Posé avant d'importer l'app : dossier des données jetable, aucune vraie base lue.
        os.environ["PATRIMOINE_DATA_DIR"] = dossier
        # Réglages du poste laissés de côté : ils changeraient les réponses d'un passage à l'autre.
        for nom in ("COINGECKO_API_KEY", "BACKUP_COPY_DIR", "PRICE_TTL_MINUTES"):
            os.environ.pop(nom, None)

        import httpx2
        from fastapi.testclient import TestClient

        from app import main as api
        from app.main import app, http_client

        def coupe(request: httpx2.Request) -> httpx2.Response:
            raise httpx2.ConnectError("réseau coupé pour l'instantané", request=request)

        app.dependency_overrides[http_client] = lambda: httpx2.Client(
            transport=httpx2.MockTransport(coupe)
        )
        api.ALLOWED_HOSTS = api.ALLOWED_HOSTS | {"testserver"}

        resultat = {"routes": _routes(app), "openapi": app.openapi(), "bases": {}}
        for base in BASES:
            copie = Path(dossier) / base
            shutil.copyfile(RACINE / "data" / base, copie)
            os.environ["PATRIMOINE_DB"] = str(copie)
            with TestClient(app) as client:
                resultat["bases"][base] = _lectures(client, app)
        texte = json.dumps(_sans_chemins(resultat, dossier), ensure_ascii=False, indent=1)
    sortie.write_text(texte + "\n", encoding="utf-8")
    print(f"{sortie} : {len(resultat['routes'])} routes, {len(texte) // 1024} Ko")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(Path(sys.argv[1]))
