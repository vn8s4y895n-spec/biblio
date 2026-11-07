-- 0010 : historique des questions posées sur l'écran Question (la plus récente d'abord, sans doublon).

CREATE TABLE question_history (
    question TEXT NOT NULL,
    scope    TEXT NOT NULL CHECK (scope IN ('online', 'library')),
    asked_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),  -- à la milliseconde : ordre exact
    times    INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (question, scope)
) WITHOUT ROWID;
CREATE INDEX question_history_recent ON question_history (asked_at);
