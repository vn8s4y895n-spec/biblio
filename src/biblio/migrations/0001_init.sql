-- 0001 : notices, identifiants, auteurs, sujets, journal des modifications.

CREATE TABLE works (
    id               INTEGER PRIMARY KEY,
    citekey          TEXT NOT NULL UNIQUE,
    csl_type         TEXT NOT NULL DEFAULT 'article-journal',
    study_type       TEXT,
    title            TEXT NOT NULL,
    abstract         TEXT,
    container_title  TEXT,
    container_abbrev TEXT,
    issn_print       TEXT,
    issn_e           TEXT,
    nlmid            TEXT,
    year             INTEGER,
    issued           TEXT,
    volume           TEXT,
    issue            TEXT,
    pages            TEXT,
    language         TEXT,
    license          TEXT,
    read_status      TEXT CHECK (read_status IN ('to_read', 'read', 'to_reread')),
    rating           INTEGER CHECK (rating BETWEEN 1 AND 5),
    fingerprint      TEXT NOT NULL,
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX works_fingerprint ON works (fingerprint);

-- La clé primaire (scheme, value) interdit qu'un même DOI/PMID désigne deux notices.
CREATE TABLE identifiers (
    scheme  TEXT NOT NULL,
    value   TEXT NOT NULL,
    work_id INTEGER NOT NULL REFERENCES works (id) ON DELETE CASCADE,
    PRIMARY KEY (scheme, value)
) WITHOUT ROWID;
CREATE INDEX identifiers_work ON identifiers (work_id);

CREATE TABLE work_authors (
    work_id  INTEGER NOT NULL REFERENCES works (id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    family   TEXT NOT NULL,
    given    TEXT,
    orcid    TEXT,
    PRIMARY KEY (work_id, position)
) WITHOUT ROWID;
CREATE INDEX work_authors_orcid ON work_authors (orcid) WHERE orcid IS NOT NULL;

CREATE TABLE subjects (
    id     INTEGER PRIMARY KEY,
    scheme TEXT NOT NULL,
    term   TEXT NOT NULL,
    ui     TEXT,
    UNIQUE (scheme, term)
);

CREATE TABLE work_subjects (
    work_id    INTEGER NOT NULL REFERENCES works (id) ON DELETE CASCADE,
    subject_id INTEGER NOT NULL REFERENCES subjects (id),
    major      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (work_id, subject_id)
) WITHOUT ROWID;

CREATE TABLE changes (
    id       INTEGER PRIMARY KEY,
    work_id  INTEGER REFERENCES works (id) ON DELETE SET NULL,
    client   TEXT NOT NULL,
    at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    op       TEXT NOT NULL,
    field    TEXT,
    old_json TEXT,
    new_json TEXT
);
CREATE INDEX changes_work ON changes (work_id);
