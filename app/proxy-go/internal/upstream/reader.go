// Package upstream reads a live MPEG-TS HTTP stream and feeds aligned chunks to a RingBuffer.
package upstream

import (
	"context"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"time"

	"github.com/acestream/proxy/internal/buffer"
	"github.com/acestream/proxy/internal/config"
	"github.com/acestream/proxy/internal/ts"
)

const vlcUserAgent = "VLC/3.0.21 LibVLC/3.0.21"

// Reader fetches a streaming URL and writes aligned TS chunks to a RingBuffer.
type Reader struct {
	contentID string
	url       string
	buf       *buffer.RingBuffer

	client    *http.Client
	stopCh    chan struct{}

	// filled after first connect
	resp *http.Response
}

// New creates a Reader for the given URL/buffer pair.
func New(contentID, url string, buf *buffer.RingBuffer) *Reader {
	return &Reader{
		contentID: contentID,
		url:       url,
		buf:       buf,
		stopCh:    make(chan struct{}),
		client: &http.Client{
			Transport: &http.Transport{
				DisableKeepAlives:   false,
				MaxIdleConns:        2,
				IdleConnTimeout:     90 * time.Second,
				TLSHandshakeTimeout: 5 * time.Second,
			},
		},
	}
}

// Start begins reading in the current goroutine. It blocks until the stream
// ends, the context is cancelled, or Stop is called.
// Callers should run this in its own goroutine.
func (r *Reader) Start(ctx context.Context) error {
	tag := fmt.Sprintf("[upstream:%s]", r.contentID)
	slog.Info("upstream reader starting", "stream", r.contentID, "url", r.url)

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, r.url, nil)
	if err != nil {
		return fmt.Errorf("%s build request: %w", tag, err)
	}
	req.Header.Set("User-Agent", vlcUserAgent)
	req.Header.Set("Accept", "*/*")
	req.Header.Set("Accept-Encoding", "identity")
	req.Header.Set("Connection", "keep-alive")

	// Apply connect timeout via the client's transport
	r.client.Timeout = config.C.UpstreamConnectTimeout + config.C.UpstreamReadTimeout

	resp, err := r.client.Do(req)
	if err != nil {
		return fmt.Errorf("%s connect: %w", tag, err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("%s upstream returned HTTP %d", tag, resp.StatusCode)
	}

	slog.Info("upstream connected", "stream", r.contentID, "status", resp.StatusCode)

	hunter := ts.NewSyncHunter()
	rawBuf := make([]byte, ts.PacketSize*44) // 8272 bytes, same as Python
	chunkCount := 0

	for {
		select {
		case <-r.stopCh:
			slog.Info("upstream reader stopped by signal", "stream", r.contentID)
			return nil
		case <-ctx.Done():
			return ctx.Err()
		default:
		}

		n, err := resp.Body.Read(rawBuf)
		if n > 0 {
			aligned := hunter.Feed(rawBuf[:n])
			if len(aligned) > 0 {
				written := r.buf.Write(aligned)
				chunkCount += written
			}
		}
		if err != nil {
			if err == io.EOF {
				slog.Info("upstream EOF", "stream", r.contentID, "chunks_written", chunkCount)
				return nil
			}
			return fmt.Errorf("%s read error: %w", tag, err)
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
