@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "APP=MessagerieLocale.exe"
set "URL=https://raw.githubusercontent.com/grosdada/Messagerie-reseau-local/main/MessagerieLocale.exe"
set "NEW=%~dp0MessagerieLocale.new.exe"

if not exist "%APP%" (
  echo.
  echo Ce script doit etre place dans le meme dossier que %APP%.
  echo Glisse-le dans le dossier de la messagerie, puis double-clique dessus.
  pause
  exit /b 1
)

echo.
echo Fermeture de la messagerie puis telechargement de la derniere version...
taskkill /f /im "%APP%" >nul 2>&1
timeout /t 4 /nobreak >nul

powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $out='%NEW%'; Remove-Item -LiteralPath $out -Force -ErrorAction SilentlyContinue; Invoke-WebRequest -UseBasicParsing -Uri '%URL%' -OutFile $out; if ((Get-Item -LiteralPath $out).Length -lt 1000000) { throw 'Le fichier telecharge est invalide.' }; Move-Item -LiteralPath $out -Destination '%~dp0%APP%' -Force"
if errorlevel 1 (
  echo.
  echo La mise a jour a echoue. Verifie la connexion Internet et reessaie.
  pause
  exit /b 1
)

echo Mise a jour terminee. Relancement...
start "" "%~dp0%APP%"
exit /b 0
