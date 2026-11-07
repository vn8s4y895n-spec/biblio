"""Biblio : gestionnaire de bibliographie local, autonome et ouvert."""

__version__ = "0.1.0"


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
