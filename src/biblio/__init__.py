"""Biblio : gestionnaire de bibliographie local, autonome et ouvert."""

__version__ = "0.1.4"
REPOSITORY = "vn8s4y895n-spec/biblio"  # GitHub : code, versions publiées (mises à jour)


def command() -> list[str]:
    """Commande qui lance Biblio : l'exécutable de l'application empaquetée (PyInstaller), sinon Python."""
    import shutil
    import sys
    from pathlib import Path

    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [shutil.which("biblio") or str(Path(sys.executable).with_name("biblio"))]


def main() -> None:
    from .cli import app

    app()
