"""Application FastAPI : API JSON sous /api, page web statique sous /."""

import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from app import db, diagnostic, profiles
from app.api import (
    aave,
    accounts,
    benchmarks,
    imports,
    machine,
    operations,
    performance,
    projection,
    quotes,
    targets,
    watchlists,
)
from app.api import crypto as crypto_routes
from app.api import debts as debts_routes
from app.api import income as income_routes
from app.api import portfolio as portfolio_routes
from app.api import profiles as profiles_routes
from app.api import recurring as recurring_routes
from app.api import tax as tax_routes
from app.api.deps import SESSION_COOKIE, unready_profiles

# Les tests remplacent ce client par `app.dependency_overrides[http_client]` : le nom reste
# importable d'ici.
from app.api.deps import http_client as http_client

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    unready_profiles.clear()
    if profiles.enabled():
        for profile in profiles.load():
            try:
                db.init_db(profiles.db_file(profile))
            except RuntimeError as err:
                unready_profiles[profile.id] = str(err)
    else:
        db.init_db()  # crée la base au premier lancement
    yield


# Pas de /docs ni /redoc : ces pages chargent leur JS et leur CSS depuis un CDN (D-028, D-039).
# Télémétrie native de FastAPI (OpenTelemetry) coupée d'office, même si un fournisseur ou les
# variables OTEL_* / FASTAPI_OTEL_AUTO_CONFIGURE apparaissaient sur la machine (règle 2, D-122).
NO_TELEMETRY = {"tracing": False, "metrics": False, "logs": False, "auto_configure": False}
app = FastAPI(
    title="Patrimoine",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    telemetry=NO_TELEMETRY,
)

# Profils (D-098) : chaque requête de l'API porte un cookie de session, jeton aléatoire remis
# à l'ouverture d'un profil, qui désigne sa base. Sans profil valide, seules les routes qui
# servent à en choisir un répondent. Ce middleware est déclaré en premier : il passe après les
# contrôles du nom de site et de l'origine.
OPEN_PROFILE = re.compile(r"/api/profiles/[^/]+/open")


WINDOW_ACTIONS = (
    "/api/window/focus",
    "/api/window/browser",
    "/api/window/folder",
    "/api/window/zoom",
)


def _without_profile(request: Request) -> bool:
    """Routes qui servent à choisir un profil, et celles de la fenêtre (D-125, D-127) : elles ne
    lisent aucune base, et le bouton « Fenêtre » marche aussi sur l'écran de choix du profil,
    comme la barre de menu qu'il remplace. Le diagnostic (D-132) aussi : un profil qui ne
    s'ouvre plus doit pouvoir être signalé ; sans profil ouvert, il ne lit aucune base. La page
    de démarrage (D-134) lit son fond et son mot d'accueil avant le choix du profil."""
    path, method = request.url.path, request.method
    if path in ("/api/health", "/api/profiles", "/api/window", "/api/diagnostic"):
        return method == "GET"
    if path in ("/api/start", "/api/start/image"):  # page de démarrage : lecture seule (D-134)
        return method == "GET"
    return method == "POST" and (
        path == "/api/profiles/lock" or path in WINDOW_ACTIONS or bool(OPEN_PROFILE.fullmatch(path))
    )


@app.middleware("http")
async def profile_scope(request: Request, call_next):
    if not request.url.path.startswith("/api/") or not profiles.enabled():
        return await call_next(request)
    profile = profiles.session_profile(request.cookies.get(SESSION_COOKIE))
    if profile is None:
        if _without_profile(request):
            return await call_next(request)
        return PlainTextResponse("Choisir un profil", 401, headers={"X-Profile": "required"})
    # Les onglets d'un même navigateur partagent le cookie : un onglet resté sur Marie alors
    # qu'un autre a ouvert Paul annonce « Marie » dans X-Profile-Id et reçoit 409 au lieu
    # d'écrire chez Paul. En-tête absent (anciennes pages) : accepté.
    expected = request.headers.get("x-profile-id")
    if expected is not None and expected != profile.id and not _without_profile(request):
        return PlainTextResponse(
            "Profil changé dans un autre onglet : recharger la page",
            409,
            headers={"X-Profile": "changed"},
        )
    if profile.id in unready_profiles and not _without_profile(request):
        return PlainTextResponse(unready_profiles[profile.id], 503)
    request.state.profile = profile
    token = db.current_profile_db.set(profiles.db_file(profile))
    try:
        return await call_next(request)
    finally:
        db.current_profile_db.reset(token)


# Noms de site acceptés dans l'en-tête Host. Tout autre nom est refusé : une page web piégée
# qui ferait pointer son propre domaine vers 127.0.0.1 (« DNS rebinding ») ne peut donc ni
# lire les montants ni modifier la base (D-039). Les tests ajoutent « testserver ».
ALLOWED_HOSTS = {"localhost", "127.0.0.1"}


@app.middleware("http")
async def check_host(request: Request, call_next):
    host = request.headers.get("host", "").rsplit(":", 1)[0].lower()
    if host not in ALLOWED_HOSTS:
        return PlainTextResponse("Nom de site refusé : ouvrir l'app par http://localhost", 400)
    return await call_next(request)


# Un site ouvert dans un autre onglet peut envoyer des requêtes vers localhost avec le bon
# nom de site : le contrôle du Host ne suffit pas. Le navigateur indique d'où vient une
# requête (Sec-Fetch-Site, Origin) : l'API ne répond qu'à la page de l'app elle-même (D-080).
SAME_SITE_VALUES = {"same-origin", "none"}  # « none » : adresse tapée, favori
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@app.middleware("http")
async def check_origin(request: Request, call_next):
    if request.url.path.startswith("/api/"):
        fetch_site = request.headers.get("sec-fetch-site")
        origin = request.headers.get("origin")
        if fetch_site is not None and fetch_site not in SAME_SITE_VALUES:
            return PlainTextResponse("Requête venue d'un autre site : refusée", 403)
        if request.method in UNSAFE_METHODS and origin is not None:
            if origin != f"http://{request.headers.get('host', '')}":
                return PlainTextResponse("Requête venue d'un autre site : refusée", 403)
    return await call_next(request)


# En-têtes de sécurité de chaque réponse (D-080). La politique de contenu (CSP) n'autorise que
# les fichiers de l'app : même un script copié dans web/vendor/ ne pourrait envoyer les montants
# ailleurs (connect-src 'self'), et aucun autre site ne peut afficher l'app dans un cadre
# invisible pour faire cliquer « Supprimer » à l'insu de l'utilisateur (frame-ancestors).
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
        "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; "
        "form-action 'self'; frame-ancestors 'none'"
    ),
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Cross-Origin-Opener-Policy": "same-origin",
}


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.update(SECURITY_HEADERS)
    return response


@app.middleware("http")
async def revalidate_web_files(request: Request, call_next):
    """Page, JS et CSS : le navigateur redemande à chaque chargement s'ils ont changé (réponse
    304 très légère sinon). Sans cela, il peut garder un ancien app.js après une mise à jour.
    API : rien n'est gardé en cache."""
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache"
    elif "cache-control" not in response.headers:
        # Réponses de l'API (montants) : jamais écrites dans le cache disque du navigateur ou du
        # moteur de la fenêtre (D-125), qui survivrait à la fermeture et au chiffrement à venir.
        response.headers["Cache-Control"] = "no-store"
    return response


# Erreur inattendue (D-132) : retenue en mémoire pour le diagnostic (type et lignes de l'app,
# jamais son message ni l'adresse demandée), et un message qui dit quoi faire. L'erreur reste
# écrite dans la console ou le journal local, comme avant.
@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    route = getattr(request.scope.get("route"), "path", None) or "(route inconnue)"
    diagnostic.record(exc, f"{request.method} {route}")
    return JSONResponse(
        {"detail": "Erreur inattendue. Pour la signaler : Réglages, « Copier le diagnostic »."},
        status_code=500,
    )


# Toutes les routes sont dans app/api/ (D-139), une famille par fichier. Elles sont branchées
# dans l'ordre où elles étaient déclarées ici : quand deux chemins se ressemblent, la première
# route branchée répond.
for routes in (
    machine,
    profiles_routes,
    accounts,
    operations,
    recurring_routes,
    income_routes,
    crypto_routes,
    debts_routes,
    aave,
    targets,
    tax_routes,
    imports,
    quotes,
    portfolio_routes,
    performance,
    benchmarks,
    watchlists,
    projection,
):
    app.include_router(routes.router)


# Monté en dernier : les routes /api/... déclarées au-dessus restent prioritaires.
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
