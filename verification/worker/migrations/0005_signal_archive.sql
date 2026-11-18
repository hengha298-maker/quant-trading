CREATE TABLE IF NOT EXISTS cloud_signal_versions(event_id TEXT NOT NULL, version_sha TEXT NOT NULL, cohort TEXT NOT NULL, signal_at TEXT NOT NULL, symbol TEXT NOT NULL, imported_at TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY(event_id,version_sha));
CREATE TABLE IF NOT EXISTS cloud_signal_outcomes(report_id TEXT NOT NULL, event_id TEXT NOT NULL, evaluated_at TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY(report_id,event_id));
CREATE TABLE IF NOT EXISTS cloud_tracking_runs(report_id TEXT PRIMARY KEY,status TEXT NOT NULL,at TEXT NOT NULL,code TEXT,events_written INTEGER);
