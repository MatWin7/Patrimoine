"""Routes de la machine, communes à tous les profils : état de l'app, avis de mise à jour
(D-129), carte Réglages (D-130), page de démarrage (D-134), « Ce qui sort » (D-131), diagnostic
(D-132) et bouton « Fenêtre » (D-125, D-127)."""

import sqlite3
from datetime import date
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool

from app import backup, config, db, diagnostic, outbound, profiles, settings, startpage, updates
from app.api import deps
from app.api.deps import HttpClient, body_up_to
from app.schemas import SettingsIn, StartPageIn, UpdatesIn
from app.version import __version__

router = APIRouter()


@router.get("/api/health")
def health(request: Request) -> dict:
    """« app » : le lanceur reconnaît une instance déjà ouverte de Patrimoine (D-118) ;
    « version » : numéro de l'app, affiché dans la barre du haut (D-121)."""
    base = {"status": "ok", "app": "patrimoine", "version": __version__}
    if profiles.enabled() and getattr(request.state, "profile", None) is None:
        return {**base, "profile_required": True}
    with db.connect() as conn:
        version = db.schema_version(conn)
    conn.close()
    return {**base, "schema_version": version}


# --- Avis de mise à jour (D-129) ---------------------------------------------------------------
# Réglage de la machine, pas d'un profil. GET vérifie une fois par jour au plus, et seulement
# si l'utilisateur l'a accepté : avant sa réponse à la question du premier lancement, aucun appel.


@router.get("/api/updates")
def updates_status(client: HttpClient) -> dict:
    return updates.status(updates.check(client, date.today()))


@router.put("/api/updates")
def updates_choice(body: UpdatesIn) -> dict:
    return updates.status(updates.set_enabled(body.enabled))


@router.post("/api/updates/check")
def updates_check_now(client: HttpClient) -> dict:
    """Clic sur « Vérifier maintenant » : passe outre la limite d'une fois par jour."""
    return updates.status(updates.check(client, date.today(), force=True))


# --- Carte Réglages (D-130) ---------------------------------------------------------------------
# Réglages de la machine (tous les profils), rangés dans reglages.json. La clé CoinGecko n'est
# jamais renvoyée ; un réglage fixé par .env est montré sans pouvoir être changé ici.


@router.get("/api/settings")
def settings_status() -> dict:
    return settings.status()


@router.put("/api/settings")
def settings_update(body: SettingsIn) -> dict:
    changes = {name: getattr(body, name) for name in body.model_fields_set}
    with settings.file_lock:  # rien d'autre n'écrit reglages.json entre la lecture et l'écriture
        try:
            section = settings.prepare(changes)
        except settings.SettingsError as err:
            raise HTTPException(status_code=400, detail=str(err)) from err
        message = None
        dest = section.get("backup_copy_dir")
        if "backup_copy_dir" in changes and dest:
            # Première copie tout de suite : un dossier où l'on ne peut pas écrire est refusé
            # avant d'être retenu, et l'utilisateur voit que la copie marche sans attendre.
            try:
                copied = backup.copy_out(Path(dest))
            except (OSError, sqlite3.Error, RuntimeError) as err:
                raise HTTPException(status_code=400, detail=f"Copie impossible : {err}") from err
            message = f"{len(copied)} sauvegarde(s) recopiée(s)." if copied else "Dossier prêt."
        settings.save(section)
    return {**settings.status(), "message": message}


# --- Page de démarrage (D-134) ------------------------------------------------------------------
# Fond et mot d'accueil, réglages de la machine. La lecture répond sans profil ouvert : c'est
# l'écran affiché avant le choix du profil, et il ne lit aucune base. Changer le fond, le texte
# ou l'image demande un profil ouvert, comme les autres réglages.


def _start_page_error(err: startpage.StartPageError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(err))


@router.get("/api/start")
def start_page() -> dict:
    return startpage.status()


@router.put("/api/start")
def start_page_update(body: StartPageIn) -> dict:
    changes = {name: getattr(body, name) for name in body.model_fields_set}
    try:
        return startpage.update(changes)
    except startpage.StartPageError as err:
        raise _start_page_error(err) from err


@router.get("/api/start/image")
def start_page_image() -> Response:
    """L'image personnelle, telle qu'enregistrée. Type fixé ici, jamais deviné par le
    navigateur (X-Content-Type-Options: nosniff sur toute réponse)."""
    data = startpage.image_bytes()
    if data is None:
        raise HTTPException(404, "Aucune image enregistrée.")
    return Response(content=data, media_type="image/jpeg")


@router.put("/api/start/image")
async def start_page_set_image(request: Request) -> dict:
    """Corps de la requête = l'image, déjà réduite et réencodée en JPEG par la page."""
    data = await body_up_to(request, startpage.IMAGE_MAX_BYTES, "Image trop lourde : 3 Mo au plus.")
    try:
        return await run_in_threadpool(startpage.set_image, data)
    except startpage.StartPageError as err:
        raise _start_page_error(err) from err


@router.delete("/api/start/image")
def start_page_remove_image() -> dict:
    return startpage.remove_image()


# --- Comment ça marche et Ce qui sort de cet ordinateur (D-131) ---------------------------------
# Lecture seule, sans réseau : où sont les données, les sauvegardes du profil ouvert, le choix
# des mises à jour (lu, jamais vérifié ici) et la liste des services joints avec leur compteur.


@router.get("/api/about")
def about() -> dict:
    try:
        copy_dir, copy_error = settings.copy_dir(), None
    except RuntimeError as err:
        copy_dir, copy_error = None, str(err)
    return {
        "version": __version__,
        "data_dir": str(profiles.DATA_DIR),
        "backups": {
            "folder": str(backup.backup_dir()),
            "keep": config.backup_keep(),
            "months": backup.MONTHS_KEPT,
            "last": backup.last_auto_day(db.db_path().stem),
            "copy_dir": str(copy_dir) if copy_dir else None,
            "copy_error": copy_error,
        },
        "updates": updates.load().enabled,
        "outbound": outbound.summary(),
    }


# --- Diagnostic à copier dans un signalement (D-132) --------------------------------------------
# Lecture seule, sans réseau, sans montant ni nom ni chemin. Répond aussi sans profil ouvert
# (écran de choix, profil qui ne s'ouvre plus) : la base n'est alors pas lue.


@router.get("/api/diagnostic")
def diagnostic_report(request: Request) -> dict:
    opened = not profiles.enabled() or getattr(request.state, "profile", None) is not None
    path = db.db_path() if opened else None
    text = diagnostic.report(path, unready=len(deps.unready_profiles))
    return {"text": text, "report_url": diagnostic.REPORT_URL}


@router.post("/api/window/focus")
def window_focus() -> dict:
    """Second lancement de l'app (D-125) : ramène devant la fenêtre déjà ouverte. Ne lit ni
    n'écrit aucune donnée ; « focused » faux : l'app tourne dans le navigateur."""
    from app import window

    return {"focused": window.bring_to_front()}


# Ce que faisait la barre de menu de la fenêtre, retirée (D-127). Actions fixes : rien ne vient
# de la requête (ni adresse, ni chemin) ; refusées par check_origin si un autre site les envoie.


@router.get("/api/window")
def window_info() -> dict:
    """La page s'affiche-t-elle dans la fenêtre de l'app, et peut-elle remettre le zoom à 100 %."""
    from app import window

    return window.info()


@router.post("/api/window/browser")
def window_browser() -> dict:
    from app import window

    return {"done": window.open_app_in_browser()}


@router.post("/api/window/folder")
def window_folder() -> dict:
    from app import window

    return {"done": window.open_data_folder()}


@router.post("/api/window/zoom")
def window_zoom() -> dict:
    from app import window

    return {"done": window.reset_current_zoom()}
