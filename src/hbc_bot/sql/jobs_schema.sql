-- Job history for hbc-bot (SQLite). Applied via executescript on startup.
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tool TEXT NOT NULL,
    command TEXT NOT NULL,
    args TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    user_name TEXT NOT NULL,
    status TEXT NOT NULL,
    returncode INTEGER,
    created_at REAL NOT NULL,
    started_at REAL,
    ended_at REAL,
    log_path TEXT
);
