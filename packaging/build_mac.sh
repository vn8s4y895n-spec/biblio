#!/bin/sh
# Construit dist.nosync/Biblio-<version>.dmg (Mac, Apple Silicon). Les dossiers « .nosync » ne sont pas copiés par
# iCloud Drive (qui dupliquait les fichiers en cours d'écriture : « Biblio 2.app ») ; l'app elle-même reste dans un
# dossier « .noindex », ignoré par Spotlight : seul le Biblio installé dans Applications y apparaît. Signature ad hoc : sans compte
# Apple Developer, un Mac qui reçoit l'app par Internet demande une confirmation au premier lancement (README).
set -e
cd "$(dirname "$0")/.."
export PATH="$HOME/Library/Python/3.14/bin:$HOME/.local/bin:$PATH"
# Construction sur un Mac (hors GitHub Actions, qui met le numéro de la version publiée) : même numéro que la
# dernière version publiée, pour que l'app ne propose pas en « mise à jour » une version plus ancienne qu'elle.
if [ -z "$GITHUB_ACTIONS" ] && git fetch -q --tags 2>/dev/null; TAG=$(git tag --list 'v*' --sort=-v:refname | head -1) && [ -n "$TAG" ]; then
  python3 packaging/set_version.py "$TAG"
fi
VERSION=$(uv run python -c "import biblio; print(biblio.__version__)")

npm --prefix ui run build
mkdir -p packaging/build.nosync/Biblio.iconset
for s in 16 32 128 256 512; do
  sips -z $s $s packaging/icon.png --out packaging/build.nosync/Biblio.iconset/icon_${s}x${s}.png >/dev/null
  sips -z $((s*2)) $((s*2)) packaging/icon.png --out packaging/build.nosync/Biblio.iconset/icon_${s}x${s}@2x.png >/dev/null
done
iconutil -c icns packaging/build.nosync/Biblio.iconset -o packaging/build.nosync/Biblio.icns

cd packaging
uv run pyinstaller --noconfirm --clean --distpath build.nosync/app.noindex --workpath build.nosync/pyinstaller.noindex biblio.spec
cd ..
OUT=dist.nosync
STAGE=packaging/build.nosync/dmg.noindex
rm -rf "$OUT" "$STAGE" && mkdir -p "$OUT" "$STAGE"
cp -R packaging/build.nosync/app.noindex/Biblio.app "$STAGE/" && ln -s /Applications "$STAGE/Applications"
hdiutil create -volname Biblio -srcfolder "$STAGE" -ov -format UDZO "$OUT/Biblio-$VERSION.dmg" >/dev/null
rm -rf "$STAGE"
echo "✓ $OUT/Biblio-$VERSION.dmg (app pour les essais : packaging/build.nosync/app.noindex/Biblio.app)"
