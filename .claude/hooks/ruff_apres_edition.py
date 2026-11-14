"""Hook PostToolUse : formate et corrige avec ruff chaque fichier Python que Claude modifie.

La CI refuse un fichier mal formaté (`ruff format --check`) : on le corrige dès l'écriture.
Les erreurs que ruff ne sait pas corriger sont renvoyées à Claude (code 2) pour qu'il les traite.
"""

import json
import subprocess
import sys


def main() -> int:
    appel = json.load(sys.stdin)
    fichier = (appel.get("tool_input") or {}).get("file_path", "")
    if not fichier.endswith(".py"):
        return 0
    ruff = ["uv", "run", "--quiet", "ruff"]
    try:
        subprocess.run([*ruff, "format", "--quiet", fichier], check=False, timeout=60)
        verif = subprocess.run(
            [*ruff, "check", "--fix", "--quiet", fichier],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 0  # uv absent ou bloqué : la CI reste le filet
    if verif.returncode != 0:
        print(
            f"ruff signale des erreurs à corriger dans {fichier} :\n{verif.stdout}", file=sys.stderr
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
