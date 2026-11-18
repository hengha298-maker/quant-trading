-- Short-lived reader authorization, separate from holdings, reports and ledger history.
CREATE TABLE IF NOT EXISTS cloud_device_links (
  token_hash TEXT PRIMARY KEY CHECK(length(token_hash)=64),
  origin TEXT NOT NULL,
  created_epoch INTEGER NOT NULL,
  expires_epoch INTEGER NOT NULL,
  used_epoch INTEGER
);
