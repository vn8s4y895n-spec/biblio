#!/bin/sh
# Construit dist/Biblio.app et dist/Biblio-<version>.dmg (Mac, Apple Silicon). Signature ad hoc : sans compte
# Apple Developer, un Mac qui reçoit l'app par Internet demande une confirmation au premier lancement (README).
set -e
cd "$(dirname "$0")/.."
export PATH="$HOME/Library/Python/3.14/bin:$HOME/.local/bin:$PATH"
VERSION=$(uv run python -c "import biblio; print(biblio.__version__)")

npm --prefix ui run build
mkdir -p packaging/build/Biblio.iconset
for s in 16 32 128 256 512; do
  sips -z $s $s packaging/icon.png --out packaging/build/Biblio.iconset/icon_${s}x${s}.png >/dev/null
  sips -z $((s*2)) $((s*2)) packaging/icon.png --out packaging/build/Biblio.iconset/icon_${s}x${s}@2x.png >/dev/null
done
iconutil -c icns packaging/build/Biblio.iconset -o packaging/build/Biblio.icns

cd packaging
uv run pyinstaller --noconfirm --clean --distpath ../dist --workpath build/pyinstaller biblio.spec
cd ..
rm -rf dist/Biblio   # dossier intermédiaire : seul Biblio.app sert
rm -f "dist/Biblio-$VERSION.dmg"
mkdir -p packaging/build/dmg && rm -rf packaging/build/dmg/* && cp -R dist/Biblio.app packaging/build/dmg/
ln -s /Applications packaging/build/dmg/Applications
hdiutil create -volname Biblio -srcfolder packaging/build/dmg -ov -format UDZO "dist/Biblio-$VERSION.dmg" >/dev/null
echo "✓ dist/Biblio.app et dist/Biblio-$VERSION.dmg"
