// Package upstream reads a live MPEG-TS HTTP stream and feeds aligned chunks to a RingBuffer.
package upstream

import (
	"context"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"time"

	"github.com/acestream/proxy/internal/buffer"
	"github.com/acestream/proxy/internal/config"
	"github.com/acestream/proxy/internal/telemetry"
	"github.com/acestream/proxy/internal/ts"
)

const vlcUserAgent = "VLC/3.0.21 LibVLC/3.0.21"

// retryInterval is replaced by exponential backoff in Start().
const (
	initialRetryInterval = 1 * time.Second
	maxRetryInterval     = 8 * time.Second
)

// Reader fetches a streaming URL and writes aligned TS chunks to a RingBuffer.
type Reader struct {
	contentID string
	url       string
	buf       *buffer.RingBuffer
	mode      string

	client *http.Client
	stopCh chan struct{}
}

// New creates a Reader for the given URL/buffer pair.
func New(contentID, url string, buf *buffer.RingBuffer, mode string) *Reader {
	return &Reader{
		contentID: contentID,
		url:       url,
		buf:       buf,
		mode:      mode,
		stopCh:    make(chan struct{}),
		client: &http.Client{
			// No total Timeout here — we use an idle-reset context inside readOnce
			// so P2P starvation gaps are retried without killing the stream.
			Transport: &http.Transport{
				DisableKeepAlives:   false,
				MaxIdleConns:        2,
				IdleConnTimeout:     90 * time.Second,
				TLSHandshakeTimeout: 5 * time.Second,
				DialContext:         (&net.Dialer{Timeout: config.C.Load().UpstreamConnectTimeout}).DialContext,
			},
		},
	}
}

func (r *Reader) Start(ctx context.Context) error {
	tag := fmt.Sprintf("[upstream:%s]", r.contentID)
	slog.Info("upstream reader starting", "stream", r.contentID, "url", r.url)

	deadline := time.Now().Add(config.C.Load().ChannelInitGracePeriod)
	attempt := 0
	everHadData := false

	for {
		select {
		case <-r.stopCh:
			slog.Info("upstream reader stopped", "stream", r.contentID)
			return nil
		case <-ctx.Done():
			return ctx.Err()
		default:
		}

		if attempt > 0 {
			// Calculate exponential backoff: 1, 2, 4, 8s (max 8)
			backoff := initialRetryInterval * time.Duration(1<<(uint(min(attempt-1, 3))))
			if backoff > maxRetryInterval {
				backoff = maxRetryInterval
			}

			slog.Debug("upstream retry backoff", "stream", r.contentID, "wait", backoff, "attempt", attempt)
			select {
			case <-r.stopCh:
				return nil
			case <-ctx.Done():
				return ctx.Err()
			case <-time.After(backoff):
			}
		}

		// Only enforce the init deadline before the first byte has arrived.

		// After that, P2P starvation gaps are expected and we retry indefinitely.
		if !everHadData && time.Now().After(deadline) {
			return fmt.Errorf("%s engine did not start streaming within %s",
				tag, config.C.Load().ChannelInitGracePeriod)
		}

		attempt++
		chunks, err := r.readOnce(ctx, tag)
		if chunks > 0 {
			everHadData = true
		}
		if err != nil {
			// If the context was cancelled (intentional shutdown), exit cleanly
			// without logging a spurious "P2P gap" warning.
			if ctx.Err() != nil {
				return ctx.Err()
			}
			if everHadData {
				slog.Warn("upstream stall (P2P gap), reconnecting", "stream", r.contentID,
					"attempt", attempt, "err", err)
			} else {
				slog.Warn("upstream connect/read error", "stream", r.contentID,
					"attempt", attempt, "err", err)
			}
			continue
		}
		if chunks > 0 {
			// Stream delivered data and then ended normally (clean EOF).
			return nil
		}
		// EOF with zero chunks: engine not ready yet.
		slog.Info("upstream empty response, engine warming up",
			"stream", r.contentID, "attempt", attempt)
	}
}

// readOnce makes one HTTP request and drains the body into the ring buffer.
// Returns the number of chunks written and any non-EOF error.
//
// An idle-reset sub-context (child of ctx) is used so that the body read is
// cancelled after UpstreamReadTimeout of *silence* — not after that duration
// from connection start. Resetting the timer on every data arrival means
// short P2P starvation gaps are survived; the outer Start loop handles retries.
func (r *Reader) readOnce(ctx context.Context, tag string) (int, error) {
	// Idle-reset context: cancelled when no bytes arrive for UpstreamReadTimeout.
	idleCtx, idleCancel := context.WithCancel(ctx)
	defer idleCancel()
	idleTimer := time.AfterFunc(config.C.Load().UpstreamReadTimeout, idleCancel)
	defer idleTimer.Stop()

	req, err := http.NewRequestWithContext(idleCtx, http.MethodGet, r.url, nil)
	if err != nil {
		return 0, fmt.Errorf("%s build request: %w", tag, err)
	}
	req.Header.Set("User-Agent", vlcUserAgent)
	req.Header.Set("Accept", "*/*")
	req.Header.Set("Accept-Encoding", "identity")
	req.Header.Set("Connection", "keep-alive")

	resp, err := r.client.Do(req)
	if err != nil {
		return 0, fmt.Errorf("%s connect: %w", tag, err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return 0, fmt.Errorf("%s upstream returned HTTP %d", tag, resp.StatusCode)
	}

	slog.Info("upstream connected", "stream", r.contentID, "status", resp.StatusCode)

	hunter := ts.NewSyncHunter()
	rawBuf := make([]byte, ts.PacketSize*44) // 8272 bytes
	chunkCount := 0

	for {
		select {
		case <-r.stopCh:
			slog.Info("upstream reader stopped by signal", "stream", r.contentID)
			return chunkCount, nil
		case <-ctx.Done():
			return chunkCount, ctx.Err()
		default:
		}

		n, readErr := resp.Body.Read(rawBuf)
		if n > 0 {
			idleTimer.Reset(config.C.Load().UpstreamReadTimeout)
			aligned := hunter.Feed(rawBuf[:n])
			if len(aligned) > 0 {
				written := r.buf.Write(aligned)
				chunkCount += written
				telemetry.DefaultTelemetry.ObserveIngress(r.mode, int64(len(aligned)))
			}
		}
		if readErr != nil {
			if readErr == io.EOF {
				slog.Info("upstream EOF", "stream", r.contentID, "chunks_written", chunkCount)
				return chunkCount, nil
			}
			return chunkCount, fmt.Errorf("%s read error: %w", tag, readErr)
		}
	}
}

// Stop signals the reader to stop at the next iteration.
func (r *Reader) Stop() {
	select {
	case <-r.stopCh:
	default:
		close(r.stopCh)
	}
}
