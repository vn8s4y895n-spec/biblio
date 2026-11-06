-- 0002 : liste des revues recommandées (CDD / CNU santé, Sorbonne Université), versions conservées.

CREATE TABLE journal_lists (
    id           INTEGER PRIMARY KEY,
    source_url   TEXT NOT NULL,
    sha256       TEXT NOT NULL UNIQUE,
    published_on TEXT,
    fetched_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    file_path    TEXT,
    row_count    INTEGER NOT NULL,
    warnings     TEXT NOT NULL DEFAULT '[]'   -- anomalies repérées dans le fichier (JSON)
);

CREATE TABLE journals (
    id                INTEGER PRIMARY KEY,
    list_id           INTEGER NOT NULL REFERENCES journal_lists (id) ON DELETE CASCADE,
    name              TEXT,                   -- NULL : ligne du fichier avec un ISSN mais sans nom
    sheet_row         INTEGER,                -- numéro de ligne dans le fichier Excel
    abbrev            TEXT,
    abbrev_norm       TEXT,
    issn_print        TEXT,
    issn_e            TEXT,
    nlmid             TEXT,
    start_year        INTEGER,
    end_year          INTEGER,
    new_name          TEXT,
    withdrawn_on      TEXT,
    withdrawal_reason INTEGER   -- 1 nouveau nom, 2 n'est plus recommandée, 3 arrêt de publication
);
CREATE INDEX journals_issn_print ON journals (list_id, issn_print);
CREATE INDEX journals_issn_e ON journals (list_id, issn_e);
CREATE INDEX journals_nlmid ON journals (list_id, nlmid);
CREATE INDEX journals_abbrev ON journals (list_id, abbrev_norm);

-- Petits états du programme (date de la dernière vérification de la liste, dernière erreur…).
CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
