-- 0005 : fichiers déjà examinés dans un dossier surveillé (Téléchargements), pour ne pas les relire.

CREATE TABLE watched_files (
    path   TEXT NOT NULL,
    mtime  REAL NOT NULL,
    size   INTEGER NOT NULL,
    status TEXT NOT NULL,   -- attached, duplicate, ignored (pas un article reconnu), refused (pas un PDF lisible)
    seen_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (path, mtime, size)
) WITHOUT ROWID;
