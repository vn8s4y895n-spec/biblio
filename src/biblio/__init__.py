"""Biblio : gestionnaire de bibliographie local, autonome et ouvert."""

__version__ = "0.1.0"


def main() -> None:
    from .cli import app

    app()
