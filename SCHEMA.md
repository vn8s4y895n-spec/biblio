# Schéma de la base (`biblio.sqlite`)

SQLite en mode WAL, clés étrangères actives. Les migrations sont dans `src/biblio/migrations/NNNN_nom.sql` : chacune est appliquée une seule fois, dans une transaction, au démarrage du serveur. Elles sont tracées dans `schema_migrations`. Les dates sont en UTC, au format ISO 8601 (`2026-11-06T21:37:00Z`).

Ce document décrit le schéma **réellement en place**. Les tables prévues pour les phases suivantes sont dans [PLAN.md](PLAN.md).

## `schema_migrations` (version actuelle : 1)

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

## Exemple de requête

```sql
-- Articles de type essai randomisé avec leur DOI
SELECT w.citekey, w.title, i.value AS doi
FROM works w LEFT JOIN identifiers i ON i.work_id = w.id AND i.scheme = 'doi'
WHERE w.study_type = 'rct';
```
