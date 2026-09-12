PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  start_date TEXT NOT NULL,
  end_date TEXT,
  description TEXT DEFAULT '',
  category TEXT NOT NULL DEFAULT 'Life',
  status TEXT NOT NULL DEFAULT 'confirmed' CHECK(status IN ('confirmed','booked','planned','cancelled','resold','uncertain')),
  confidence REAL NOT NULL DEFAULT 1.0 CHECK(confidence BETWEEN 0 AND 1),
  importance TEXT NOT NULL DEFAULT 'medium' CHECK(importance IN ('major','medium','minor')),
  place_id INTEGER REFERENCES places(id) ON DELETE SET NULL,
  trip_id INTEGER REFERENCES trips(id) ON DELETE SET NULL,
  review_state TEXT NOT NULL DEFAULT 'clear' CHECK(review_state IN ('clear','needs_review','resolved')),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sources (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  source_type TEXT NOT NULL,
  account TEXT DEFAULT '',
  notes TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS evidence (
  id INTEGER PRIMARY KEY,
  event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  source_id INTEGER REFERENCES sources(id) ON DELETE SET NULL,
  evidence_type TEXT NOT NULL DEFAULT 'record',
  source_ref TEXT DEFAULT '',
  observed_date TEXT,
  excerpt TEXT DEFAULT '',
  confidence REAL NOT NULL DEFAULT 1.0 CHECK(confidence BETWEEN 0 AND 1),
  imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS people (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  relationship TEXT DEFAULT '',
  notes TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS person_aliases (
  id INTEGER PRIMARY KEY,
  person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
  alias TEXT NOT NULL,
  normalized_alias TEXT NOT NULL UNIQUE,
  source TEXT NOT NULL DEFAULT 'manual',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(person_id, alias)
);

CREATE TABLE IF NOT EXISTS person_merge_history (
  id INTEGER PRIMARY KEY,
  source_person_id INTEGER NOT NULL,
  source_name TEXT NOT NULL,
  target_person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE RESTRICT,
  target_name TEXT NOT NULL,
  impact_json TEXT NOT NULL,
  source_snapshot_json TEXT NOT NULL,
  merged_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(source_person_id)
);

CREATE TABLE IF NOT EXISTS event_people (
  event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
  role TEXT DEFAULT 'with',
  PRIMARY KEY(event_id, person_id, role)
);

CREATE TABLE IF NOT EXISTS places (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  city TEXT DEFAULT '',
  region TEXT DEFAULT '',
  country TEXT DEFAULT '',
  latitude REAL,
  longitude REAL,
  notes TEXT DEFAULT '',
  UNIQUE(name, city, country)
);

CREATE TABLE IF NOT EXISTS trips (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  start_date TEXT,
  end_date TEXT,
  status TEXT NOT NULL DEFAULT 'confirmed' CHECK(status IN ('confirmed','booked','planned','cancelled','resold','uncertain')),
  notes TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS tags (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  color TEXT NOT NULL DEFAULT '#7c9cff'
);

CREATE TABLE IF NOT EXISTS event_tags (
  event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
  PRIMARY KEY(event_id, tag_id)
);

CREATE TABLE IF NOT EXISTS review_items (
  id INTEGER PRIMARY KEY,
  event_id INTEGER REFERENCES events(id) ON DELETE CASCADE,
  issue_type TEXT NOT NULL,
  summary TEXT NOT NULL,
  details TEXT DEFAULT '',
  status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','resolved','ignored')),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS imports (
  id INTEGER PRIMARY KEY,
  source_id INTEGER REFERENCES sources(id) ON DELETE SET NULL,
  filename TEXT NOT NULL,
  checksum TEXT NOT NULL,
  imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  row_count INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'completed',
  UNIQUE(checksum)
);

CREATE TABLE IF NOT EXISTS ingestion_runs (
  id INTEGER PRIMARY KEY,
  source_id INTEGER REFERENCES sources(id) ON DELETE SET NULL,
  connector TEXT NOT NULL,
  package_checksum TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'running' CHECK(status IN ('running','completed','failed')),
  discovered_count INTEGER NOT NULL DEFAULT 0 CHECK(discovered_count >= 0),
  inserted_count INTEGER NOT NULL DEFAULT 0 CHECK(inserted_count >= 0),
  skipped_count INTEGER NOT NULL DEFAULT 0 CHECK(skipped_count >= 0),
  review_count INTEGER NOT NULL DEFAULT 0 CHECK(review_count >= 0),
  started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  completed_at TEXT,
  UNIQUE(connector, package_checksum)
);

CREATE TABLE IF NOT EXISTS source_records (
  id INTEGER PRIMARY KEY,
  source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE RESTRICT,
  object_type TEXT NOT NULL,
  external_id TEXT NOT NULL,
  content_sha256 TEXT NOT NULL,
  source_ref TEXT DEFAULT '',
  observed_date TEXT,
  first_seen_run_id INTEGER REFERENCES ingestion_runs(id) ON DELETE SET NULL,
  last_seen_run_id INTEGER REFERENCES ingestion_runs(id) ON DELETE SET NULL,
  first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(source_id, object_type, external_id)
);

CREATE TABLE IF NOT EXISTS event_source_records (
  event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  source_record_id INTEGER NOT NULL REFERENCES source_records(id) ON DELETE RESTRICT,
  relationship TEXT NOT NULL DEFAULT 'origin' CHECK(relationship IN ('origin','evidence','corroboration')),
  linked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(event_id, source_record_id),
  UNIQUE(source_record_id)
);

CREATE TABLE IF NOT EXISTS entity_links (
  id INTEGER PRIMARY KEY,
  entity_type TEXT NOT NULL CHECK(entity_type IN ('event','trip','place','person')),
  entity_id INTEGER NOT NULL,
  label TEXT NOT NULL,
  url TEXT NOT NULL,
  link_type TEXT NOT NULL DEFAULT 'website',
  notes TEXT DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS chapters (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  start_date TEXT NOT NULL,
  end_date TEXT,
  summary TEXT DEFAULT '',
  color TEXT NOT NULL DEFAULT '#8ba3ff',
  confidence REAL NOT NULL DEFAULT 1.0 CHECK(confidence BETWEEN 0 AND 1)
);

CREATE TABLE IF NOT EXISTS media (
  id INTEGER PRIMARY KEY,
  event_id INTEGER REFERENCES events(id) ON DELETE CASCADE,
  person_id INTEGER REFERENCES people(id) ON DELETE CASCADE,
  captured_date TEXT,
  media_type TEXT NOT NULL DEFAULT 'photo' CHECK(media_type IN ('photo','video','audio','document')),
  local_path TEXT DEFAULT '',
  external_url TEXT DEFAULT '',
  caption TEXT DEFAULT '',
  is_featured INTEGER NOT NULL DEFAULT 0 CHECK(is_featured IN (0,1)),
  source_id INTEGER REFERENCES sources(id) ON DELETE SET NULL,
  source_ref TEXT DEFAULT '',
  mime_type TEXT NOT NULL DEFAULT 'image/webp',
  sha256 TEXT DEFAULT '',
  width INTEGER,
  height INTEGER,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS weather_cache (
  event_id INTEGER PRIMARY KEY REFERENCES events(id) ON DELETE CASCADE,
  provider TEXT NOT NULL DEFAULT 'Open-Meteo',
  weather_json TEXT NOT NULL,
  fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS agent_mutations (
  id INTEGER PRIMARY KEY,
  idempotency_key TEXT NOT NULL UNIQUE,
  operation TEXT NOT NULL,
  target_type TEXT NOT NULL,
  target_id INTEGER NOT NULL,
  request_sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_events_dates ON events(start_date, end_date);
CREATE INDEX IF NOT EXISTS idx_events_status ON events(status);
CREATE INDEX IF NOT EXISTS idx_evidence_event ON evidence(event_id);
CREATE INDEX IF NOT EXISTS idx_review_status ON review_items(status);
CREATE INDEX IF NOT EXISTS idx_entity_links ON entity_links(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_chapters_dates ON chapters(start_date, end_date);
CREATE INDEX IF NOT EXISTS idx_media_event ON media(event_id, captured_date);
CREATE INDEX IF NOT EXISTS idx_person_aliases_person ON person_aliases(person_id);
CREATE INDEX IF NOT EXISTS idx_person_merge_target ON person_merge_history(target_person_id, merged_at);
CREATE INDEX IF NOT EXISTS idx_ingestion_runs_source ON ingestion_runs(source_id, started_at);
CREATE INDEX IF NOT EXISTS idx_source_records_last_seen ON source_records(last_seen_run_id);
CREATE INDEX IF NOT EXISTS idx_event_source_record ON event_source_records(source_record_id, event_id);
CREATE INDEX IF NOT EXISTS idx_event_people_person ON event_people(person_id, event_id);
CREATE INDEX IF NOT EXISTS idx_event_tags_tag ON event_tags(tag_id, event_id);
CREATE INDEX IF NOT EXISTS idx_evidence_source_ref ON evidence(source_id, source_ref);
CREATE INDEX IF NOT EXISTS idx_media_sha256 ON media(sha256) WHERE sha256 <> '';
