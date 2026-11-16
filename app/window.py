"""Fenêtre d'application (D-125) : Patrimoine s'ouvre dans sa propre fenêtre, pas dans un onglet.

pywebview affiche la page servie sur 127.0.0.1 avec le moteur web déjà présent dans le système
(WebView2 sous Windows, WebKit sous macOS) : rien n'est réécrit, le navigateur reste l'outil
de conception et le secours. Sous Linux et WSL, pas de fenêtre : le lanceur ouvre le navigateur.

Ce que la fenêtre garde d'un lancement à l'autre (dossier STORAGE_NAME) : taille de la fenêtre
et, sous Windows, données du moteur (préférences de la page, cookie de session du profil).
Sous macOS, pywebview range ces dernières dans le magasin WebKit par défaut de l'utilisateur
(~/Library/WebKit), hors du dossier des données. Les réponses de l'API ne sont jamais mises en
cache (Cache-Control: no-store).
Aucune extension de navigateur ne peut lire la page et aucun pont Python n'est exposé au
JavaScript (pas de js_api). Pas de barre de menu non plus (blanche en thème sombre, D-127) :
ses actions sont dans la page, bouton « Fenêtre » de la barre du haut, et passent par les
routes /api/window/... (actions fixes, sans paramètre). La page de la fenêtre est ouverte avec
« ?fenetre » : le navigateur, qui n'a pas ce marqueur, n'affiche pas ce bouton.
Un lien qui ouvre un nouvel onglet (target=_blank) part dans le navigateur habituel ; un lien
ordinaire vers un autre site s'ouvrirait dans la fenêtre : le front n'en a aucun et ne doit pas
en avoir.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

STORAGE_NAME = "fenetre"  # dans le dossier des données ; jamais copié ailleurs (cache du moteur)
STATE_NAME = "taille.json"
TITLE = "Patrimoine"
WINDOW_MARK = "fenetre"  # paramètre de l'adresse : la page sait qu'elle est dans la fenêtre
DEFAULT_SIZE = (1280, 860)
MIN_SIZE = (480, 560)  # assez étroit pour la mise en page mobile, assez haut pour la barre
MAX_SIDE = 10_000
# Moteurs refusés : MSHTML est Internet Explorer (Windows sans WebView2), incapable d'afficher
# l'app ; le lanceur ouvre alors le navigateur.
UNSUPPORTED_RENDERERS = {"mshtml"}
# SmartScreen enverrait à Microsoft les adresses et fichiers téléchargés (exports) : coupé,
# seules les requêtes de prix publics sortent de la machine (règle 3). La variable
# d'environnement remplace les arguments posés par pywebview et Chromium ne garde qu'un
# --disable-features : celui de pywebview (ElasticOverscroll) est donc repris ici.
WEBVIEW2_ARGS = "--disable-features=msSmartScreenProtection,ElasticOverscroll"
# Textes de pywebview en français (D-137) : boutons des boîtes confirm(), titre de la boîte
# « Enregistrer », et sous macOS les menus de la barre du haut (Patrimoine, Présentation,
# Édition), que le système affiche toujours. Le menu Édition porte Cmd+C / Cmd+V.
LOCALIZATION = {
    "global.quitConfirmation": "Quitter Patrimoine ?",
    "global.ok": "OK",
    "global.quit": "Quitter",
    "global.cancel": "Annuler",
    "global.saveFile": "Enregistrer le fichier",
    "cocoa.menu.about": "À propos de",
    "cocoa.menu.services": "Services",
    "cocoa.menu.view": "Présentation",
    "cocoa.menu.edit": "Édition",
    "cocoa.menu.hide": "Masquer",
    "cocoa.menu.hideOthers": "Masquer les autres",
    "cocoa.menu.showAll": "Tout afficher",
    "cocoa.menu.quit": "Quitter",
    "cocoa.menu.fullscreen": "Passer en plein écran",
    "cocoa.menu.cut": "Couper",
    "cocoa.menu.copy": "Copier",
    "cocoa.menu.paste": "Coller",
    "cocoa.menu.selectAll": "Tout sélectionner",
    "windows.fileFilter.allFiles": "Tous les fichiers",
    "windows.fileFilter.otherFiles": "Autres types de fichiers",
}
_current: Any = None  # fenêtre ouverte par ce processus, ramenée devant par un second lancement
_size_state: dict[str, bool] = {}  # agrandie, réduite : suivis pour la ramener telle quelle
_actions: dict[str, Any] = {}  # adresse, dossier des données, ouverture du navigateur (D-127)
_quit_observers: list[Any] = []  # macOS : abonnements à la fin de l'app (gardés en vie)


def unblock_bundled_dlls(root: Path) -> int:
    """Patrimoine.exe dézippé depuis un téléchargement (D-126) : Windows marque chaque fichier
    « venu d'Internet » (flux Zone.Identifier) et .NET refuse alors de charger les DLL de la
    fenêtre (pythonnet, WebView2) : l'app retombait sur le navigateur (vu en CI). La marque est
    retirée des seules DLL du paquet de l'app, que l'utilisateur a déjà choisi d'exécuter
    (avertissement ou SmartScreen passés) ; rien d'autre n'est touché. Renvoie le nombre de
    marques retirées. Dossier en lecture seule : ignoré, la fenêtre tentera quand même."""
    removed = 0
    for dll in root.rglob("*.dll"):
        try:
            os.remove(f"{dll}:Zone.Identifier")
        except OSError:  # pas de marque (cas courant), ou pas le droit
            continue
        removed += 1
    return removed


def unavailable_reason(platform: str | None = None, wsl: bool = False) -> str | None:
    """Pourquoi la fenêtre ne peut pas s'ouvrir ici (None : elle le peut)."""
    platform = platform or sys.platform
    if wsl:
        return "WSL : la fenêtre demande Windows, le navigateur de Windows est ouvert à la place"
    if platform not in ("win32", "darwin"):
        return "fenêtre prévue pour Windows et macOS : ouverture dans le navigateur"
    try:
        import webview  # noqa: F401
    except ImportError:
        return "module pywebview absent (lancer « uv sync ») : ouverture dans le navigateur"
    return None


def storage_dir(data_dir: Path, forced_db: str | None) -> Path:
    """Dossier propre à la fenêtre. Une base forcée (démo) a le sien, à côté d'elle : deux
    instances ouvertes en même temps ne se partagent jamais les réglages de la page."""
    if forced_db:
        db = Path(forced_db)
        return db.with_name(db.name + "." + STORAGE_NAME)
    return data_dir / STORAGE_NAME


# --- Taille gardée d'un lancement à l'autre ---------------------------------------------------


def load_size(folder: Path) -> tuple[int, int, bool]:
    """(largeur, hauteur, agrandie) du dernier lancement ; la taille par défaut sinon."""
    try:
        saved = json.loads((folder / STATE_NAME).read_text(encoding="utf-8"))
        width, height = int(saved["width"]), int(saved["height"])
        maximized = saved.get("maximized") is True
    except (OSError, ValueError, KeyError, TypeError):
        return (*DEFAULT_SIZE, False)
    if not (MIN_SIZE[0] <= width <= MAX_SIDE and MIN_SIZE[1] <= height <= MAX_SIDE):
        return (*DEFAULT_SIZE, maximized)
    return width, height, maximized


def save_size(folder: Path, width: int, height: int, maximized: bool) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    data = {"width": width, "height": height, "maximized": maximized}
    (folder / STATE_NAME).write_text(json.dumps(data), encoding="utf-8")


# --- Ouverture --------------------------------------------------------------------------------


def open_folder(path: Path) -> None:
    """Montre le dossier des données dans l'explorateur de fichiers (bouton « Fenêtre »)."""
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 : dossier local, choisi par l'app
    elif sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)


def run(
    url: str,
    storage: Path,
    data_dir: Path,
    open_in_browser: Callable[[str], bool],
    gui: str | None = None,
    on_quit: Callable[[], None] | None = None,
) -> bool:
    """Ouvre la fenêtre et rend la main quand elle est fermée. À appeler depuis le fil
    principal (exigence de macOS). False : aucune fenêtre n'a pu s'ouvrir (moteur absent ou
    refusé), au lanceur d'ouvrir le navigateur. `on_quit` : rangement final, appelé sous
    macOS quand l'app est quittée sans rendre la main (voir watch_app_quit)."""
    if sys.platform == "win32" and hasattr(sys, "_MEIPASS"):  # Patrimoine.exe (PyInstaller)
        if count := unblock_bundled_dlls(Path(sys._MEIPASS)):
            print(f"Marque « venu d'Internet » retirée de {count} DLL de l'app.")
    import webview

    storage.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        extra = os.environ.get("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "")
        if WEBVIEW2_ARGS not in extra:
            os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = f"{extra} {WEBVIEW2_ARGS}".strip()
    # Exports (CSV, base d'un profil) : boîte « Enregistrer sous » du système.
    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True

    width, height, maximized = load_size(storage)
    window = webview.create_window(
        TITLE,
        window_url(url),
        width=width,
        height=height,
        min_size=MIN_SIZE,
        maximized=maximized,
        text_select=True,  # copier un montant, un ISIN
        zoomable=True,  # Ctrl + molette, Ctrl + / Ctrl -
    )
    if window is None:
        return False
    global _current
    _current = window
    _actions.update(url=url, data_dir=data_dir, open_in_browser=open_in_browser)
    _size_state.clear()
    _size_state.update(maximized=maximized, minimized=False)
    state: dict[str, Any] = {"renderer": None, "maximized": maximized, "shown": False}

    def on_initialized(renderer: str) -> bool:
        state["renderer"] = renderer
        return renderer not in UNSUPPORTED_RENDERERS  # False : la fenêtre n'est pas créée

    def on_shown() -> None:
        state["shown"] = True

    def on_loaded() -> None:
        if sys.platform == "win32" and not state.get("reputation_off"):
            state["reputation_off"] = disable_reputation_checks(window)
        if sys.platform == "win32" and not state.get("zoom_shown"):
            state["zoom_shown"] = show_zoom_in_title(window)

    def on_closing() -> None:
        try:
            if not state["maximized"]:
                save_size(storage, window.width, window.height, False)
            else:
                old_w, old_h, _ = load_size(storage)
                save_size(storage, old_w, old_h, True)
        except (OSError, AttributeError, TypeError, ValueError):
            pass  # une taille non gardée ne doit jamais empêcher de fermer

    window.events.initialized += on_initialized
    window.events.shown += on_shown
    window.events.loaded += on_loaded
    window.events.closing += on_closing

    def on_maximized() -> None:
        state["maximized"] = True
        _size_state.update(maximized=True, minimized=False)

    def on_minimized() -> None:
        _size_state["minimized"] = True  # agrandie ou non : retenu pour la ramener pareille

    def on_restored() -> None:  # taille normale, après agrandie ou réduite
        state["maximized"] = False
        _size_state.update(maximized=False, minimized=False)

    window.events.maximized += on_maximized
    window.events.minimized += on_minimized
    window.events.restored += on_restored
    if sys.platform == "darwin" and on_quit is not None:
        watch_app_quit(on_quit)

    try:
        webview.start(
            gui=gui,
            private_mode=False,  # sinon préférences et session perdues à chaque fermeture
            storage_path=str(storage),
            localization=LOCALIZATION,
        )
    finally:
        _current = None
        _actions.clear()
        forget_app_quit()
    return state["shown"]


def watch_app_quit(callback: Callable[[], None]) -> bool:
    """macOS : Cmd+Q, « Quitter » du Dock ou la fermeture de session passent par
    NSApplication terminate:, qui finit le processus par exit() sans revenir dans Python : les
    `finally` du lanceur (arrêt du serveur, sauvegarde en cours menée à son terme, verrou)
    ne tourneraient pas. Le rangement est donc aussi branché sur l'avis « l'app va se
    terminer », envoyé juste avant, sur le fil principal. Fermer la fenêtre, lui, rend la main
    normalement. False si AppKit manque (hors macOS)."""
    try:
        import AppKit
        import Foundation
    except ImportError:
        return False

    def on_terminate(notification: Any) -> None:
        try:
            callback()
        except Exception as err:  # noqa: BLE001 : une exception ne doit pas remonter dans Cocoa
            print(f"Arrêt incomplet : {err}")

    center = Foundation.NSNotificationCenter.defaultCenter()
    _quit_observers.append(
        center.addObserverForName_object_queue_usingBlock_(
            AppKit.NSApplicationWillTerminateNotification, None, None, on_terminate
        )
    )
    return True


def forget_app_quit() -> None:
    if not _quit_observers:
        return
    import Foundation

    center = Foundation.NSNotificationCenter.defaultCenter()
    while _quit_observers:
        center.removeObserver_(_quit_observers.pop())


def window_url(url: str) -> str:
    """Adresse ouverte dans la fenêtre : celle de l'app avec le marqueur « ?fenetre »."""
    return f"{url.rstrip('/')}/?{WINDOW_MARK}"


# --- Actions de la page (D-127) ---------------------------------------------------------------
# Ce que faisait la barre de menu, demandé par la page par POST /api/window/... : chaque action
# est fixe (aucune adresse ni aucun chemin ne vient de la requête) et ne lit aucune donnée.


def info() -> dict[str, bool]:
    """Ce que la page peut proposer : rien dans le navigateur, le zoom sous Windows seulement
    (Ctrl + 0 ne le remet pas à 100 % dans WebView2, raccourcis du navigateur coupés)."""
    opened = _current is not None
    return {"window": opened, "zoom": opened and sys.platform == "win32"}


def open_app_in_browser() -> bool:
    """Ouvre l'app dans le navigateur habituel (sans le marqueur de la fenêtre)."""
    actions = dict(_actions)  # copie : la fenêtre peut se fermer pendant la requête
    if _current is None or "url" not in actions:
        return False
    return bool(actions["open_in_browser"](actions["url"]))


def open_data_folder() -> bool:
    folder = _actions.get("data_dir")
    if _current is None or folder is None:
        return False
    open_folder(folder)
    return True


def reset_current_zoom() -> bool:
    window = _current
    if window is None or sys.platform != "win32":
        return False
    return reset_zoom(window)


def zoom_title(factor: Any) -> str:
    """Titre de la fenêtre : le zoom y apparaît dès qu'il n'est plus à 100 %, comme dans la
    barre d'adresse d'un navigateur. Calcul en entiers (pas de float)."""
    try:
        percent = int(round(Decimal(str(factor)) * 100))
    except (ArithmeticError, ValueError, TypeError):
        return TITLE
    return TITLE if percent == 100 else f"{TITLE} · zoom {percent} %"


def reset_zoom(window: Any) -> bool:
    """Remet le zoom de WebView2 à 100 % (bouton « Fenêtre »), sur le fil de l'interface, et
    met le titre à jour soi-même : on ne compte pas sur l'événement ZoomFactorChanged, que le
    moteur n'envoie pas toujours pour un changement fait par l'app (essai sous Windows)."""
    try:
        from System import Action, Double
        from System.Globalization import CultureInfo

        form = window.native
        # Double .NET construit depuis du texte : pythonnet ne convertit pas toujours un int
        # Python en double, et la règle 7 interdit le float.
        one = Double.Parse("1", CultureInfo.InvariantCulture)

        def reset() -> None:
            form.webview.ZoomFactor = one
            window.set_title(zoom_title(form.webview.ZoomFactor))

        form.Invoke(Action(reset))
        return True
    except Exception as err:  # noqa: BLE001 : un confort ne doit jamais fermer la fenêtre
        print(f"Zoom à 100 % impossible : {err}")
        return False


def show_zoom_in_title(window: Any) -> bool:
    """Sous Windows, suit le zoom de WebView2 (Ctrl + molette, Ctrl + / -) et l'écrit dans le
    titre. False si le moteur ne le permet pas : le zoom marche, seul le pourcentage manque."""
    try:
        from System import Action, EventHandler  # pythonnet, présent avec pywebview sous Windows

        form = window.native

        def on_change(sender: Any, args: Any) -> None:
            window.set_title(zoom_title(sender.ZoomFactor))

        def subscribe() -> None:
            form.webview.ZoomFactorChanged += EventHandler(on_change)

        form.Invoke(Action(subscribe))
        return True
    except Exception as err:  # noqa: BLE001 : un affichage de confort ne doit jamais fermer la fenêtre
        print(f"Zoom non affiché dans le titre : {err}")
        return False


def disable_reputation_checks(window: Any) -> bool:
    """SmartScreen coupé aussi par l'API de WebView2 (IsReputationCheckingRequired), en plus de
    l'argument de lancement : sur le fil de l'interface, seul autorisé à toucher le moteur.
    False si l'API manque (runtime ancien) : l'argument de lancement reste seul."""
    try:
        from System import Action  # pythonnet, présent avec pywebview sous Windows

        form = window.native

        def turn_off() -> None:
            form.webview.CoreWebView2.Settings.IsReputationCheckingRequired = False

        form.Invoke(Action(turn_off))
        return True
    except Exception:  # noqa: BLE001 : un réglage de confort ne doit jamais fermer la fenêtre
        return False


def bring_to_front() -> bool:
    """Second lancement : ramène devant la fenêtre déjà ouverte. False : pas de fenêtre ici
    (app ouverte dans le navigateur), au second lancement de rouvrir la page."""
    window = _current
    if window is None:
        return False
    # restore() remet la taille normale : seulement pour une fenêtre réduite, et une fenêtre
    # agrandie avant d'être réduite revient agrandie (plein écran gardé au second lancement).
    if _size_state.get("minimized"):
        if _size_state.get("maximized"):
            window.maximize()
        else:
            window.restore()
        _size_state["minimized"] = False
    window.show()
    window.on_top = True  # Windows refuse souvent le premier plan à une autre app : on insiste
    window.on_top = False
    return True
