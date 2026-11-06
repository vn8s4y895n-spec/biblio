-- 0004 : fichiers (PDF rangés par empreinte), passages indexés en plein texte, notes, doublons suspectés.

CREATE TABLE files (
    sha256        TEXT PRIMARY KEY,           -- chemin : library/ab/cd/<sha256>.pdf
    size          INTEGER NOT NULL,
    mime          TEXT NOT NULL,
    pages         INTEGER,
    original_name TEXT,
    added_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    text_status   TEXT NOT NULL DEFAULT 'pending' CHECK (text_status IN ('pending', 'done', 'empty', 'error'))
);

CREATE TABLE work_files (
    work_id    INTEGER NOT NULL REFERENCES works (id) ON DELETE CASCADE,
    sha256     TEXT NOT NULL REFERENCES files (sha256),
    role       TEXT NOT NULL DEFAULT 'main',  -- main | supplement
    source_url TEXT,                          -- d'où vient le PDF (accès libre), NULL si déposé
    added_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (work_id, sha256)
) WITHOUT ROWID;
CREATE INDEX work_files_sha ON work_files (sha256);

-- Passages : unité de recherche (plein texte ici, sémantique en phase 5).
CREATE TABLE passages (
    id      INTEGER PRIMARY KEY,
    work_id INTEGER NOT NULL REFERENCES works (id) ON DELETE CASCADE,
    origin  TEXT NOT NULL CHECK (origin IN ('metadata', 'abstract', 'fulltext', 'note')),
    sha256  TEXT,                             -- fichier d'origine (fulltext)
    page    INTEGER,                          -- page d'origine (fulltext), à partir de 1
    ord     INTEGER NOT NULL,
    text    TEXT NOT NULL
);
CREATE INDEX passages_work ON passages (work_id, origin);

CREATE VIRTUAL TABLE passages_fts USING fts5 (
    text, content = 'passages', content_rowid = 'id', tokenize = 'porter unicode61 remove_diacritics 2'
);
CREATE TRIGGER passages_ai AFTER INSERT ON passages BEGIN
    INSERT INTO passages_fts (rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER passages_ad AFTER DELETE ON passages BEGIN
    INSERT INTO passages_fts (passages_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
CREATE TRIGGER passages_au AFTER UPDATE ON passages BEGIN
    INSERT INTO passages_fts (passages_fts, rowid, text) VALUES ('delete', old.id, old.text);
    INSERT INTO passages_fts (rowid, text) VALUES (new.id, new.text);
END;

-- Notes : le texte vit dans notes/<citekey>.md (compatible Obsidian) ; la base n'en garde que l'index.
CREATE TABLE notes (
    work_id     INTEGER PRIMARY KEY REFERENCES works (id) ON DELETE CASCADE,
    path        TEXT NOT NULL UNIQUE,         -- relatif au dossier notes/
    content_sha TEXT NOT NULL,
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE TABLE note_links (
    work_id        INTEGER NOT NULL REFERENCES works (id) ON DELETE CASCADE,
    target_citekey TEXT NOT NULL,             -- lien [[clé]] écrit dans la note de work_id
    PRIMARY KEY (work_id, target_citekey)
) WITHOUT ROWID;
CREATE INDEX note_links_target ON note_links (target_citekey);

CREATE TABLE dedup_candidates (
    work_a     INTEGER NOT NULL REFERENCES works (id) ON DELETE CASCADE,
    work_b     INTEGER NOT NULL REFERENCES works (id) ON DELETE CASCADE,
    reason     TEXT NOT NULL,                 -- fingerprint (même titre, année, premier auteur)
    status     TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'dismissed')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (work_a, work_b),
    CHECK (work_a < work_b)
) WITHOUT ROWID;
