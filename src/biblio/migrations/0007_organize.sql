-- 0007 : collections et tags (même table, distingués par `kind`), recherches enregistrées.

CREATE TABLE labels (
    id         INTEGER PRIMARY KEY,
    kind       TEXT NOT NULL CHECK (kind IN ('collection', 'tag')),
    name       TEXT NOT NULL COLLATE NOCASE,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (kind, name)
);

CREATE TABLE work_labels (
    label_id INTEGER NOT NULL REFERENCES labels (id) ON DELETE CASCADE,
    work_id  INTEGER NOT NULL REFERENCES works (id) ON DELETE CASCADE,
    added_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (label_id, work_id)
) WITHOUT ROWID;
CREATE INDEX work_labels_work ON work_labels (work_id);

-- Une question, sa réponse et ses sources telles qu'affichées au moment de l'enregistrement.
-- `origin` : biblio (écran de question) ; claude (synthèse enregistrée par Claude via MCP, phase 10).
CREATE TABLE saved_searches (
    id           INTEGER PRIMARY KEY,
    question     TEXT NOT NULL,
    origin       TEXT NOT NULL DEFAULT 'biblio',
    scope        TEXT NOT NULL DEFAULT 'online' CHECK (scope IN ('online', 'library')),
    query        TEXT,
    answer_json  TEXT NOT NULL DEFAULT '[]',
    sources_json TEXT NOT NULL DEFAULT '[]',
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
