-- 0008 : questions qui ont mené à ajouter un article (écran Question). Un article peut en avoir plusieurs :
-- celle de son ajout, puis celles où il a été ajouté de nouveau alors qu'il était déjà là.

CREATE TABLE work_questions (
    work_id  INTEGER NOT NULL REFERENCES works (id) ON DELETE CASCADE,
    question TEXT NOT NULL,
    scope    TEXT NOT NULL DEFAULT 'online' CHECK (scope IN ('online', 'library')),
    added_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (work_id, question)
) WITHOUT ROWID;
