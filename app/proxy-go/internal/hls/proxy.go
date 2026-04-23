// Package hls implements an HLS manifest and segment reverse proxy.
// It rewrites segment URLs in .m3u8 manifests so clients always use the
// proxy as the origin, enabling caching and auth enforcement.
package hls

import (
	"bufio"
	"context"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/acestream/proxy/internal/config"
)

const vlcUserAgent = "VLC/3.0.21 LibVLC/3.0.21"

// ProxySession handles HLS proxying for one stream.
// It fetches the upstream manifest periodically and rewrites segment URLs.
type ProxySession struct {
	contentID   string
	upstreamURL string // full .m3u8 URL on the engine
	proxyBase   string // base URL of the proxy (for rewriting)
	httpClient  *http.Client
}

// NewSession creates a new HLS proxy session.
// upstreamURL: the engine's .m3u8 URL
// proxyBase: e.g. "http://proxy-host:8000"
func NewSession(contentID, upstreamURL, proxyBase string) *ProxySession {
	return &ProxySession{
		contentID:   contentID,
		upstreamURL: upstreamURL,
		proxyBase:   strings.TrimRight(proxyBase, "/"),
		httpClient: &http.Client{
			Timeout: 30 * time.Second,
			Transport: &http.Transport{
				DisableKeepAlives:   false,
				MaxIdleConns:        4,
				IdleConnTimeout:     90 * time.Second,
				TLSHandshakeTimeout: 5 * time.Second,
			},
		},
	}
}

func (ps *ProxySession) Stop() {
	// No-op. Global cache manages its own GC.
}

// ServeManifest fetches the upstream manifest and rewrites segment URLs, then writes to w.
func (ps *ProxySession) ServeManifest(ctx context.Context, w http.ResponseWriter) error {
	manifest, err := ps.fetchManifest(ctx)
	if err != nil {
		return fmt.Errorf("hls manifest fetch: %w", err)
	}
	rewritten := ps.rewriteManifest(manifest)

	w.Header().Set("Content-Type", "application/vnd.apple.mpegurl")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Access-Control-Allow-Origin", "*")
	fmt.Fprint(w, rewritten)
	return nil
}

// ServeSegment fetches a segment by its original upstream URL (cached) and streams it to w.
func (ps *ProxySession) ServeSegment(ctx context.Context, segmentURL string, w http.ResponseWriter) error {
	if DefaultCache != nil {
		if data, ok := DefaultCache.Get(segmentURL); ok {
			w.Header().Set("Content-Type", "video/MP2T")
			w.Header().Set("Content-Length", fmt.Sprintf("%d", len(data)))
			w.Write(data) //nolint:errcheck
			return nil
		}
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, segmentURL, nil)
	if err != nil {
		return err
	}
	req.Header.Set("User-Agent", vlcUserAgent)
	req.Header.Set("Accept", "*/*")

	resp, err := ps.httpClient.Do(req)
	if err != nil {
		return fmt.Errorf("segment fetch: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("segment returned HTTP %d", resp.StatusCode)
	}

	data, err := io.ReadAll(resp.Body)
	if err != nil {
		return fmt.Errorf("segment read: %w", err)
	}

	ttl := time.Duration(config.C.Load().HLSClientIdleTimeout) * 2
	if DefaultCache != nil {
		DefaultCache.Set(segmentURL, data, ttl)
	}

	w.Header().Set("Content-Type", "video/MP2T")
	w.Header().Set("Content-Length", fmt.Sprintf("%d", len(data)))
	w.Write(data) //nolint:errcheck
	return nil
}

// fetchManifest retrieves the raw .m3u8 content from the engine.
func (ps *ProxySession) fetchManifest(ctx context.Context) (string, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, ps.upstreamURL, nil)
	if err != nil {
		return "", err
	}
	req.Header.Set("User-Agent", vlcUserAgent)

	resp, err := ps.httpClient.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("manifest fetch HTTP %d", resp.StatusCode)
	}

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return "", err
	}
	return string(body), nil
}

// rewriteManifest parses a simple HLS manifest and rewrites segment URLs to go through the proxy.
func (ps *ProxySession) rewriteManifest(raw string) string {
	baseURL, err := url.Parse(ps.upstreamURL)
	if err != nil {
		slog.Warn("hls: could not parse upstream URL", "url", ps.upstreamURL, "err", err)
		return raw
	}

	var sb strings.Builder
	scanner := bufio.NewScanner(strings.NewReader(raw))
	for scanner.Scan() {
		line := scanner.Text()
		if strings.HasPrefix(line, "#") || strings.TrimSpace(line) == "" {
			sb.WriteString(line)
			sb.WriteByte('\n')
			continue
		}
		// Resolve segment URL relative to manifest base
		segURL, err := baseURL.Parse(line)
		if err != nil {
			sb.WriteString(line)
		} else {
			// Rewrite: /ace/hls/segment.ts?stream=<id>&url=<encoded>
			proxied := fmt.Sprintf("%s/ace/hls/segment.ts?stream=%s&url=%s",
				ps.proxyBase, url.QueryEscape(ps.contentID), url.QueryEscape(segURL.String()))
			sb.WriteString(proxied)
		}
		sb.WriteByte('\n')
	}
	return sb.String()
}
