package api

import (
	"math"
	"sort"
	"strconv"
	"time"

	"github.com/acestream/acestream/internal/state"
)

func buildDashboardSnapshot(st *state.Store, windowSeconds int) map[string]any {
	if windowSeconds <= 0 {
		windowSeconds = 900
	}

	streams := st.ListStreams()
	engines := st.ListEngines()
	streamCounts := st.GetAllStreamCounts()

	activeClients := 0
	peersTotal := 0
	var bufferPieces []int
	var ingressSum int
	var egressSum int

	activeKeysMap := map[string]struct{}{}
	for _, s := range streams {
		activeClients += s.ActiveClients
		if s.Peers != nil {
			peersTotal += *s.Peers
		}
		if s.SpeedDown != nil {
			ingressSum += *s.SpeedDown
		}
		if s.SpeedUp != nil {
			egressSum += *s.SpeedUp
		}

		buffer := extractBufferPieces(s)
		if buffer >= 0 {
			bufferPieces = append(bufferPieces, buffer)
		}

		key := s.Key
		if key == "" {
			key = s.ContentID
		}
		if key != "" {
			activeKeysMap[key] = struct{}{}
		}
	}

	activeKeys := make([]string, 0, len(activeKeysMap))
	for key := range activeKeysMap {
		activeKeys = append(activeKeys, key)
	}
	sort.Strings(activeKeys)

	bufferAvg, bufferMin := summarizeBuffers(bufferPieces)

	usedEngines := 0
	var healthy, unhealthy, draining, unknown int
	for _, e := range engines {
		count := streamCounts[e.ContainerID]
		if count > 0 {
			usedEngines++
		}
		switch {
		case e.Draining:
			draining++
		case e.HealthStatus == "healthy":
			healthy++
		case e.HealthStatus == "unhealthy":
			unhealthy++
		default:
			unknown++
		}
	}

	engineStateCounts := map[string]int{
		"playing":   usedEngines,
		"idle":      int(math.Max(float64(healthy-usedEngines), 0)),
		"unhealthy": unhealthy,
		"unknown":   unknown,
	}

	ingressMbps := toMbps(ingressSum)
	egressMbps := toMbps(egressSum)

	cpuPercent, memBytes := summarizeEngineResources(engines)

	return map[string]any{
		"timestamp":                  time.Now().UTC().Unix(),
		"observation_window_seconds": windowSeconds,
		"north_star": map[string]any{
			"global_active_streams":        len(streams),
			"global_egress_bandwidth_mbps": round3(egressMbps),
			"system_success_rate_percent":  100,
			"proxy_active_clients":         activeClients,
		},
		"proxy": map[string]any{
			"throughput": map[string]any{
				"ingress_mbps": ingressMbps,
				"egress_mbps":  egressMbps,
			},
			"request_window_1m": map[string]any{
				"success_rate_percent":   100,
				"total_requests_1m":      0,
				"error_4xx_rate_per_min": 0,
				"error_5xx_rate_per_min": 0,
				"ttfb_p95_ms":            0,
			},
			"ttfb": map[string]any{
				"avg_ms": 0,
				"p95_ms": 0,
			},
			"active_clients": map[string]any{
				"total": activeClients,
				"ts":    0,
				"hls":   0,
			},
		},
		"engines": map[string]any{
			"total":              len(engines),
			"healthy":            healthy,
			"unhealthy":          unhealthy,
			"unknown":            unknown,
			"used":               usedEngines,
			"state_counts":       engineStateCounts,
			"uptime_avg_seconds": 0,
		},
		"streams": map[string]any{
			"active":              len(streams),
			"total_peers":         peersTotal,
			"download_speed_mbps": round3(ingressMbps),
			"active_keys":         activeKeys,
			"buffer": map[string]any{
				"avg_pieces": bufferAvg,
				"min_pieces": bufferMin,
			},
		},
		"docker": map[string]any{
			"cpu_percent":      round3(cpuPercent),
			"memory_usage":     int64(memBytes),
			"restart_total":    0,
			"oom_killed_total": 0,
		},
	}
}

func extractBufferPieces(s *state.StreamState) int {
	if s == nil {
		return -1
	}
	if s.ProxyBufferPieces != nil {
		return *s.ProxyBufferPieces
	}
	if s.Livepos != nil {
		switch v := s.Livepos.BufferPieces.(type) {
		case int:
			return v
		case int64:
			return int(v)
		case float64:
			return int(v)
		case string:
			if n, err := strconv.Atoi(v); err == nil {
				return n
			}
		}
	}
	return -1
}

func summarizeBuffers(values []int) (avg int, min int) {
	if len(values) == 0 {
		return 0, 0
	}
	sum := 0
	min = values[0]
	for _, v := range values {
		sum += v
		if v < min {
			min = v
		}
	}
	avg = sum / len(values)
	return avg, min
}

func toMbps(speed int) float64 {
	if speed <= 0 {
		return 0
	}
	return float64(speed) * 8.0 / 1024.0 / 1024.0
}

func summarizeEngineResources(engines []*state.Engine) (cpuAvg float64, memTotal int64) {
	if len(engines) == 0 {
		return 0, 0
	}
	var cpuSum float64
	for _, e := range engines {
		cpuSum += e.CPUPercent
		memTotal += e.MemoryUsage
	}
	cpuAvg = cpuSum / float64(len(engines))
	return cpuAvg, memTotal
}

func round3(value float64) float64 {
	return math.Round(value*1000) / 1000
}
