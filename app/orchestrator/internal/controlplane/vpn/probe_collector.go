package vpn

import (
	"context"
	"database/sql"
	"log/slog"
	"sync/atomic"
	"time"

	"github.com/acestream/acestream/internal/persistence"
)

// ProbeCollector is an async, bounded queue that writes VPN probes to SQLite
// without blocking the hot data path. Dropped probes are counted.
type ProbeCollector struct {
	db      *sql.DB
	queue   chan persistence.VPNProbeRow
	dropped atomic.Int64
	notify  func(persistence.VPNProbeRow) // called after successful DB write (for SSE)
}

func NewProbeCollector(db *sql.DB, notify func(persistence.VPNProbeRow)) *ProbeCollector {
	return &ProbeCollector{
		db:     db,
		queue:  make(chan persistence.VPNProbeRow, 1000),
		notify: notify,
	}
}

// Record enqueues a probe for async write. Never blocks; drops if queue is full.
func (pc *ProbeCollector) Record(probe persistence.VPNProbeRow) {
	select {
	case pc.queue <- probe:
	default:
		pc.dropped.Add(1)
		slog.Warn("ProbeCollector queue full, probe dropped",
			"server_id", probe.ServerID,
			"dropped_total", pc.dropped.Load(),
		)
	}
}

// Run drains the queue and writes probes to the DB. Call in a goroutine.
func (pc *ProbeCollector) Run(ctx context.Context) {
	for {
		select {
		case <-ctx.Done():
			// Drain remaining items before exit.
			for {
				select {
				case probe := <-pc.queue:
					pc.write(probe)
				default:
					return
				}
			}
		case probe := <-pc.queue:
			pc.write(probe)
		}
	}
}

func (pc *ProbeCollector) write(probe persistence.VPNProbeRow) {
	if probe.StartedAt.IsZero() {
		probe.StartedAt = time.Now().UTC()
	}

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	id, err := persistence.InsertProbe(ctx, pc.db, probe)
	if err != nil {
		slog.Warn("ProbeCollector: insert failed", "server_id", probe.ServerID, "err", err)
		return
	}
	probe.ID = id

	if pc.notify != nil {
		pc.notify(probe)
	}
}
