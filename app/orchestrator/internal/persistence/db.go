package persistence

import (
	"database/sql"
	"fmt"
	"log/slog"
	"os"
	"path/filepath"

	_ "modernc.org/sqlite"
)

const schema = `
CREATE TABLE IF NOT EXISTS runtime_settings (
	id                   INTEGER PRIMARY KEY,
	engine_config        TEXT NOT NULL DEFAULT '{}',
	engine_settings      TEXT NOT NULL DEFAULT '{}',
	orchestrator_settings TEXT NOT NULL DEFAULT '{}',
	proxy_settings       TEXT NOT NULL DEFAULT '{}',
	vpn_settings         TEXT NOT NULL DEFAULT '{}',
	updated_at           DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS vpn_credentials (
	id          TEXT PRIMARY KEY,
	settings_id INTEGER NOT NULL REFERENCES runtime_settings(id),
	provider    TEXT,
	protocol    TEXT,
	payload     TEXT NOT NULL DEFAULT '{}',
	created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS vpn_server (
	id                TEXT PRIMARY KEY,
	source            TEXT NOT NULL CHECK(source IN ('proton','gluetun')),
	hostname          TEXT NOT NULL,
	ips               TEXT NOT NULL DEFAULT '[]',
	country           TEXT NOT NULL DEFAULT '',
	country_code      TEXT NOT NULL DEFAULT '',
	city              TEXT NOT NULL DEFAULT '',
	server_name       TEXT NOT NULL DEFAULT '',
	tier              INTEGER NOT NULL DEFAULT 0,
	flags             TEXT NOT NULL DEFAULT '{}',
	load_pct          INTEGER,
	status            TEXT NOT NULL DEFAULT 'unknown' CHECK(status IN ('up','down','unknown')),
	quarantined_until DATETIME,
	quarantine_reason TEXT,
	pinned            INTEGER NOT NULL DEFAULT 0,
	first_seen_at     DATETIME NOT NULL,
	last_seen_at      DATETIME NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS vpn_server_source_hostname ON vpn_server(source, hostname);

CREATE TABLE IF NOT EXISTS vpn_probe (
	id          INTEGER PRIMARY KEY AUTOINCREMENT,
	server_id   TEXT NOT NULL REFERENCES vpn_server(id),
	content_id  TEXT NOT NULL DEFAULT '',
	category    TEXT NOT NULL DEFAULT 'long-tail',
	started_at  DATETIME NOT NULL,
	outcome     TEXT NOT NULL CHECK(outcome IN ('success','timeout','engine_error','vpn_error','peer_starved','dropped')),
	ttfb_ms     INTEGER,
	duration_ms INTEGER,
	peers_max   INTEGER,
	bytes_down  INTEGER NOT NULL DEFAULT 0,
	engine_id   TEXT NOT NULL DEFAULT '',
	lease_id    TEXT NOT NULL DEFAULT '',
	meta        TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS vpn_probe_server_started   ON vpn_probe(server_id, started_at DESC);
CREATE INDEX IF NOT EXISTS vpn_probe_content_started  ON vpn_probe(content_id, started_at DESC);
CREATE INDEX IF NOT EXISTS vpn_probe_category_started ON vpn_probe(category,   started_at DESC);

CREATE TABLE IF NOT EXISTS vpn_reputation (
	server_id       TEXT NOT NULL REFERENCES vpn_server(id),
	category        TEXT NOT NULL,
	window          TEXT NOT NULL DEFAULT '30d' CHECK(window IN ('1h','24h','7d','30d')),
	probes_n        INTEGER NOT NULL DEFAULT 0,
	successes_n     INTEGER NOT NULL DEFAULT 0,
	success_rate    REAL NOT NULL DEFAULT 0,
	ttfb_p50_ms     INTEGER,
	ttfb_p95_ms     INTEGER,
	duration_avg_ms INTEGER,
	drops_n         INTEGER NOT NULL DEFAULT 0,
	score           REAL NOT NULL DEFAULT 0,
	score_color     TEXT NOT NULL DEFAULT 'red' CHECK(score_color IN ('green','amber','magenta','red')),
	low_confidence  INTEGER NOT NULL DEFAULT 1,
	history_30      TEXT NOT NULL DEFAULT '[]',
	updated_at      DATETIME NOT NULL,
	PRIMARY KEY (server_id, category, window)
);
`

// Open opens (or creates) the SQLite database at path and applies the schema.
func Open(path string) (*sql.DB, error) {
	if err := os.MkdirAll(filepath.Dir(path), 0755); err != nil {
		return nil, fmt.Errorf("create db directory: %w", err)
	}
	db, err := sql.Open("sqlite", fmt.Sprintf("file:%s?_foreign_keys=on&_journal_mode=WAL", path))
	if err != nil {
		return nil, fmt.Errorf("open sqlite: %w", err)
	}
	db.SetMaxOpenConns(1) // SQLite WAL mode, single writer
	if _, err := db.Exec(schema); err != nil {
		db.Close()
		return nil, fmt.Errorf("apply schema: %w", err)
	}
	slog.Info("SQLite opened", "path", path)
	return db, nil
}
