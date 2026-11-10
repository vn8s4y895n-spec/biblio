"""Mises à jour de Biblio : la dernière version publiée sur GitHub (sans aucune donnée de la bibliothèque ;
à chaque ouverture tant que Biblio est en 0.x, une fois par jour au plus ensuite), puis son installation en un clic.

Mac : le .dmg est téléchargé, vérifié (empreinte SHA-256 publiée par GitHub), l'application est remplacée et
relancée. Windows : l'installeur vérifié est lancé en mode silencieux ; il ferme Biblio, le remplace et le relance.
Les données ne sont jamais touchées : elles sont hors de l'application et migrées au lancement.
"""

import hashlib
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import REPOSITORY, __version__
from .sources import http

LATEST = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
MAX_SIZE = 500_000_000
SUFFIX = {"darwin": ".dmg", "win32": "-setup.exe"}


def _parse(version: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", version)[:3])


def _asset(assets: list[dict]) -> dict | None:
    suffix = SUFFIX.get(sys.platform)
    return next((a for a in assets if suffix and a["name"].endswith(suffix)), None)


def _latest() -> dict | None:
    ttl = None if __version__.startswith("0.") else 24 * 3600  # 0.x : versions fréquentes, vérifiées à chaque ouverture
    response = http.get(LATEST, ttl=ttl, headers={"Accept": "application/vnd.github+json"})
    return response.json() if response is not None else None


def check() -> dict:
    """`available` : une version plus récente est publiée **avec son fichier pour ce système** (une version dont la
    construction a échoué n'est pas proposée) ; `installable` : elle peut s'installer en un clic."""
    out = {"current": __version__, "latest": None, "available": False, "installable": False,
           "url": f"https://github.com/{REPOSITORY}/releases"}  # fmt: skip
    if (release := _latest()) is None:  # aucune version publiée
        return out
    latest, asset = release["tag_name"].lstrip("v"), _asset(release.get("assets", []))
    available = asset is not None and _parse(latest) > _parse(__version__)
    return {**out, "latest": latest, "available": available, "installable": available and _app() is not None,
            "url": release["html_url"], "download": asset and asset["browser_download_url"],
            "notes": (release.get("body") or "")[:3000]}  # fmt: skip


def _app() -> Path | None:
    """L'application installée qui tourne (Biblio.app sur Mac, dossier de Biblio.exe sur Windows), si elle peut être
    remplacée ; None pour Biblio lancé depuis le code source."""
    if not getattr(sys, "frozen", False):
        return None
    exe = Path(sys.executable).resolve()
    target = exe.parents[2] if sys.platform == "darwin" else exe.parent
    if sys.platform == "darwin" and target.suffix != ".app":
        return None
    return target if sys.platform in SUFFIX and _writable(target.parent) else None


def _writable(folder: Path) -> bool:
    try:
        with tempfile.TemporaryFile(dir=folder):
            return True
    except OSError:
        return False


def _download(asset: dict, folder: Path) -> Path:
    data = http.download(asset["browser_download_url"], MAX_SIZE)
    digest = (asset.get("digest") or "").removeprefix("sha256:")
    if not digest or hashlib.sha256(data).hexdigest() != digest:
        raise ValueError("Fichier de mise à jour invalide (empreinte SHA-256 absente ou différente) : rien n'a été changé")
    path = folder / asset["name"]
    path.write_bytes(data)
    return path


def install(home: Path) -> dict:
    """Télécharge et vérifie la nouvelle version, puis lance son installation ; Biblio redémarre de lui-même dans
    les secondes qui suivent. ValueError si une installation en un clic n'est pas possible ici."""
    app = _app()
    if app is None:
        raise ValueError("Mise à jour en un clic impossible ici (Biblio lancé depuis le code source, ou dossier de "
                         "l'application protégé en écriture) : télécharger la nouvelle version à la main")
    release = _latest()
    asset = release and _asset(release.get("assets", []))
    if not asset or _parse(release["tag_name"]) <= _parse(__version__):
        raise ValueError("Aucune nouvelle version à installer")
    version = release["tag_name"].lstrip("v")
    folder = home / "updates"
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    package = _download(asset, folder)
    if sys.platform == "darwin":
        _install_mac(app, package, folder, version)
    else:  # Windows : l'installeur ferme Biblio, le remplace puis le relance (biblio.iss : CloseApplications)
        subprocess.Popen([str(package), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
                         creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)  # fmt: skip
    return {"installing": version}


def _install_mac(app: Path, dmg: Path, folder: Path, version: str) -> None:
    mount, staged = folder / "mnt", app.parent / ".Biblio-update.app"
    shutil.rmtree(staged, ignore_errors=True)
    subprocess.run(["hdiutil", "attach", str(dmg), "-nobrowse", "-readonly", "-noautoopen", "-mountpoint", str(mount)],
                   check=True, capture_output=True)  # fmt: skip
    try:
        subprocess.run(["ditto", str(mount / "Biblio.app"), str(staged)], check=True, capture_output=True)
    finally:
        subprocess.run(["hdiutil", "detach", str(mount), "-force"], capture_output=True)
    found = plistlib.loads((staged / "Contents" / "Info.plist").read_bytes()).get("CFBundleShortVersionString")
    if found != version:
        shutil.rmtree(staged, ignore_errors=True)
        raise ValueError(f"Le fichier téléchargé contient la version {found}, pas {version} : rien n'a été changé")
    exe = app / "Contents" / "MacOS" / "Biblio"
    # Après la réponse à l'interface : fermer la fenêtre et le serveur de cette version (les serveurs MCP lancés par
    # Claude, « Biblio mcp », continuent), échanger les applications (même disque : renommages instantanés), relancer.
    script = folder / "install.sh"
    script.write_text(f'''#!/bin/sh
sleep 1
pkill -f '^{exe}( serve)?$'
for i in 1 2 3 4 5 6 7 8 9 10; do pgrep -f '^{exe}( serve)?$' >/dev/null || break; sleep 0.5; done
pkill -9 -f '^{exe}( serve)?$'  # fenêtre restée ouverte (bloquée en se fermant) : fermée de force
sleep 1
mv "{app}" "{folder}/old.app" && mv "{staged}" "{app}" || mv "{folder}/old.app" "{app}"
rm -rf "{folder}/old.app"
open "{app}"
''')
    subprocess.Popen(["/bin/sh", str(script)], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
