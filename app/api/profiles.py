"""Routes des profils (D-098) : liste, ajout, ouverture, verrouillage, nom, code, photo, export,
restauration, suppression. La logique est dans `app/profiles.py`."""

import os
from datetime import date

from fastapi import APIRouter, HTTPException, Query, Request, Response
from starlette.concurrency import run_in_threadpool

from app import profiles
from app.api.deps import SESSION_COOKIE, body_up_to
from app.schemas import (
    ProfileCodeIn,
    ProfileDeleteIn,
    ProfileIn,
    ProfileOpenIn,
    ProfilePatchIn,
    ProfilePhotoIn,
)

router = APIRouter()


def _set_session(response: Response, token: str) -> None:
    # HttpOnly : le JavaScript de la page ne lit pas le jeton ; SameSite=Strict : un autre site
    # ne peut pas l'envoyer. Cookie de session : oublié à la fermeture du navigateur.
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="strict", path="/")


def _current(request: Request) -> profiles.Profile | None:
    return getattr(request.state, "profile", None)


def _profile_error(err: profiles.ProfileError) -> HTTPException:
    return HTTPException(
        status_code=429 if isinstance(err, profiles.LockedOut) else 400, detail=str(err)
    )


@router.get("/api/profiles")
def list_profiles(request: Request) -> dict:
    """Profils de l'ordinateur (nom, couleur, code ou non) : jamais d'empreinte ni de chemin."""
    current = _current(request)
    return {
        "enabled": profiles.enabled(),
        "current": current.public() if current else None,
        "profiles": [p.public() for p in profiles.load()] if profiles.enabled() else [],
        "lock_minutes": int(profiles.idle_limit().total_seconds() // 60),
    }


@router.post("/api/profiles", status_code=201)
def create_profile(body: ProfileIn, request: Request, response: Response) -> dict:
    """Ajoute un profil. Il faut être dans un profil ouvert, sauf au tout premier ajout : la base
    actuelle devient alors le profil « Principal », ouvert d'office pour ce navigateur."""
    if os.environ.get("PATRIMOINE_DB"):
        raise HTTPException(409, "PATRIMOINE_DB force une base unique : profils indisponibles.")
    first = not profiles.enabled()
    try:
        profile = profiles.create(body.name, body.code or None, adopt_empty=body.adopt_empty)
    except profiles.ProfileError as err:
        raise _profile_error(err) from err
    if first:
        _set_session(response, profiles.open_session(profiles.load()[0].id, just_created=True))
    return profile.public()


@router.post("/api/profiles/demo", status_code=201)
def create_demo_profile(response: Response) -> dict:
    """Ajoute le profil Démo (données fictives), ou le remet à neuf s'il existe (D-098)."""
    if os.environ.get("PATRIMOINE_DB"):
        raise HTTPException(409, "PATRIMOINE_DB force une base unique : profils indisponibles.")
    first = not profiles.enabled()
    try:
        profile = profiles.create_demo()
    except profiles.ProfileError as err:
        raise _profile_error(err) from err
    if first:
        _set_session(response, profiles.open_session(profiles.load()[0].id))
    return profile.public()


@router.post("/api/profiles/{profile_id}/open")
def open_profile(profile_id: str, body: ProfileOpenIn, request: Request, response: Response):
    if not profiles.enabled():
        raise HTTPException(404, "Profils non activés.")
    try:
        token = profiles.open_session(profile_id, body.code)
    except profiles.ProfileError as err:
        raise _profile_error(err) from err
    profiles.close_session(request.cookies.get(SESSION_COOKIE))  # l'ancien jeton ne sert plus
    _set_session(response, token)
    return profiles.get(profile_id).public()


@router.post("/api/profiles/lock", status_code=204)
def lock_profile(request: Request, response: Response) -> None:
    """Verrouiller : le jeton est oublié par le serveur et effacé du navigateur."""
    profiles.close_session(request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.patch("/api/profiles/{profile_id}")
def patch_profile(profile_id: str, body: ProfilePatchIn, request: Request) -> dict:
    """Renommer un profil (depuis n'importe quel profil ouvert : le nom n'est pas secret) ;
    « tutoriel vu » seulement pour son propre profil."""
    current = _current(request)
    try:
        if body.tutorial_done is not None:
            if current is None or current.id != profile_id:
                raise HTTPException(403, "Seul le profil ouvert peut changer ce réglage.")
            profiles.set_tutorial_done(profile_id, body.tutorial_done)
        if body.name is not None:
            profiles.rename(profile_id, body.name)
    except profiles.ProfileError as err:
        raise _profile_error(err) from err
    return profiles.get(profile_id).public()


@router.put("/api/profiles/{profile_id}/code")
def set_profile_code(profile_id: str, body: ProfileCodeIn, request: Request) -> dict:
    """Seul le profil ouvert change son code ; l'ancien est redemandé. Un code oublié se retire
    dans le terminal (`uv run python -m app.profiles sans-code <id>`), jamais depuis l'app."""
    _profile_or_403(request, profile_id, "changer son code")
    try:
        profile = profiles.set_code(profile_id, body.new or None, body.current)
    except profiles.ProfileError as err:
        raise _profile_error(err) from err
    return profile.public()


@router.put("/api/profiles/{profile_id}/photo")
def set_profile_photo(profile_id: str, body: ProfilePhotoIn, request: Request) -> dict:
    """Seul le profil ouvert change sa photo."""
    _profile_or_403(request, profile_id, "changer sa photo")
    try:
        profile = profiles.set_photo(profile_id, body.photo)
    except profiles.ProfileError as err:
        raise _profile_error(err) from err
    return profile.public()


def _profile_or_403(request: Request, profile_id: str, action: str) -> None:
    current = _current(request)
    if current is None or current.id != profile_id:
        raise HTTPException(403, f"Seul le profil ouvert peut {action}.")


@router.get("/api/profiles/{profile_id}/export")
def export_profile(profile_id: str, request: Request) -> Response:
    """Copie de la base de son propre profil, téléchargée par le navigateur : elle reste sur la
    machine tant que la personne ne la déplace pas. Jamais celle d'un autre profil."""
    _profile_or_403(request, profile_id, "exporter ses données")
    try:
        data = profiles.export(profile_id)
    except profiles.ProfileError as err:
        raise _profile_error(err) from err
    name = f"patrimoine-{profile_id}-{date.today().isoformat()}.db"
    return Response(
        data,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/api/profiles/restore", status_code=201)
async def restore_profile(request: Request, response: Response, name: str = Query(max_length=40)):
    """Nouveau profil à partir d'une base exportée (corps de la requête = le fichier .db),
    vérifiée avant d'être acceptée (D-098). Jamais par-dessus un profil existant."""
    if os.environ.get("PATRIMOINE_DB"):
        raise HTTPException(409, "PATRIMOINE_DB force une base unique : profils indisponibles.")
    too_big = "Fichier trop gros pour une base de l'app."
    data = await body_up_to(request, profiles.RESTORE_MAX_BYTES, too_big)
    first = not profiles.enabled()
    try:
        profile = await run_in_threadpool(profiles.restore, name, data)
    except profiles.ProfileError as err:
        raise _profile_error(err) from err
    if first:
        _set_session(response, profiles.open_session(profiles.load()[0].id))
    return profile.public()


@router.delete("/api/profiles/{profile_id}", status_code=204)
def delete_profile(
    profile_id: str, body: ProfileDeleteIn, request: Request, response: Response
) -> None:
    """Supprimer un profil : nom retapé et code demandés ; sauvegarde puis corbeille (D-098)."""
    current = _current(request)
    try:
        profiles.delete(profile_id, body.confirm, body.code)
    except profiles.ProfileError as err:
        raise _profile_error(err) from err
    if current is not None and current.id == profile_id:
        response.delete_cookie(SESSION_COOKIE, path="/")
