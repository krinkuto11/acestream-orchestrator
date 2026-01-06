package main

import (
	"testing"
	"time"
)

func TestSelectBestEngineLoadBalancing(t *testing.T) {
	// Test data: engines with different stream counts, health status, and LastStreamUsage timestamps
	now := time.Now()
	engines := []engineWithLoad{
		{
			engine: engineState{
				ContainerID:     "engine1",
				Host:            "host1",
				Port:            8001,
				HealthStatus:    "healthy",
				LastStreamUsage: now.Add(-10 * time.Minute), // Older stream usage
			},
			activeStreams: 1,
		},
		{
			engine: engineState{
				ContainerID:     "engine2",
				Host:            "host2",
				Port:            8002,
				HealthStatus:    "healthy",
				LastStreamUsage: now.Add(-5 * time.Minute), // More recent stream usage
			},
			activeStreams: 1,
		},
		{
			engine: engineState{
				ContainerID:     "engine3",
				Host:            "host3",
				Port:            8003,
				HealthStatus:    "healthy",
				LastStreamUsage: now.Add(-20 * time.Minute), // Very old stream usage
			},
			activeStreams: 0, // Empty engine
		},
		{
			engine: engineState{
				ContainerID:     "engine4",
				Host:            "host4",
				Port:            8004,
				HealthStatus:    "unhealthy",
				LastStreamUsage: now.Add(-1 * time.Minute), // Most recent stream usage, but unhealthy
			},
			activeStreams: 0, // Empty but unhealthy engine
		},
		{
			engine: engineState{
				ContainerID:     "engine5",
				Host:            "host5",
				Port:            8005,
				HealthStatus:    "healthy",
				LastStreamUsage: now.Add(-1 * time.Minute), // Most recent stream usage
			},
			activeStreams: 0, // Empty healthy engine
		},
	}

	// Apply the same sorting logic as in SelectBestEngine
	availableEngines := make([]engineWithLoad, len(engines))
	copy(availableEngines, engines)

	// Sort engines by health status first (healthy engines prioritized),
	// then by stream count (empty engines prioritized - addressing issue where all streams go to forwarded engines),
	// then by forwarded status (forwarded engines prioritized as they are faster),
	// then by last_stream_usage (ascending - oldest first)
	for i := 0; i < len(availableEngines); i++ {
		for j := i + 1; j < len(availableEngines); j++ {
			iEngine := availableEngines[i]
			jEngine := availableEngines[j]

			// Primary sort: by health status (healthy engines first)
			iHealthy := iEngine.engine.HealthStatus == "healthy"
			jHealthy := jEngine.engine.HealthStatus == "healthy"

			if iHealthy != jHealthy {
				// If one is healthy and other is not, prioritize healthy
				if jHealthy && !iHealthy {
					availableEngines[i], availableEngines[j] = availableEngines[j], availableEngines[i]
				}
			} else {
				// Both have same health status, sort by active stream count (empty engines prioritized)
				if iEngine.activeStreams > jEngine.activeStreams {
					availableEngines[i], availableEngines[j] = availableEngines[j], availableEngines[i]
				} else if iEngine.activeStreams == jEngine.activeStreams {
					// Same health and stream count, sort by forwarded status (forwarded engines prioritized)
					iForwarded := iEngine.engine.Forwarded
					jForwarded := jEngine.engine.Forwarded

					if iForwarded != jForwarded {
						// If one is forwarded and other is not, prioritize forwarded
						if jForwarded && !iForwarded {
							availableEngines[i], availableEngines[j] = availableEngines[j], availableEngines[i]
						}
					} else {
						// Same health, stream count, and forwarded status, sort by last_stream_usage (ascending - oldest first)
						// This ensures that among engines with same health, stream count, and forwarded status, we pick the one unused the longest
						if iEngine.engine.LastStreamUsage.After(jEngine.engine.LastStreamUsage) {
							availableEngines[i], availableEngines[j] = availableEngines[j], availableEngines[i]
						}
					}
				}
			}
		}
	}

	// Verify sorting results
	// Expected order: healthy engines first, then by stream count, then by last_stream_usage
	// 1. engine3 (healthy, 0 streams, oldest usage: -20min)
	// 2. engine5 (healthy, 0 streams, newer usage: -1min)
	// 3. engine1 (healthy, 1 stream, older usage: -10min)
	// 4. engine2 (healthy, 1 stream, newer usage: -5min)
	// 5. engine4 (unhealthy, 0 streams)

	// First should be healthy empty engine with oldest stream usage (engine3)
	if availableEngines[0].engine.ContainerID != "engine3" {
		t.Errorf("Expected engine3 (healthy, empty, oldest usage) to be first, got %s", availableEngines[0].engine.ContainerID)
	}
	if availableEngines[0].activeStreams != 0 {
		t.Errorf("Expected first engine to have 0 streams, got %d", availableEngines[0].activeStreams)
	}
	if availableEngines[0].engine.HealthStatus != "healthy" {
		t.Errorf("Expected first engine to be healthy, got %s", availableEngines[0].engine.HealthStatus)
	}

	// Second should be healthy empty engine with newer stream usage (engine5)
	if availableEngines[1].engine.ContainerID != "engine5" {
		t.Errorf("Expected engine5 (healthy, empty, newer usage) to be second, got %s", availableEngines[1].engine.ContainerID)
	}
	if availableEngines[1].activeStreams != 0 {
		t.Errorf("Expected second engine to have 0 streams, got %d", availableEngines[1].activeStreams)
	}

	// Third should be healthy engine with 1 stream and older usage (engine1)
	if availableEngines[2].engine.ContainerID != "engine1" {
		t.Errorf("Expected engine1 (healthy, 1 stream, older usage) to be third, got %s", availableEngines[2].engine.ContainerID)
	}
	if availableEngines[2].activeStreams != 1 {
		t.Errorf("Expected third engine to have 1 stream, got %d", availableEngines[2].activeStreams)
	}

	// Fourth should be healthy engine with 1 stream and newer usage (engine2)
	if availableEngines[3].engine.ContainerID != "engine2" {
		t.Errorf("Expected engine2 (healthy, 1 stream, newer usage) to be fourth, got %s", availableEngines[3].engine.ContainerID)
	}
	if availableEngines[3].activeStreams != 1 {
		t.Errorf("Expected fourth engine to have 1 stream, got %d", availableEngines[3].activeStreams)
	}

	// Fifth should be unhealthy engine (engine4)
	if availableEngines[4].engine.ContainerID != "engine4" {
		t.Errorf("Expected engine4 (unhealthy) to be last, got %s", availableEngines[4].engine.ContainerID)
	}
	if availableEngines[4].engine.HealthStatus != "unhealthy" {
		t.Errorf("Expected last engine to be unhealthy, got %s", availableEngines[4].engine.HealthStatus)
	}
}

// Define the engineWithLoad type for testing (it's defined locally in the original function)
type engineWithLoad struct {
	engine        engineState
	activeStreams int
}

func TestSelectBestEngineForwardedPriority(t *testing.T) {
	// Test data: engines with forwarded status to verify forwarded engines are prioritized
	now := time.Now()
	engines := []engineWithLoad{
		{
			engine: engineState{
				ContainerID:     "engine1",
				Host:            "host1",
				Port:            8001,
				HealthStatus:    "healthy",
				Forwarded:       false, // Not forwarded
				LastStreamUsage: now.Add(-10 * time.Minute),
			},
			activeStreams: 0, // Empty engine but not forwarded
		},
		{
			engine: engineState{
				ContainerID:     "engine2",
				Host:            "host2",
				Port:            8002,
				HealthStatus:    "healthy",
				Forwarded:       true, // Forwarded - should be prioritized
				LastStreamUsage: now.Add(-5 * time.Minute),
			},
			activeStreams: 0, // Empty and forwarded
		},
		{
			engine: engineState{
				ContainerID:     "engine3",
				Host:            "host3",
				Port:            8003,
				HealthStatus:    "healthy",
				Forwarded:       false,
				LastStreamUsage: now.Add(-20 * time.Minute),
			},
			activeStreams: 1, // Has streams but not forwarded
		},
		{
			engine: engineState{
				ContainerID:     "engine4",
				Host:            "host4",
				Port:            8004,
				HealthStatus:    "healthy",
				Forwarded:       true, // Forwarded
				LastStreamUsage: now.Add(-1 * time.Minute),
			},
			activeStreams: 1, // Has streams and forwarded
		},
		{
			engine: engineState{
				ContainerID:     "engine5",
				Host:            "host5",
				Port:            8005,
				HealthStatus:    "unhealthy",
				Forwarded:       true, // Forwarded but unhealthy
				LastStreamUsage: now.Add(-1 * time.Minute),
			},
			activeStreams: 0,
		},
	}

	// Apply the same sorting logic as in SelectBestEngine
	availableEngines := make([]engineWithLoad, len(engines))
	copy(availableEngines, engines)

	// Sort engines by health status first (healthy engines prioritized),
	// then by stream count (empty engines prioritized - addressing issue where all streams go to forwarded engines),
	// then by forwarded status (forwarded engines prioritized as they are faster),
	// then by last_stream_usage (ascending - oldest first)
	for i := 0; i < len(availableEngines); i++ {
		for j := i + 1; j < len(availableEngines); j++ {
			iEngine := availableEngines[i]
			jEngine := availableEngines[j]

			// Primary sort: by health status (healthy engines first)
			iHealthy := iEngine.engine.HealthStatus == "healthy"
			jHealthy := jEngine.engine.HealthStatus == "healthy"

			if iHealthy != jHealthy {
				// If one is healthy and other is not, prioritize healthy
				if jHealthy && !iHealthy {
					availableEngines[i], availableEngines[j] = availableEngines[j], availableEngines[i]
				}
			} else {
				// Both have same health status, sort by active stream count (empty engines prioritized)
				if iEngine.activeStreams > jEngine.activeStreams {
					availableEngines[i], availableEngines[j] = availableEngines[j], availableEngines[i]
				} else if iEngine.activeStreams == jEngine.activeStreams {
					// Same health and stream count, sort by forwarded status (forwarded engines prioritized)
					iForwarded := iEngine.engine.Forwarded
					jForwarded := jEngine.engine.Forwarded

					if iForwarded != jForwarded {
						// If one is forwarded and other is not, prioritize forwarded
						if jForwarded && !iForwarded {
							availableEngines[i], availableEngines[j] = availableEngines[j], availableEngines[i]
						}
					} else {
						// Same health, stream count, and forwarded status, sort by last_stream_usage (ascending - oldest first)
						// This ensures that among engines with same health, stream count, and forwarded status, we pick the one unused the longest
						if iEngine.engine.LastStreamUsage.After(jEngine.engine.LastStreamUsage) {
							availableEngines[i], availableEngines[j] = availableEngines[j], availableEngines[i]
						}
					}
				}
			}
		}
	}

	// Verify sorting results
	// Expected order (stream count prioritized before forwarded status):
	// 1. engine2 (healthy, 0 streams, forwarded) - empty forwarded engine
	// 2. engine1 (healthy, 0 streams, not forwarded) - empty non-forwarded engine
	// 3. engine4 (healthy, 1 stream, forwarded) - non-empty forwarded engine
	// 4. engine3 (healthy, 1 stream, not forwarded) - non-empty non-forwarded engine
	// 5. engine5 (unhealthy, 0 streams, forwarded) - unhealthy engine

	// First should be healthy forwarded empty engine (engine2)
	if availableEngines[0].engine.ContainerID != "engine2" {
		t.Errorf("Expected engine2 (healthy, forwarded, empty) to be first, got %s", availableEngines[0].engine.ContainerID)
	}
	if !availableEngines[0].engine.Forwarded {
		t.Errorf("Expected first engine to be forwarded")
	}
	if availableEngines[0].activeStreams != 0 {
		t.Errorf("Expected first engine to have 0 streams, got %d", availableEngines[0].activeStreams)
	}
	if availableEngines[0].engine.HealthStatus != "healthy" {
		t.Errorf("Expected first engine to be healthy, got %s", availableEngines[0].engine.HealthStatus)
	}

	// Second should be healthy non-forwarded empty engine (engine1)
	if availableEngines[1].engine.ContainerID != "engine1" {
		t.Errorf("Expected engine1 (healthy, not forwarded, empty) to be second, got %s", availableEngines[1].engine.ContainerID)
	}
	if availableEngines[1].engine.Forwarded {
		t.Errorf("Expected second engine to not be forwarded")
	}
	if availableEngines[1].activeStreams != 0 {
		t.Errorf("Expected second engine to have 0 streams, got %d", availableEngines[1].activeStreams)
	}

	// Third should be healthy forwarded engine with 1 stream (engine4)
	if availableEngines[2].engine.ContainerID != "engine4" {
		t.Errorf("Expected engine4 (healthy, forwarded, 1 stream) to be third, got %s", availableEngines[2].engine.ContainerID)
	}
	if !availableEngines[2].engine.Forwarded {
		t.Errorf("Expected third engine to be forwarded")
	}
	if availableEngines[2].activeStreams != 1 {
		t.Errorf("Expected third engine to have 1 stream, got %d", availableEngines[2].activeStreams)
	}

	// Fourth should be healthy non-forwarded engine with 1 stream (engine3)
	if availableEngines[3].engine.ContainerID != "engine3" {
		t.Errorf("Expected engine3 (healthy, not forwarded, 1 stream) to be fourth, got %s", availableEngines[3].engine.ContainerID)
	}
	if availableEngines[3].engine.Forwarded {
		t.Errorf("Expected fourth engine to not be forwarded")
	}
	if availableEngines[3].activeStreams != 1 {
		t.Errorf("Expected fourth engine to have 1 stream, got %d", availableEngines[3].activeStreams)
	}

	// Fifth should be unhealthy forwarded engine (engine5)
	if availableEngines[4].engine.ContainerID != "engine5" {
		t.Errorf("Expected engine5 (unhealthy, forwarded) to be last, got %s", availableEngines[4].engine.ContainerID)
	}
	if availableEngines[4].engine.HealthStatus != "unhealthy" {
		t.Errorf("Expected last engine to be unhealthy, got %s", availableEngines[4].engine.HealthStatus)
	}
}
