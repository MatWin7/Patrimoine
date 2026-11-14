"""Fenêtre d'application (D-125) : choix de l'affichage, réglages de pywebview, taille gardée,
repli sur le navigateur, second lancement qui ramène la fenêtre devant.

pywebview n'est installé que sous Windows et macOS : un faux module le remplace ici, ce qui
permet de vérifier les options passées sans ouvrir de fenêtre."""

import json
import sys
import threading
import types

import pytest
from fastapi.testclient import TestClient

from app import launcher, window
from app.main import app


class FausseFenetre:
    def __init__(self, title, url, **options):
        self.title, self.url, self.options = title, url, options
        self.width, self.height = 1000, 700
        self.events = types.SimpleNamespace(
            **{
                nom: Evenement()
                for nom in (
                    "initialized",
                    "shown",
                    "loaded",
                    "closing",
                    "maximized",
                    "minimized",
                    "restored",
                )
            }
        )
        self.gestes = []

    def restore(self):
        self.gestes.append("restore")

    def maximize(self):
        self.gestes.append("maximize")

    def show(self):
        self.gestes.append("show")

    def load_url(self, url):
        self.gestes.append(("load_url", url))

    @property
    def on_top(self):
        return False

    @on_top.setter
    def on_top(self, valeur):
        self.gestes.append(("on_top", valeur))


class Evenement:
    def __init__(self):
        self.fonctions = []

    def __iadd__(self, fonction):
        self.fonctions.append(fonction)
        return self

    def set(self, *args):
        return [f(*args) for f in self.fonctions]


@pytest.fixture
def faux_webview(monkeypatch):
    """Module « webview » factice. `moteur` : moteur annoncé ; `pendant` : ce que fait
    l'utilisateur une fois la fenêtre ouverte (rien par défaut, puis fermeture)."""
    module = types.ModuleType("webview")
    module.settings = {"ALLOW_DOWNLOADS": False, "OPEN_EXTERNAL_LINKS_IN_BROWSER": False}
    module.fenetres = []
    module.demarrage = {}
    module.moteur = "edgechromium"
    module.pendant = lambda fenetre: None

    def create_window(title, url, **options):
        fenetre = FausseFenetre(title, url, **options)
        module.fenetres.append(fenetre)
        return fenetre

    def start(**options):
        module.demarrage.update(options)
        fenetre = module.fenetres[-1]
        if False in fenetre.events.initialized.set(module.moteur):
            return  # fenêtre refusée : pywebview rend la main sans rien ouvrir
        fenetre.events.shown.set()
        fenetre.events.loaded.set()
        module.pendant(fenetre)
        fenetre.events.closing.set()

    module.create_window = create_window
    module.start = start
    monkeypatch.setitem(sys.modules, "webview", module)
    return module


# --- Choix de l'affichage ---------------------------------------------------------------------


def test_pas_de_fenetre_sous_linux_ni_wsl(faux_webview):
    assert window.unavailable_reason("linux") is not None
    assert "WSL" in window.unavailable_reason("linux", wsl=True)
    assert window.unavailable_reason("win32", wsl=True) is not None


@pytest.mark.parametrize("plateforme", ["win32", "darwin"])
def test_fenetre_sous_windows_et_macos(faux_webview, plateforme):
    assert window.unavailable_reason(plateforme) is None


def test_pywebview_absent_signale(monkeypatch):
    monkeypatch.setitem(sys.modules, "webview", None)  # import impossible
    assert "pywebview" in window.unavailable_reason("win32")


def test_fenetre_impossible_donne_le_navigateur(monkeypatch, capsys):
    monkeypatch.setattr(launcher, "is_wsl", lambda: False)
    monkeypatch.setattr(window, "unavailable_reason", lambda wsl: "moteur absent")
    monkeypatch.setattr(sys, "platform", "win32")
    assert launcher.choose_display(launcher.WINDOW) == launcher.BROWSER
    assert "moteur absent" in capsys.readouterr().out
    assert launcher.choose_display(launcher.NOTHING) == launcher.NOTHING


def test_dossier_de_la_fenetre(tmp_path):
    assert window.storage_dir(tmp_path, None) == tmp_path / "fenetre"
    demo = tmp_path / "demo.db"
    assert window.storage_dir(tmp_path, str(demo)) == tmp_path / "demo.db.fenetre"


# --- Taille gardée ----------------------------------------------------------------------------


def test_taille_par_defaut_puis_gardee(tmp_path):
    assert window.load_size(tmp_path) == (1280, 860, False)
    window.save_size(tmp_path / "fenetre", 1100, 750, True)
    assert window.load_size(tmp_path / "fenetre") == (1100, 750, True)


@pytest.mark.parametrize(
    "contenu",
    ["pas du json", "[]", '{"width": "large"}', '{"width": 50, "height": 50}', '{"width": 1e9}'],
)
def test_taille_abimee_ignoree(tmp_path, contenu):
    (tmp_path / window.STATE_NAME).write_text(contenu, encoding="utf-8")
    largeur, hauteur, _ = window.load_size(tmp_path)
    assert (largeur, hauteur) == window.DEFAULT_SIZE


# --- Ouverture --------------------------------------------------------------------------------


def test_reglages_de_la_fenetre(faux_webview, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", raising=False)
    stockage = tmp_path / "fenetre"
    assert window.run("http://localhost:8000", stockage, tmp_path, lambda url: True)
    fenetre = faux_webview.fenetres[0]
    assert fenetre.url == "http://localhost:8000/?fenetre"  # la page sait qu'elle est la fenêtre
    assert fenetre.options["text_select"] and fenetre.options["zoomable"]
    assert "js_api" not in fenetre.options  # aucun pont Python exposé à la page
    # Préférences et session gardées dans le dossier des données, pas dans un dossier temporaire.
    assert faux_webview.demarrage["private_mode"] is False
    assert faux_webview.demarrage["storage_path"] == str(stockage)
    assert faux_webview.settings["ALLOW_DOWNLOADS"] is True
    assert faux_webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] is True
    # SmartScreen coupé : ni adresse ni fichier exporté envoyé à Microsoft (règle 3).
    import os

    arguments = os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"]
    # Un seul --disable-features (Chromium ne garde que le dernier), celui de pywebview repris.
    assert arguments.count("--disable-features=") == 1
    assert "msSmartScreenProtection" in arguments and "ElasticOverscroll" in arguments
    # Taille de la fenêtre au moment de la fermeture, reprise au lancement suivant.
    assert json.loads((stockage / "taille.json").read_text(encoding="utf-8")) == {
        "width": 1000,
        "height": 700,
        "maximized": False,
    }
    assert window._current is None  # fenêtre fermée : plus rien à ramener devant


def test_smartscreen_coupe_par_l_api_de_webview2(monkeypatch):
    """Sous Windows, IsReputationCheckingRequired passe à False sur le fil de l'interface."""
    reglages = types.SimpleNamespace(IsReputationCheckingRequired=True)
    formulaire = types.SimpleNamespace(
        webview=types.SimpleNamespace(CoreWebView2=types.SimpleNamespace(Settings=reglages)),
        Invoke=lambda action: action(),
    )
    system = types.ModuleType("System")
    system.Action = lambda fonction: fonction
    monkeypatch.setitem(sys.modules, "System", system)
    assert window.disable_reputation_checks(types.SimpleNamespace(native=formulaire))
    assert reglages.IsReputationCheckingRequired is False


def test_smartscreen_api_absente_sans_erreur(monkeypatch):
    monkeypatch.setitem(sys.modules, "System", None)  # pythonnet absent
    assert window.disable_reputation_checks(types.SimpleNamespace(native=None)) is False


def test_arguments_webview2_existants_gardes(faux_webview, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "--lang=fr")
    window.run("http://localhost:8000", tmp_path / "f", tmp_path, lambda url: True)
    import os

    valeur = os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"]
    assert valeur.startswith("--lang=fr ") and valeur.count("msSmartScreenProtection") == 1


def test_fenetre_agrandie_garde_la_taille_d_avant(faux_webview, tmp_path):
    stockage = tmp_path / "fenetre"
    window.save_size(stockage, 1111, 777, False)
    faux_webview.pendant = lambda fenetre: fenetre.events.maximized.set()
    window.run("http://localhost:8000", stockage, tmp_path, lambda url: True)
    assert window.load_size(stockage) == (1111, 777, True)


def test_internet_explorer_refuse(faux_webview, tmp_path):
    """Windows sans WebView2 : pywebview prendrait MSHTML, incapable d'afficher l'app."""
    faux_webview.moteur = "mshtml"
    assert window.run("http://localhost:8000", tmp_path / "f", tmp_path, lambda url: True) is False


def test_sans_barre_de_menu(faux_webview, tmp_path):
    """Barre de menu WinForms blanche en thème sombre : retirée, ses actions sont dans la page
    (D-127)."""
    window.run("http://localhost:8000", tmp_path / "f", tmp_path, lambda url: True)
    assert "menu" not in faux_webview.demarrage


def test_actions_de_la_page(faux_webview, tmp_path, monkeypatch):
    """Bouton « Fenêtre » : navigateur et dossier des données, par l'API, fenêtre ouverte."""
    ouvertes, dossiers = [], []
    monkeypatch.setattr(window, "open_folder", dossiers.append)
    with TestClient(app) as client:

        def pendant(fenetre):
            assert client.get("/api/window").json()["window"] is True
            assert client.post("/api/window/browser").json() == {"done": True}
            assert client.post("/api/window/folder").json() == {"done": True}

        faux_webview.pendant = pendant
        ouvrir = lambda url: ouvertes.append(url) or True  # noqa: E731
        window.run("http://localhost:8000", tmp_path / "f", tmp_path, ouvrir)
        assert ouvertes == ["http://localhost:8000"]  # sans « ?fenetre »
        assert dossiers == [tmp_path]
        # Fenêtre fermée, ou app dans le navigateur : rien n'est fait.
        assert client.get("/api/window").json() == {"window": False, "zoom": False}
        for action in ("browser", "folder", "zoom"):
            assert client.post(f"/api/window/{action}").json() == {"done": False}


def test_actions_refusees_depuis_un_autre_site(tmp_path):
    with TestClient(app) as client:
        for action in ("browser", "folder", "zoom", "focus"):
            refus = client.post(f"/api/window/{action}", headers={"Origin": "https://exemple.test"})
            assert refus.status_code == 403
            refus = client.post(f"/api/window/{action}", headers={"Sec-Fetch-Site": "cross-site"})
            assert refus.status_code == 403


def test_bouton_fenetre_sans_profil_ouvert(tmp_path):
    """Écran de choix du profil : le bouton « Fenêtre » marche, comme la barre de menu."""
    from app import profiles

    profiles.create("Alice fictive")
    with TestClient(app) as client:
        assert client.get("/api/window").status_code == 200
        for action in ("browser", "folder", "zoom"):
            assert client.post(f"/api/window/{action}").status_code == 200
        assert client.get("/api/accounts").status_code == 401


# --- Second lancement -------------------------------------------------------------------------


def test_ramener_la_fenetre_devant(faux_webview, tmp_path, db_file):
    with TestClient(app) as client:
        assert client.post("/api/window/focus").json() == {"focused": False}  # navigateur

        def pendant(fenetre):
            assert client.post("/api/window/focus").json() == {"focused": True}
            assert fenetre.gestes[0] == "show"  # ouverte : ni réduite ni redimensionnée

        faux_webview.pendant = pendant
        window.run("http://localhost:8000", tmp_path / "f", tmp_path, lambda url: True)


def _ramenee_apres(faux_webview, tmp_path, gestes_utilisateur, maximized=False):
    """Gestes de l'utilisateur sur la fenêtre, puis second lancement : ce que fait l'app."""
    vu = []
    if maximized:
        window.save_size(tmp_path / "f", 1200, 800, True)

    def pendant(fenetre):
        for evenement in gestes_utilisateur:
            getattr(fenetre.events, evenement).set()
        assert window.bring_to_front()
        vu.extend(g for g in fenetre.gestes if not isinstance(g, tuple))

    faux_webview.pendant = pendant
    window.run("http://localhost:8000", tmp_path / "f", tmp_path, lambda url: True)
    return vu


def test_agrandie_reste_en_plein_ecran(faux_webview, tmp_path):
    """Bug vu sous Windows : restore() remettait la taille normale."""
    assert _ramenee_apres(faux_webview, tmp_path, ["maximized"]) == ["show"]
    assert _ramenee_apres(faux_webview, tmp_path, [], maximized=True) == ["show"]


def test_agrandie_puis_reduite_revient_agrandie(faux_webview, tmp_path):
    vu = _ramenee_apres(faux_webview, tmp_path, ["maximized", "minimized"])
    assert vu == ["maximize", "show"]
    vu = _ramenee_apres(faux_webview, tmp_path, ["minimized"], maximized=True)
    assert vu == ["maximize", "show"]


def test_reduite_revient_a_sa_taille(faux_webview, tmp_path):
    assert _ramenee_apres(faux_webview, tmp_path, ["minimized"]) == ["restore", "show"]
    vu = _ramenee_apres(faux_webview, tmp_path, ["maximized", "restored", "minimized"])
    assert vu == ["restore", "show"]


def test_ramener_devant_sans_profil_ouvert(tmp_path, monkeypatch):
    """Profils activés et aucun ouvert : le second lancement peut quand même ramener la
    fenêtre (sur l'écran de choix du profil), sans rien lire des bases."""
    from app import profiles

    profiles.create("Alice fictive")
    assert profiles.enabled()
    with TestClient(app) as client:
        assert client.post("/api/window/focus").status_code == 200
        assert client.get("/api/accounts").status_code == 401  # le reste demande un profil


def test_second_lancement_ramene_la_fenetre(tmp_path, monkeypatch):
    monkeypatch.setenv("PATRIMOINE_DB", str(tmp_path / "test.db"))
    premier = launcher.InstanceLock(launcher.lock_path(tmp_path, str(tmp_path / "test.db")))
    assert premier.acquire()
    premier.write_port(8123)
    monkeypatch.setattr(launcher, "wait_for_instance", lambda lock: 8123)
    monkeypatch.setattr(launcher, "focus_instance", lambda port: port == 8123)
    monkeypatch.setattr(launcher, "open_browser", lambda url: pytest.fail("navigateur ouvert"))
    try:
        assert launcher.run(display=launcher.WINDOW) == 0
    finally:
        premier.release()


def test_instance_sans_fenetre_non_ramenee():
    """Le port ne répond pas (ou pas en Patrimoine) : rien n'est ramené."""
    import socket

    libre = socket.socket()
    libre.bind(("127.0.0.1", 0))
    port = libre.getsockname()[1]
    libre.close()
    assert launcher.focus_instance(port, timeout_ms=200) is False


# --- Lancement complet en mode fenêtre --------------------------------------------------------


def _lancer_avec_fenetre(tmp_path, monkeypatch, fenetre_ouverte):
    """Vrai serveur sur 127.0.0.1 ; `fenetre_ouverte(url)` remplace la fenêtre."""
    from app import backup

    monkeypatch.setenv("PATRIMOINE_DB", str(tmp_path / "test.db"))
    monkeypatch.setattr(backup, "daily", lambda paths: [])
    monkeypatch.setattr(launcher, "choose_display", lambda wanted: launcher.WINDOW)
    monkeypatch.setattr(
        window, "run", lambda url, storage, data_dir, open_in_browser: fenetre_ouverte(url)
    )
    return launcher.run(display=launcher.WINDOW)


def test_fermer_la_fenetre_arrete_l_app(tmp_path, monkeypatch):
    vu = []

    def fenetre(url):
        port = int(url.rsplit(":", 1)[1])
        vu.append(launcher.is_patrimoine(port))  # le serveur répond pendant que la fenêtre vit
        return True  # puis l'utilisateur la ferme

    assert _lancer_avec_fenetre(tmp_path, monkeypatch, fenetre) == 0
    assert vu == [True]
    verrou = launcher.InstanceLock(launcher.lock_path(tmp_path, str(tmp_path / "test.db")))
    assert not verrou.port_file.exists()
    assert verrou.acquire()  # verrou relâché : l'app est bien arrêtée
    verrou.release()
    assert not any(t.name == "serveur" and t.is_alive() for t in threading.enumerate())


def test_fenetre_ratee_ouvre_le_navigateur(tmp_path, monkeypatch):
    import uvicorn

    serveurs = []

    class Serveur(uvicorn.Server):
        def __init__(self, config):
            super().__init__(config)
            serveurs.append(self)

    monkeypatch.setattr(uvicorn, "Server", Serveur)
    ouvertes = []

    def navigateur(url):
        ouvertes.append(url)
        serveurs[0].should_exit = True  # l'utilisateur fait Ctrl+C plus tard
        return True

    monkeypatch.setattr(launcher, "open_browser", navigateur)

    def fenetre(url):
        raise RuntimeError("WebView2 absent")

    assert _lancer_avec_fenetre(tmp_path, monkeypatch, fenetre) == 0
    assert len(ouvertes) == 1 and ouvertes[0].startswith("http://localhost:")


# --- Zoom affiché dans le titre ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("facteur", "titre"),
    [
        (1.0, "Patrimoine"),
        (1.1, "Patrimoine · zoom 110 %"),
        (0.8999999, "Patrimoine · zoom 90 %"),
        ("abîmé", "Patrimoine"),
    ],
)
def test_titre_selon_le_zoom(facteur, titre):
    assert window.zoom_title(facteur) == titre


def test_zoom_suivi_dans_le_titre(monkeypatch):
    """WebView2 annonce chaque changement de zoom : le titre de la fenêtre le reprend."""
    abonnes = []
    vue = types.SimpleNamespace(ZoomFactor=1.25)

    class Evenement:
        def __iadd__(self, fonction):
            abonnes.append(fonction)
            return self

    vue.ZoomFactorChanged = Evenement()
    titres = []
    fenetre = types.SimpleNamespace(
        native=types.SimpleNamespace(webview=vue, Invoke=lambda action: action()),
        set_title=titres.append,
    )
    system = types.ModuleType("System")
    system.Action = system.EventHandler = lambda fonction: fonction
    monkeypatch.setitem(sys.modules, "System", system)
    assert window.show_zoom_in_title(fenetre)
    abonnes[0](vue, None)
    assert titres == ["Patrimoine · zoom 125 %"]


def test_zoom_sans_pythonnet_sans_erreur(monkeypatch):
    monkeypatch.setitem(sys.modules, "System", None)
    assert window.show_zoom_in_title(types.SimpleNamespace(native=None)) is False


def test_zoom_remis_a_100_par_la_page(faux_webview, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    remis = []
    monkeypatch.setattr(window, "reset_zoom", lambda f: remis.append(f) or True)
    with TestClient(app) as client:

        def pendant(fenetre):
            assert client.get("/api/window").json() == {"window": True, "zoom": True}
            assert client.post("/api/window/zoom").json() == {"done": True}

        faux_webview.pendant = pendant
        window.run("http://localhost:8000", tmp_path / "f", tmp_path, lambda url: True)
    assert remis == [faux_webview.fenetres[0]]


def test_pas_de_zoom_hors_windows(faux_webview, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    remis = []
    monkeypatch.setattr(window, "reset_zoom", remis.append)
    with TestClient(app) as client:

        def pendant(fenetre):
            assert client.get("/api/window").json() == {"window": True, "zoom": False}
            assert client.post("/api/window/zoom").json() == {"done": False}

        faux_webview.pendant = pendant
        window.run("http://localhost:8000", tmp_path / "f", tmp_path, lambda url: True)
    assert remis == []


def test_remise_a_100_par_l_api_de_webview2(monkeypatch):
    """Le titre suit sans attendre l'événement du moteur (bug vu sous Windows : zoom remis à
    100 % par le menu, titre resté à « zoom 110 % »)."""
    vue = types.SimpleNamespace(ZoomFactor=1.5)
    titres = []
    fenetre = types.SimpleNamespace(
        native=types.SimpleNamespace(webview=vue, Invoke=lambda action: action()),
        set_title=titres.append,
    )
    system = types.ModuleType("System")
    system.Action = lambda fonction: fonction
    system.Double = types.SimpleNamespace(Parse=lambda texte, culture: int(texte))
    globalisation = types.ModuleType("System.Globalization")
    globalisation.CultureInfo = types.SimpleNamespace(InvariantCulture=None)
    monkeypatch.setitem(sys.modules, "System", system)
    monkeypatch.setitem(sys.modules, "System.Globalization", globalisation)
    assert window.reset_zoom(fenetre)
    assert vue.ZoomFactor == 1
    assert titres == ["Patrimoine"]


def test_remise_a_100_sans_pythonnet_signalee(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "System", None)
    assert window.reset_zoom(types.SimpleNamespace(native=None)) is False
    assert "Zoom à 100 % impossible" in capsys.readouterr().out
