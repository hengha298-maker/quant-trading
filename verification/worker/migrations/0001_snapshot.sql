CREATE TABLE IF NOT EXISTS private_snapshot (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  report_id TEXT NOT NULL,
  generated_at TEXT NOT NULL,
  archive_sha256 TEXT NOT NULL,
  content_sha256 TEXT NOT NULL,
  upload_epoch INTEGER NOT NULL,
  received_at TEXT NOT NULL,
  body TEXT NOT NULL CHECK (length(CAST(body AS BLOB)) <= 1600000)
);
