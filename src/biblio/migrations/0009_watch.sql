-- 0009 : veille. Recherches relancées périodiquement, avis sur les articles (rétractation, correction,
-- version publiée d'un preprint), alertes à lire.

ALTER TABLE saved_searches ADD COLUMN watch_days INTEGER;  -- NULL : pas suivie ; sinon intervalle en jours
ALTER TABLE saved_searches ADD COLUMN last_run TEXT;        -- dernière relance par la veille

-- Résultats déjà vus d'une recherche suivie (clé DOI, PMID ou titre normalisé + année).
CREATE TABLE search_seen (
    search_id  INTEGER NOT NULL REFERENCES saved_searches (id) ON DELETE CASCADE,
    key        TEXT NOT NULL,
    first_seen TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (search_id, key)
) WITHOUT ROWID;

-- Avis publiés sur un article de la bibliothèque (Crossref, dont les données de Retraction Watch).
CREATE TABLE work_updates (
    work_id    INTEGER NOT NULL REFERENCES works (id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,   -- retraction, correction, expression_of_concern, erratum, published (preprint publié)…
    notice_doi TEXT NOT NULL,   -- DOI de l'avis, ou de la version publiée
    date       TEXT,
    source     TEXT,            -- retraction-watch, publisher
    found_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (work_id, kind, notice_doi)
) WITHOUT ROWID;

CREATE TABLE alerts (
    id         INTEGER PRIMARY KEY,
    kind       TEXT NOT NULL CHECK (kind IN ('new_results', 'retraction', 'concern', 'correction', 'published')),
    work_id    INTEGER REFERENCES works (id) ON DELETE CASCADE,
    search_id  INTEGER REFERENCES saved_searches (id) ON DELETE CASCADE,
    data_json  TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    seen_at    TEXT
);
CREATE INDEX alerts_unseen ON alerts (created_at) WHERE seen_at IS NULL;
