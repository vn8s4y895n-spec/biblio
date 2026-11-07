"""Configuration : `config.toml` dans le dossier de données, créé au premier lancement."""

import json
import os
import secrets
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

TEMPLATE = """\
# Configuration de Biblio (modifiable ici ou dans les réglages de l'application)

[server]
host = {host}   # mettre "0.0.0.0" pour ouvrir l'accès au réseau local
port = {port}
token = {token}   # jeton de l'API : en-tête « Authorization: Bearer <jeton> »

[sources]
contact_email = {contact_email}   # recommandé : Crossref, OpenAlex et NCBI le demandent ; indispensable pour Unpaywall
ncbi_api_key = {ncbi}    # facultatif : fait passer la limite PubMed de 3 à 10 requêtes/s
openalex_api_key = {openalex}         # facultatif, gratuit (openalex.org/settings/api) : sans clé, petit budget quotidien partagé
semanticscholar_api_key = {semanticscholar}  # facultatif, gratuit sur demande : sans clé, Semantic Scholar refuse souvent (HTTP 429)

[inbox]
# Importer automatiquement les PDF d'articles téléchargés dans ce dossier (ceux qui ne sont pas reconnus comme
# des articles sont laissés de côté). Les fichiers du dossier ne sont jamais déplacés ni supprimés.
watch_downloads = {watch_downloads}
downloads_dir = {downloads_dir}

[semantic]
# Recherche par le sens (modèle bge-m3, multilingue, exécuté localement : aucun texte n'est envoyé).
# Le modèle (~2,3 Go) se télécharge une fois, depuis les réglages ou avec `biblio semantic download`.
enabled = {semantic}
models_dir = {models_dir}   # vide : dossier « models » du dossier de données

[ui]
language = {language}   # "fr", "en" ou vide (langue du système)
theme = {theme}   # "system", "light" ou "dark"
citation_style = {citation_style}   # style CSL de la touche « c » et des exports Markdown (vide : Vancouver)

[llm]
# Intelligence artificielle (facultative) : requête PubMed, réponse rédigée et citée, résumés.
# Toute API « chat completions » compatible OpenAI : Ollama en local (gratuit, rien ne sort de l'ordinateur),
# Mistral, OpenAI… La clé éventuelle est rangée dans le trousseau du système, jamais dans ce fichier.
enabled = {llm_enabled}
provider = {llm_provider}   # ollama, mistral ou custom
base_url = {llm_base_url}   # vide : adresse par défaut du fournisseur
model = {llm_model}   # vide : modèle par défaut du fournisseur
"""

API_KEYS = ("ncbi", "openalex", "semanticscholar")


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
    language: str = ""
    theme: str = "system"
    citation_style: str = ""
    llm_enabled: bool = False
    llm_provider: str = "ollama"
    llm_base_url: str = ""
    llm_model: str = ""

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


def save(cfg: Config) -> None:
    """Réécrit config.toml (lisible par le seul utilisateur : il contient le jeton et les clés)."""
    def value(v) -> str:  # json.dumps produit une chaîne TOML valide
        return str(v).lower() if isinstance(v, bool) else json.dumps(str(v), ensure_ascii=False)

    downloads = str(cfg.downloads_dir).replace(str(Path.home()), "~", 1)
    text = TEMPLATE.format(
        host=value(cfg.host), port=cfg.port, token=value(cfg.token), contact_email=value(cfg.contact_email),
        **{name: value(cfg.api_keys.get(name, "")) for name in API_KEYS},
        watch_downloads=value(cfg.watch_downloads), downloads_dir=value(downloads), semantic=value(cfg.semantic),
        models_dir=value(cfg.models_dir_setting), language=value(cfg.language), theme=value(cfg.theme),
        citation_style=value(cfg.citation_style), llm_enabled=value(cfg.llm_enabled),
        llm_provider=value(cfg.llm_provider), llm_base_url=value(cfg.llm_base_url), llm_model=value(cfg.llm_model),
    )  # fmt: skip
    cfg.home.mkdir(parents=True, exist_ok=True)
    tmp = cfg.path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(cfg.path)


def load() -> Config:
    root = home()
    path = root / "config.toml"
    if not path.exists():
        save(Config(home=root, host="127.0.0.1", port=8765, token=secrets.token_urlsafe(32), contact_email="",
                    api_keys={}))  # fmt: skip
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    server, sources, inbox = data.get("server", {}), data.get("sources", {}), data.get("inbox", {})
    semantic, ui, llm = data.get("semantic", {}), data.get("ui", {}), data.get("llm", {})
    return Config(
        home=root,
        host=server.get("host", "127.0.0.1"),
        port=int(server.get("port", 8765)),
        token=server["token"],
        contact_email=sources.get("contact_email", ""),
        api_keys={name: sources.get(f"{name}_api_key", "") for name in API_KEYS},
        watch_downloads=bool(inbox.get("watch_downloads", False)),
        downloads_dir=Path(inbox.get("downloads_dir", "~/Downloads")).expanduser(),
        semantic=bool(semantic.get("enabled", True)),
        models_dir_setting=semantic.get("models_dir", ""),
        language=ui.get("language", ""),
        theme=ui.get("theme", "system"),
        citation_style=ui.get("citation_style", ""),
        llm_enabled=bool(llm.get("enabled", False)),
        llm_provider=llm.get("provider", "ollama"),
        llm_base_url=llm.get("base_url", ""),
        llm_model=llm.get("model", ""),
    )
