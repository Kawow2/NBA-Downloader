# Anime & NBA Downloader - lance main.py dans un environnement virtuel (.venv)
#
# Dans PowerShell, depuis le dossier du projet :
#     .\start.ps1
#     .\start.ps1 nba --url "https://basketball-video.com/..."
# Si Windows bloque le script : Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
#
# Équivalent manuel :
#     python -m venv .venv
#     .\.venv\Scripts\Activate.ps1
#     pip install -r requirements.txt
#     python main.py

# Dossiers (bibliothèques Plex) pour ce lanceur, ex :
#   $CheminAnime = "D:\Plex\Anime"
#   $CheminNBA   = "D:\Plex\Sports\NBA"
# Laissés vides : les dossiers choisis dans le menu du programme (Réglages),
# demandés la première fois que chaque catégorie est utilisée.
$CheminAnime = ""
$CheminNBA = ""

# "Continue" : sous Windows PowerShell 5.1, "Stop" transforme la moindre
# sortie d'erreur de python en exception.
$ErrorActionPreference = "Continue"
Set-Location -Path $PSScriptRoot
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    $python = $null
    foreach ($candidate in @(@("py", "-3"), @("python"), @("python3"))) {
        if (Get-Command $candidate[0] -ErrorAction SilentlyContinue) {
            $pyArgs = @($candidate | Select-Object -Skip 1)
            & $candidate[0] @pyArgs -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" 2>$null
            if ($LASTEXITCODE -eq 0) { $python = $candidate; break }
        }
    }
    if (-not $python) {
        Write-Host "Python 3.8+ introuvable. Installez-le : winget install Python.Python.3.12" -ForegroundColor Red
        exit 1
    }
    Write-Host "Création de l'environnement virtuel .venv..." -ForegroundColor Cyan
    $pyArgs = @($python | Select-Object -Skip 1)
    & $python[0] @pyArgs -m venv .venv
    if (-not (Test-Path $venvPython)) {
        Write-Host "Impossible de créer .venv" -ForegroundColor Red
        exit 1
    }
}

# Dépendances (installées dans .venv, une seule fois)
& $venvPython -c "import requests, bs4, tqdm, yt_dlp, curl_cffi, av, Crypto, cloudscraper" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installation des dépendances dans .venv..." -ForegroundColor Cyan
    & $venvPython -m pip install --upgrade pip
    & $venvPython -m pip install -r requirements.txt
}

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Host "ffmpeg introuvable (fortement conseillé) : winget install Gyan.FFmpeg" -ForegroundColor Yellow
}

$mainArgs = @()
if ($CheminAnime) { $mainArgs += @("--anime-dir", $CheminAnime) }
if ($CheminNBA) { $mainArgs += @("--nba-dir", $CheminNBA) }
$mainArgs += @($args)

& $venvPython main.py @mainArgs
