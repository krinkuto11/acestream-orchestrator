// Package aceapi implements the AceStream telnet-style legacy API protocol.
package aceapi

import (
	"bufio"
	"crypto/sha1"
	"fmt"
	"log/slog"
	"net"
	"net/url"
	"strings"
	"sync"
	"time"
)

const (
	defaultProductKey = "n51LvQoTlJzNGaFxseRK-uvnvX-sD4Vm5Axwmc4UcoD-jruxmKsuJaH0eVgE"
)

// StartInfo contains the parsed result from a successful START response.
type StartInfo struct {
	PlaybackURL       string
	StatURL           string
	CommandURL        string
	PlaybackSessionID string
}

// Error is returned when the engine reports a protocol-level failure.
type Error struct {
	Msg string
}

func (e *Error) Error() string { return "ace_api: " + e.Msg }

// Client is a single-connection client for the AceStream legacy API.
// It is NOT goroutine-safe — wrap with a mutex if shared.
type Client struct {
	host           string
	port           int
	connectTimeout time.Duration
	readTimeout    time.Duration
	productKey     string

	mu   sync.Mutex
	conn net.Conn
	rd   *bufio.Reader

	authenticated bool
}

// New creates a Client. Call Connect before any other method.
func New(host string, port int) *Client {
	return &Client{
		host:           host,
		port:           port,
		connectTimeout: 10 * time.Second,
		readTimeout:    10 * time.Second,
		productKey:     defaultProductKey,
	}
}

// Connect opens the TCP connection to the AceStream API port.
func (c *Client) Connect() error {
	c.mu.Lock()
	defer c.mu.Unlock()

	conn, err := net.DialTimeout("tcp", fmt.Sprintf("%s:%d", c.host, c.port), c.connectTimeout)
	if err != nil {
		return fmt.Errorf("ace_api connect %s:%d: %w", c.host, c.port, err)
	}
	c.conn = conn
	c.rd = bufio.NewReader(conn)
	c.authenticated = false
	slog.Debug("ace_api connected", "host", c.host, "port", c.port)
	return nil
}

// Close closes the connection.
func (c *Client) Close() {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.conn != nil {
		c.conn.Close()
		c.conn = nil
	}
	c.authenticated = false
}

// Authenticate runs the HELLOBG/READY handshake.
func (c *Client) Authenticate() error {
	c.mu.Lock()
	defer c.mu.Unlock()

	if err := c.write("HELLOBG version=4"); err != nil {
		return err
	}
	kv, err := c.waitFor("HELLOTS", 10*time.Second)
	if err != nil {
		return fmt.Errorf("ace_api HELLOTS: %w", err)
	}

	reqKey := kv["key"]
	h := sha1.New()
	h.Write([]byte(reqKey + c.productKey))
	digest := fmt.Sprintf("%x", h.Sum(nil))
	prefix := strings.SplitN(c.productKey, "-", 2)[0]
	readyKey := prefix + "-" + digest

	if err := c.write("READY key=" + readyKey); err != nil {
		return err
	}

	deadline := time.Now().Add(10 * time.Second)
	for time.Now().Before(deadline) {
		line, err := c.readLine(time.Until(deadline))
		if err != nil {
			return fmt.Errorf("ace_api auth wait: %w", err)
		}
		cmd := strings.SplitN(line, " ", 2)[0]
		if cmd == "AUTH" {
			c.authenticated = true
			slog.Debug("ace_api authenticated")
			return nil
		}
		if cmd == "NOTREADY" {
			return &Error{"engine returned NOTREADY during auth"}
		}
	}
	return &Error{"auth timeout: AUTH not received"}
}

// LoadAndStart resolves + starts a content_id stream and returns playback info.
// mode should be "content_id", "infohash", "torrent_url", "direct_url", or "raw_data".
func (c *Client) LoadAndStart(contentID, mode, fileIndexes string, seekback int) (*StartInfo, error) {
	c.mu.Lock()
	defer c.mu.Unlock()

	mode = normalizeMode(mode)
	if fileIndexes == "" {
		fileIndexes = "0"
	}

	// LOADASYNC (not needed for direct_url)
	if mode != "direct_url" {
		var loadCmd string
		switch mode {
		case "infohash":
			loadCmd = fmt.Sprintf("LOADASYNC 0 INFOHASH %s 0 0 0", contentID)
		case "torrent_url":
			loadCmd = fmt.Sprintf("LOADASYNC 0 TORRENT %s 0 0 0", contentID)
		default:
			loadCmd = fmt.Sprintf("LOADASYNC 0 PID %s", contentID)
		}
		if err := c.write(loadCmd); err != nil {
			return nil, err
		}
		if _, err := c.waitFor("LOADRESP", 15*time.Second); err != nil {
			return nil, fmt.Errorf("LOADASYNC: %w", err)
		}
	}

	// START
	var startCmd string
	streamType := "output_format=http"
	switch mode {
	case "content_id":
		startCmd = fmt.Sprintf("START PID %s %s %s", contentID, fileIndexes, streamType)
	case "infohash":
		startCmd = fmt.Sprintf("START INFOHASH %s %s 0 0 0 %s", contentID, fileIndexes, streamType)
	case "torrent_url":
		startCmd = fmt.Sprintf("START TORRENT %s %s 0 0 0 %s", contentID, fileIndexes, streamType)
	case "direct_url":
		startCmd = fmt.Sprintf("START URL %s %s 0 0 0 %s", contentID, fileIndexes, streamType)
	default:
		return nil, &Error{"unsupported START mode: " + mode}
	}

	if err := c.write(startCmd); err != nil {
		return nil, err
	}

	kv, err := c.waitFor("START", 30*time.Second)
	if err != nil {
		return nil, fmt.Errorf("START response: %w", err)
	}

	info := parseStartKV(kv)

	if seekback <= 0 {
		return info, nil
	}

	// Seekback: wait for livepos EVENT then LIVESEEK
	livepos, err := c.waitForEvent("livepos", 30*time.Second)
	if err != nil {
		slog.Warn("seekback requested but no livepos received", "seekback", seekback, "err", err)
		return info, nil
	}
	lastTS := parseIntField(livepos, "last")
	if lastTS <= 0 {
		lastTS = parseIntField(livepos, "last_ts")
	}
	if lastTS > 0 {
		target := lastTS - int64(seekback)
		if target < 0 {
			target = 0
		}
		if err := c.write(fmt.Sprintf("LIVESEEK %d", target)); err != nil {
			return info, nil
		}
		// Wait briefly for second START or livepos confirmation
		deadline := time.Now().Add(5 * time.Second)
		for time.Now().Before(deadline) {
			line, err := c.readLine(time.Until(deadline))
			if err != nil {
				break
			}
			parts := strings.Fields(line)
			if len(parts) == 0 {
				continue
			}
			if parts[0] == "START" {
				merged := parseStartKV(parseKV(line))
				if merged.PlaybackURL != "" {
					info = merged
				}
				break
			}
			if parts[0] == "EVENT" && len(parts) > 1 && parts[1] == "livepos" {
				break
			}
		}
	}
	return info, nil
}

// LiveSeek sends a LIVESEEK command to jump to the given Unix timestamp.
func (c *Client) LiveSeek(targetTimestamp int64) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.write(fmt.Sprintf("LIVESEEK %d", targetTimestamp))
}

// Stop sends STOPDL to the engine.
func (c *Client) Stop() error {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.write("STOPDL")
}

// StopPlayback sends STOP to end the active playback session.
func (c *Client) StopPlayback() error {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.write("STOP")
}

// Ping sends a STATUS command and discards the response.
// Used as a keepalive to prevent the engine from closing an idle API session.
func (c *Client) Ping() error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if err := c.write("STATUS"); err != nil {
		return err
	}
	// Drain one line (STATUS response or an event) with a short timeout.
	_, _ = c.readLine(2 * time.Second)
	return nil
}

// ---- internal helpers ----

func (c *Client) write(line string) error {
	if c.conn == nil {
		return &Error{"not connected"}
	}
	c.conn.SetWriteDeadline(time.Now().Add(5 * time.Second))
	_, err := fmt.Fprintf(c.conn, "%s\r\n", line)
	return err
}

func (c *Client) readLine(timeout time.Duration) (string, error) {
	if timeout <= 0 {
		timeout = c.readTimeout
	}
	c.conn.SetReadDeadline(time.Now().Add(timeout))
	line, err := c.rd.ReadString('\n')
	return strings.TrimRight(line, "\r\n"), err
}

func (c *Client) waitFor(cmd string, timeout time.Duration) (map[string]string, error) {
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		line, err := c.readLine(time.Until(deadline))
		if err != nil {
			return nil, err
		}
		parts := strings.Fields(line)
		if len(parts) == 0 {
			continue
		}
		if parts[0] == cmd {
			return parseKV(line), nil
		}
		// skip async events
	}
	return nil, &Error{"timeout waiting for " + cmd}
}

func (c *Client) waitForEvent(eventType string, timeout time.Duration) (map[string]string, error) {
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		line, err := c.readLine(time.Until(deadline))
		if err != nil {
			return nil, err
		}
		parts := strings.Fields(line)
		if len(parts) >= 2 && parts[0] == "EVENT" && parts[1] == eventType {
			return parseKV(line), nil
		}
	}
	return nil, &Error{"timeout waiting for EVENT " + eventType}
}

// parseKV parses "CMD key=val key2=val2 ..." into a map.
func parseKV(line string) map[string]string {
	m := make(map[string]string)
	parts := strings.Fields(line)
	for _, p := range parts[1:] {
		kv := strings.SplitN(p, "=", 2)
		if len(kv) == 2 {
			m[kv[0]] = kv[1]
		}
	}
	return m
}

func parseStartKV(kv map[string]string) *StartInfo {
	return &StartInfo{
		PlaybackURL:       unescape(kv["url"]),
		StatURL:           unescape(kv["stat_url"]),
		CommandURL:        unescape(kv["command_url"]),
		PlaybackSessionID: kv["sid"],
	}
}

// unescape percent-decodes a URL field returned by the AceStream API.
// Falls back to the raw value if decoding fails.
func unescape(s string) string {
	if d, err := url.QueryUnescape(s); err == nil {
		return d
	}
	return s
}

func parseIntField(kv map[string]string, key string) int64 {
	var n int64
	fmt.Sscanf(kv[key], "%d", &n)
	return n
}

func normalizeMode(mode string) string {
	mode = strings.ToLower(strings.TrimSpace(mode))
	switch mode {
	case "pid":
		return "content_id"
	case "torrent":
		return "torrent_url"
	case "url":
		return "direct_url"
	case "raw":
		return "raw_data"
	case "":
		return "content_id"
	default:
		return mode
	}
}
