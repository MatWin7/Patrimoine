# Sortir une version

Une **étiquette** (tag) est un nom fixe posé sur un commit, par exemple `v0.2.0`. Quand elle
arrive sur GitHub, le workflow `.github/workflows/release.yml` publie la **release** : une page
GitHub avec les notes du journal, l'archive du code, `Patrimoine-X.Y.Z-windows.zip` (le dossier de
`Patrimoine.exe`, construit et essayé sous Windows par `exe-windows.yml`, D-126) et les empreintes
SHA-256 (D-121).

## 1. PR de version

Sur une branche, dans une PR comme les autres :

1. Choisir le numéro (`majeur.mineur.correctif`) : correctif pour des réparations seules, mineur
   s'il y a une nouveauté, majeur pour un changement qui casse quelque chose.
2. Le mettre dans `app/version.py` (`__version__`) et `pyproject.toml` (`version`), puis
   `uv lock` (recopie le numéro dans `uv.lock`). Un test vérifie que les trois concordent.
3. Dans `CHANGELOG.md`, renommer `## Non publié` en `## X.Y.Z` (date facultative :
   `## X.Y.Z - 2026-12-01`) et remettre un `## Non publié` vide au-dessus.

## 2. Étiquette, après la fusion

```bash
git switch main && git pull
git tag -a v0.2.0 -m "Patrimoine 0.2.0"
git push origin v0.2.0
```

Le workflow refuse l'étiquette si elle ne correspond pas à `app/version.py`, si le journal n'a pas
de section pour cette version, si le commit n'est pas sur `main`, ou si tests et lint échouent.
Dans ce cas, aucune release n'est créée : supprimer l'étiquette (`git push --delete origin v0.2.0`
puis `git tag -d v0.2.0`), corriger par une PR, recommencer.

Préversion pour essayer avant de publier : `v0.2.0-rc.1` (même numéro dans `app/version.py`),
publiée comme « pré-release », jamais proposée comme dernière version.

## 3. Vérifier un fichier téléchargé

L'empreinte SHA-256 est l'« empreinte digitale » d'un fichier : un seul octet changé la change
entièrement. Comparer celle du fichier téléchargé à la ligne de `SHA256SUMS.txt` :

```bash
sha256sum patrimoine-0.2.0.zip                     # Linux, WSL
shasum -a 256 patrimoine-0.2.0.zip                 # macOS
Get-FileHash patrimoine-0.2.0.zip                  # Windows PowerShell
```

Elle prouve que le fichier n'a pas été abîmé ou remplacé en route, pas qui l'a fabriqué : la
signature viendra à l'étape 15 du plan de distribution.

## 4. Patrimoine.exe hors d'une release

Pour essayer l'exécutable d'une branche : onglet **Actions** du dépôt, workflow
« Patrimoine.exe », bouton **Run workflow** (ou le passage lancé par la PR), puis l'artefact
`Patrimoine-windows` en bas de la page du passage (gardé 7 jours). Le dézipper : il contient le
dossier `Patrimoine/`. En local, sous Windows : `uv sync --group build` puis
`uv run --group build pyinstaller packaging/patrimoine.spec` (résultat dans `dist\Patrimoine`).

Tant que l'exe n'est pas signé (étape 15), Windows affiche au premier lancement « Windows a
protégé votre ordinateur » : « Informations complémentaires », puis « Exécuter quand même ».
