package config

import "testing"

func TestApplySettingsAllowsZeroProxyPrebuffer(t *testing.T) {
	original := C.Load()
	baseline := *original
	baseline.ProxyPrebufferSeconds = 3
	C.Store(&baseline)
	t.Cleanup(func() { C.Store(original) })

	ApplySettings(map[string]any{"proxy_prebuffer_seconds": float64(0)})

	if got := C.Load().ProxyPrebufferSeconds; got != 0 {
		t.Errorf("ProxyPrebufferSeconds = %d, want 0 to disable prebuffer", got)
	}
}
