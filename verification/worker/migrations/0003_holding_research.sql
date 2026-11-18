CREATE TABLE IF NOT EXISTS cloud_holding_reports (
 id TEXT PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
 status TEXT NOT NULL, trigger_kind TEXT NOT NULL, body TEXT
);
