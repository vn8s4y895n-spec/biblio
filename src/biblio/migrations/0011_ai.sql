-- 0011 : IA. Résumés rédigés (cache par modèle et langue) ; synthèse rédigée gardée avec une recherche enregistrée
-- (par Biblio, ou par Claude via MCP en phase 10).

CREATE TABLE ai_summaries (
    key        TEXT PRIMARY KEY,   -- sha256(modèle | langue | titre + résumé)
    model      TEXT NOT NULL,
    lang       TEXT NOT NULL,
    text       TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

ALTER TABLE saved_searches ADD COLUMN synthesis_json TEXT;  -- {"text", "sentences", "model"} ; NULL sans synthèse
