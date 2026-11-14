import httpx2
import pytest

from app import main, prices
from app.main import app, http_client


@pytest.fixture
def db_file(tmp_path, monkeypatch):
    """Base jetable dans un dossier temporaire : les tests ne touchent jamais data/."""
    path = tmp_path / "test.db"
    monkeypatch.setenv("PATRIMOINE_DB", str(path))
    return path


def offline(request: httpx2.Request) -> httpx2.Response:
    raise httpx2.ConnectError("réseau coupé pendant les tests", request=request)


def fake_client(handler=offline) -> httpx2.Client:
    """Client HTTP dont les réponses viennent de `handler` : aucune requête ne sort."""
    return httpx2.Client(transport=httpx2.MockTransport(handler))


@pytest.fixture(autouse=True)
def data_dir_jetable(tmp_path_factory, monkeypatch):
    """Dossier des données (D-119) jetable : un test ne lit jamais le vrai dossier de l'app,
    ni ses sauvegardes (qui suivent DATA_DIR), ni l'ancien data/ du code, qu'il recopierait."""
    from app import datadir, profiles

    jetable = tmp_path_factory.mktemp("donnees")
    monkeypatch.setattr(profiles, "DATA_DIR", jetable)
    monkeypatch.setattr(profiles, "DATA_ORIGIN", "par défaut")
    monkeypatch.setattr(datadir, "LEGACY_DIR", tmp_path_factory.mktemp("ancien-data-vide"))
    vrai = datadir.default_dir  # appelé avec un système donné : la vraie règle, testée à part
    monkeypatch.setattr(
        datadir, "default_dir", lambda *a, **k: vrai(*a, **k) if a or k else jetable
    )
    monkeypatch.delenv("PATRIMOINE_DATA_DIR", raising=False)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Coupe le réseau de l'API pour tous les tests ; un test peut fournir son faux serveur."""
    monkeypatch.delenv("PRICE_TTL_MINUTES", raising=False)
    monkeypatch.delenv("COINGECKO_API_KEY", raising=False)
    # Nom de site utilisé par le TestClient de FastAPI, refusé hors des tests (D-039).
    monkeypatch.setattr(main, "ALLOWED_HOSTS", main.ALLOWED_HOSTS | {"testserver"})
    app.dependency_overrides[http_client] = fake_client
    prices._previous_close.clear()  # mémoire du processus : repartir de zéro à chaque test
    prices._history_checked.clear()
    prices._fx_checked.clear()
    prices._benchmarks.clear()
    prices._coin_history.clear()
    prices._coin_failed.clear()
    prices._inflation.clear()
    yield
    app.dependency_overrides.clear()
