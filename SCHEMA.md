# Schéma de la base (`biblio.sqlite`)

SQLite en mode WAL, clés étrangères actives. Les migrations sont dans `src/biblio/migrations/NNNN_nom.sql` : chacune est appliquée une seule fois, dans une transaction, au démarrage du serveur. Elles sont tracées dans `schema_migrations`. Les dates sont en UTC, au format ISO 8601 (`2026-11-06T21:37:00Z`).

Ce document décrit le schéma **réellement en place**. Les tables prévues pour les phases suivantes sont dans [PLAN.md](PLAN.md).

## `schema_migrations` (version actuelle : 10)

| Colonne | Type | Rôle |
|---|---|---|
| version | INTEGER PK | Numéro de la migration |
| applied_at | TEXT | Date d'application |

## `works` : une ligne par article

| Colonne | Type | Rôle |
|---|---|---|
| id | INTEGER PK | |
| citekey | TEXT UNIQUE | Clé de citation `auteurAnnéeMot` (ex. `harris2018effect`). Suffixe `a`, `b`… en cas de collision. Stable : jamais recalculée. |
| csl_type | TEXT | Type CSL (`article-journal`, `chapter`…) |
| study_type | TEXT | `meta-analysis`, `systematic-review`, `rct`, `clinical-trial`, `guideline`, `observational`, `case-report`, `cohort`, `case-control`, `cross-sectional`, `review` ou NULL. Déduit des types de publication PubMed, puis des MeSH de schéma d'étude. |
| title, abstract | TEXT | Titre sans balises. Résumé structuré sous la forme `ÉTIQUETTE: texte`, une section par ligne. |
| container_title, container_abbrev | TEXT | Revue (nom complet, abréviation ISO) |
| issn_print, issn_e, nlmid | TEXT | Identifiants de la revue. Ils serviront à la comparaison avec la liste des revues recommandées (phase 2). |
| year | INTEGER | Année de publication |
| issued | TEXT | Date de publication, aussi précise que connue : `2018`, `2018-09` ou `2018-09-11` |
| volume, issue, pages | TEXT | |
| language | TEXT | Code ISO 639-1 (`en`, `fr`…) quand il est connu |
| license | TEXT | URL de la licence (Crossref) |
| read_status | TEXT | `to_read`, `read`, `to_reread` ou NULL |
| rating | INTEGER | 1 à 5 ou NULL |
| fingerprint | TEXT, indexé | `titre normalisé|année|premier auteur`, pour repérer les doublons sans identifiant commun (phase 4) |
| created_at, updated_at | TEXT | |

## `identifiers` : identifiants d'un article

| Colonne | Type | Rôle |
|---|---|---|
| scheme | TEXT | `doi`, `pmid`, `pmcid` (d'autres viendront : `arxiv`, `openalex`…) |
| value | TEXT | Normalisée : DOI en minuscules, PMCID en majuscules |
| work_id | INTEGER → works | |

**Clé primaire (scheme, value)** : un même identifiant ne peut pas désigner deux notices. C'est la base de la garantie « pas de doublon ».

## `work_authors` : auteurs, dans l'ordre

| Colonne | Type | Rôle |
|---|---|---|
| work_id, position | PK | Position à partir de 0 |
| family | TEXT | Nom (ou nom du groupe, pour un auteur collectif) |
| given | TEXT | Prénoms |
| orcid | TEXT, indexé | `0000-0002-…` quand il est connu |

Les auteurs sont stockés par article, sans table d'auteurs commune. Fusionner les homonymes sans ORCID créerait de fausses identités ; l'index sur `orcid` suffit pour retrouver les articles d'un même chercheur.

## `subjects` et `work_subjects` : MeSH et mots-clés

| `subjects` | Rôle |
|---|---|
| id | |
| scheme | `mesh` ou `keyword` |
| term | Libellé (unique par schéma) |
| ui | Identifiant MeSH (ex. `D016470`) |

| `work_subjects` | Rôle |
|---|---|
| work_id, subject_id | PK |
| major | 1 si sujet majeur (MeSH `MajorTopicYN`) |

## `changes` : journal des modifications

| Colonne | Rôle |
|---|---|
| id | |
| work_id | Notice concernée |
| client | Origine : `api`, `cli`, `web`, `mcp`, `inbox`, `worker`, `package` |
| at | Date |
| op | `create`, `update` |
| field | Champ modifié (`abstract`, `identifiers.pmid`, `authors`…), NULL pour `create` |
| old_json, new_json | Valeurs avant et après, en JSON (notice complète pour `create`) |

Les identifiants peuvent aussi être `arxiv`, `url` (saisie manuelle d'une ressource web) et `s2` / `openalex`.

## Fichiers, passages, notes, doublons (migration 4)

### `files` : un PDF, rangé dans `library/<sha[0:2]>/<sha[2:4]>/<sha256>.pdf`

| Colonne | Rôle |
|---|---|
| sha256 | Empreinte du contenu (PK) : un même PDF n'est stocké qu'une fois |
| size, mime, pages | |
| original_name | Nom du fichier déposé, ou fin de l'URL de téléchargement |
| added_at | |
| text_status | `pending`, `done`, `empty` (PDF scanné sans texte), `error` |

### `work_files` : rattachement d'un PDF à une notice

`work_id`, `sha256`, `role` (`main`), `source_url` (adresse de la version libre téléchargée, NULL si déposé), `added_at`. Un PDF sans ligne ici attend une saisie assistée.

### `passages` et `passages_fts` : unités de recherche

| Colonne | Rôle |
|---|---|
| id, work_id | |
| origin | `metadata` (titre, auteurs, revue, année, MeSH), `abstract` (une section par passage), `fulltext`, `note` |
| sha256, page | Pour `fulltext` : fichier et page d'origine (à partir de 1) |
| ord | Ordre dans l'origine |
| text | ~1 200 caractères au plus, coupés aux limites de phrases |

`passages_fts` est l'index plein texte FTS5 (BM25) de `passages.text`, tenu à jour par des déclencheurs. Le tokenizer est `porter unicode61 remove_diacritics 2` : accents ignorés, racinisation anglaise. Une seule table d'index couvre métadonnées, résumés, notes et textes intégraux, au lieu des deux prévues au plan.

### `notes` et `note_links`

Le texte d'une note vit dans `notes/<citekey>.md`, avec un en-tête YAML (citekey, title, year, doi) ; la base n'en garde que l'index :
- `notes` : `work_id` (PK), `path` (relatif à `notes/`), `content_sha` (empreinte, pour repérer une modification extérieure), `updated_at` ;
- `note_links` : `(work_id, target_citekey)`, un lien `[[clé]]` écrit dans la note de `work_id`. Les rétroliens sont la lecture inverse.

### `dedup_candidates` : doublons probables

`(work_a, work_b)` avec `work_a < work_b`, `reason` (`fingerprint`), `status` (`open`, `dismissed`), `created_at`. Une ligne est créée quand une nouvelle notice a la même empreinte (titre, année, premier auteur) qu'une notice existante sans partager d'identifiant. La fusion (`merge_works`) reprend identifiants, sujets, fichiers, note et historique dans la notice gardée, puis supprime l'autre.

### Vecteurs sémantiques (migration 6)

**Extension requise** : ces tables utilisent l'extension [sqlite-vec](https://github.com/asg017/sqlite-vec). Biblio la charge à chaque connexion. Un autre client SQLite peut lire les autres tables sans elle, mais ne peut pas supprimer de passages (des déclencheurs écrivent dans `passage_bits`).

- `passage_bits` (table virtuelle `vec0`, `embedding bit[1024]`, `rowid` = `passages.id`) : vecteur binaire (signe de chaque dimension), pour la présélection rapide par distance de Hamming (400 candidats).
- `passage_vectors` : `passage_id` (PK → passages, suppression en cascade), `model` (`BAAI/bge-m3`), `vector` (1024 × float16, normalisé), pour le classement exact par cosinus des candidats.
- **Cohérence** : des déclencheurs suppriment les deux vecteurs quand un passage est supprimé ou que son texte change. Le passage repart alors dans la file d'indexation, qui n'est autre que la liste des passages sans vecteur.

### `watched_files` : fichiers déjà examinés dans les Téléchargements (migration 5)

`(path, mtime, size)` (PK), `status` (`attached`, `duplicate`, `ignored` : pas un article reconnu, `refused` : pas un PDF lisible), `seen_at`. Un fichier modifié (autre date ou taille) est réexaminé. La date d'activation de la surveillance est dans `meta` (`downloads.since`).

## Rangement et recherches enregistrées (migration 7)

### `labels` : collections et tags

| Colonne | Rôle |
|---|---|
| id | Clé |
| kind | `collection` ou `tag` (même table : ils ne diffèrent que par leur présentation) |
| name | Nom, unique par type, casse ignorée (`COLLATE NOCASE`) |
| created_at | Date de création |

### `work_labels` : notices rangées

`(label_id, work_id)` en clé primaire, `added_at`. Supprimer une collection ou un tag ne supprime pas ses notices ; supprimer une notice la retire de ses collections. À la fusion de deux notices, les rangements sont repris. Chaque ajout ou retrait est inscrit au journal `changes` (champ `collection` ou `tag`).

### `saved_searches` : recherches enregistrées

| Colonne | Rôle |
|---|---|
| question | La question posée |
| origin | `biblio` (écran Question) ; `claude` réservé aux synthèses enregistrées par Claude via MCP (phase 10) |
| scope | `online` ou `library` |
| query | Requête envoyée aux bases (éventuellement corrigée à la main) |
| answer_json | Phrases de la réponse : `[{"text", "source", "score", "page"?}]`, `source` = numéro dans `sources_json` (1…) |
| sources_json | Sources telles qu'affichées (notices complètes, avec résumé Biblio et effectif) |
| created_at, updated_at | Dates |

Les sources sont copiées telles quelles : une recherche enregistrée se relit à l'identique, même hors ligne, et « Relancer » la refait avec les données du jour.

### `work_questions` : questions d'origine (migration 8)

La question posée sur l'écran Question qui a mené à ajouter l'article : `(work_id, question)` en clé primaire, `scope` (`online` ou `library`), `added_at`. Un article ajouté de nouveau depuis une autre question en garde une ligne de plus ; la même question n'est gardée qu'une fois. Reprises à la fusion de deux notices.

## Veille (migration 9)

- `saved_searches` reçoit `watch_days` (NULL : non suivie ; sinon intervalle de relance en jours) et `last_run`.
- `search_seen (search_id, key, first_seen)` : résultats déjà vus d'une recherche suivie. `key` : `doi:…`, `pmid:…`, `pmcid:…` ou titre normalisé + année. Les sources enregistrées avec la recherche comptent comme vues.
- `work_updates (work_id, kind, notice_doi, date, source, found_at)` : avis sur un article. `kind` : type Crossref (`retraction`, `expression_of_concern`, `correction`, `erratum`…) ou `published` (version publiée d'un preprint, `notice_doi` = DOI de la revue). `source` : `retraction-watch`, `publisher`, `crossref`. Les avis sont repris à la fusion de deux notices, sauf `published`.
- `alerts (id, kind, work_id, search_id, data_json, created_at, seen_at)` : alertes à lire. `kind` : `new_results` (`data_json` : question, nombre, notices nouvelles), `retraction`, `concern`, `correction`, `published` (`data_json` : DOI de l'avis, date, source). Une seule alerte non lue par article et par type.
- Identifiant `preprint-doi` : DOI d'un preprint fusionné dans sa version publiée.

## `question_history` : questions posées (migration 10)

`(question, scope)` en clé primaire, `asked_at` (à la milliseconde), `times` (nombre de fois posée). Chaque question de l'écran Question y est inscrite ; la reposer la fait remonter en tête.

## Liste des revues recommandées (migration 2)

Source : la liste de la Conférence des doyens et du CNU santé, publiée par Sorbonne Université. Chaque version téléchargée ou importée est conservée. La version **en vigueur** est la dernière enregistrée (`max(id)`).

### `journal_lists` : une ligne par version de la liste

| Colonne | Rôle |
|---|---|
| id | |
| source_url | URL du fichier, ou `manual:<nom>` pour un import manuel |
| sha256 | Empreinte du fichier (UNIQUE) : une version déjà connue n'est jamais réimportée |
| published_on | Date tirée du nom du fichier (`…_01-10-26.xlsx` → `2026-10-01`) |
| fetched_at | Date d'enregistrement |
| file_path | Copie du fichier dans `journals/` du dossier de données |
| row_count | Nombre de revues |
| warnings | Anomalies repérées dans le fichier (JSON) : lignes sans nom, colonne décalée… |

### `journals` : les revues d'une version

| Colonne | Rôle |
|---|---|
| id, list_id → journal_lists | |
| sheet_row | Numéro de ligne dans le fichier Excel |
| name | Nom complet. NULL si la ligne du fichier n'a qu'un identifiant. |
| abbrev, abbrev_norm | Abréviation NLM ; `abbrev_norm` = `lower(trim(replace(abbrev, '.', '')))` |
| issn_print, issn_e | Normalisés `NNNN-NNNN` : espaces insécables retirés, zéro initial ajouté si besoin |
| nlmid | Identifiant NLM |
| start_year, end_year | Années de publication |
| new_name | Nouveau nom (revue renommée) |
| withdrawn_on | Date de retrait de la liste |
| withdrawal_reason | 1 nouveau nom, 2 n'est plus recommandée, 3 arrêt de publication |

Index : `(list_id, issn_print)`, `(list_id, issn_e)`, `(list_id, nlmid)`, `(list_id, abbrev_norm)`.

### Vue `work_journal_status` : statut de revue de chaque notice

`work_id`, `journal_id` (ligne retenue dans la liste en vigueur, ou NULL), `status` :

| status | Règle |
|---|---|
| `no_list` | Aucune liste chargée |
| `recommended` | Revue trouvée dans la liste, active ou retirée pour motif 1 ou 3 |
| `withdrawn` | Revue trouvée, retirée pour motif 2. Une date de retrait sans motif est traitée de même, par prudence. |
| `preprint` | `csl_type = 'article'` (preprint Crossref) |
| `unidentified` | Ni ISSN, ni NLMID, ni abréviation |
| `not_listed` | Sinon |

**Correspondance.** On cherche successivement par NLMID, par ISSN papier, par ISSN électronique (chacun des deux ISSN de la notice contre chacune des deux colonnes), puis par abréviation normalisée. L'identifiant le plus sûr l'emporte ; à égalité, la ligne active passe avant la ligne retirée. Chaque recherche est indexée, réunies par `UNION ALL` : environ 30 ms pour filtrer 10 000 notices.

La vue est du SQL standard : `SELECT * FROM work_journal_status` marche dans n'importe quel client SQLite. Elle est recréée au démarrage depuis `journals.py`, qui reste la seule définition de la règle.

## `http_cache` : réponses des sources en ligne (migration 3)

Cache des recherches en ligne (un jour) et des réponses Unpaywall (sept jours). On peut le vider sans aucune perte : `DELETE FROM http_cache`.

| Colonne | Rôle |
|---|---|
| key | sha256 de l'URL et des paramètres (PK) |
| url | URL sans les paramètres : aucune clé API n'est stockée en clair |
| status, content_type, body | Réponse |
| fetched_at | Date de la requête |
| expires_at | Expiration (horodatage Unix, indexé) ; les entrées expirées sont purgées à chaque écriture |

## `meta` : petits états du programme

| key | value |
|---|---|
| `journals.last_check` | Dernière vérification réussie de la liste en ligne |
| `journals.last_error` | Dernière erreur de vérification (supprimée après une vérification réussie) |
| `index.version` | Version de la méthode d'extraction du texte des PDF ; si elle change, les PDF sont réindexés au démarrage |
| `librarybib.state` | État de la bibliothèque (dernier changement, nombre de notices) lors de la dernière écriture de `library.bib` |
| `watch.library_check` | Dernière vérification des articles de la bibliothèque (rétractations, preprints publiés) |
| `downloads.since` | Début de la surveillance des Téléchargements (horodatage Unix) : les fichiers plus anciens sont ignorés |

## Exemples de requêtes

```sql
-- Articles de type essai randomisé avec leur DOI
SELECT w.citekey, w.title, i.value AS doi
FROM works w LEFT JOIN identifiers i ON i.work_id = w.id AND i.scheme = 'doi'
WHERE w.study_type = 'rct';

-- Articles publiés dans une revue hors liste ou retirée
SELECT w.citekey, w.container_title, s.status
FROM works w JOIN work_journal_status s ON s.work_id = w.id
WHERE s.status IN ('not_listed', 'withdrawn');
```
