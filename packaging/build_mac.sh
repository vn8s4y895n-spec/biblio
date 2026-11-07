#!/bin/sh
# Construit dist.noindex/Biblio.app et dist.noindex/Biblio-<version>.dmg (Mac, Apple Silicon). Les dossiers en
# « .noindex » ne sont pas indexés par Spotlight : seul le Biblio installé dans Applications y apparaît. Signature ad hoc : sans compte
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
mkdir -p packaging/build/Biblio.iconset
for s in 16 32 128 256 512; do
  sips -z $s $s packaging/icon.png --out packaging/build/Biblio.iconset/icon_${s}x${s}.png >/dev/null
  sips -z $((s*2)) $((s*2)) packaging/icon.png --out packaging/build/Biblio.iconset/icon_${s}x${s}@2x.png >/dev/null
done
iconutil -c icns packaging/build/Biblio.iconset -o packaging/build/Biblio.icns

cd packaging
uv run pyinstaller --noconfirm --clean --distpath build/app.noindex --workpath build/pyinstaller.noindex biblio.spec
cd ..
OUT=dist.noindex
STAGE=packaging/build/dmg.noindex
rm -rf "$OUT" "$STAGE" && mkdir -p "$OUT" "$STAGE"
cp -R packaging/build/app.noindex/Biblio.app "$OUT/"
cp -R "$OUT/Biblio.app" "$STAGE/" && ln -s /Applications "$STAGE/Applications"
hdiutil create -volname Biblio -srcfolder "$STAGE" -ov -format UDZO "$OUT/Biblio-$VERSION.dmg" >/dev/null
rm -rf "$STAGE" packaging/build/app.noindex
echo "✓ $OUT/Biblio.app et $OUT/Biblio-$VERSION.dmg"
