# Essai de Patrimoine.exe en CI Windows (D-126) : lancement normal, comme un double-clic.
# La fenêtre doit s'ouvrir avec WebView2 (processus msedgewebview2 lancé par Patrimoine.exe),
# sans repli sur le navigateur. Dossier de données vide et propre à chaque essai.
param([Parameter(Mandatory)] [string] $Nom)
$ErrorActionPreference = 'Stop'
$data = Join-Path $env:RUNNER_TEMP "donnees-$Nom"
New-Item -ItemType Directory -Force $data | Out-Null
$env:PATRIMOINE_DATA_DIR = $data
$env:PYTHONUNBUFFERED = '1' # messages de l'app visibles ici même si elle est arrêtée net
$exe = Resolve-Path 'dist\Patrimoine\Patrimoine.exe'
# -NoNewWindow : lancement direct (CreateProcess), sans l'avertissement « Fichier ouvert » que
# Windows montre pour un exe venu d'Internet et qui attendrait un clic indéfiniment en CI.
# L'utilisateur, lui, voit cet avertissement (ou SmartScreen) et choisit d'exécuter.
$proc = Start-Process -FilePath $exe -NoNewWindow -PassThru
try {
  $seen = $false
  for ($i = 0; $i -lt 90 -and -not $seen; $i++) {
    if ($proc.HasExited) { throw "Patrimoine.exe s'est arrêté (code $($proc.ExitCode))" }
    $children = Get-CimInstance Win32_Process -Filter "Name = 'msedgewebview2.exe'" |
      Where-Object { $_.ParentProcessId -eq $proc.Id }
    if ($children) { $seen = $true } else { Start-Sleep -Milliseconds 500 }
  }
  if (-not $seen) { throw "Aucun moteur WebView2 lancé par Patrimoine.exe en 45 s ($Nom)" }
  Write-Output "OK ($Nom) : fenêtre ouverte avec WebView2"
} finally {
  Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
  Start-Sleep -Seconds 2 # le verrou et le moteur web se libèrent avant l'essai suivant
}
