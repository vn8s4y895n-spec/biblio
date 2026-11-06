# Biblio — Plan

Statut : **plan validé le 2026-11-06. Phases 1 à 4 terminées** (2026-11-06). Prochaine étape : phase 5.

## Objectif

Une application de **recherche bibliographique optimisée** pour la santé. Elle interroge et fusionne les grandes bases et, si on le souhaite, des moteurs IA. Elle ne garde que les revues de la **liste des revues recommandées de la Conférence des doyens et du CNU santé** (Sorbonne Université), mise à jour automatiquement. Elle stocke une bibliothèque personnelle propre et exporte vers Zotero ou n'importe quel gestionnaire de bibliographie.

L'application est personnelle : chacun l'installe sur son ordinateur, sa bibliothèque reste chez lui, et elle **se partage en la donnant à d'autres** sous forme d'application à double-cliquer. On peut **s'envoyer des articles d'un Biblio à l'autre** avec tout leur contexte : notice, PDF, résumé, notes, tags, statut de revue. L'interface est disponible **en français et en anglais**. Toutes les fonctions de base marchent sans compte ni clé d'IA ; les fonctions IA s'ajoutent si on les configure.

## 0. Décisions prises

| Sujet | Choix | Pourquoi |
|---|---|---|
| Usage | Personnel, une bibliothèque par ordinateur | Rien ne sort de la machine, à part les requêtes vers les bases en ligne. |
| Diffusion | **Application à double-cliquer**, Mac (`.app`) d'abord, puis Windows (`.exe`) | Les collègues n'ont pas à ouvrir de terminal. |
| Fenêtre | Fenêtre native (`pywebview`) qui affiche l'interface web locale | Elle s'ouvre comme une vraie application, pas comme un onglet de navigateur. Le même serveur reste joignable par la CLI, `curl` et le MCP. |
| Interface | **Svelte** (SvelteKit en site statique), compilée une fois et livrée dans l'application | Une interface soignée demande ce que HTMX fait mal : recherche instantanée, raccourcis clavier, panneaux redimensionnables, lecteur PDF intégré (pdf.js), surlignage des passages, affichage progressif des synthèses IA, glisser-déposer. Svelte reste léger et rapide. L'interface devient un pur client de l'API REST, donc aucune fonction cachée, par construction. Node ne sert qu'à moi pour compiler ; les utilisateurs n'en ont pas besoin. |
| Embeddings | `BAAI/bge-m3` (1024 dim., multilingue), **téléchargé au premier lancement** | Requêtes en français, articles en anglais. Le télécharger au premier lancement plutôt que de l'intégrer allège l'installeur d'environ 2 Go. |
| Revues | Liste Sorbonne, **filtre actif par défaut**, désactivable | Un bouton affiche les articles hors liste avec un badge. Les preprints sont signalés à part. |
| IA | Les 4 niveaux (voir §2), tous facultatifs | Sans IA configurée, Biblio reste complet : fusion des bases et classement par le modèle local. |
| Export | Fichiers (BibTeX, RIS, CSL-JSON) + **envoi direct dans Zotero** | Envoi par le connecteur local de Zotero desktop (`localhost:23119`, celui qu'utilise l'extension de navigateur). Aucun compte Zotero nécessaire. |
| Envoi entre Biblio | **Paquet `.biblio`** (voir §1 bis), transmis par e-mail, AirDrop, clé USB… | Aucun serveur ni compte. Un double-clic sur le fichier l'importe dans le Biblio du destinataire. |
| Claude avec un plan Pro | **Connecteur Claude** dans les réglages : extension Claude Desktop, skill, Claude Code (voir §2 bis) | Un abonnement Pro ne peut pas servir de clé API dans une autre application (voir §2). Dans l'autre sens, c'est autorisé : Claude Desktop appelle Biblio, et c'est inclus dans le plan Pro. |
| PDF | Accès libre seulement | Unpaywall, PMC, Europe PMC. |
| Extraction PDF | `pypdfium2` | Rapide, licence BSD/Apache, compatible avec la diffusion de l'app (PyMuPDF est sous AGPL). |
| Langues | **Français et anglais**, au choix dans les réglages (par défaut : langue du système) | Les textes de l'interface sont dans `ui/locales/fr.json` et `en.json`, et un test vérifie que les deux fichiers ont les mêmes clés. Les réponses de l'IA suivent la langue de la question. Les notices restent dans leur langue d'origine. |

## 1. Liste des revues recommandées

Source : la page `https://sante.sorbonne-universite.fr/recherche/liste-des-revues-presumees-non-predatrices`, qui renvoie vers un fichier `.xlsx` daté (actuellement `liste_de_revues_recommandables_cdd_cnu_sante_01-10-26.xlsx`, 3 554 lignes). La liste est mise à jour tous les trimestres.

Colonnes du fichier : nom complet, nom abrégé, ISSN papier, ISSN électronique, **NLMID**, années de début et de fin, nouveau nom, date de retrait, motif de retrait (1 = nouveau nom, 2 = n'est plus recommandée, 3 = arrêt de publication).

- **Mise à jour** : chaque semaine (et à la demande), Biblio relit la page, repère le lien `.xlsx` le plus récent et ne télécharge le fichier que s'il a changé (empreinte sha256). Chaque version est conservée avec sa date, ce qui permet de savoir quelle liste était en vigueur. Une copie est livrée dans l'application, pour que le filtre marche hors ligne dès le premier lancement.
- **Comparaison des revues** : ISSN papier ou électronique, puis NLMID (fourni par PubMed), puis nom abrégé normalisé. Crossref, OpenAlex et PubMed fournissent ces identifiants.
- **Statut d'un article** :
  - **recommandée** : la revue est dans la liste ; un retrait pour motif 1 ou 3 compte aussi, puisqu'il s'agit d'un changement de nom ou d'un arrêt de publication, sans désaveu.
  - **retirée de la liste** : motif 2. Le badge indique la date de retrait, et on peut comparer avec l'année de publication.
  - **hors liste** : la revue n'y figure pas.
  - **preprint** : medRxiv, bioRxiv, arXiv.
  - **revue non identifiée** : aucun ISSN ni NLMID disponible.
- Le statut est **calculé au moment de la requête**, par jointure, et jamais figé dans la notice. Quand la liste change, toute la bibliothèque est donc à jour aussitôt.

## 1 bis. Envoi d'articles entre Biblio (paquet `.biblio`)

Un paquet `.biblio` est un fichier zip, lisible même sans Biblio :

```
manifest.json      # version du format, expéditeur (nom affiché seulement), date, langue
items.json         # pour chaque article : notice CSL-JSON complète + champs Biblio :
                   #   identifiants, MeSH, type d'étude, résumé Biblio, tags, collections,
                   #   note de pertinence, statut de lecture, statut de revue chez l'expéditeur,
                   #   rétractation, version de la liste des revues utilisée
notes/<citekey>.md # notes de l'expéditeur (si cochées)
syntheses/*.json   # question + réponse + sources citées (si l'envoi part d'une recherche)
pdf/<sha256>.pdf   # si cochés
```

- **À l'envoi** : tu sélectionnes des articles, une collection ou une recherche avec sa synthèse, puis tu coches ce que tu veux joindre (PDF, notes, tags, note de pertinence). Les notes sont décochées par défaut. Pour un PDF qui n'est pas sous licence libre, un avertissement indique qu'il s'agit d'une copie personnelle. Sur Mac, le bouton « Partager » ouvre la feuille de partage du système (Mail, AirDrop, Messages) ; ailleurs, le paquet est enregistré là où tu le choisis.
- **À la réception** : un double-clic ouvre Biblio sur un écran d'aperçu, qui montre ce qui est nouveau, ce qui est déjà dans la bibliothèque et ce qui diffère. Les doublons sont fusionnés selon les règles habituelles, les tags et notes reçus sont rangés sous « Reçu de <nom> » sans écraser les tiens, et le statut de revue est recalculé avec **ta** liste. Le journal des modifications garde l'origine (`client = package`).
- **Sécurité** : le paquet est traité comme une donnée non fiable. Le format est vérifié, les chemins dangereux (`../`) sont refusés, la taille est limitée et aucun code n'est exécuté. Le Markdown des notes est affiché sans HTML actif.

## 2. Recherche optimisée et IA

Quatre niveaux, qui s'ajoutent les uns aux autres :

1. **Bases fusionnées, classement local (sans IA externe).** PubMed, Europe PMC, Crossref, OpenAlex et Semantic Scholar sont interrogés en parallèle. Les résultats sont dédoublonnés (DOI > PMID > PMCID > titre normalisé + année + premier auteur), filtrés par la liste des revues, puis reclassés par bge-m3 selon leur proximité avec la question, ce classement étant combiné avec les rangs fournis par chaque source (RRF). Filtres : année, type d'étude, revue, accès libre, MeSH. La syntaxe PubMed avancée est transmise telle quelle à PubMed.
2. **Question → requête PubMed (LLM).** Tu poses ta question en français. Le LLM propose une requête PubMed (MeSH, `[tiab]`, booléens) et une version adaptée aux autres bases. Tu vois et corriges la requête avant de lancer la recherche.
3. **Synthèse avec citations (LLM).** Réponse à la question à partir des résumés et des PDF retenus. Chaque phrase renvoie à un passage source, cliquable. Si une phrase ne correspond à aucun passage, elle est signalée au lieu d'être affichée comme sûre.
4. **Moteurs IA externes** (Consensus, Elicit, Scite…). Chacun est un adaptateur de source comme les autres, activé seulement si tu saisis ta clé. Leurs résultats passent par la même dédup et le même filtre des revues.

Le module LLM a une interface unique : Ollama ou llama.cpp en local, ou n'importe quelle API compatible OpenAI (Mistral, OpenAI, Anthropic via un proxy compatible…). Il est désactivé par défaut. Les clés sont stockées dans le trousseau du système (Keychain sur Mac, Credential Manager sur Windows, via `keyring`), jamais en clair dans un fichier.

**Résumé Biblio (sans IA).** Chaque article a un résumé de quelques lignes, produit localement :
- les sections Objectif, Méthodes, Résultats et Conclusion, extraites du résumé structuré ;
- le type d'étude et l'effectif (`n = …`) quand ils se repèrent ;
- les phrases les plus proches de ta question, classées par bge-m3 ;
- les badges (revue, accès libre, rétractation).

Si un LLM est configuré, un résumé rédigé s'ajoute à côté, avec ses citations. Le résumé extrait reste toujours visible.

**Claude avec un abonnement Pro.** Ce n'est **pas possible dans l'interface de Biblio** sans surcoût. Depuis février 2026, Anthropic interdit explicitement d'utiliser les identifiants d'un abonnement Free, Pro ou Max dans une application tierce : l'accès par programme passe par une clé API facturée à l'usage.

C'est en revanche **possible dans l'autre sens** : l'application Claude Desktop, avec ton plan Pro, se branche sur le serveur MCP de Biblio. Tu poses ta question dans Claude ; il cherche dans les bases et dans ta bibliothèque avec les outils de Biblio, puis il **enregistre sa synthèse et ses sources dans Biblio** avec l'outil `save_synthesis`. Tu les retrouves ensuite dans l'interface de Biblio, comme n'importe quelle recherche. C'est inclus dans le plan Pro, dans la limite de ses quotas.

Dans l'interface de Biblio, les options sans frais sont Ollama en local (qualité moindre sur un MacBook) ou le résumé extrait.

## 2 bis. Réglages → Connecteurs

Un écran regroupe tout ce qui relie Biblio à l'extérieur. Chaque connecteur a un état (actif, inactif ou en erreur), un bouton « Tester » et un bouton pour le désactiver.

| Connecteur | Ce qu'il fait |
|---|---|
| **Claude (plan Pro)** | Détaillé ci-dessous. |
| Autres clients MCP | Config JSON à copier pour LeChat, LM Studio, Cursor, ChatGPT… (stdio ou HTTP). |
| Zotero | Test du connecteur local, choix de la collection Zotero de destination. |
| LLM intégré | Ollama (détection automatique des modèles installés) ou API compatible OpenAI avec clé. |
| Moteurs IA externes | Clés Consensus, Elicit, Scite… (selon les API disponibles). |
| Bases en ligne | E-mail de contact, clés NCBI et Semantic Scholar facultatives. |
| Webhooks | Adresses de destination et événements envoyés. |

### Intégration Claude (plan Pro, sans surcoût)

L'objectif : que Claude se serve de Biblio comme d'un outil maison, sans que tu aies à lui expliquer quoi faire.

1. **Installation en un clic dans Claude Desktop.** Biblio génère une extension Claude Desktop (format MCP Bundle, `.mcpb`) qui contient la connexion au serveur local et le jeton. Un double-clic l'installe dans Claude Desktop. Si ce format n'est pas disponible, Biblio écrit lui-même l'entrée dans `claude_desktop_config.json`, après l'avoir sauvegardé et seulement avec ton accord.
2. **Claude Code** (inclus dans le plan Pro) : la commande `claude mcp add biblio …` est prête à copier.
3. **Skill « Biblio » pour Claude**, en `.zip` à importer dans les réglages de Claude. Il décrit la méthode à suivre :
   - transformer la question en requête PubMed avec MeSH ;
   - chercher en ligne **avec le filtre des revues recommandées** ;
   - vérifier dans la bibliothèque ce qui y est déjà ;
   - répondre en citant des passages précis ;
   - signaler les rétractations et les preprints ;
   - **enregistrer la synthèse et les sources dans Biblio** ;
   - proposer l'export en Vancouver.
4. **Serveur MCP pensé pour un LLM** :
   - **Instructions du serveur** : les mêmes règles que le skill, en version courte, transmises automatiquement à tout client MCP.
   - **Outils** : `search_online` (avec filtre des revues), `search_library`, `get_reference`, `get_summary`, `get_passages`, `add_by_identifier`, `tag`, `add_to_collection`, `add_note`, `save_synthesis`, `export_bibliography`, `create_package`. Leurs descriptions sont précises, leurs réponses sont courtes et structurées (pas de notices de 50 Ko), et la pagination est explicite.
   - **Prompts** prêts à l'emploi, qui apparaissent dans le menu de Claude : « Recherche sourcée », « Revue rapide de la littérature », « Bibliographie Vancouver d'une collection », « Quoi de neuf dans ma veille ».
   - **Ressources** : `biblio://work/<citekey>`, `biblio://collection/<id>`, `biblio://journals/status`.
5. **Retour dans Biblio** : chaque synthèse enregistrée par Claude apparaît dans l'interface (origine « Claude », avec la question, la réponse et les sources). On peut la relire, la compléter, l'envoyer en paquet ou l'exporter.
6. **Dans l'autre sens** : depuis une recherche dans Biblio, le bouton « Continuer dans Claude » copie un prompt prêt à coller, qui fait référence aux sources déjà trouvées par leur identifiant.

Seul le **paquetage** (extension `.mcpb`, skill, commande Claude Code) est spécifique à Claude. Le serveur MCP, ses outils et ses instructions restent standard et servent de la même façon aux autres clients.

**Limite** : claude.ai dans le navigateur ne peut pas joindre un serveur qui tourne sur ton Mac, seulement des serveurs accessibles sur Internet. L'intégration passe donc par Claude Desktop ou Claude Code, pas par le site web.

## 3. Architecture

```
 ┌──────────── Biblio.app ─────────────────────────────────────────────┐
 │ fenêtre pywebview ──▶ interface Svelte (fichiers statiques)         │
 │                            │ fetch /api/v1                          │
 │ serveur FastAPI (localhost, port aléatoire + jeton)                 │
 │   /api/v1 (OpenAPI)   /mcp   ──▶ core/ ──▶ SQLite (WAL, FTS5, vec)  │
 │                                    │   ──▶ library/ (PDF sha256)    │
 │                                    │   ──▶ notes/ (Markdown)        │
 │   sources/ : PubMed, EPMC, Crossref, OpenAlex, S2, Unpaywall,       │
 │              + adaptateurs IA facultatifs                           │
 │   llm/ : Ollama | API compatible OpenAI (facultatif)                │
 │   worker : file de tâches SQLite → texte, embeddings, PDF libres,   │
 │            liste des revues, veille, boîte d'entrée, webhooks       │
 └─────────────────────────────────────────────────────────────────────┘
   CLI `biblio …`, curl, clients MCP ──▶ même API (jeton dans config.toml)
```

Code (`src/biblio/`) : `db/` (connexion, migrations versionnées), `core/` (logique métier, l'unique point d'entrée), `sources/`, `llm/`, `formats/`, `api/`, `journals.py` (liste Sorbonne), `cli.py`, `worker.py`, `app.py` (lancement de la fenêtre). L'interface est dans `ui/` (Svelte) ; sa version compilée est copiée dans le paquet.

**Jeton** : il est créé au premier lancement. L'interface le reçoit au démarrage de la fenêtre, et la CLI le lit dans `config.toml`. Toute requête sans jeton est refusée, même depuis `localhost`, pour qu'une page web ouverte dans un navigateur ne puisse pas lire la bibliothèque.

Données (dossier d'application du système : `~/Library/Application Support/Biblio`, `%APPDATA%\Biblio`) :

```
biblio.sqlite
library/ab/cd/<sha256>.pdf
notes/<citekey>.md                 # ouvrable comme coffre Obsidian
inbox/                             # tout PDF déposé ici est importé
styles/*.csl                       # Vancouver + APA fournis, autres ajoutables
journals/<date>.xlsx               # versions de la liste des revues
exports/library.bib, library.json  # régénérés à chaque modification
models/                            # bge-m3, téléchargé au premier lancement
backups/biblio-AAAA-MM-JJ.tar.gz
```

La base reste **hors d'iCloud Drive / OneDrive** : leur synchro peut corrompre une base SQLite en mode WAL. Les sauvegardes, elles, peuvent aller n'importe où.

## 4. Schéma SQLite (ébauche, version finale dans `SCHEMA.md`)

```
schema_migrations(version PK, applied_at)

works(id, citekey UNIQUE, csl_type, study_type, title, abstract, container_title,
      issn_print, issn_e, nlmid, year, issued, volume, issue, pages, language,
      license, is_oa, cited_by_count, cited_by_at, retracted, retraction_json,
      read_status, rating, fingerprint, csl_json, merged_into_id,
      created_at, updated_at)
identifiers(work_id, scheme, value, UNIQUE(scheme, value))
      -- doi | pmid | pmcid | arxiv | biorxiv | medrxiv | openalex | s2
authors(id, family, given, orcid UNIQUE NULL, norm_key)
work_authors(work_id, author_id, position)
subjects(id, scheme, term, ui, UNIQUE(scheme, term))     -- mesh | keyword
work_subjects(work_id, subject_id, major)
relations(work_id, kind, target_work_id NULL, target_identifier NULL)
      -- preprint_of | published_as | retracted_by | cites | cited_by

journal_lists(id, source_url, sha256 UNIQUE, published_on, fetched_at, row_count)
journals(id, list_id, name, abbrev, abbrev_norm, issn_print, issn_e, nlmid,
         start_year, end_year, new_name, withdrawn_on, withdrawal_reason)
      -- index sur issn_print, issn_e, nlmid, abbrev_norm (liste courante)
-- vue journal_status(work_id, status, journal_id) : jointure calculée à la volée

files(sha256 PK, size, mime, pages, added_at, text_status)
work_files(work_id, sha256, role, source_url)

passages(id, work_id, origin, sha256 NULL, note_id NULL, page, ord, text)
passages_fts  FTS5(text, content=passages)
works_fts     FTS5(title, abstract, authors, journal, subjects)
passages_vec  vec0(embedding float[1024])
passages_bin  vec0(embedding bit[1024])

notes(id, work_id UNIQUE, path, content_sha, updated_at)
note_links(note_id, target_citekey)
collections(id, parent_id, name);  collection_works(collection_id, work_id)
tags(id, name UNIQUE);             work_tags(work_id, tag_id)

changes(id, work_id, client, at, op, field, old_json, new_json)
dedup_candidates(work_a, work_b, reason, score, status)
saved_searches(id, name, query, sources, filters_json, every_hours, next_run_at)
watch_hits(search_id, dedup_key, first_seen_at, status, UNIQUE(search_id, dedup_key))
syntheses(id, question, language, query_json, answer_md, model, origin, created_at)
      -- origin : biblio | mcp (ex. Claude Desktop) | package
synthesis_sources(synthesis_id, work_id, rank, cited_passages_json)
summaries(work_id, kind, language, text, model NULL, created_at, UNIQUE(work_id, kind, language))
      -- kind : extractive | llm
packages(id, sha256 UNIQUE, direction, peer_name, item_count, at)   -- envois / réceptions

http_cache(key PK, url, status, body, fetched_at, expires_at)
jobs(id, kind, payload_json, status, attempts, run_after, error)
webhooks(id, url, events, secret, active)
events(id, type, payload_json, created_at)
```

**Déduplication.** Une correspondance par identifiant fusionne automatiquement dans la notice existante, en complétant les champs vides. Une correspondance par `fingerprint` seulement crée une entrée `dedup_candidates` à valider, parce que deux articles différents peuvent avoir le même titre, la même année et le même premier auteur.

**Recherche locale hybride.** Elle combine BM25 (FTS5) et un passage vectoriel en deux temps : présélection binaire en distance de Hamming (top 200), puis rerank exact sur les vecteurs float. Les classements sont fusionnés par RRF, et les extraits correspondants sont surlignés.

## 5. Interface

L'écran principal reprend le principe des assistants de recherche médicale (une question, une réponse sourcée), avec un design propre à Biblio, et y ajoute ce qui leur manque : **la sauvegarde des sources dans ta bibliothèque**.

```
┌──────────────────────────────────────────────────────────────────────────┐
│  Biblio                     [Bibliothèque] [Veille] [Envois]   FR/EN  ⚙  │
├──────────────────────────────────────────────┬───────────────────────────┤
│  ┌────────────────────────────────────────┐  │ SOURCES (12)  ☑ revues    │
│  │ Quelle transmission intrafamiliale des │  │ recommandées seulement    │
│  │ E. coli BLSE ?                     ⏎   │  │───────────────────────────│
│  └────────────────────────────────────────┘  │ [1] Martischang 2021      │
│  Requête PubMed ▸ (modifiable)               │  Clin Microbiol Infect    │
│                                              │  ● recommandée · OA       │
│  RÉPONSE                                     │  Cohorte, n = 312         │
│  Le taux de transmission dans les foyers     │  Résumé Biblio ▾          │
│  varie de 20 à 40 % [1][3]. Le risque        │  [＋ Ajouter] [Zotero]    │
│  augmente avec … [2]                         │───────────────────────────│
│                                              │ [2] Riccio 2024 …         │
│  ⚠ 1 phrase sans source vérifiée             │  ◐ hors liste             │
│                                              │───────────────────────────│
│  [Enregistrer la recherche] [Envoyer ▸]      │ [Tout ajouter] [Exporter] │
│  Questions liées : …                         │                           │
└──────────────────────────────────────────────┴───────────────────────────┘
```

- **Zone centrale** : la question en langage naturel (ou en syntaxe PubMed), puis la requête générée (modifiable), puis la réponse. Avec un LLM, elle est rédigée et citée (« [1] ») ; sans LLM, ce sont les passages les plus pertinents, chacun avec sa référence. Elle s'affiche au fur et à mesure.
- **Panneau des sources, à droite** : chaque source montre ses badges (revue recommandée ou non, accès libre, rétracté, preprint, déjà dans ma bibliothèque), le type d'étude, l'effectif et le **résumé Biblio** à déplier. Cliquer sur un numéro de citation dans la réponse fait défiler jusqu'à la source et surligne le passage cité.
- **Sauvegarder** : `＋` sur une source, « Tout ajouter », ou « Enregistrer la recherche ». Cette dernière garde la question, la réponse, les sources et leurs résumés, dans une collection ou une recherche relancée périodiquement.
- **Envoyer** : la recherche entière ou une sélection, en paquet `.biblio`, ou vers Zotero.
- **Bibliothèque** : vue en trois panneaux (filtres, liste, article avec lecteur PDF, notes, passages surlignés). Les panneaux sont redimensionnables et la sélection multiple permet les actions en lot.
- **Clavier** : `/` pour une question, `j`/`k` pour passer d'une source à l'autre, `a` pour ajouter, `z` pour Zotero, `c` pour copier la citation Vancouver.
- **Thème clair et sombre**, qui suit le système. Bascule FR/EN dans l'en-tête.

## 6. Phases

Chaque phase se termine par : tests verts (aucun appel réseau, réponses enregistrées en fixtures), commit, démo, mise à jour de ce plan.

1. ✅ **Socle.** Projet `uv`, config + jeton, migrations, schéma de base, cœur métier, API REST minimale, `biblio add <DOI|PMID>` (Crossref + PubMed), dédup par identifiant, journal des modifications, CLI de base.
   - *Bilan* : 31 tests verts, sans réseau. Démo réelle : premier ajout en 2,1 s (démarrage du serveur compris), ré-ajout sans doublon en 0,4 s.
   - *Écarts au plan* :
     - pas de table `authors` commune (auteurs stockés par article, voir SCHEMA.md) ;
     - pas de colonne `csl_json` : le CSL sera produit à partir des colonnes en phase 7, pour éviter deux sources de vérité ;
     - ajout de `biblio stop` et `POST /api/v1/shutdown`.
   - *Constats* :
     - le Python installé par `uv` charge bien les extensions SQLite (risque levé) ;
     - `uv` a été installé avec `pip --user`, Homebrew étant absent ;
     - iCloud masque des fichiers de `.venv`, d'où un environnement placé dans `.venv.nosync` (voir README).
2. ✅ **Liste des revues.** Lecture du `.xlsx`, versions conservées, mise à jour automatique depuis la page Sorbonne, comparaison ISSN / NLMID / nom abrégé, vue de statut, filtre dans l'API.
   - *Bilan* : 62 tests verts. Démo réelle contre le site de la Sorbonne : le fichier en ligne est identique à celui livré, et la base de la phase 1 a été migrée sans perte. Filtrer 10 000 notices par statut prend environ 27 ms.
   - *Écarts au plan* :
     - lecture du `.xlsx` avec la bibliothèque standard, sans `openpyxl` (un .xlsx est un zip de XML) ;
     - la vérification hebdomadaire tourne dans un thread du serveur, en attendant la file de tâches de la phase 5 ;
     - la recherche d'une revue absente de la bibliothèque (`/journals/lookup`) est déjà prête pour les résultats en ligne de la phase 3.
   - *Constats* :
     - **le fichier Sorbonne du 01-10-26 contient une erreur** : la colonne « ISSN Electronic » est décalée d'une ligne à partir de la ligne 2650. Biblio la détecte et la signale, et la correspondance passe d'abord par NLMID et ISSN papier pour que le statut reste juste ;
     - 192 ISSN mal formés (espaces insécables, zéro initial manquant) sont normalisés ;
     - un premier essai de correspondance avec un OR entre colonnes prenait 20 s pour 10 000 notices ; il a été réécrit en recherches indexées, et un test garde-fou protège ce gain.
3. ✅ **Recherche en ligne fédérée.** Les 5 adaptateurs en parallèle, cache, limites de débit, fusion et dédup, filtres, syntaxe PubMed, PDF libres via Unpaywall / PMC.
   - *Bilan* : 83 tests verts, sans réseau. Démo réelle : 4 sources sur 5 répondent en 3,5 s (100 notices, fusionnées en 42 résultats, dont 50 masqués par le filtre des revues). La même recherche servie par le cache prend 0,4 s.
   - *Écarts au plan* :
     - le PDF libre est *trouvé* (lien), son téléchargement et son stockage par empreinte arrivent en phase 4 ;
     - les filtres de type d'étude, d'accès libre et de revue sont appliqués après fusion, seuls la date et les MeSH sont transmis aux sources ;
     - le classement fusionne les rangs des sources (RRF), le reclassement sémantique viendra en phase 5 ;
     - ajout d'une pause de 10 minutes pour une source qui refuse (HTTP 429), pour ne pas ralentir chaque recherche.
   - *Constats* :
     - **Semantic Scholar refuse toutes les requêtes sans clé** (HTTP 429), d'où sa fixture écrite à la main d'après le format documenté ;
     - **OpenAlex sans clé** partage un petit budget quotidien par adresse IP (environ 100 requêtes/jour) : une clé gratuite est recommandée ;
     - **Unpaywall exige un e-mail de contact** (`contact_email`) ;
     - Crossref en texte libre remonte surtout des thèses et des posters, écartés par le filtre des revues ;
     - le filtre par ISSN s'appuie aussi sur la liste des revues, car PubMed ne donne souvent qu'un des deux ISSN.
4. ✅ **Import et stockage.** PDF par empreinte, extraction du DOI, saisie assistée, BibTeX / RIS / CSL-JSON / nbib, arXiv / bioRxiv / medRxiv / URL, extraction du texte et découpage en passages, FTS5, notes Markdown, boîte d'entrée, fusion des doublons.
   - *Bilan* : 116 tests verts, sans réseau (PDF générés par les tests). Démo réelle : les ajouts par PMCID, arXiv et URL d'éditeur fonctionnent, un PDF libre a été téléchargé et indexé (Scientific Reports, via OpenAlex), la recherche plein texte trouve le passage page 10 en 0,4 s, la boîte d'entrée reconnaît un PDF déjà présent, et un BibTeX réimporté ne crée aucun doublon.
   - *Écarts au plan* :
     - un seul index plein texte (`passages_fts`) au lieu de deux ;
     - RIS et MEDLINE sont lus par un petit lecteur commun (sans `rispy`) ;
     - le DOI trouvé dans un PDF est vérifié contre le titre avant tout rattachement ;
     - une notice sans identifiant réimportée (même titre, année, premier auteur) complète l'existante au lieu d'être recréée ;
     - la boîte d'entrée est surveillée par le serveur (dossier `inbox/` des données) et non par un `biblio watch` côté client, l'usage étant personnel.
   - *Constats* :
     - **le téléchargement automatique des PDF libres est souvent refusé** (HTTP 403) par Europe PMC et par des éditeurs (JAMA). Biblio ne contourne pas ces protections : il essaie la source suivante, puis signale l'échec. L'ancien service OA de PMC (`oa.fcgi`) ne répond plus, et je n'ai pas trouvé de documentation officielle de son remplaçant (le service cloud de PMC) : à vérifier quand il sera documenté ;
     - **ajout (2026-11-06), sur le modèle de Zotero** :
       - lecture du lien PDF déclaré par l'éditeur (`citation_pdf_url`) sur la page de l'article, téléchargé dans la même session ; vérifié en réel sur PLoS One et Frontiers ;
       - `biblio pdf --open`, qui ouvre la page de l'article dans le navigateur pour un téléchargement avec la session de l'utilisateur ;
       - surveillance facultative des Téléchargements (seuls les PDF reconnus comme articles sont importés, les fichiers ne sont jamais touchés) ;
       - pas de navigateur caché pour passer les protections anti-robots. Une extension de navigateur « Envoyer à Biblio » est envisageable en phase 10 ou 11 ;
     - les accents LaTeX de BibTeX (`{\'\i}`) sont recomposés correctement ;
     - OpenAlex indique déjà, pour chaque revue, sa présence dans la liste des doyens (`listed_in: doyens`), ce qui fournira une seconde source pour le statut de revue.
5. **Sémantique et hybride.** File de tâches, bge-m3 (téléchargé au premier lancement), sqlite-vec, RRF, reclassement des résultats en ligne, **benchmark sur 10 000 notices** (objectif < 200 ms).
6. **Interface Svelte, FR + EN.** Écran question → réponse → sources (réponse extraite, sans LLM pour l'instant), résumé Biblio, enregistrement des recherches, bibliothèque en trois panneaux, lecteur PDF, collections, tags, notes, clavier, thèmes, traductions FR/EN, **écran de réglages** (e-mail de contact et clés OpenAlex / Semantic Scholar / NCBI, à faire avec toi à ce moment-là). Fenêtre `pywebview`.
7. **Citation, exports et envois.** CSL (Vancouver, APA, `.csl` ajoutés), BibTeX / RIS / CSL-JSON / Markdown, clés `auteurAnnéeMot`, `library.bib` synchronisé, **envoi direct dans Zotero**, **paquets `.biblio`** (création, aperçu, import, fusion).
8. **Veille et suivi.** Recherches enregistrées, articles proches (OpenAlex / Semantic Scholar), preprint → publié, rétractations (Crossref / Retraction Watch).
9. **IA.** Module LLM interchangeable, question → requête PubMed, réponse rédigée avec citations vérifiées, résumé rédigé, adaptateurs des moteurs IA externes (selon les API disponibles, voir les risques).
10. **Interopérabilité et connecteurs.** OpenAPI complet, `openapi.json` + `llms.txt`, serveur MCP (stdio + HTTP) avec outils, instructions, prompts et ressources, écran Réglages → Connecteurs, **intégration Claude** (extension `.mcpb`, skill, commande Claude Code, synthèses visibles dans Biblio), webhooks signés HMAC, `backup` / `restore` testés, export complet en formats ouverts. Test de bout en bout : un client MCP simulé déroule le skill (recherche → filtre → enregistrement de la synthèse) et on vérifie le résultat en base.
11. **Application à double-cliquer.** Paquet macOS (`.app` dans un `.dmg`), puis Windows (`.exe`), association des fichiers `.biblio` pour l'ouverture par double-clic, mise à jour de l'application. README et guide d'installation pour les collègues (FR + EN).

Tu peux utiliser Biblio dès la fin de la phase 1 (CLI), avec l'interface dès la phase 6.

## 7. Risques

| Risque | Parade |
|---|---|
| **Moteurs IA externes** : Consensus, Elicit et Scite ont des API payantes, sur demande, ou pas d'API publique. Les conditions changent souvent. | Je vérifie chaque API en phase 9 et je n'intègre que celles accessibles légalement par clé. Celles qui n'ont pas d'API restent un simple lien « ouvrir la recherche dans X ». Aucun scraping. |
| **Signature des applications** : sans compte Apple Developer (99 €/an) et sans certificat Windows, macOS et Windows affichent un avertissement au premier lancement. | Le guide d'installation explique comment passer outre (clic droit → Ouvrir). Si tu prends le compte Apple, j'ajoute la signature et la notarisation. |
| **Poids de l'application** : torch et sentence-transformers pèsent ~500 Mo, et bge-m3 ~2,2 Go à télécharger. | Le modèle est téléchargé au premier lancement, avec une barre de progression. Je testerai `onnxruntime` à la place de torch en phase 5 : si les résultats sont identiques, l'application tombe sous ~150 Mo. |
| **Format de la liste Sorbonne** : le nom du fichier, les colonnes ou la page peuvent changer. | La lecture vérifie les colonnes attendues. En cas d'échec, Biblio garde la dernière version valide et affiche une alerte avec la date de cette version. Import manuel d'un `.xlsx` possible. |
| **Revues sans ISSN dans les métadonnées** (fréquent chez Semantic Scholar). | Complément par OpenAlex ou Crossref via le DOI. Sinon, statut « revue non identifiée », visible et filtrable. |
| **Claude via un plan Pro** : seule la voie MCP est autorisée, et elle est limitée par les quotas du plan. Les formats (extension `.mcpb`, skills) et les règles d'Anthropic peuvent évoluer. | Le cœur de Biblio ne contient aucun code propre à Claude : seul le paquetage l'est. Je revérifierai les formats et les conditions d'Anthropic en phase 10, avec une écriture directe de `claude_desktop_config.json` en repli. |
| **Paquets `.biblio` reçus** : fichier venu d'ailleurs, potentiellement malformé ou malveillant. | Format vérifié, chemins et taille contrôlés, rien n'est exécuté, aperçu avant import. Des tests portent sur des paquets volontairement corrompus. |
| **PDF non libres dans un paquet** : les envoyer peut sortir de la licence de l'éditeur. | PDF non libres décochés par défaut, avec un avertissement s'ils sont cochés. La décision revient à l'expéditeur. |
| **Synthèses IA fausses ou hallucinées.** | Chaque phrase est rattachée à un passage précis. Une phrase sans passage correspondant est marquée. Les synthèses sont enregistrées avec le modèle utilisé. |
| **bge-m3 est lourd** : plusieurs heures pour la première indexation de 10 000 PDF, ~2 Go de RAM. | Indexation en arrière-plan qui reprend où elle s'est arrêtée, MPS sur Apple Silicon. Modèle configurable (`multilingual-e5-small` en repli). |
| **< 200 ms** : 300 000 vecteurs float ne peuvent pas être parcourus en entier assez vite. | Présélection binaire (≈ 40 Mo) puis rerank. Mesure réelle en phase 5 ; si l'objectif n'est pas tenu, je le dis chiffres à l'appui. |
| **Envoi direct vers Zotero** : le connecteur local de Zotero n'est pas une API officiellement documentée. | Si Zotero est fermé ou si l'envoi échoue, Biblio crée un fichier RIS à importer en un clic. |
| **Extensions SQLite** avec le Python de python.org sur macOS. | Python géré par `uv` (**à installer** : `brew install uv`), puis Python embarqué dans l'application. |
| **`citeproc-py` incomplet pour certains styles.** | Tests Vancouver et APA sur des cas réels. Sinon, `pandoc --citeproc` en repli, décidé en phase 7. |
| **Limites de débit** des API. | Un limiteur par source, le cache, les clés facultatives. |

## 8. Dépendances prévues

Python : `fastapi`, `uvicorn`, `httpx`, `typer`, `pywebview`, `keyring`, `openpyxl`, `sqlite-vec`, `sentence-transformers` (ou `onnxruntime`), `pypdfium2`, `bibtexparser`, `rispy`, `citeproc-py`, `mcp`. Tests : `pytest`, `respx`.
Interface : `svelte`, `@sveltejs/kit` (adaptateur statique), `pdfjs-dist`. Paquet : `briefcase` ou `pyinstaller` (choix en phase 11).

Écrits à la main : parseur MEDLINE/nbib, file de tâches, planificateur, surveillance de la boîte d'entrée, comparaison avec la liste des revues.
