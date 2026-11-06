-- 0003 : cache des réponses des sources en ligne (recherches), avec date d'expiration.

CREATE TABLE http_cache (
    key          TEXT PRIMARY KEY,    -- sha256 de l'URL et des paramètres
    url          TEXT NOT NULL,       -- sans les paramètres (aucune clé API stockée en clair)
    status       INTEGER NOT NULL,
    content_type TEXT,
    body         BLOB NOT NULL,
    fetched_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    expires_at   REAL NOT NULL        -- horodatage Unix
);
CREATE INDEX http_cache_expires ON http_cache (expires_at);
