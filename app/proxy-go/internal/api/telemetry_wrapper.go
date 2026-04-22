package api

import (
	"net/http"
	"time"

	"github.com/acestream/proxy/internal/telemetry"
)

type ttfbResponseWriter struct {
	http.ResponseWriter
	statusCode int
	wroteHeader bool
	ttfb        time.Duration
	start       time.Time
}

func newTTFBResponseWriter(w http.ResponseWriter) *ttfbResponseWriter {
	return &ttfbResponseWriter{
		ResponseWriter: w,
		statusCode:     http.StatusOK,
		start:          time.Now(),
	}
}

func (w *ttfbResponseWriter) WriteHeader(code int) {
	if !w.wroteHeader {
		w.statusCode = code
		w.wroteHeader = true
	}
	w.ResponseWriter.WriteHeader(code)
}

func (w *ttfbResponseWriter) Write(b []byte) (int, error) {
	if !w.wroteHeader {
		w.statusCode = http.StatusOK
		w.wroteHeader = true
	}
	if w.ttfb == 0 {
		w.ttfb = time.Since(w.start)
	}
	return w.ResponseWriter.Write(b)
}

func (w *ttfbResponseWriter) Flush() {
	if f, ok := w.ResponseWriter.(http.Flusher); ok {
		f.Flush()
	}
}

func withTelemetry(mode string, next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		tw := newTTFBResponseWriter(w)
		
		next(tw, r)
		
		duration := time.Since(start).Seconds()
		ttfbSecs := tw.ttfb.Seconds()
		if !tw.wroteHeader {
			ttfbSecs = duration // If nothing wrote, TTFB is the whole duration
		}

		success := tw.statusCode >= 200 && tw.statusCode < 400
		telemetry.DefaultTelemetry.ObserveRequest(mode, r.URL.Path, duration, success, tw.statusCode, ttfbSecs)
	}
}
