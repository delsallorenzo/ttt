CREATE TABLE IF NOT EXISTS samples(
  ts INTEGER NOT NULL,
  app_name TEXT,
  bundle_id TEXT,
  kind TEXT,
  domain TEXT,
  finder_path TEXT,
  title TEXT,
  is_idle INTEGER NOT NULL DEFAULT 0,
  success TEXT NOT NULL DEFAULT 'ok',
  error_code TEXT,
  profile TEXT
);
CREATE INDEX IF NOT EXISTS idx_samples_ts ON samples(ts);
