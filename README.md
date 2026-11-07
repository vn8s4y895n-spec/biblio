# Biblio

Gestionnaire de bibliographie local, autonome et ouvert, pensé pour la recherche en santé.
Le projet avance par phases : voir [PLAN.md](PLAN.md). Ce README décrit ce qui fonctionne **aujourd'hui** (phases 1 à 7).

## Installation (développement)

Biblio utilise [uv](https://docs.astral.sh/uv/) et Python 3.13.

```bash
python3 -m pip install --user uv     # ou : brew install uv
uv sync
uv run biblio --help
```

L'interface (Svelte, dans `ui/`) se compile une fois avec Node.js ; le résultat va dans `src/biblio/web/`, que le serveur sert sur `/`. Les utilisateurs de l'application n'auront pas besoin de Node.

```bash
npm --prefix ui install && npm --prefix ui run build
npm --prefix ui run dev      # développement : rechargement à chaud, l'API du serveur local est relayée
```

**Si le dossier du projet est synchronisé par iCloud** (Documents, Bureau) : iCloud peut masquer des fichiers de l'environnement Python, que Python 3.13 ignore alors (`No module named 'biblio'`). Placez l'environnement hors synchro :

```bash
rm -rf .venv && mkdir .venv.nosync && ln -s .venv.nosync .venv && uv sync
```

## L'application

```bash
uv run biblio app              # fenêtre native (démarre le serveur ; l'arrête à la fermeture s'il l'a lancé)
uv run biblio app --browser    # même interface dans le navigateur
```

- **Question** : une question en langage courant (ou une requête PubMed), dans les bases en ligne ou dans ta bibliothèque. Une question en langage courant est réduite à ses mots-clés, traduits en anglais pour les termes courants d'infectiologie (vancomycine → vancomycin, BLSE → ESBL, E. faecium → Enterococcus faecium) ; la requête est assouplie si elle trouve moins de 10 résultats, et les sources qui contiennent moins de la moitié des mots-clés sont écartées. Une requête en syntaxe PubMed est envoyée telle quelle. La réponse est faite des phrases des résumés les plus proches de la question, chacune avec le numéro de sa source ; cliquer le numéro fait défiler jusqu'à la source et surligne la phrase. Sans IA pour l'instant (phase 9).
- **Sources**, à droite : statut de revue (filtre « revues recommandées » actif par défaut ; les résultats masqués sont comptés), accès libre, type d'étude, effectif, **résumé Biblio** (la conclusion des auteurs), ajout à la bibliothèque (`＋` ou « Tout ajouter »). La requête envoyée aux bases est modifiable.
- **Question d'origine** : un article ajouté depuis l'écran Question garde la question posée ; sa fiche l'affiche (« Ajouté depuis la question »), et un clic la repose.
- **Enregistrer la recherche** garde la question, la réponse et les sources ; les recherches enregistrées s'affichent sur l'écran d'accueil.
- **Bibliothèque** en trois panneaux redimensionnables : filtres (statut de lecture, collections, tags, statut de revue), liste (recherche plein texte et par le sens, sélection multiple, glisser une notice sur une collection), fiche (statut de lecture, note étoilée, tags, collections, résumé, MeSH, **lecteur PDF**, **note Markdown**).
- **Glisser-déposer** n'importe où : PDF, ou fichiers .bib, .ris, .nbib, .json.
- **Citer, exporter, envoyer** : la fiche affiche la référence dans le style choisi (Vancouver par défaut) ; boutons et sélection multiple pour exporter (BibTeX, RIS, CSL-JSON, liste Markdown), envoyer dans Zotero ou créer un paquet `.biblio`. Une recherche enregistrée s'envoie aussi en paquet. Un paquet `.biblio` déposé sur la fenêtre s'ouvre en aperçu avant import.
- **Clavier** : `/` question ou recherche, `j` / `k` élément suivant / précédent, `a` ajouter la source, `c` copier la citation, `z` envoyer dans Zotero, `x` sélectionner, `Échap` quitter le champ, `⌘S` enregistrer la note.
- **Réglages** : langue (français, anglais ou celle du système), thème (clair, sombre ou celui du système), e-mail de contact et clés des bases, surveillance des Téléchargements, téléchargement du modèle sémantique et avancement de l'indexation, vérification de la liste des revues. Ils sont écrits dans `config.toml`.
- **Jeton** : l'interface le reçoit dans l'adresse (`#token=…`, partie jamais envoyée au serveur). Les fichiers de l'interface sont servis sans jeton ; aucune donnée ne l'est.

## Ligne de commande

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

### Imports, PDF, notes

```bash
uv run biblio add PMC6143100 arXiv:2306.04338 https://www.nature.com/articles/s41467-026-75634-0
uv run biblio add 10.1038/s41598-025-29778-6       # avec le PDF s'il est en accès libre (--no-pdf pour s'en passer)
uv run biblio import article.pdf zotero.bib export.ris pubmed.nbib items.json
uv run biblio files                                # PDF dont la notice n'a pas été trouvée
uv run biblio attach 501f83bb 10.1001/jama.2018.12163   # saisie assistée : rattacher un PDF
uv run biblio find "wastewater blaCTX-M household" # plein texte : notices, résumés, notes, PDF
uv run biblio note harris2018effect --edit         # note Markdown (notes/<clé>.md, liens [[clé]])
uv run biblio duplicates                           # doublons probables
uv run biblio merge <garder> <supprimer>           # fusion (ou --dismiss : ce ne sont pas les mêmes)
uv run biblio pdf harris2018effect                 # chemin du PDF, ou recherche d'un PDF libre
uv run biblio pdf harris2018effect --open          # l'ouvrir, ou ouvrir la page de l'article dans le navigateur
```

**Identifiants acceptés**
- DOI, PMID, PMCID et arXiv, nus ou en URL : doi.org, PubMed, PMC, Europe PMC, arXiv, bioRxiv et medRxiv (la version et le format de l'URL sont retirés).
- Toute autre page d'article : Biblio y lit les balises `citation_doi` ou `citation_pmid`.

**PDF**
- **Rangement** : par empreinte, dans `library/ab/cd/<sha256>.pdf`. Un même fichier n'est jamais copié deux fois.
- **Repérage de la notice** : Biblio cherche le DOI dans les métadonnées XMP et du document, puis dans les deux premières pages. Il **vérifie** ensuite que le titre de la notice trouvée correspond au texte du PDF, car un DOI cité en première page n'est pas celui de l'article.
- **Sans correspondance** : le PDF est rangé, et des notices Crossref proches du titre sont proposées (`biblio attach`).
- **Texte intégral** : il est extrait page par page et découpé en passages. Un PDF scanné, sans couche texte, n'est pas lu (pas d'OCR).
- **PDF à l'ajout** : Biblio essaie, dans l'ordre et jusqu'au premier vrai PDF :
  1. PMC (via Europe PMC) ;
  2. **la page de l'éditeur**, comme Zotero : le DOI mène à la page de l'article, Biblio y lit le lien PDF que l'éditeur déclare (`citation_pdf_url`) et le télécharge dans la même session ;
  3. OpenAlex ;
  4. Unpaywall (si `contact_email` est renseigné).

  Le refus d'un site n'empêche pas l'ajout de la notice : il est signalé, source par source.
- **Sites qui refusent** (HTTP 403, par exemple JAMA ou Europe PMC) : Biblio ne contourne pas leurs protections. `biblio pdf <clé> --open` ouvre alors la page de l'article dans ton navigateur ; tu télécharges le PDF avec ta session (et ton accès institutionnel éventuel), puis tu le déposes dans la boîte d'entrée, ou tu le laisses dans les Téléchargements si leur surveillance est activée.

**Boîte d'entrée** : tout PDF déposé dans `inbox/` (dossier de données) est importé dans les 5 secondes, puis retiré de la boîte. Un fichier refusé va dans `inbox/refusés/`.

**Téléchargements (facultatif)** : avec `watch_downloads = true` dans `config.toml` (section `[inbox]`), Biblio examine les PDF qui arrivent dans `downloads_dir` (par défaut `~/Downloads`).
- **Tri** : seuls les PDF reconnus comme des articles (DOI trouvé et vérifié) sont importés ; les autres (factures, formulaires…) sont laissés de côté.
- **Tes fichiers** : ils ne sont ni déplacés ni supprimés.
- **Historique** : les fichiers déjà présents avant l'activation sont ignorés.
- **macOS** : il demandera la première fois l'autorisation d'accéder au dossier Téléchargements.

**Fichiers bibliographiques** (BibTeX, RIS, MEDLINE/nbib, CSL-JSON : exports de Zotero, EndNote, Mendeley, PubMed…)
- **Import hors ligne** : le format est détecté automatiquement.
- **Notice déjà présente** : même identifiant, ou même titre, année et premier auteur sans identifiant. Elle est complétée, jamais dupliquée.
- **Même titre avec un identifiant différent** : c'est un doublon probable. Il est signalé, à fusionner ou à écarter.

**Notes** : elles sont rangées dans `notes/<clé>.md`, un dossier qui s'ouvre comme coffre Obsidian.
- **Modifications** : celles faites hors de Biblio (Obsidian, autre éditeur) sont réindexées automatiquement.
- **Liens** : les liens `[[clé]]` donnent les rétroliens (« citée par »).

**Recherche locale** (`biblio find`)
- **Portée** : notices, résumés, notes et textes intégraux, avec les extraits surlignés. Les listes de références en fin d'article ne sont pas indexées.
- **Deux moteurs fusionnés** (RRF) :
  - **le plein texte** (BM25) : tous les mots exigés, à défaut au moins un ; les mots outils d'une question (« de », « la », « the »…) sont ignorés ; la syntaxe FTS5 est acceptée (`"phrase"`, `OR`, `NOT`, `préfixe*`) ;
  - **le sens** : le modèle local bge-m3, multilingue. Une question en français (« études sur la transmission de BLSE en foyer familial ») trouve des passages en anglais. Un passage trouvé par le sens seul est marqué ≈.
- **Choix du moteur** : `--engine lexical` (plein texte seul) ou `--engine semantic` (sens seul).
- **Vitesse** : 29 ms en médiane sur 10 000 notices (30 000 passages), encodage de la question compris.

### Citations, exports, Zotero, paquets .biblio

```bash
uv run biblio cite harris2018effect                 # référence formatée (style des réglages ; Vancouver par défaut)
uv run biblio cite harris2018effect --style apa
uv run biblio styles                                # styles disponibles
uv run biblio styles-add mon-style.csl              # ajouter un style (zotero.org/styles)
uv run biblio export -f bibtex -o refs.bib          # toute la bibliothèque (ou des clés, ou -c « collection »)
uv run biblio export -c "Thèse" -f markdown         # liste formatée, avec liens DOI et [[clé]]
uv run biblio zotero -c "Thèse"                     # envoyer dans Zotero (ouvert), avec notes, tags et PDF
uv run biblio pack -c "Thèse" -o these.biblio --title "Pour Marie"
uv run biblio import these.biblio                   # chez le destinataire
```

- **Styles** : Vancouver des bibliothèques universitaires de médecine (par défaut) et APA 7, fournis ; tout style indépendant de <https://www.zotero.org/styles> s'ajoute. Le style NLM d'origine n'est pas fourni : citeproc-py le rend mal (espaces perdus).
- **`library.bib`** : toute la bibliothèque en BibTeX, dans le dossier de données, réécrite dès qu'une notice change (pour LaTeX, Pandoc, Obsidian). Les auteurs collectifs (« MERINO Trial Investigators and … ») sont protégés par des accolades.
- **Zotero** : par son serveur local (`127.0.0.1:23119`, celui de son extension de navigateur), sans compte ni clé. Zotero doit être ouvert ; les notices arrivent dans la collection sélectionnée dans Zotero, avec leurs tags, leur note et leur PDF.
- **Paquets `.biblio`** : un fichier ZIP (`manifest.json`, `works.json`, `searches.json`, `files/<sha256>.pdf`) avec les notices, PDF, notes, tags, collections, questions d'origine et recherches enregistrées. Le statut de lecture et les étoiles restent personnels. À l'import : un article déjà présent est complété (jamais dupliqué), une note différente est ajoutée à la suite, un PDF dont l'empreinte ne correspond pas est refusé.

### Recherche par le sens (modèle local)

```bash
uv run biblio semantic            # état du modèle et de l'indexation
uv run biblio semantic download   # télécharger le modèle (~2,3 Go, une seule fois)
```

- **Modèle** : [bge-m3](https://huggingface.co/BAAI/bge-m3) (licence MIT), version ONNX officielle, à version figée. Il est exécuté sur la machine avec ONNX Runtime, sans PyTorch : **aucun texte n'est envoyé à un tiers**.
- **Téléchargement** : il se fait à la demande, pas automatiquement, vu sa taille.
- **Indexation** : une fois le modèle téléchargé, le serveur calcule en arrière-plan les vecteurs des passages (métadonnées et résumés d'abord), et reprend où il en était après un arrêt.
  - **Vitesse** : environ 5 passages de texte intégral par seconde sur un Mac récent ; la première indexation d'une grosse bibliothèque prend donc plusieurs heures.
  - **En attendant** : les passages non encore indexés restent trouvables en plein texte.
- **Recherche en ligne** : le modèle reclasse aussi ses 50 premiers résultats (proximité entre la question et le titre + début du résumé), ce qui ajoute moins d'une seconde.
- **Désactiver** : `enabled = false` dans la section `[semantic]` de `config.toml`. `models_dir` choisit le dossier du modèle.

### Recherche en ligne

```bash
uv run biblio search '(ESBL[tiab] OR "extended-spectrum beta-lactamase"[tiab]) AND household[tiab] AND transmission[tiab]'
uv run biblio search "carbapenemase colonization ICU" --from 2019 --type meta-analysis --type systematic-review
uv run biblio search "..." --oa --mesh "Escherichia coli Infections" --journal "Clin Infect Dis"
uv run biblio search "..." --source pubmed --source europepmc --all-journals --json
uv run biblio oa 10.1016/j.cmi.2016.08.021        # version libre et légale (PMC, Unpaywall)
```

**Sources et requêtes**
- PubMed, Europe PMC, OpenAlex, Crossref et Semantic Scholar sont interrogés **en parallèle**.
- **PubMed** reçoit la requête telle quelle : `[MeSH]`, `[tiab]` et les booléens fonctionnent.
- **Europe PMC et OpenAlex** reçoivent la requête sans les étiquettes entre crochets.
- **Crossref et Semantic Scholar** reçoivent du texte libre.

**Fusion et classement**
- **Doublons** : ils sont fusionnés (DOI > PMID > PMCID > titre + année), en gardant le meilleur de chaque source : MeSH et résumé de PubMed, citations d'OpenAlex…
- **Classement** : par fusion des rangs (RRF). Un article trouvé par plusieurs sources monte.
- **Lettres affichées** : elles indiquent les sources (P E O C S) ; ★ marque un article déjà dans la bibliothèque.

**Filtres**
- **Revues** : par défaut, seules les revues recommandées sont montrées. Le nombre de résultats masqués est affiché, et `--all-journals` les montre.
- **Date et MeSH** : transmis aux sources qui les comprennent, puis réappliqués à tous les résultats.
- **Autres filtres** : type d'étude, accès libre et revue.

**Fiabilité**
- **Source défaillante** : elle est signalée et n'empêche pas les autres de répondre. Après un refus pour excès de requêtes (HTTP 429), elle est mise en pause 10 minutes.
- **Cache** : les réponses sont gardées un jour (table `http_cache`).
- **Débit** : il est limité par source (PubMed : 3 requêtes/s, 10 avec une clé).
- **PDF libre** : il vient d'Europe PMC, d'OpenAlex ou de Semantic Scholar, et `biblio oa` interroge PMC puis Unpaywall. Uniquement des sources légales.

**Recommandé dans `config.toml`**
- `contact_email` : demandé par Crossref, OpenAlex et NCBI, **indispensable pour Unpaywall**.
- `openalex_api_key` : gratuite. Sans elle, OpenAlex partage un petit budget quotidien entre tous les utilisateurs d'une même adresse IP.
- `semanticscholar_api_key` : gratuite sur demande. Sans elle, Semantic Scholar refuse presque toujours (HTTP 429).

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
| POST | `/api/v1/works` | Ajouter par identifiant, avec PDF libre (`{"identifier", "pdf", "question"}` ; 201 créé, 200 déjà présent ; `question` gardée avec la notice) |
| POST | `/api/v1/works/manual` | Saisie manuelle |
| POST | `/api/v1/import?format=` | Importer un fichier BibTeX / RIS / nbib / CSL-JSON (corps brut) |
| POST | `/api/v1/files?filename=` | Déposer un PDF (corps brut) |
| GET | `/api/v1/files` | PDF en attente de notice |
| GET | `/api/v1/files/{sha256}` | Télécharger un PDF |
| POST | `/api/v1/files/{sha256}/attach` | Rattacher un PDF (`{"identifier"}` ou `{"work"}`) |
| GET | `/api/v1/works/{ref}/files` | PDF d'une notice |
| POST | `/api/v1/works/{ref}/pdf` | Chercher et rattacher le PDF libre |
| GET / PUT | `/api/v1/works/{ref}/note` | Lire / écrire la note (`{"text"}`) |
| GET | `/api/v1/search/library?q=&engine=` | Recherche locale (hybrid, lexical, semantic) |
| GET | `/api/v1/semantic` | État du modèle et de l'indexation |
| POST | `/api/v1/semantic/download` | Télécharger le modèle (arrière-plan) |
| GET | `/api/v1/duplicates` | Doublons probables |
| POST | `/api/v1/duplicates/merge` | Fusionner (`{"keep", "drop"}`) |
| POST | `/api/v1/duplicates/dismiss` | Écarter (`{"a", "b"}`) |
| POST | `/api/v1/notes/sync` | Réindexer les notes modifiées hors de Biblio |
| POST | `/api/v1/inbox/scan` | Traiter la boîte d'entrée (et les Téléchargements si activé) maintenant |
| GET | `/api/v1/works?limit=&offset=&journal_status=&label=&read_status=` | Lister (statut de revue répétable, collection ou tag, statut de lecture ou `unread`) |
| GET | `/api/v1/works/{ref}` | Lire (id, clé, DOI, PMID) |
| GET | `/api/v1/works/{ref}/changes` | Journal des modifications |
| GET | `/api/v1/search/online?q=&source=&per_source=&limit=&year_from=&year_to=&study_type=&open_access=&mesh=&journal=&journal_status=&all_journals=` | Recherche fédérée |
| GET | `/api/v1/search/open-access?doi=` ou `?pmcid=` | Version libre et légale |
| GET | `/api/v1/journals` | Liste des revues : version en vigueur, historique, dernière vérification |
| POST | `/api/v1/journals/update` | Vérifier en ligne (502 si échec, la liste précédente reste en place) |
| POST | `/api/v1/journals/import?filename=` | Importer un .xlsx (corps brut de la requête) |
| GET | `/api/v1/journals/lookup?issn=&nlmid=&abbrev=` | Statut d'une revue |
| POST | `/api/v1/shutdown` | Arrêter le serveur |
| POST | `/api/v1/ask` | Question → réponse extraite et sources (`{"question", "scope": "online"\|"library", "query", "all_journals", "year_from", "open_access"}`) |
| GET / POST | `/api/v1/searches` | Recherches enregistrées : lister / enregistrer (`{"question", "scope", "query", "answer", "sources"}`) |
| GET / DELETE | `/api/v1/searches/{id}` | Relire / supprimer |
| GET / POST | `/api/v1/labels` | Collections et tags (`{"kind": "collection"\|"tag", "name"}`) |
| PATCH / DELETE | `/api/v1/labels/{id}` | Renommer / supprimer (les notices restent) |
| POST | `/api/v1/labels/{id}/works` | Ranger des notices (`{"works": [...], "remove": false}`) |
| PATCH | `/api/v1/works/{ref}` | Statut de lecture et note (`{"read_status", "rating"}` ; null efface) |
| GET | `/api/v1/works/{ref}/citation?style=` | Référence formatée |
| POST | `/api/v1/citation` | Référence d'une notice hors bibliothèque (`{"record", "style"}`) |
| GET / POST | `/api/v1/styles` | Styles disponibles / ajouter un .csl (corps brut, `?filename=`) |
| POST | `/api/v1/export` | Exporter (`{"works"` ou `"label", "format": "bibtex"\|"ris"\|"csljson"\|"markdown", "style"}` ; vide : toute la bibliothèque) |
| GET / POST | `/api/v1/zotero` | Zotero ouvert ? / envoyer (`{"works"` ou `"label", "pdf"}` ; 503 si Zotero est fermé) |
| POST | `/api/v1/packages` | Créer un paquet .biblio (`{"works"` ou `"label", "title", "searches"}`) |
| POST | `/api/v1/packages/preview` | Aperçu d'un paquet (corps brut) |
| POST | `/api/v1/packages/import` | Importer un paquet (corps brut) |
| GET / PUT | `/api/v1/settings` | Réglages de l'interface (PUT : champs omis inchangés ; écrit `config.toml`) |

L'en-tête facultatif `X-Biblio-Client` (`api`, `cli`, `web`, `mcp`…) est inscrit dans le journal des modifications.

## Données et configuration

Dossier de données : `~/Library/Application Support/Biblio` (macOS), `%APPDATA%\Biblio` (Windows), `~/.local/share/biblio` (Linux). La variable `BIBLIO_HOME` le remplace.

- `config.toml` : créé au premier lancement (droits 600). Contient le port, le jeton de l'API, l'e-mail de contact et les clés des bases, la surveillance des Téléchargements, la recherche par le sens, la langue et le thème. Il se modifie à la main ou depuis les réglages de l'application (qui le réécrivent sans les commentaires ajoutés à la main).
- `library/` : les PDF (par empreinte). `notes/` : les notes Markdown. `inbox/` : la boîte d'entrée. `styles/` : les styles de citation ajoutés. `library.bib` : toute la bibliothèque en BibTeX.
- `biblio.sqlite` : la base (SQLite en mode WAL), documentée dans [SCHEMA.md](SCHEMA.md). **Ne la placez pas dans un dossier synchronisé** (iCloud Drive, OneDrive) : la synchro peut la corrompre.

## Sauvegarde

En attendant `biblio backup` (phase 10) : arrêtez le serveur (`biblio stop`), puis copiez le dossier de données entier.

## Tests

```bash
uv run pytest
```

Les tests n'accèdent jamais au réseau :
- les réponses de Crossref et PubMed sont enregistrées dans `tests/fixtures/` (essai MERINO, JAMA 2018) ;
- une recherche réelle auprès des 5 sources est enregistrée dans `tests/fixtures/search/`. La réponse de Semantic Scholar est écrite à la main d'après son format documenté, le service ayant refusé toutes les requêtes sans clé ;
- le site de Sorbonne Université est simulé, avec des fichiers .xlsx générés par les tests ;
- les PDF de test sont générés par les tests (`make_pdf` dans `tests/conftest.py`) : texte réel, titre, DOI en XMP ;
- les deux fichiers de traduction (`ui/src/locales/fr.json`, `en.json`) doivent avoir les mêmes clés, et toute clé utilisée dans l'interface doit exister ;
- la recherche par le sens est testée avec un modèle simulé (`FakeEmbedder`). Un test supplémentaire utilise le vrai modèle s'il est présent : `BIBLIO_TEST_MODELS=<dossier des modèles> uv run pytest`.
