# Biblio

Gestionnaire de bibliographie local, autonome et ouvert, pensé pour la recherche en santé.
Le projet avance par phases : voir [PLAN.md](PLAN.md). Ce README décrit ce qui fonctionne **aujourd'hui** (phase 1).

## Installation (développement)

Biblio utilise [uv](https://docs.astral.sh/uv/) et Python 3.13.

```bash
python3 -m pip install --user uv     # ou : brew install uv
uv sync
uv run biblio --help
```

**Si le dossier du projet est synchronisé par iCloud** (Documents, Bureau) : iCloud peut masquer des fichiers de l'environnement Python, que Python 3.13 ignore alors (`No module named 'biblio'`). Placez l'environnement hors synchro :

```bash
rm -rf .venv && mkdir .venv.nosync && ln -s .venv.nosync .venv && uv sync
```

## Usage

```bash
uv run biblio add 10.1001/jama.2018.12163       # par DOI (nu, doi:…, ou URL doi.org)
uv run biblio add 30208454                      # par PMID (ou URL pubmed)
uv run biblio list                              # notices, les plus récentes d'abord
uv run biblio get harris2018effect              # notice (clé, id, DOI ou PMID)
uv run biblio get harris2018effect --json       # notice complète en JSON
uv run biblio serve                             # serveur au premier plan
uv run biblio stop                              # arrête le serveur
uv run biblio token                             # jeton de l'API
```

- La CLI est un client de l'API REST. Si le serveur ne tourne pas, elle le démarre en arrière-plan (journal : `server.log` dans le dossier de données). Il reste actif jusqu'à `biblio stop`.
- Un article est cherché sur **PubMed** (résumé structuré, MeSH, type d'étude) et complété par **Crossref** (ISSN papier, licence). Si une source est injoignable, l'import se fait avec l'autre et un avertissement s'affiche.
- **Aucun doublon** : un même article ajouté par DOI, PMID ou URL renvoie la notice existante, éventuellement complétée.

## API REST

Documentation interactive : <http://127.0.0.1:8765/docs> (schéma : `/openapi.json`). Toutes les routes, sauf `/api/v1/health`, exigent le jeton :

```bash
TOKEN=$(uv run biblio token)
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8765/api/v1/works
curl -H "Authorization: Bearer $TOKEN" -X POST -H 'Content-Type: application/json' \
     -d '{"identifier": "10.1001/jama.2018.12163"}' http://127.0.0.1:8765/api/v1/works
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8765/api/v1/works/10.1001/jama.2018.12163/changes
```

| Méthode | Route | Rôle |
|---|---|---|
| GET | `/api/v1/health` | État du serveur (public) |
| POST | `/api/v1/works` | Ajouter par identifiant (201 créé, 200 déjà présent) |
| GET | `/api/v1/works?limit=&offset=` | Lister |
| GET | `/api/v1/works/{ref}` | Lire (id, clé, DOI, PMID) |
| GET | `/api/v1/works/{ref}/changes` | Journal des modifications |
| POST | `/api/v1/shutdown` | Arrêter le serveur |

L'en-tête facultatif `X-Biblio-Client` (`api`, `cli`, `web`, `mcp`…) est inscrit dans le journal des modifications.

## Données et configuration

Dossier de données : `~/Library/Application Support/Biblio` (macOS), `%APPDATA%\Biblio` (Windows), `~/.local/share/biblio` (Linux). La variable `BIBLIO_HOME` le remplace.

- `config.toml` : créé au premier lancement (droits 600). Contient le port, le jeton de l'API et, à remplir, l'e-mail de contact demandé par Crossref et NCBI, ainsi qu'une clé NCBI facultative.
- `biblio.sqlite` : la base (SQLite en mode WAL), documentée dans [SCHEMA.md](SCHEMA.md). **Ne la placez pas dans un dossier synchronisé** (iCloud Drive, OneDrive) : la synchro peut la corrompre.

## Sauvegarde

En attendant `biblio backup` (phase 10) : arrêtez le serveur (`biblio stop`), puis copiez le dossier de données entier.

## Tests

```bash
uv run pytest
```

Les tests n'accèdent jamais au réseau : les réponses de Crossref et PubMed sont enregistrées dans `tests/fixtures/` (essai MERINO, JAMA 2018).
