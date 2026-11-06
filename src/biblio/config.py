"""Configuration : `config.toml` dans le dossier de données, créé au premier lancement."""

import os
import secrets
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

TEMPLATE = """\
# Configuration de Biblio

[server]
host = "127.0.0.1"   # mettre "0.0.0.0" pour ouvrir l'accès au réseau local
port = 8765
token = "{token}"   # jeton de l'API : en-tête « Authorization: Bearer <jeton> »

[sources]
contact_email = ""   # recommandé : Crossref, OpenAlex et NCBI le demandent ; indispensable pour Unpaywall
ncbi_api_key = ""    # facultatif : fait passer la limite PubMed de 3 à 10 requêtes/s
openalex_api_key = ""         # facultatif, gratuit (openalex.org/settings/api) : sans clé, petit budget quotidien partagé
semanticscholar_api_key = ""  # facultatif, gratuit sur demande : sans clé, Semantic Scholar refuse souvent (HTTP 429)

[inbox]
# Importer automatiquement les PDF d'articles téléchargés dans ce dossier (ceux qui ne sont pas reconnus comme
# des articles sont laissés de côté). Les fichiers du dossier ne sont jamais déplacés ni supprimés.
watch_downloads = false
downloads_dir = "~/Downloads"

[semantic]
# Recherche par le sens (modèle bge-m3, multilingue, exécuté localement : aucun texte n'est envoyé).
# Le modèle (~2,3 Go) est téléchargé une fois au premier démarrage du serveur.
enabled = true
models_dir = ""   # vide : dossier « models » du dossier de données
"""


def home() -> Path:
    """Dossier de données ; `BIBLIO_HOME` le remplace (tests, essais)."""
    if env := os.environ.get("BIBLIO_HOME"):
        return Path(env)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Biblio"
    if sys.platform == "win32":
        return Path(os.environ["APPDATA"]) / "Biblio"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "biblio"


@dataclass(frozen=True)
class Config:
    home: Path
    host: str
    port: int
    token: str
    contact_email: str
    api_keys: dict[str, str]  # ncbi, openalex, semanticscholar
    watch_downloads: bool = False
    downloads_dir: Path = Path.home() / "Downloads"
    semantic: bool = True
    models_dir_setting: str = ""

    @property
    def models_dir(self) -> Path:
        return Path(self.models_dir_setting).expanduser() if self.models_dir_setting else self.home / "models"

    @property
    def path(self) -> Path:
        return self.home / "config.toml"

    @property
    def db_path(self) -> Path:
        return self.home / "biblio.sqlite"

    @property
    def client_url(self) -> str:
        host = "127.0.0.1" if self.host in ("0.0.0.0", "::") else self.host
        return f"http://{host}:{self.port}"


def load() -> Config:
    root = home()
    path = root / "config.toml"
    if not path.exists():
        root.mkdir(parents=True, exist_ok=True)
        path.write_text(TEMPLATE.format(token=secrets.token_urlsafe(32)), encoding="utf-8")
        path.chmod(0o600)
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    server, sources, inbox = data.get("server", {}), data.get("sources", {}), data.get("inbox", {})
    semantic = data.get("semantic", {})
    return Config(
        home=root,
        host=server.get("host", "127.0.0.1"),
        port=int(server.get("port", 8765)),
        token=server["token"],
        contact_email=sources.get("contact_email", ""),
        api_keys={name: sources.get(f"{name}_api_key", "") for name in ("ncbi", "openalex", "semanticscholar")},
        watch_downloads=bool(inbox.get("watch_downloads", False)),
        downloads_dir=Path(inbox.get("downloads_dir", "~/Downloads")).expanduser(),
        semantic=bool(semantic.get("enabled", True)),
        models_dir_setting=semantic.get("models_dir", ""),
    )
