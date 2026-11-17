"""Ce que les routes partagent sans passer par `app/main.py` : client HTTP des sources de prix,
nom du cookie de session, profils qui n'ont pas pu être ouverts au lancement, lecture bornée
du corps d'une requête."""

from collections.abc import Iterator
from typing import Annotated

import httpx2
from fastapi import Depends, HTTPException, Request

from app import providers

# Profils (D-098) : chaque requête de l'API porte un cookie de session, jeton aléatoire remis
# à l'ouverture d'un profil, qui désigne sa base.
SESSION_COOKIE = "patrimoine_session"

# Profils dont la base n'a pas pu être ouverte au lancement (version du schéma en retard...) :
# leur page dit quoi faire au lieu d'empêcher les autres profils de démarrer.
unready_profiles: dict[str, str] = {}


def http_client() -> Iterator[httpx2.Client]:
    """Client HTTP des sources de prix. Les tests le remplacent (app.dependency_overrides)."""
    with providers.new_client() as client:
        yield client


HttpClient = Annotated[httpx2.Client, Depends(http_client)]


async def body_up_to(request: Request, limit: int, message: str) -> bytes:
    """Corps d'une requête, lu morceau par morceau et abandonné dès qu'il dépasse `limit`.
    L'en-tête Content-Length ne suffit pas : un envoi en morceaux n'en porte pas, et
    `request.body()` le garderait en entier en mémoire avant tout refus."""
    try:
        announced = int(request.headers.get("content-length") or 0)
    except ValueError:
        announced = 0
    if announced > limit:
        raise HTTPException(413, message)
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise HTTPException(413, message)
        chunks.append(chunk)
    return b"".join(chunks)
