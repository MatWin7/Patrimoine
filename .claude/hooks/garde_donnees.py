"""Hook PreToolUse : tient Claude à l'écart des vraies données de l'utilisateur (règles 2, 4, 5).

Claude Code envoie l'appel d'outil en JSON sur l'entrée standard. Sortie :
- rien : l'appel passe ;
- code 2 + message : l'appel est refusé (vraie base, sauvegardes, .env) ;
- JSON « ask » : l'utilisateur doit accepter (relevés réels déposés dans le projet).
Filet de sécurité, pas un coffre-fort : seuls les noms de fichiers cités sont vus.
Bibliothèque standard uniquement : lancé par python3 sans l'environnement du projet.
"""

import json
import re
import sys
from pathlib import Path

# Jamais lus ni modifiés par Claude : le débogage se fait sur data/demo.db.
INTERDITS = [
    (re.compile(r"patrimoine\.db"), "la vraie base (déboguer sur data/demo.db)"),
    (re.compile(r"data/sauvegardes"), "les sauvegardes de la vraie base"),
    # Profils (D-098) : une vraie base par personne, et profils.json (empreintes des codes).
    (re.compile(r"data/profils"), "les bases des profils (déboguer sur data/demo.db)"),
    (re.compile(r"data/corbeille"), "les profils supprimés"),
    # Copie hors de WSL (D-114, BACKUP_COPY_DIR) : même contenu que data/sauvegardes.
    (re.compile(r"Patrimoine-sauvegardes"), "les sauvegardes recopiées hors de WSL"),
    # Dossier des données hors du code (D-119), par défaut sous Linux/WSL, Windows et macOS.
    (re.compile(r"\.local/share/patrimoine", re.I), "le dossier des données réelles"),
    (re.compile(r"AppData[/\\]+Local[/\\]+Patrimoine", re.I), "le dossier des données réelles"),
    (re.compile(r"Application\\?\s*Support/Patrimoine", re.I), "le dossier des données réelles"),
    (re.compile(r"LOCALAPPDATA%?[/\\]+Patrimoine", re.I), "le dossier des données réelles"),
    (re.compile(r"XDG_DATA_HOME\}?/patrimoine", re.I), "le dossier des données réelles"),
    # Dossier choisi (D-119) : son chemin est dans ce fichier ; le dossier lui-même ne peut pas
    # être reconnu à son nom.
    (re.compile(r"dossier-choisi\.txt"), "le choix du dossier des données réelles"),
    (re.compile(r"(^|[^\w.])\.env($|[^\w.])"), "le fichier .env (secrets)"),
]
# Relevés réels : seulement avec l'accord de l'utilisateur. Règles générales, sans nom de
# fichier personnel (le dépôt est public) : un export de plateforme, ou un fichier de données
# cité avec le dossier partagé du projet (captures et notes exceptées).
EXTENSIONS = r"\.(csv|tsv|txt|xlsx?|ods|pdf|ofx|qif|json)\b"
A_DEMANDER = [
    re.compile(r"Exportation de transactions"),  # nom de l'export Trade Republic (D-071)
    # Export CSV, OFX, QIF ou tableur hors des fixtures fictives (« *.csv » n'est pas un fichier).
    re.compile(r"[\w)]\.(csv|tsv|xlsx?|ods|ofx|qif)\b", re.I),
]
DOSSIER_PROJET = re.compile(r"project-files")
LIBRES_PROJET = re.compile(r"project-files/+(\.?notes|captures)/")
FICHIER_DONNEES = re.compile(r"[\w)]" + EXTENSIONS, re.I)
FIXTURES = re.compile(r"tests/fixtures/[\w.-]+")
DEPOTS = re.compile(r"uploads/hearth/([\w-]+)")
DOSSIER_DEPOTS = Path("/mnt/project-files/uploads/hearth")
# Les captures d'écran déposées restent libres : on reconnaît une image à ses premiers octets.
SIGNATURES_IMAGES = (b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"RIFF")
REFUS = (
    "Refusé par le garde-fou du projet : {quoi}. Voir CLAUDE.md, règles 2 à 5. "
    "Pour chercher un texte dans le code, utiliser l'outil Grep."
)
DEMANDE = (
    "Fichier de données réelles : à lire seulement si l'utilisateur l'a demandé, "
    "sans en recopier un montant."
)


def textes_a_verifier(outil: str, entree: dict) -> list[str]:
    if outil == "Bash":
        return [entree.get("command", "")]
    # Le motif de Grep est un texte cherché, pas un fichier ; celui de Glob est un chemin.
    cles = ("file_path", "path", "notebook_path")
    if outil == "Glob":
        cles += ("pattern",)
    return [str(entree[c]) for c in cles if entree.get(c)]


def depot_non_image(texte: str) -> bool:
    for nom in DEPOTS.findall(texte):
        try:
            debut = (DOSSIER_DEPOTS / nom).read_bytes()[:4]
        except OSError:
            continue
        if not debut.startswith(SIGNATURES_IMAGES):
            return True
    return False


def a_demander(texte: str) -> bool:
    texte = FIXTURES.sub("", texte)
    if any(m.search(texte) for m in A_DEMANDER):
        return True
    libre = LIBRES_PROJET.search(texte) and ".." not in texte
    dans_projet = DOSSIER_PROJET.search(texte) and not libre
    return bool(dans_projet and FICHIER_DONNEES.search(texte))


def main() -> int:
    appel = json.load(sys.stdin)
    textes = textes_a_verifier(appel.get("tool_name", ""), appel.get("tool_input") or {})
    for texte in textes:
        for motif, quoi in INTERDITS:
            if motif.search(texte):
                print(REFUS.format(quoi=quoi), file=sys.stderr)
                return 2
    if any(map(a_demander, textes)) or any(map(depot_non_image, textes)):
        decision = {"hookEventName": "PreToolUse", "permissionDecision": "ask"}
        decision["permissionDecisionReason"] = DEMANDE
        print(json.dumps({"hookSpecificOutput": decision}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
