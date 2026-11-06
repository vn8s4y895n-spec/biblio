# Biblio

Gestionnaire de bibliographie local, autonome et ouvert, pensé pour la recherche en santé.
Le projet avance par phases : voir [PLAN.md](PLAN.md). Ce README décrit ce qui fonctionne **aujourd'hui** (phases 1 et 2).

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

### Liste des revues recommandées

Chaque article porte un statut de revue, calculé avec la [liste des revues recommandées](https://sante.sorbonne-universite.fr/recherche/liste-des-revues-presumees-non-predatrices) de la Conférence des doyens et du CNU santé :

- ● revue recommandée
- ▲ retirée de la liste (n'est plus recommandée)
- ○ hors liste
- ◇ preprint
- ? revue non identifiée

```bash
uv run biblio journals                          # version en vigueur, dernière vérification, anomalies
uv run biblio journals update                   # vérifier en ligne maintenant
uv run biblio journals lookup "Clin Infect Dis" # une revue est-elle dans la liste ? (ISSN, NLMID ou abréviation)
uv run biblio journals import liste.xlsx        # import manuel d'un fichier
uv run biblio list --revue recommended          # filtrer (répétable : --revue not_listed --revue withdrawn)
```

- **Disponible hors ligne** : la liste du 1er octobre 2026 est livrée avec Biblio.
- **Mise à jour automatique** : le serveur vérifie la page de Sorbonne Université une fois par semaine. Il ne télécharge le fichier que s'il a changé, et uniquement depuis `sante.sorbonne-universite.fr`.
- **En cas d'échec** (page modifiée, fichier illisible) : la version précédente reste en vigueur et l'erreur est affichée par `biblio journals`.
- **Statut toujours à jour** : il est recalculé à chaque lecture, donc toute la bibliothèque suit une nouvelle liste sans modification des notices.
- **Anomalie du fichier actuel** : dans le fichier du 01-10-26, la colonne « ISSN Electronic » est décalée d'une ligne à partir de la ligne 2650 (« Molecular Vision » → fin). Biblio le détecte et le signale. Le statut reste fiable, car la correspondance se fait d'abord par NLMID et ISSN papier, mais le nom de revue affiché peut être celui de la ligne voisine quand seul l'ISSN électronique est connu.

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
| GET | `/api/v1/works?limit=&offset=&journal_status=` | Lister (filtre répétable sur le statut de revue) |
| GET | `/api/v1/works/{ref}` | Lire (id, clé, DOI, PMID) |
| GET | `/api/v1/works/{ref}/changes` | Journal des modifications |
| GET | `/api/v1/journals` | Liste des revues : version en vigueur, historique, dernière vérification |
| POST | `/api/v1/journals/update` | Vérifier en ligne (502 si échec, la liste précédente reste en place) |
| POST | `/api/v1/journals/import?filename=` | Importer un .xlsx (corps brut de la requête) |
| GET | `/api/v1/journals/lookup?issn=&nlmid=&abbrev=` | Statut d'une revue |
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

Les tests n'accèdent jamais au réseau :
- les réponses de Crossref et PubMed sont enregistrées dans `tests/fixtures/` (essai MERINO, JAMA 2018) ;
- le site de Sorbonne Université est simulé, avec des fichiers .xlsx générés par les tests.
