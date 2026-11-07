# Construit dist\Biblio\Biblio.exe puis, si Inno Setup est installé, l'installeur dist\Biblio-<version>-setup.exe
# (raccourci, association des fichiers .biblio). À lancer sur Windows, depuis le dossier du projet :
#   powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
# Prérequis : uv (https://docs.astral.sh/uv/), Node.js ; facultatif : Inno Setup 6 (https://jrsoftware.org/isinfo.php).
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
$version = uv run python -c "import biblio; print(biblio.__version__)"

npm --prefix ui install
npm --prefix ui run build
Push-Location packaging
uv run --with pillow pyinstaller --noconfirm --clean --distpath ..\dist --workpath build\pyinstaller biblio.spec
Pop-Location

$iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
if (Test-Path $iscc) {
    & $iscc "/DVersion=$version" packaging\biblio.iss
    Write-Host "OK dist\Biblio-$version-setup.exe"
} else {
    Write-Host "OK dist\Biblio\Biblio.exe (Inno Setup absent : pas d'installeur)"
}
