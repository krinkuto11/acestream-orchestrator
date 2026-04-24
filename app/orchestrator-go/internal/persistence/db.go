package persistence

import (
	"database/sql"
	"fmt"
	"log/slog"

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
`

// Open opens (or creates) the SQLite database at path and applies the schema.
func Open(path string) (*sql.DB, error) {
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
