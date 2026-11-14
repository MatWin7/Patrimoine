"""Numéro de version et contrôles de release (D-121)."""

import tomllib

import pytest

from app import version
from app.__main__ import main as app_main
from app.version import ReleaseError, __version__

JOURNAL = """# Journal

## Non publié

- Une nouveauté pas encore sortie.

## 0.2.0 - 2026-12-01

### Ajouté

- Fenêtre d'application.

## 0.1.0

- Première version.
"""


def test_meme_version_partout():
    """app/version.py, pyproject.toml et uv.lock portent le même numéro."""
    pyproject = tomllib.loads((version.ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["version"] == __version__
    lock = tomllib.loads((version.ROOT / "uv.lock").read_text(encoding="utf-8"))
    (projet,) = [p for p in lock["package"] if p["name"] == "patrimoine"]
    assert projet["version"] == __version__


def test_journal_a_une_section_pour_la_version_du_code():
    """Sinon l'étiquette de cette version serait refusée par le workflow de release."""
    texte = version.CHANGELOG.read_text(encoding="utf-8")
    assert version.release_notes(texte, __version__)
    assert "## Non publié" in texte


@pytest.mark.parametrize(
    ("tag", "attendu"),
    [("v0.2.0", "0.2.0"), ("v1.10.3", "1.10.3"), ("v0.2.0-rc.1", "0.2.0-rc.1")],
)
def test_etiquette_valide(tag, attendu):
    assert version.version_from_tag(tag) == attendu


@pytest.mark.parametrize(
    "tag", ["0.2.0", "v0.2", "v0.2.0.1", "version-0.2.0", "v0.2.0;rm -rf", "v0.2.0 ", "v0.2.0/x"]
)
def test_etiquette_refusee(tag):
    with pytest.raises(ReleaseError):
        version.version_from_tag(tag)


def test_preversion():
    assert version.is_prerelease("0.2.0-rc.1")
    assert not version.is_prerelease("0.2.0")


def test_notes_d_une_version_jusqu_a_la_suivante():
    assert version.release_notes(JOURNAL, "0.2.0") == "### Ajouté\n\n- Fenêtre d'application."
    assert version.release_notes(JOURNAL, "0.1.0") == "- Première version."


def test_notes_absentes_ou_vides():
    with pytest.raises(ReleaseError, match="pas de section"):
        version.release_notes(JOURNAL, "0.3.0")
    with pytest.raises(ReleaseError, match="vide"):
        version.release_notes("## 0.3.0\n\n## 0.2.0\n- x\n", "0.3.0")


def test_etiquette_differente_du_code_refusee():
    with pytest.raises(ReleaseError, match="ne correspond pas"):
        version.check_tag("v0.2.0", JOURNAL, version="0.1.0")
    assert version.check_tag("v0.2.0", JOURNAL, version="0.2.0").startswith("### Ajouté")


def test_ligne_de_commande(tmp_path, monkeypatch, capsys):
    journal = tmp_path / "CHANGELOG.md"
    journal.write_text(JOURNAL, encoding="utf-8")
    monkeypatch.setattr(version, "CHANGELOG", journal)
    monkeypatch.setattr(version, "__version__", "0.2.0")
    notes = tmp_path / "notes.md"
    assert version.main(["--verifier-etiquette", "v0.2.0", "--notes", str(notes)]) == 0
    assert notes.read_text(encoding="utf-8").startswith("### Ajouté")
    assert version.main(["--verifier-etiquette", "v0.3.0"]) == 1
    assert "ne correspond pas" in capsys.readouterr().err


def test_option_version_du_lanceur(capsys):
    with pytest.raises(SystemExit) as fin:
        app_main(["--version"])
    assert fin.value.code == 0
    assert capsys.readouterr().out.strip() == f"Patrimoine {__version__}"
