"""Lanceur multiplateforme (D-118) : Windows, macOS et Linux, avec ou sans WSL.

`python -m app` choisit un port libre (8000 de préférence), refuse un second lancement sur les
mêmes données (il ramène alors l'app déjà ouverte), ouvre la fenêtre d'application (D-125) ou
à défaut le navigateur une fois le serveur prêt, et s'arrête proprement (fenêtre fermée ou
Ctrl+C : fin de la sauvegarde en cours, fichiers de lancement retirés). Le serveur reste lié à
127.0.0.1 (règle 6) : rien ici ne l'expose au réseau.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import IO

HOST = "127.0.0.1"  # jamais toutes les interfaces : joignable depuis cette machine seule
DEFAULT_PORT = 8000
PORT_RANGE = range(8000, 8020)  # ports essayés dans l'ordre, après le dernier utilisé
APP_ID = "patrimoine"  # renvoyé par /api/health : reconnaître notre app, pas un autre service
# Durées en millisecondes entières (le code n'a aucun float, règle 7 vérifiée par un test).
POLL_MS = 100


# --- Verrou : une seule instance par jeu de données -------------------------------------------
# Un fichier verrouillé par le système (flock sous Linux et macOS, msvcrt sous Windows) : si
# l'app s'arrête mal (fenêtre fermée, coupure), le système relâche le verrou tout seul, il n'y a
# jamais de verrou « fantôme » à effacer à la main. Le port est écrit dans un fichier à côté :
# sous Windows, un octet verrouillé ne peut pas être lu par un autre processus.


class InstanceLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.port_file = path.with_name(path.name + ".port")
        self._handle: IO[bytes] | None = None

    def acquire(self) -> bool:
        """Prend le verrou sans attendre ; False si une autre instance le tient."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+b")  # gardé ouvert tant que l'app tourne
        try:
            _lock(handle)
        except OSError:
            handle.close()
            return False
        self._handle = handle
        return True

    def release(self, forget_port: bool = True) -> None:
        if self._handle is None:
            return
        if forget_port:
            self.port_file.unlink(missing_ok=True)
        try:
            _unlock(self._handle)
        finally:
            self._handle.close()
            self._handle = None

    def write_port(self, port: int) -> None:
        self.port_file.write_text(json.dumps({"port": port, "pid": os.getpid()}), encoding="utf-8")

    def read_port(self) -> int | None:
        """Port de l'instance en cours (ou du dernier lancement si le fichier est resté)."""
        try:
            port = json.loads(self.port_file.read_text(encoding="utf-8"))["port"]
        except (OSError, ValueError, KeyError, TypeError):
            return None
        return port if isinstance(port, int) and 0 < port < 65536 else None


if sys.platform == "win32":
    import msvcrt

    def _lock(handle: IO[bytes]) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)  # OSError si déjà pris

    def _unlock(handle: IO[bytes]) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock(handle: IO[bytes]) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # OSError si déjà pris

    def _unlock(handle: IO[bytes]) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def lock_path(data_dir: Path, forced_db: str | None) -> Path:
    """Verrou des données lancées : PATRIMOINE_DB a le sien (la démo peut tourner à côté de la
    vraie base), sinon un seul pour data/ (tous les profils sont servis par la même instance)."""
    if forced_db:
        db = Path(forced_db)
        return db.with_name(db.name + ".lancement")
    return data_dir / "lancement.lock"


# --- Port -------------------------------------------------------------------------------------


def candidate_ports(last: int | None) -> list[int]:
    """Dernier port utilisé d'abord, puis 8000 et les suivants. Garder le même port compte : le
    navigateur range les réglages de la page (ordre des onglets, tris) par adresse, port compris."""
    ports = [last] if last else []
    return ports + [p for p in PORT_RANGE if p != last]


def bind_socket(port: int) -> socket.socket:
    """Ouvre le port sur 127.0.0.1. Le socket est passé tel quel à uvicorn : pas d'écart entre
    « le port est libre » et « le serveur l'occupe » où un autre programme pourrait le prendre."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if sys.platform == "win32":
            # Sous Windows, SO_REUSEADDR laisserait voler un port déjà occupé : l'inverse ici.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # relance immédiate
        sock.bind((HOST, port))
        sock.listen(2048)
    except OSError:
        sock.close()
        raise
    return sock


def first_free_socket(ports: Iterable[int]) -> socket.socket:
    for port in ports:
        try:
            return bind_socket(port)
        except OSError:
            continue
    raise SystemExit(
        f"Aucun port libre entre {PORT_RANGE.start} et {PORT_RANGE.stop - 1} : "
        "fermer un programme qui les occupe."
    )


# --- Instance déjà ouverte --------------------------------------------------------------------


def url_for(port: int) -> str:
    return f"http://localhost:{port}"  # « localhost » : seul nom accepté avec 127.0.0.1 (D-039)


def is_patrimoine(port: int, timeout_ms: int = 1000) -> bool:
    """Le port répond-il, et est-ce bien Patrimoine ? (Requête locale seulement.)"""
    try:
        url = f"http://{HOST}:{port}/api/health"
        with urllib.request.urlopen(url, timeout=timeout_ms / 1000) as res:
            return json.loads(res.read().decode("utf-8")).get("app") == APP_ID
    except (OSError, ValueError, AttributeError):
        return False


def wait_for_instance(lock: InstanceLock, seconds: int = 30) -> int | None:
    """Autre instance qui démarre peut-être encore : attendre qu'elle réponde."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        port = lock.read_port()
        if port is not None and is_patrimoine(port):
            return port
        time.sleep(POLL_MS / 1000)
    return None


# --- Navigateur -------------------------------------------------------------------------------


def is_wsl() -> bool:
    if sys.platform != "linux":
        return False
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        return "microsoft" in Path("/proc/version").read_text(encoding="utf-8").lower()
    except OSError:
        return False


def open_browser(url: str) -> bool:
    """Ouvre l'adresse dans le navigateur habituel. Sous WSL, celui de Windows (wslview, sinon
    explorer.exe) ; sous Linux sans écran, rien (le module webbrowser pourrait sinon lancer un
    navigateur texte dans le terminal). Renvoie False si rien n'a été ouvert."""
    if is_wsl():
        for command in ("wslview", "explorer.exe"):
            if shutil.which(command):
                # explorer.exe renvoie 1 même quand tout va bien : code de retour ignoré.
                subprocess.run([command, url], check=False, stdout=subprocess.DEVNULL)
                return True
        return False
    if sys.platform == "linux" and not (
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    ):
        return False
    try:
        return webbrowser.open(url)
    except webbrowser.Error:
        return False


def show(url: str, browser: bool, opener: Callable[[str], bool] | None = None) -> None:
    if not (browser and (opener or open_browser)(url)):
        print(f"Ouvrir {url} dans le navigateur.")


# --- Affichage : fenêtre, navigateur ou rien --------------------------------------------------

WINDOW = "fenetre"  # par défaut : fenêtre d'application, le navigateur si elle est impossible
BROWSER = "navigateur"  # --navigateur : toujours le navigateur (conception, outils du développeur)
NOTHING = "aucun"  # --sans-navigateur : rien n'est ouvert, l'adresse est affichée
DISPLAYS = (WINDOW, BROWSER, NOTHING)


def choose_display(wanted: str) -> str:
    """Fenêtre demandée mais impossible ici (Linux, WSL, pywebview absent) : navigateur."""
    from app import window

    if wanted != WINDOW:
        return wanted
    reason = window.unavailable_reason(wsl=is_wsl())
    if reason is None:
        return WINDOW
    if sys.platform in ("win32", "darwin"):  # sous Linux, le navigateur est le mode normal
        print(f"Pas de fenêtre d'application ({reason}).")
    return BROWSER


def focus_instance(port: int, timeout_ms: int = 2000) -> bool:
    """Demande à l'instance déjà lancée de ramener sa fenêtre devant (requête locale)."""
    request = urllib.request.Request(f"http://{HOST}:{port}/api/window/focus", method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout_ms / 1000) as res:
            return json.loads(res.read().decode("utf-8")).get("focused") is True
    except (OSError, ValueError, AttributeError):
        return False


# --- Lancement --------------------------------------------------------------------------------

# Erreurs qui ont empêché le démarrage : sans console (Patrimoine.exe), elles sont montrées dans
# une boîte de dialogue en plus du journal (D-126).
startup_errors: list[str] = []


def fail(message: str) -> int:
    """Affiche une erreur de démarrage, la retient et renvoie le code de sortie 1."""
    print(message)
    startup_errors.append(message)
    return 1


def run(display: str = WINDOW) -> int:
    """Lancement complet. Renvoie le code de sortie du programme."""
    import uvicorn

    from app import backup, datadir, migrate, profiles, window
    from app.config import FROZEN, load_dotenv
    from app.db import describe
    from app.version import __version__

    load_dotenv()  # avant tout : PATRIMOINE_DB, PRICE_TTL_MINUTES... peuvent venir de .env
    forced = os.environ.get("PATRIMOINE_DB")
    if not forced:
        datadir.ensure(profiles.DATA_DIR, profiles.DATA_ORIGIN)  # DataDirError : rien créé
    lock = InstanceLock(lock_path(profiles.DATA_DIR, forced))
    if not lock.acquire():
        port = wait_for_instance(lock)
        if port is None:
            return fail("Patrimoine est déjà lancé mais ne répond pas : fermer l'autre fenêtre.")
        if display != NOTHING and focus_instance(port):
            print("Patrimoine est déjà ouvert : sa fenêtre est ramenée devant.")
            return 0
        print(f"Patrimoine tourne déjà : {url_for(port)}")
        show(url_for(port), display != NOTHING)
        return 0

    backups = BackupWorker(lambda: backup.daily(profiles.databases()))
    finished: list[bool] = []

    def finish() -> None:
        """Rangement final, une seule fois : en sortie normale, ou sous macOS quand l'app est
        quittée par Cmd+Q (window.watch_app_quit), où le `finally` ne tournerait pas."""
        if finished:
            return
        finished.append(True)
        backups.stop()  # une sauvegarde en cours va jusqu'au bout, au plus 30 s
        lock.release()
        print("Patrimoine arrêté.")

    try:
        if not forced:
            print(f"Dossier des données ({profiles.DATA_ORIGIN}) : {profiles.DATA_DIR}")
            for message in datadir.adopt_legacy(profiles.DATA_DIR):  # copie de l'ancien data/
                print(message)
        paths = profiles.databases()  # une base par profil (D-098), sinon la base unique
        try:  # schéma en retard : copie vérifiée puis mise à jour (D-120)
            for message in migrate.upgrade(paths):
                print(message)
        except migrate.UpgradeError as err:
            return fail(str(err))
        for path in paths:
            print(describe(path))
        for message in backup.daily(paths):
            print(message)
        backups.start()

        sock = first_free_socket(candidate_ports(lock.read_port()))
        port = sock.getsockname()[1]
        lock.write_port(port)
        url = url_for(port)
        # Patrimoine.exe : pas une ligne par requête dans le journal, que personne ne lit (D-126).
        config = uvicorn.Config("app.main:app", host=HOST, port=port, access_log=not FROZEN)
        server = uvicorn.Server(config)
        display = choose_display(display)
        _exit_cleanly_on_sigterm()
        if display == WINDOW:
            print(f"Patrimoine {__version__} : {url} (fermer la fenêtre pour arrêter)")
            storage = window.storage_dir(profiles.DATA_DIR, forced)
            return _serve_with_window(server, sock, url, storage, profiles.DATA_DIR, finish)
        threading.Thread(
            target=_open_when_ready,
            args=(server, url, display == BROWSER),
            name="navigateur",
            daemon=True,
        ).start()
        print(f"Patrimoine {__version__} : {url} (Ctrl+C pour arrêter)")
        try:
            server.run(sockets=[sock])
        except KeyboardInterrupt:
            pass  # uvicorn a déjà fermé le serveur, puis relancé le Ctrl+C reçu
        return 0
    finally:
        finish()


def _exit_cleanly_on_sigterm() -> None:
    """Après un arrêt propre, uvicorn renvoie le signal reçu au processus. Pour SIGTERM (arrêt
    demandé par le système), le comportement par défaut tuerait l'app avant le rangement final :
    on le remplace par une sortie normale, qui passe par les `finally`."""
    if threading.current_thread() is threading.main_thread() and hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(0))


def _open_when_ready(server, url: str, browser: bool) -> None:
    if _wait_started(server):
        show(url, browser)


def _wait_started(server, alive: Callable[[], bool] = lambda: True) -> bool:
    for _ in range(30_000 // POLL_MS):  # 30 s au plus
        if server.started:
            return True
        if server.should_exit or not alive():
            return False
        time.sleep(POLL_MS / 1000)
    return False


def _serve_with_window(
    server,
    sock: socket.socket,
    url: str,
    storage: Path,
    data_dir: Path,
    finish: Callable[[], None] = lambda: None,
) -> int:
    """Fenêtre au premier plan du programme : elle occupe le fil principal (exigence de macOS),
    le serveur tourne à côté. La fermer arrête le serveur. Si elle ne s'ouvre pas (moteur web
    absent), le navigateur prend le relais et le serveur tourne jusqu'au Ctrl+C."""
    from app import window

    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [sock]}, name="serveur", daemon=True
    )
    thread.start()  # hors du fil principal, uvicorn n'intercepte pas Ctrl+C : géré ici
    try:
        if not _wait_started(server, thread.is_alive):
            return fail("Le serveur n'a pas démarré.")
        try:

            def on_quit() -> None:  # macOS, Cmd+Q : le processus s'arrête juste après
                server.should_exit = True
                thread.join(10)
                finish()

            shown = window.run(
                url, storage, data_dir, open_in_browser=open_browser, on_quit=on_quit
            )
        except Exception as err:  # noqa: BLE001 : moteur web en panne, l'app reste utilisable
            print(f"Fenêtre impossible : {err}")
            from app import diagnostic

            diagnostic.record(err, "ouverture de la fenêtre")
            shown = False
        if not shown:
            print("Ouverture dans le navigateur (Ctrl+C pour arrêter).")
            show(url, True)
            while thread.is_alive():
                thread.join(POLL_MS / 1000)
        return 0
    except KeyboardInterrupt:
        return 0
    finally:
        server.should_exit = True
        thread.join(10)


HOURLY = 3600  # secondes entre deux vérifications de la sauvegarde du jour (D-114)


class BackupWorker:
    """App laissée ouverte plusieurs jours : la sauvegarde du jour est quand même faite.

    Au plus une copie par jour et par base ; une heure sans changement ne coûte qu'un test.
    À l'arrêt, une sauvegarde commencée va jusqu'au bout au lieu d'être coupée net.
    """

    def __init__(self, job: Callable[[], list[str]], every: float = HOURLY) -> None:
        self.job = job
        self.every = every
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="sauvegarde", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout: int = 30) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout)

    def _loop(self) -> None:
        while not self._stop.wait(self.every):
            try:
                for message in self.job():
                    print(message)
            except Exception as err:  # noqa: BLE001 : ce fil ne doit jamais s'arrêter en silence
                print(f"Attention, sauvegarde automatique impossible : {err}")
                from app import diagnostic

                diagnostic.record(err, "sauvegarde de chaque heure")
