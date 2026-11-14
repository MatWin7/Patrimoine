"""Lanceur multiplateforme (D-118) : verrou, port libre, instance déjà ouverte, navigateur."""

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app import __main__ as entree
from app import launcher


def test_second_verrou_refuse_puis_libre(tmp_path):
    premier = launcher.InstanceLock(tmp_path / "lancement.lock")
    second = launcher.InstanceLock(tmp_path / "lancement.lock")
    assert premier.acquire()
    try:
        assert not second.acquire()  # même dossier de données : refusé
    finally:
        premier.release()
    assert second.acquire()  # relâché : un nouveau lancement passe
    second.release()


def test_port_ecrit_puis_retire(tmp_path):
    verrou = launcher.InstanceLock(tmp_path / "lancement.lock")
    assert verrou.acquire()
    verrou.write_port(8003)
    assert verrou.read_port() == 8003
    verrou.release()
    assert not verrou.port_file.exists()


@pytest.mark.parametrize("contenu", ["", "pas du json", '{"port": "8000"}', '{"port": 70000}'])
def test_fichier_de_port_abime_ignore(tmp_path, contenu):
    verrou = launcher.InstanceLock(tmp_path / "lancement.lock")
    verrou.port_file.write_text(contenu, encoding="utf-8")
    assert verrou.read_port() is None


def test_verrou_propre_a_une_base_forcee(tmp_path):
    """La démo (PATRIMOINE_DB) peut tourner à côté de la vraie base : verrous distincts."""
    commun = launcher.lock_path(tmp_path, None)
    demo = launcher.lock_path(tmp_path, str(tmp_path / "demo.db"))
    assert commun == tmp_path / "lancement.lock"
    assert demo == tmp_path / "demo.db.lancement"


def test_ports_essayes_dans_l_ordre():
    assert launcher.candidate_ports(None)[:3] == [8000, 8001, 8002]
    ports = launcher.candidate_ports(8005)
    assert ports[0] == 8005 and ports.count(8005) == 1 and ports[1] == 8000


def test_port_occupe_saute():
    occupe = launcher.bind_socket(0)
    try:
        pris = occupe.getsockname()[1]
        sock = launcher.first_free_socket([pris, 0])
        assert sock.getsockname()[1] != pris
        sock.close()
    finally:
        occupe.close()


def test_aucun_port_libre():
    occupe = launcher.bind_socket(0)
    try:
        with pytest.raises(SystemExit):
            launcher.first_free_socket([occupe.getsockname()[1]])
    finally:
        occupe.close()


def serveur_local(reponse: dict):
    """Faux service sur 127.0.0.1 : répond `reponse` à /api/health."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # nom imposé par http.server
            body = json.dumps(reponse).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_reconnait_patrimoine_et_pas_un_autre_service():
    nous = serveur_local({"status": "ok", "app": "patrimoine"})
    autre = serveur_local({"status": "ok"})
    try:
        assert launcher.is_patrimoine(nous.server_port)
        assert not launcher.is_patrimoine(autre.server_port)
    finally:
        nous.shutdown()
        autre.shutdown()
    libre = socket.socket()
    libre.bind(("127.0.0.1", 0))
    port = libre.getsockname()[1]
    libre.close()
    assert not launcher.is_patrimoine(port, timeout_ms=200)  # personne n'écoute


def test_second_lancement_rouvre_l_instance(tmp_path, monkeypatch):
    """Données déjà ouvertes : pas de second serveur, la page de la première est rouverte."""
    nous = serveur_local({"status": "ok", "app": "patrimoine"})
    monkeypatch.setenv("PATRIMOINE_DB", str(tmp_path / "test.db"))
    premier = launcher.InstanceLock(launcher.lock_path(tmp_path, str(tmp_path / "test.db")))
    assert premier.acquire()
    premier.write_port(nous.server_port)
    ouvertes = []
    monkeypatch.setattr(launcher, "open_browser", lambda url: ouvertes.append(url) or True)
    try:
        assert launcher.run(display=launcher.BROWSER) == 0
    finally:
        premier.release()
        nous.shutdown()
    assert ouvertes == [f"http://localhost:{nous.server_port}"]


def test_instance_muette_signalee(tmp_path, monkeypatch):
    monkeypatch.setenv("PATRIMOINE_DB", str(tmp_path / "test.db"))
    premier = launcher.InstanceLock(launcher.lock_path(tmp_path, str(tmp_path / "test.db")))
    assert premier.acquire()
    monkeypatch.setattr(launcher, "wait_for_instance", lambda lock: None)
    try:
        assert launcher.run(display=launcher.NOTHING) == 1
    finally:
        premier.release()


def test_navigateur_wsl(monkeypatch):
    appels = []
    monkeypatch.setattr(launcher, "is_wsl", lambda: True)
    monkeypatch.setattr(launcher.shutil, "which", lambda c: c if c == "explorer.exe" else None)
    monkeypatch.setattr(launcher.subprocess, "run", lambda cmd, **kw: appels.append(cmd))
    assert launcher.open_browser("http://localhost:8000")
    assert appels == [["explorer.exe", "http://localhost:8000"]]


def test_linux_sans_ecran_n_ouvre_rien(monkeypatch):
    """Sans écran, webbrowser pourrait lancer un navigateur texte dans le terminal."""
    monkeypatch.setattr(launcher, "is_wsl", lambda: False)
    monkeypatch.setattr(launcher.sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(launcher.webbrowser, "open", lambda url: pytest.fail("ouvert"))
    assert not launcher.open_browser("http://localhost:8000")


@pytest.mark.parametrize("plateforme", ["win32", "darwin"])
def test_navigateur_windows_et_macos(monkeypatch, plateforme):
    ouvertes = []
    monkeypatch.setattr(launcher, "is_wsl", lambda: False)
    monkeypatch.setattr(launcher.sys, "platform", plateforme)
    monkeypatch.setattr(launcher.webbrowser, "open", lambda url: ouvertes.append(url) or True)
    assert launcher.open_browser("http://localhost:8001")
    assert ouvertes == ["http://localhost:8001"]


def test_adresse_affichee_si_rien_ne_s_ouvre(capsys):
    launcher.show("http://localhost:8000", browser=True, opener=lambda url: False)
    assert "http://localhost:8000" in capsys.readouterr().out


def test_sauvegarde_finie_avant_l_arret():
    """Arrêt pendant une sauvegarde : elle va jusqu'au bout (le fil n'est pas coupé net)."""
    etapes = []

    def job():
        etapes.append("début")
        time.sleep(0.3)
        etapes.append("fin")
        return []

    worker = launcher.BackupWorker(job, every=0.01)
    worker.start()
    while not etapes:
        time.sleep(0.01)
    worker.stop()
    assert etapes[:2] == ["début", "fin"]
    assert not worker._thread.is_alive()


@pytest.mark.parametrize(
    ("options", "affichage"),
    [([], "fenetre"), (["--navigateur"], "navigateur"), (["--sans-navigateur"], "aucun")],
)
def test_options_d_affichage(monkeypatch, options, affichage):
    recu = {}
    monkeypatch.setattr(launcher, "run", lambda display: recu.update(display=display) or 0)
    entree.main(options)
    assert recu == {"display": affichage}


def test_options_d_affichage_exclusives():
    with pytest.raises(SystemExit):
        entree.main(["--navigateur", "--sans-navigateur"])


def test_lancement_complet_puis_arret(tmp_path, monkeypatch):
    """Vrai serveur sur 127.0.0.1 : il répond, un second lancement le retrouve, puis l'arrêt
    retire le fichier de port et relâche le verrou."""
    import uvicorn

    from app import backup

    base = tmp_path / "test.db"
    monkeypatch.setenv("PATRIMOINE_DB", str(base))
    monkeypatch.setattr(backup, "daily", lambda paths: [])  # rien d'écrit hors de tmp_path
    serveurs = []

    class Serveur(uvicorn.Server):
        def __init__(self, config):
            super().__init__(config)
            serveurs.append(self)

    monkeypatch.setattr(uvicorn, "Server", Serveur)
    codes = []
    fil = threading.Thread(target=lambda: codes.append(launcher.run(display=launcher.NOTHING)))
    fil.start()
    verrou = launcher.InstanceLock(launcher.lock_path(tmp_path, str(base)))
    try:
        for _ in range(200):
            port = verrou.read_port()
            if serveurs and serveurs[0].started and port:
                break
            time.sleep(0.05)
        assert port and launcher.is_patrimoine(port)
        assert launcher.run(display=launcher.NOTHING) == 0  # second lancement : vers le premier
        assert len(serveurs) == 1
    finally:
        if serveurs:
            serveurs[0].should_exit = True
        fil.join(10)
    assert codes == [0]
    assert not verrou.port_file.exists()
    assert verrou.acquire()
    verrou.release()
