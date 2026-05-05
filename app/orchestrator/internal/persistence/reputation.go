package persistence

import (
	"context"
	"crypto/sha1"
	"database/sql"
	"encoding/json"
	"fmt"
	"math"
	"sort"
	"strings"
	"time"
)

// ── Row types ─────────────────────────────────────────────────────────────────

type VPNServerRow struct {
	ID               string
	Source           string // "proton" | "gluetun"
	Hostname         string
	IPs              []string
	Country          string
	CountryCode      string
	City             string
	ServerName       string
	Tier             int
	Flags            VPNServerFlags
	LoadPct          *int
	Status           string // "up" | "down" | "unknown"
	QuarantinedUntil *time.Time
	QuarantineReason string
	Pinned           bool
	FirstSeenAt      time.Time
	LastSeenAt       time.Time
}

type VPNServerFlags struct {
	PortForward bool `json:"port_forward"`
	Stream      bool `json:"stream"`
	SecureCore  bool `json:"secure_core"`
	Tor         bool `json:"tor"`
	Free        bool `json:"free"`
}

type VPNProbeRow struct {
	ID         int64
	ServerID   string
	ContentID  string
	Category   string
	StartedAt  time.Time
	Outcome    string // "success"|"timeout"|"engine_error"|"vpn_error"|"peer_starved"|"dropped"
	TtfbMs     *int
	DurationMs *int
	PeersMax   *int
	BytesDown  int64
	EngineID   string
	LeaseID    string
	Meta       map[string]any
}

type VPNReputationRow struct {
	ServerID      string
	Category      string
	Window        string
	ProbesN       int
	SuccessesN    int
	SuccessRate   float64
	TtfbP50Ms     *int
	TtfbP95Ms     *int
	DurationAvgMs *int
	DropsN        int
	Score         float64
	ScoreColor    string
	LowConfidence bool
	History30     []float64
	UpdatedAt     time.Time
}

// VPNServerWithRep is a VPNServerRow joined with its _overall reputation.
type VPNServerWithRep struct {
	VPNServerRow
	Rep *VPNReputationRow
}

type RecentProbeSummary struct {
	ContentID  string
	Category   string
	StartedAt  time.Time
	SampleN    int
	SuccessesN int
	TtfbAvgMs  *int
}

type ServerDetail struct {
	Server       VPNServerWithRep
	ByCategory   []CategoryRepRow
	RecentProbes []VPNProbeRow
}

type CategoryRepRow struct {
	Category    string
	Score       float64
	ScoreColor  string
	ProbesN     int
	SuccessRate float64
	TtfbP50Ms   *int
}

// ListServersFilter specifies filter/sort/pagination for ListVPNServers.
type ListServersFilter struct {
	Source      string // "all"|"proton"|"gluetun"
	Q           string // substring on name/country/city/hostname
	CC          []string
	Quarantined string // "include"|"exclude"|"only"
	UsedOnly    bool   // when true, only return servers with at least one probe record
	Sort        string // "score"|"load"|"ttfb"|"name"|"country"
	Dir         string // "asc"|"desc"
	Category    string // category key for reputation join (default "_overall")
	Cursor      string // opaque keyset cursor
	Limit       int
}

// ListStats is summary counts returned alongside a server list.
type ListStats struct {
	BySource map[string]int
	ByStatus map[string]int
	ByColor  map[string]int
}

// ── Server ID ─────────────────────────────────────────────────────────────────

func ServerID(source, hostname string) string {
	h := sha1.Sum([]byte(source + ":" + strings.ToLower(hostname)))
	return fmt.Sprintf("srv-%x", h[:6])
}

// ── UpsertVPNServer ───────────────────────────────────────────────────────────

func UpsertVPNServer(ctx context.Context, db *sql.DB, s VPNServerRow) error {
	ipsJSON, _ := json.Marshal(s.IPs)
	flagsJSON, _ := json.Marshal(s.Flags)

	now := time.Now().UTC()
	if s.FirstSeenAt.IsZero() {
		s.FirstSeenAt = now
	}
	s.LastSeenAt = now
	if s.Status == "" {
		s.Status = "unknown"
	}

	_, err := db.ExecContext(ctx, `
		INSERT INTO vpn_server
			(id, source, hostname, ips, country, country_code, city, server_name,
			 tier, flags, load_pct, status, quarantined_until, quarantine_reason,
			 pinned, first_seen_at, last_seen_at)
		VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
		ON CONFLICT(id) DO UPDATE SET
			ips             = excluded.ips,
			country         = excluded.country,
			country_code    = excluded.country_code,
			city            = excluded.city,
			server_name     = excluded.server_name,
			tier            = excluded.tier,
			flags           = excluded.flags,
			load_pct        = excluded.load_pct,
			status          = excluded.status,
			last_seen_at    = excluded.last_seen_at
	`,
		s.ID, s.Source, s.Hostname, string(ipsJSON),
		s.Country, s.CountryCode, s.City, s.ServerName,
		s.Tier, string(flagsJSON), s.LoadPct, s.Status,
		nullTime(s.QuarantinedUntil), nullStr(s.QuarantineReason),
		boolInt(s.Pinned), s.FirstSeenAt.Format(time.RFC3339), s.LastSeenAt.Format(time.RFC3339),
	)
	return err
}

// MarkServersDown sets status='down' for servers of given source not in seenIDs.
func MarkServersDown(ctx context.Context, db *sql.DB, source string, seenIDs []string) error {
	if len(seenIDs) == 0 {
		_, err := db.ExecContext(ctx, `UPDATE vpn_server SET status='down' WHERE source=?`, source)
		return err
	}
	placeholders := strings.Repeat("?,", len(seenIDs))
	placeholders = placeholders[:len(placeholders)-1]
	args := make([]any, 0, 1+len(seenIDs))
	args = append(args, source)
	for _, id := range seenIDs {
		args = append(args, id)
	}
	_, err := db.ExecContext(ctx,
		fmt.Sprintf(`UPDATE vpn_server SET status='down' WHERE source=? AND id NOT IN (%s)`, placeholders),
		args...,
	)
	return err
}

func GetVPNServer(ctx context.Context, db *sql.DB, id string) (*VPNServerRow, error) {
	row := db.QueryRowContext(ctx, `
		SELECT id, source, hostname, ips, country, country_code, city, server_name,
		       tier, flags, load_pct, status, quarantined_until, quarantine_reason,
		       pinned, first_seen_at, last_seen_at
		FROM vpn_server WHERE id=?`, id)
	return scanServer(row)
}

func GetVPNServerByHostname(ctx context.Context, db *sql.DB, source, hostname string) (*VPNServerRow, error) {
	row := db.QueryRowContext(ctx, `
		SELECT id, source, hostname, ips, country, country_code, city, server_name,
		       tier, flags, load_pct, status, quarantined_until, quarantine_reason,
		       pinned, first_seen_at, last_seen_at
		FROM vpn_server WHERE source=? AND hostname=?`, source, strings.ToLower(hostname))
	return scanServer(row)
}

// ── ListVPNServers ────────────────────────────────────────────────────────────

func ListVPNServers(ctx context.Context, db *sql.DB, f ListServersFilter) ([]VPNServerWithRep, string, ListStats, error) {
	cat := f.Category
	if cat == "" {
		cat = "_overall"
	}
	limit := f.Limit
	if limit <= 0 || limit > 500 {
		limit = 100
	}

	where := []string{"1=1"}
	args := []any{}

	if f.Source != "" && f.Source != "all" {
		where = append(where, "s.source=?")
		args = append(args, f.Source)
	}
	if f.Q != "" {
		q := "%" + strings.ToLower(f.Q) + "%"
		where = append(where, "(lower(s.server_name) LIKE ? OR lower(s.hostname) LIKE ? OR lower(s.country) LIKE ? OR lower(s.city) LIKE ?)")
		args = append(args, q, q, q, q)
	}
	if len(f.CC) > 0 {
		phs := strings.Repeat("?,", len(f.CC))
		phs = phs[:len(phs)-1]
		where = append(where, fmt.Sprintf("s.country_code IN (%s)", phs))
		for _, cc := range f.CC {
			args = append(args, cc)
		}
	}
	if f.UsedOnly {
		where = append(where, "EXISTS (SELECT 1 FROM vpn_probe p WHERE p.server_id=s.id)")
	}
	switch f.Quarantined {
	case "exclude":
		where = append(where, "(s.quarantined_until IS NULL OR s.quarantined_until <= datetime('now'))")
	case "only":
		where = append(where, "s.quarantined_until > datetime('now')")
	}

	sortCol := "COALESCE(r.score,0)"
	switch f.Sort {
	case "load":
		sortCol = "COALESCE(s.load_pct,100)"
	case "ttfb":
		sortCol = "COALESCE(r.ttfb_p50_ms,9999999)"
	case "name":
		sortCol = "s.server_name"
	case "country":
		sortCol = "s.country"
	}
	dir := "DESC"
	if strings.ToLower(f.Dir) == "asc" {
		dir = "ASC"
	}

	whereStr := strings.Join(where, " AND ")
	query := fmt.Sprintf(`
		SELECT s.id, s.source, s.hostname, s.ips, s.country, s.country_code, s.city,
		       s.server_name, s.tier, s.flags, s.load_pct, s.status,
		       s.quarantined_until, s.quarantine_reason, s.pinned, s.first_seen_at, s.last_seen_at,
		       r.probes_n, r.successes_n, r.success_rate,
		       r.ttfb_p50_ms, r.ttfb_p95_ms, r.duration_avg_ms, r.drops_n,
		       r.score, r.score_color, r.low_confidence, r.history_30, r.updated_at
		FROM vpn_server s
		LEFT JOIN vpn_reputation r ON r.server_id=s.id AND r.category=? AND r.window='24h'
		WHERE %s
		ORDER BY %s %s, s.id ASC
		LIMIT ?
	`, whereStr, sortCol, dir)

	queryArgs := append([]any{cat}, args...)
	queryArgs = append(queryArgs, limit+1) // fetch one extra for next_cursor

	rows, err := db.QueryContext(ctx, query, queryArgs...)
	if err != nil {
		return nil, "", ListStats{}, err
	}
	defer rows.Close()

	var results []VPNServerWithRep
	for rows.Next() {
		item, err := scanServerWithRep(rows)
		if err != nil {
			return nil, "", ListStats{}, err
		}
		results = append(results, item)
	}

	var nextCursor string
	if len(results) > limit {
		results = results[:limit]
		last := results[len(results)-1]
		nextCursor = encodeCursor(last.ID)
	}

	// Compute stats from full set (separate cheap query).
	stats, _ := serverListStats(ctx, db, cat, f.UsedOnly)

	return results, nextCursor, stats, nil
}

func serverListStats(ctx context.Context, db *sql.DB, cat string, usedOnly bool) (ListStats, error) {
	stats := ListStats{
		BySource: map[string]int{},
		ByStatus: map[string]int{},
		ByColor:  map[string]int{},
	}
	usedClause := ""
	if usedOnly {
		usedClause = "WHERE EXISTS (SELECT 1 FROM vpn_probe p WHERE p.server_id=s.id)"
	}
	rows, err := db.QueryContext(ctx, `
		SELECT s.source, s.status, COALESCE(r.score_color,'red') as color, count(*) as n
		FROM vpn_server s
		LEFT JOIN vpn_reputation r ON r.server_id=s.id AND r.category=? AND r.window='24h'
		`+usedClause+`
		GROUP BY s.source, s.status, color
	`, cat)
	if err != nil {
		return stats, err
	}
	defer rows.Close()
	for rows.Next() {
		var source, status, color string
		var n int
		if err := rows.Scan(&source, &status, &color, &n); err != nil {
			continue
		}
		stats.BySource[source] += n
		stats.ByStatus[status] += n
		stats.ByColor[color] += n
	}
	return stats, nil
}

// ── Probes ────────────────────────────────────────────────────────────────────

func InsertProbe(ctx context.Context, db *sql.DB, p VPNProbeRow) (int64, error) {
	metaJSON, _ := json.Marshal(p.Meta)
	if metaJSON == nil {
		metaJSON = []byte("{}")
	}
	if p.Category == "" {
		p.Category = "long-tail"
	}
	res, err := db.ExecContext(ctx, `
		INSERT INTO vpn_probe
			(server_id, content_id, category, started_at, outcome,
			 ttfb_ms, duration_ms, peers_max, bytes_down, engine_id, lease_id, meta)
		VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
	`,
		p.ServerID, p.ContentID, p.Category, p.StartedAt.Format(time.RFC3339), p.Outcome,
		p.TtfbMs, p.DurationMs, p.PeersMax, p.BytesDown, p.EngineID, p.LeaseID, string(metaJSON),
	)
	if err != nil {
		return 0, err
	}
	return res.LastInsertId()
}

// BackfillProbeDuration updates duration_ms and peers_max on open probes for a server.
func BackfillProbeDuration(ctx context.Context, db *sql.DB, serverID string, durationMs int, peersMax int) error {
	_, err := db.ExecContext(ctx, `
		UPDATE vpn_probe SET duration_ms=?, peers_max=?
		WHERE server_id=? AND duration_ms IS NULL
		ORDER BY started_at DESC LIMIT 1
	`, durationMs, peersMax, serverID)
	return err
}

func ListRecentProbes(ctx context.Context, db *sql.DB, limit int) ([]RecentProbeSummary, error) {
	if limit <= 0 {
		limit = 10
	}
	rows, err := db.QueryContext(ctx, `
		SELECT content_id, category, MAX(started_at) as last_started,
		       count(*) as sample_n,
		       sum(CASE WHEN outcome='success' THEN 1 ELSE 0 END) as successes_n,
		       avg(ttfb_ms) as ttfb_avg
		FROM vpn_probe
		GROUP BY content_id
		ORDER BY last_started DESC
		LIMIT ?
	`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []RecentProbeSummary
	for rows.Next() {
		var p RecentProbeSummary
		var lastStarted string
		var ttfbAvg sql.NullFloat64
		if err := rows.Scan(&p.ContentID, &p.Category, &lastStarted, &p.SampleN, &p.SuccessesN, &ttfbAvg); err != nil {
			continue
		}
		p.StartedAt, _ = time.Parse(time.RFC3339, lastStarted)
		if ttfbAvg.Valid {
			v := int(ttfbAvg.Float64)
			p.TtfbAvgMs = &v
		}
		out = append(out, p)
	}
	return out, nil
}

// ── Reputation ────────────────────────────────────────────────────────────────

func GetReputation(ctx context.Context, db *sql.DB, serverID, category, window string) (*VPNReputationRow, error) {
	row := db.QueryRowContext(ctx, `
		SELECT server_id, category, window, probes_n, successes_n, success_rate,
		       ttfb_p50_ms, ttfb_p95_ms, duration_avg_ms, drops_n,
		       score, score_color, low_confidence, history_30, updated_at
		FROM vpn_reputation WHERE server_id=? AND category=? AND window=?
	`, serverID, category, window)
	return scanReputation(row)
}

func UpsertReputation(ctx context.Context, db *sql.DB, r VPNReputationRow) error {
	h30JSON, _ := json.Marshal(r.History30)
	_, err := db.ExecContext(ctx, `
		INSERT INTO vpn_reputation
			(server_id, category, window, probes_n, successes_n, success_rate,
			 ttfb_p50_ms, ttfb_p95_ms, duration_avg_ms, drops_n,
			 score, score_color, low_confidence, history_30, updated_at)
		VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
		ON CONFLICT(server_id, category, window) DO UPDATE SET
			probes_n        = excluded.probes_n,
			successes_n     = excluded.successes_n,
			success_rate    = excluded.success_rate,
			ttfb_p50_ms     = excluded.ttfb_p50_ms,
			ttfb_p95_ms     = excluded.ttfb_p95_ms,
			duration_avg_ms = excluded.duration_avg_ms,
			drops_n         = excluded.drops_n,
			score           = excluded.score,
			score_color     = excluded.score_color,
			low_confidence  = excluded.low_confidence,
			history_30      = excluded.history_30,
			updated_at      = excluded.updated_at
	`,
		r.ServerID, r.Category, r.Window, r.ProbesN, r.SuccessesN, r.SuccessRate,
		r.TtfbP50Ms, r.TtfbP95Ms, r.DurationAvgMs, r.DropsN,
		r.Score, r.ScoreColor, boolInt(r.LowConfidence), string(h30JSON),
		r.UpdatedAt.Format(time.RFC3339),
	)
	return err
}

// RefreshReputation recomputes reputation for (serverID, category, window='24h') from raw probes.
func RefreshReputation(ctx context.Context, db *sql.DB, serverID, category string, cfg ReputationConfig) (*VPNReputationRow, error) {
	since := time.Now().UTC().Add(-24 * time.Hour)

	catFilter := ""
	args := []any{serverID, since.Format(time.RFC3339)}
	if category != "_overall" {
		catFilter = "AND category=?"
		args = append(args, category)
	}

	rows, err := db.QueryContext(ctx, fmt.Sprintf(`
		SELECT content_id, outcome, ttfb_ms, duration_ms
		FROM vpn_probe
		WHERE server_id=? AND started_at>=? %s
		ORDER BY started_at DESC
	`, catFilter), args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	// Collect probe rows and track failed content IDs so we can check whether
	// those content IDs had successes on other servers. If a content ID had no
	// successes anywhere else in the window, we treat failures for it as global
	// (dead content) and do not penalize individual servers for them.
	type probeRow struct {
		contentID string
		outcome   string
		ttfb      sql.NullInt64
		dur       sql.NullInt64
	}
	var probeRows []probeRow
	var failedContentIDsMap = make(map[string]struct{})
	for rows.Next() {
		var pr probeRow
		if err := rows.Scan(&pr.contentID, &pr.outcome, &pr.ttfb, &pr.dur); err != nil {
			continue
		}
		probeRows = append(probeRows, pr)
		if pr.outcome != "success" {
			failedContentIDsMap[pr.contentID] = struct{}{}
		}
	}

	// If there are failed content IDs, query whether any of them had successes
	// on other servers within the same window. Build a set of content IDs that
	// did see success elsewhere.
	var failedContentIDs []string
	for id := range failedContentIDsMap {
		failedContentIDs = append(failedContentIDs, id)
	}
	otherSuccessSet := make(map[string]struct{})
	if len(failedContentIDs) > 0 {
		// Build IN clause placeholders.
		placeholders := strings.Repeat("?,", len(failedContentIDs))
		placeholders = strings.TrimSuffix(placeholders, ",")
		qargs := make([]any, 0, len(failedContentIDs)+2)
		for _, id := range failedContentIDs {
			qargs = append(qargs, id)
		}
		// since and exclude current server to focus on other nodes' successes.
		qargs = append(qargs, since.Format(time.RFC3339), serverID)
		q := fmt.Sprintf(`
			SELECT DISTINCT content_id FROM vpn_probe
			WHERE content_id IN (%s) AND outcome='success' AND started_at>=? AND server_id!=?
		`, placeholders)
		osRows, oerr := db.QueryContext(ctx, q, qargs...)
		if oerr == nil {
			defer osRows.Close()
			for osRows.Next() {
				var cid string
				if err := osRows.Scan(&cid); err == nil {
					otherSuccessSet[cid] = struct{}{}
				}
			}
		}
	}

	var outcomes []string
	var ttfbs []int
	var durations []int
	var drops int
	var successes int

	// Now compute counts, excluding failures for content IDs with no other
	// successes (global failures).
	for _, pr := range probeRows {
		// If not a success and there was no success on other servers for this
		// content ID, skip counting it as it likely indicates dead content.
		if pr.outcome != "success" {
			if _, ok := otherSuccessSet[pr.contentID]; !ok {
				// skip global failure
				continue
			}
		}
		outcomes = append(outcomes, pr.outcome)
		if pr.outcome == "success" {
			successes++
			if pr.ttfb.Valid {
				ttfbs = append(ttfbs, int(pr.ttfb.Int64))
			}
			if pr.dur.Valid {
				durations = append(durations, int(pr.dur.Int64))
			}
		}
		if pr.outcome == "dropped" {
			drops++
		}
	}

	n := len(outcomes)
	rep := &VPNReputationRow{
		ServerID:  serverID,
		Category:  category,
		Window:    "24h",
		ProbesN:   n,
		DropsN:    drops,
		UpdatedAt: time.Now().UTC(),
	}

	if n > 0 {
		rep.SuccessesN = successes
		rep.SuccessRate = float64(successes) / float64(n)
	}

	var ttfbP50, ttfbP95, durAvg *int
	if len(ttfbs) > 0 {
		sort.Ints(ttfbs)
		v50 := ttfbs[int(math.Floor(float64(len(ttfbs))*0.50))]
		v95 := ttfbs[int(math.Min(float64(len(ttfbs)-1), math.Floor(float64(len(ttfbs))*0.95)))]
		ttfbP50, ttfbP95 = &v50, &v95
		rep.TtfbP50Ms = ttfbP50
		rep.TtfbP95Ms = ttfbP95
	}
	if len(durations) > 0 {
		sum := 0
		for _, d := range durations {
			sum += d
		}
		avg := sum / len(durations)
		durAvg = &avg
		rep.DurationAvgMs = durAvg
	}

	score, color, lowConf := ComputeScore(rep.SuccessRate, ttfbP50, durAvg, n, cfg)
	rep.Score = score
	rep.ScoreColor = color
	rep.LowConfidence = lowConf

	// Preserve existing history_30.
	existing, _ := GetReputation(ctx, db, serverID, category, "24h")
	if existing != nil {
		rep.History30 = existing.History30
	}

	return rep, UpsertReputation(ctx, db, *rep)
}

// SnapshotHistory appends today's score to history_30 (drops oldest, keeps 30 days).
func SnapshotHistory(ctx context.Context, db *sql.DB) error {
	rows, err := db.QueryContext(ctx, `
		SELECT server_id, category, window, score, history_30
		FROM vpn_reputation WHERE window='24h'
	`)
	if err != nil {
		return err
	}
	defer rows.Close()

	type entry struct {
		serverID, category string
		score              float64
		history            []float64
	}
	var entries []entry
	for rows.Next() {
		var e entry
		var h30 string
		if err := rows.Scan(&e.serverID, &e.category, new(string), &e.score, &h30); err != nil {
			continue
		}
		_ = json.Unmarshal([]byte(h30), &e.history)
		entries = append(entries, e)
	}
	rows.Close()

	for _, e := range entries {
		hist := append(e.history, e.score)
		if len(hist) > 30 {
			hist = hist[len(hist)-30:]
		}
		h30JSON, _ := json.Marshal(hist)
		_, _ = db.ExecContext(ctx, `
			UPDATE vpn_reputation SET history_30=? WHERE server_id=? AND category=? AND window='24h'
		`, string(h30JSON), e.serverID, e.category)
	}
	return nil
}

// ── Quarantine / Pin ──────────────────────────────────────────────────────────

func SetQuarantine(ctx context.Context, db *sql.DB, serverID string, until *time.Time, reason string) error {
	if until == nil {
		_, err := db.ExecContext(ctx, `UPDATE vpn_server SET quarantined_until=NULL, quarantine_reason=NULL WHERE id=?`, serverID)
		return err
	}
	_, err := db.ExecContext(ctx, `UPDATE vpn_server SET quarantined_until=?, quarantine_reason=? WHERE id=?`,
		until.UTC().Format(time.RFC3339), reason, serverID)
	return err
}

func SetPinned(ctx context.Context, db *sql.DB, serverID string, pinned bool) error {
	_, err := db.ExecContext(ctx, `UPDATE vpn_server SET pinned=? WHERE id=?`, boolInt(pinned), serverID)
	return err
}

// ── GetServerDetail ───────────────────────────────────────────────────────────

func GetServerDetail(ctx context.Context, db *sql.DB, serverID string) (*ServerDetail, error) {
	srv, err := GetVPNServer(ctx, db, serverID)
	if err != nil {
		return nil, err
	}
	rep, _ := GetReputation(ctx, db, serverID, "_overall", "24h")

	detail := &ServerDetail{
		Server: VPNServerWithRep{VPNServerRow: *srv, Rep: rep},
	}

	// Per-category reputation.
	catRows, err := db.QueryContext(ctx, `
		SELECT category, score, score_color, probes_n, success_rate, ttfb_p50_ms
		FROM vpn_reputation
		WHERE server_id=? AND window='24h' AND category != '_overall'
		ORDER BY probes_n DESC
	`, serverID)
	if err == nil {
		defer catRows.Close()
		for catRows.Next() {
			var c CategoryRepRow
			if err := catRows.Scan(&c.Category, &c.Score, &c.ScoreColor, &c.ProbesN, &c.SuccessRate, &c.TtfbP50Ms); err == nil {
				detail.ByCategory = append(detail.ByCategory, c)
			}
		}
	}

	// Recent probes.
	probeRows, err := db.QueryContext(ctx, `
		SELECT id, server_id, content_id, category, started_at, outcome,
		       ttfb_ms, duration_ms, peers_max, bytes_down, engine_id, lease_id, meta
		FROM vpn_probe WHERE server_id=? ORDER BY started_at DESC LIMIT 20
	`, serverID)
	if err == nil {
		defer probeRows.Close()
		for probeRows.Next() {
			p, err := scanProbe(probeRows)
			if err == nil {
				detail.RecentProbes = append(detail.RecentProbes, *p)
			}
		}
	}

	return detail, nil
}

// ── Auto-quarantine ───────────────────────────────────────────────────────────

// FindAutoQuarantineCandidates returns server IDs with N+ consecutive non-success probes in last 30min.
func FindAutoQuarantineCandidates(ctx context.Context, db *sql.DB, n int) ([]string, error) {
	since := time.Now().UTC().Add(-30 * time.Minute).Format(time.RFC3339)
	rows, err := db.QueryContext(ctx, `
		SELECT server_id,
		       sum(CASE WHEN outcome != 'success' THEN 1 ELSE 0 END) as failures,
		       count(*) as total
		FROM vpn_probe
		WHERE started_at >= ?
		GROUP BY server_id
		HAVING failures >= ? AND failures = total
	`, since, n)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var ids []string
	for rows.Next() {
		var id string
		var failures, total int
		if err := rows.Scan(&id, &failures, &total); err == nil {
			ids = append(ids, id)
		}
	}
	return ids, nil
}

// FindDirtyServers returns server IDs that have had new probes since lastRefresh.
func FindDirtyServers(ctx context.Context, db *sql.DB, since time.Time) ([]string, error) {
	rows, err := db.QueryContext(ctx, `
		SELECT DISTINCT server_id FROM vpn_probe WHERE started_at >= ?
	`, since.Format(time.RFC3339))
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var ids []string
	for rows.Next() {
		var id string
		if err := rows.Scan(&id); err == nil {
			ids = append(ids, id)
		}
	}
	return ids, nil
}

// FindDistinctCategories returns all distinct categories for a server.
func FindDistinctCategories(ctx context.Context, db *sql.DB, serverID string) ([]string, error) {
	rows, err := db.QueryContext(ctx, `SELECT DISTINCT category FROM vpn_probe WHERE server_id=?`, serverID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var cats []string
	for rows.Next() {
		var c string
		if err := rows.Scan(&c); err == nil {
			cats = append(cats, c)
		}
	}
	return cats, nil
}

// ── GetScoredCandidates ───────────────────────────────────────────────────────

type ScoredServer struct {
	ID       string
	Hostname string
	Score    float64
	Pinned   bool
	ProbesN  int
}

// GetScoredCandidates returns servers eligible for scheduling, sorted by score desc.
func GetScoredCandidates(ctx context.Context, db *sql.DB, category string, excludeHostnames map[string]bool) ([]ScoredServer, error) {
	if category == "" {
		category = "_overall"
	}
	rows, err := db.QueryContext(ctx, `
				SELECT s.id, s.hostname, COALESCE(r.score,0) as score, s.pinned, COALESCE(r.probes_n,0) as probes_n
				FROM vpn_server s
				LEFT JOIN vpn_reputation r ON r.server_id=s.id AND r.category=? AND r.window='24h'
				WHERE s.status != 'down'
					AND (s.quarantined_until IS NULL OR s.quarantined_until <= datetime('now'))
				ORDER BY s.pinned DESC, score DESC
		`, category)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var out []ScoredServer
	for rows.Next() {
		var s ScoredServer
		var pinned int
		if err := rows.Scan(&s.ID, &s.Hostname, &s.Score, &pinned, &s.ProbesN); err != nil {
			continue
		}
		s.Pinned = pinned == 1
		if excludeHostnames[strings.ToLower(s.Hostname)] {
			continue
		}
		out = append(out, s)
	}
	return out, nil
}

// GetTotalProbeCount returns the sum of probes_n across all _overall reputation rows.
// Used as the N term in the UCB1 exploration bonus formula.
func GetTotalProbeCount(ctx context.Context, db *sql.DB) (int, error) {
	var n int
	err := db.QueryRowContext(ctx,
		`SELECT COALESCE(SUM(probes_n),0) FROM vpn_reputation WHERE category='_overall'`,
	).Scan(&n)
	return n, err
}

// GetRecentProbedContentIDs returns content IDs that had at least one successful probe
// in the last windowHours, ordered by recency, up to limit results.
func GetRecentProbedContentIDs(ctx context.Context, db *sql.DB, windowHours, limit int) ([]string, error) {
	rows, err := db.QueryContext(ctx, `
		SELECT DISTINCT content_id
		FROM vpn_probe
		WHERE outcome='success'
		  AND started_at >= datetime('now', ? || ' hours')
		ORDER BY started_at DESC
		LIMIT ?
	`, fmt.Sprintf("-%d", windowHours), limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []string
	for rows.Next() {
		var id string
		if err := rows.Scan(&id); err != nil {
			continue
		}
		out = append(out, id)
	}
	return out, nil
}

// ── Scoring ───────────────────────────────────────────────────────────────────

type ReputationConfig struct {
	WSuccess        float64
	WTtfb           float64
	WDuration       float64
	LowConfProbes   int
	ColorThresholds [4]float64 // [green, amber, magenta, red] thresholds
}

func DefaultReputationConfig() ReputationConfig {
	return ReputationConfig{
		WSuccess:        0.60,
		WTtfb:           0.25,
		WDuration:       0.15,
		LowConfProbes:   5,
		ColorThresholds: [4]float64{0.85, 0.65, 0.40, 0},
	}
}

func ComputeScore(successRate float64, ttfbP50Ms, durationAvgMs *int, probesN int, cfg ReputationConfig) (score float64, color string, lowConf bool) {
	if probesN < cfg.LowConfProbes {
		lowConf = true
		score = successRate * 0.9
		color = colorBucket(score, cfg.ColorThresholds)
		return
	}

	ttfbScore := 0.5
	if ttfbP50Ms != nil {
		ttfbScore = clamp(1-(float64(*ttfbP50Ms)-200)/1800, 0, 1)
	}
	durScore := 0.0
	if durationAvgMs != nil {
		durScore = clamp(float64(*durationAvgMs)/float64(30*60*1000), 0, 1)
	}

	score = cfg.WSuccess*successRate + cfg.WTtfb*ttfbScore + cfg.WDuration*durScore
	score = clamp(score, 0, 1)
	color = colorBucket(score, cfg.ColorThresholds)
	return
}

func colorBucket(score float64, thresholds [4]float64) string {
	switch {
	case score >= thresholds[0]:
		return "green"
	case score >= thresholds[1]:
		return "amber"
	case score >= thresholds[2]:
		return "magenta"
	default:
		return "red"
	}
}

func clamp(v, lo, hi float64) float64 {
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}

// ── Scan helpers ──────────────────────────────────────────────────────────────

type rscanner interface {
	Scan(dest ...any) error
}

func scanServer(row rscanner) (*VPNServerRow, error) {
	var s VPNServerRow
	var ipsJSON, flagsJSON string
	var loadPct sql.NullInt64
	var quarUntil, quarReason sql.NullString
	var pinned int
	var firstSeen, lastSeen string

	err := row.Scan(
		&s.ID, &s.Source, &s.Hostname, &ipsJSON, &s.Country, &s.CountryCode, &s.City,
		&s.ServerName, &s.Tier, &flagsJSON, &loadPct, &s.Status,
		&quarUntil, &quarReason, &pinned, &firstSeen, &lastSeen,
	)
	if err != nil {
		return nil, err
	}
	_ = json.Unmarshal([]byte(ipsJSON), &s.IPs)
	_ = json.Unmarshal([]byte(flagsJSON), &s.Flags)
	if loadPct.Valid {
		v := int(loadPct.Int64)
		s.LoadPct = &v
	}
	if quarUntil.Valid {
		t, _ := time.Parse(time.RFC3339, quarUntil.String)
		s.QuarantinedUntil = &t
	}
	s.QuarantineReason = quarReason.String
	s.Pinned = pinned == 1
	s.FirstSeenAt, _ = time.Parse(time.RFC3339, firstSeen)
	s.LastSeenAt, _ = time.Parse(time.RFC3339, lastSeen)
	return &s, nil
}

func scanServerWithRep(row rscanner) (VPNServerWithRep, error) {
	var s VPNServerRow
	var ipsJSON, flagsJSON string
	var loadPct sql.NullInt64
	var quarUntil, quarReason sql.NullString
	var pinned int
	var firstSeen, lastSeen string

	var probesN, successesN sql.NullInt64
	var successRate sql.NullFloat64
	var ttfbP50, ttfbP95, durAvg sql.NullInt64
	var dropsN sql.NullInt64
	var score sql.NullFloat64
	var scoreColor sql.NullString
	var lowConf sql.NullInt64
	var history30 sql.NullString
	var updatedAt sql.NullString

	err := row.Scan(
		&s.ID, &s.Source, &s.Hostname, &ipsJSON, &s.Country, &s.CountryCode, &s.City,
		&s.ServerName, &s.Tier, &flagsJSON, &loadPct, &s.Status,
		&quarUntil, &quarReason, &pinned, &firstSeen, &lastSeen,
		&probesN, &successesN, &successRate,
		&ttfbP50, &ttfbP95, &durAvg, &dropsN,
		&score, &scoreColor, &lowConf, &history30, &updatedAt,
	)
	if err != nil {
		return VPNServerWithRep{}, err
	}
	_ = json.Unmarshal([]byte(ipsJSON), &s.IPs)
	_ = json.Unmarshal([]byte(flagsJSON), &s.Flags)
	if loadPct.Valid {
		v := int(loadPct.Int64)
		s.LoadPct = &v
	}
	if quarUntil.Valid {
		t, _ := time.Parse(time.RFC3339, quarUntil.String)
		s.QuarantinedUntil = &t
	}
	s.QuarantineReason = quarReason.String
	s.Pinned = pinned == 1
	s.FirstSeenAt, _ = time.Parse(time.RFC3339, firstSeen)
	s.LastSeenAt, _ = time.Parse(time.RFC3339, lastSeen)

	result := VPNServerWithRep{VPNServerRow: s}
	if score.Valid {
		rep := &VPNReputationRow{
			ServerID:      s.ID,
			Category:      "_overall",
			Window:        "24h",
			Score:         score.Float64,
			ScoreColor:    scoreColor.String,
			LowConfidence: lowConf.Int64 == 1,
		}
		if probesN.Valid {
			rep.ProbesN = int(probesN.Int64)
		}
		if successesN.Valid {
			rep.SuccessesN = int(successesN.Int64)
		}
		if successRate.Valid {
			rep.SuccessRate = successRate.Float64
		}
		if ttfbP50.Valid {
			v := int(ttfbP50.Int64)
			rep.TtfbP50Ms = &v
		}
		if ttfbP95.Valid {
			v := int(ttfbP95.Int64)
			rep.TtfbP95Ms = &v
		}
		if durAvg.Valid {
			v := int(durAvg.Int64)
			rep.DurationAvgMs = &v
		}
		if dropsN.Valid {
			rep.DropsN = int(dropsN.Int64)
		}
		if history30.Valid {
			_ = json.Unmarshal([]byte(history30.String), &rep.History30)
		}
		if updatedAt.Valid {
			rep.UpdatedAt, _ = time.Parse(time.RFC3339, updatedAt.String)
		}
		result.Rep = rep
	}
	return result, nil
}

func scanReputation(row rscanner) (*VPNReputationRow, error) {
	var r VPNReputationRow
	var ttfbP50, ttfbP95, durAvg sql.NullInt64
	var lowConf int
	var history30, updatedAt string

	err := row.Scan(
		&r.ServerID, &r.Category, &r.Window, &r.ProbesN, &r.SuccessesN, &r.SuccessRate,
		&ttfbP50, &ttfbP95, &durAvg, &r.DropsN,
		&r.Score, &r.ScoreColor, &lowConf, &history30, &updatedAt,
	)
	if err == sql.ErrNoRows {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	if ttfbP50.Valid {
		v := int(ttfbP50.Int64)
		r.TtfbP50Ms = &v
	}
	if ttfbP95.Valid {
		v := int(ttfbP95.Int64)
		r.TtfbP95Ms = &v
	}
	if durAvg.Valid {
		v := int(durAvg.Int64)
		r.DurationAvgMs = &v
	}
	r.LowConfidence = lowConf == 1
	_ = json.Unmarshal([]byte(history30), &r.History30)
	r.UpdatedAt, _ = time.Parse(time.RFC3339, updatedAt)
	return &r, nil
}

func scanProbe(row rscanner) (*VPNProbeRow, error) {
	var p VPNProbeRow
	var ttfb, dur, peers sql.NullInt64
	var startedAt, metaJSON string

	err := row.Scan(
		&p.ID, &p.ServerID, &p.ContentID, &p.Category, &startedAt, &p.Outcome,
		&ttfb, &dur, &peers, &p.BytesDown, &p.EngineID, &p.LeaseID, &metaJSON,
	)
	if err != nil {
		return nil, err
	}
	p.StartedAt, _ = time.Parse(time.RFC3339, startedAt)
	if ttfb.Valid {
		v := int(ttfb.Int64)
		p.TtfbMs = &v
	}
	if dur.Valid {
		v := int(dur.Int64)
		p.DurationMs = &v
	}
	if peers.Valid {
		v := int(peers.Int64)
		p.PeersMax = &v
	}
	_ = json.Unmarshal([]byte(metaJSON), &p.Meta)
	return &p, nil
}

// ── Misc helpers ──────────────────────────────────────────────────────────────

func nullTime(t *time.Time) any {
	if t == nil {
		return nil
	}
	return t.UTC().Format(time.RFC3339)
}

func nullStr(s string) any {
	if s == "" {
		return nil
	}
	return s
}

func boolInt(b bool) int {
	if b {
		return 1
	}
	return 0
}

func encodeCursor(lastID string) string {
	return lastID
}
