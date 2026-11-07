"""Sauvegarde et restauration du dossier de données.

Une sauvegarde est une archive `.tar.gz` : copie cohérente de la base (API de sauvegarde de SQLite, même pendant
que Biblio écrit), PDF, notes, styles, versions de la liste des revues et config.toml (qui contient le jeton et les
clés des bases : l'archive est créée lisible par toi seul). Le modèle sémantique n'y est pas : il se retélécharge.
Une sauvegarde automatique est faite chaque jour dans `backups/` ; les 7 dernières sont gardées.
"""

import io
import json
import shutil
import sqlite3
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from . import __version__

CONTENTS = ("library", "notes", "styles", "journals", "config.toml")  # en plus de biblio.sqlite
KEEP = 7
MANIFEST = "biblio-backup.json"


def folder(home: Path) -> Path:
    return home / "backups"


def create(db_path: Path, home: Path, dest: Path | None = None) -> Path:
    """Crée l'archive dans `dest` (dossier ; défaut : backups/) et renvoie son chemin."""
    dest = dest or folder(home)
    dest.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    path = dest / f"biblio-{stamp}.tar.gz"
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "biblio.sqlite"
        src, out = sqlite3.connect(db_path), sqlite3.connect(copy)
        try:
            src.backup(out)
        finally:
            src.close()
            out.close()
        manifest = json.dumps({"app_version": __version__, "created_at": datetime.now(UTC).isoformat(timespec="seconds")})
        partial = path.with_suffix(".part")
        with tarfile.open(partial, "w:gz") as tar:
            info = tarfile.TarInfo(MANIFEST)
            info.size = len(manifest.encode())
            tar.addfile(info, io.BytesIO(manifest.encode()))
            tar.add(copy, "biblio.sqlite")
            for name in CONTENTS:
                if (home / name).exists():
                    tar.add(home / name, name)
        partial.chmod(0o600)
        partial.rename(path)
    return path


def backups(home: Path) -> list[dict]:
    return [{"name": p.name, "path": str(p), "size": p.stat().st_size}
            for p in sorted(folder(home).glob("biblio-*.tar.gz"), reverse=True)]  # fmt: skip


def daily(db_path: Path, home: Path) -> Path | None:
    """Sauvegarde du jour si elle n'existe pas encore ; ne garde que les `KEEP` dernières."""
    today = datetime.now().strftime("%Y-%m-%d")
    if any(b["name"].startswith(f"biblio-{today}") for b in backups(home)):
        return None
    path = create(db_path, home)
    for old in backups(home)[KEEP:]:
        Path(old["path"]).unlink()
    return path


def check(archive: Path) -> dict:
    """Vérifie qu'une archive est une sauvegarde Biblio lisible ; renvoie son manifeste."""
    try:
        with tarfile.open(archive, "r:gz") as tar:
            names = tar.getnames()
            if MANIFEST not in names or "biblio.sqlite" not in names:
                raise ValueError(f"{archive.name} n'est pas une sauvegarde Biblio")
            return json.loads(tar.extractfile(MANIFEST).read())
    except (tarfile.TarError, OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Sauvegarde illisible : {exc}") from exc


def restore(archive: Path, home: Path) -> Path:
    """Remplace les données par celles de l'archive (le serveur doit être arrêté). Les données actuelles ne sont pas
    effacées : elles sont mises de côté dans `avant-restauration-<date>/`, dont le chemin est renvoyé."""
    check(archive)
    aside = home / f"avant-restauration-{datetime.now().strftime('%Y-%m-%d-%H%M%S')}"
    aside.mkdir(parents=True)
    for name in ("biblio.sqlite", "biblio.sqlite-wal", "biblio.sqlite-shm", *CONTENTS):
        if (home / name).exists():
            shutil.move(home / name, aside / name)
    with tarfile.open(archive, "r:gz") as tar:
        tar.extractall(home, filter="data")  # refuse les chemins absolus, « .. » et liens hors du dossier
    (home / MANIFEST).unlink(missing_ok=True)
    return aside
