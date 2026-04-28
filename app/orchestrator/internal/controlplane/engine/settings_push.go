package engine

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"time"

	"github.com/docker/docker/api/types/container"

	"github.com/acestream/acestream/internal/state"
)

// PushEngineConfig pushes live-settable engine_config fields to every healthy
// managed engine via PATCH /api/v1/settings on the engine's REST API.
// Token is retrieved by docker exec-reading /acestream/engine_runtime.json.
// Fields that require a container restart are silently skipped.
func PushEngineConfig(ctx context.Context, cfg map[string]any) map[string]bool {
	engines := state.Global.ListEngines()
	results := make(map[string]bool, len(engines))

	payload := buildLivePushPayload(cfg)
	if len(payload) == 0 {
		return results
	}

	body, err := json.Marshal(payload)
	if err != nil {
		slog.Warn("engine config push: marshal failed", "err", err)
		return results
	}

	for _, e := range engines {
		if e.HealthStatus != state.HealthHealthy {
			continue
		}
		if e.Labels["manual"] == "true" {
			continue
		}
		name := e.ContainerName
		if name == "" {
			name = e.ContainerID
		}

		token, err := getEngineToken(ctx, name)
		if err != nil || token == "" {
			slog.Debug("engine config push: no token", "engine", name, "err", err)
			continue
		}

		ok := patchEngineSettings(ctx, e.Host, e.Port, token, body)
		results[name] = ok
		if ok {
			slog.Debug("engine config pushed", "engine", name)
		} else {
			slog.Warn("engine config push failed", "engine", name)
		}
	}

	success, failure := 0, 0
	for _, ok := range results {
		if ok {
			success++
		} else {
			failure++
		}
	}
	if len(results) > 0 {
		slog.Info("engine config push complete",
			"success", success, "failure", failure, "total", len(results))
	}
	return results
}

// buildLivePushPayload maps engine_config keys to the AceStream REST API fields.
// Only live-settable fields are included; restart-required fields are omitted.
func buildLivePushPayload(cfg map[string]any) map[string]any {
	payload := map[string]any{}
	if v, ok := cfg["total_max_upload_rate"]; ok {
		payload["upload_limit"] = anyToInt(v)
	}
	if v, ok := cfg["total_max_download_rate"]; ok {
		payload["download_limit"] = anyToInt(v)
	}
	if v, ok := cfg["buffer_time"]; ok {
		payload["live_buffer"] = anyToInt(v)
	}
	if v, ok := cfg["live_cache_type"]; ok {
		if s, ok2 := v.(string); ok2 {
			payload["live_cache_type"] = s
		}
	}
	return payload
}

func anyToInt(v any) int {
	switch n := v.(type) {
	case int:
		return n
	case int64:
		return int(n)
	case float64:
		return int(n)
	case float32:
		return int(n)
	}
	return 0
}

// getEngineToken reads the access_token from /acestream/engine_runtime.json
// inside the running container using docker exec.
func getEngineToken(ctx context.Context, containerName string) (string, error) {
	cli, err := NewDockerClientExported()
	if err != nil {
		return "", err
	}
	defer cli.Close()

	execCtx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()

	idResp, err := cli.ContainerExecCreate(execCtx, containerName, container.ExecOptions{
		AttachStdout: true,
		AttachStderr: false,
		Cmd:          []string{"cat", "/acestream/engine_runtime.json"},
	})
	if err != nil {
		return "", fmt.Errorf("exec create: %w", err)
	}

	resp, err := cli.ContainerExecAttach(execCtx, idResp.ID, container.ExecAttachOptions{Detach: false})
	if err != nil {
		return "", fmt.Errorf("exec attach: %w", err)
	}
	defer resp.Close()

	out, err := io.ReadAll(io.LimitReader(resp.Reader, 4096))
	if err != nil {
		return "", err
	}

	// Docker multiplexes stdout: strip the 8-byte header on each frame.
	data := stripDockerMux(out)

	var v map[string]any
	if err := json.Unmarshal(data, &v); err != nil {
		return "", fmt.Errorf("parse engine_runtime.json: %w", err)
	}
	token, _ := v["access_token"].(string)
	return token, nil
}

// stripDockerMux strips Docker stream multiplexing headers (8 bytes per frame).
func stripDockerMux(b []byte) []byte {
	var out []byte
	for len(b) >= 8 {
		size := int(b[4])<<24 | int(b[5])<<16 | int(b[6])<<8 | int(b[7])
		b = b[8:]
		if size > len(b) {
			size = len(b)
		}
		out = append(out, b[:size]...)
		b = b[size:]
	}
	if len(out) == 0 {
		return b // fallback: return raw if not multiplexed
	}
	return out
}

func patchEngineSettings(ctx context.Context, host string, port int, token string, body []byte) bool {
	url := fmt.Sprintf("http://%s:%d/api/v1/settings", host, port)
	req, err := http.NewRequestWithContext(ctx, http.MethodPatch, url, bytes.NewReader(body))
	if err != nil {
		return false
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("x-api-key", token)

	client := &http.Client{Timeout: 5 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return false
	}
	defer resp.Body.Close()
	return resp.StatusCode == http.StatusOK || resp.StatusCode == http.StatusNoContent
}
