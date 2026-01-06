package main

import (
	"testing"
	"time"
)

// TestEngineSelectionPriorityOrder validates the exact priority requirements from the issue:
// 1. Empty forwarded Engines
// 2. Empty non-forwarded Engines
// 3. Non-empty engines with the lowest stream count (Forwarded>non-forwarded)
func TestEngineSelectionPriorityOrder(t *testing.T) {
	now := time.Now()
	
	// Create test data with all combinations to validate the priority order
	engines := []engineWithLoad{
		// Group 1: Empty forwarded engines (should be prioritized first)
		{
			engine: engineState{
				ContainerID:     "empty-forwarded-1",
				Host:            "host1",
				Port:            8001,
				HealthStatus:    "healthy",
				Forwarded:       true,
				LastStreamUsage: now.Add(-5 * time.Minute),
			},
			activeStreams: 0,
		},
		{
			engine: engineState{
				ContainerID:     "empty-forwarded-2",
				Host:            "host2",
				Port:            8002,
				HealthStatus:    "healthy",
				Forwarded:       true,
				LastStreamUsage: now.Add(-10 * time.Minute), // Older usage
			},
			activeStreams: 0,
		},
		// Group 2: Empty non-forwarded engines (should be prioritized second)
		{
			engine: engineState{
				ContainerID:     "empty-nonforwarded-1",
				Host:            "host3",
				Port:            8003,
				HealthStatus:    "healthy",
				Forwarded:       false,
				LastStreamUsage: now.Add(-3 * time.Minute),
			},
			activeStreams: 0,
		},
		{
			engine: engineState{
				ContainerID:     "empty-nonforwarded-2",
				Host:            "host4",
				Port:            8004,
				HealthStatus:    "healthy",
				Forwarded:       false,
				LastStreamUsage: now.Add(-15 * time.Minute), // Older usage
			},
			activeStreams: 0,
		},
		// Group 3: Non-empty forwarded engines (should be prioritized third)
		{
			engine: engineState{
				ContainerID:     "nonempty-forwarded-1",
				Host:            "host5",
				Port:            8005,
				HealthStatus:    "healthy",
				Forwarded:       true,
				LastStreamUsage: now.Add(-2 * time.Minute),
			},
			activeStreams: 1,
		},
		{
			engine: engineState{
				ContainerID:     "nonempty-forwarded-2",
				Host:            "host6",
				Port:            8006,
				HealthStatus:    "healthy",
				Forwarded:       true,
				LastStreamUsage: now.Add(-1 * time.Minute),
			},
			activeStreams: 2,
		},
		// Group 4: Non-empty non-forwarded engines (should be prioritized fourth)
		{
			engine: engineState{
				ContainerID:     "nonempty-nonforwarded-1",
				Host:            "host7",
				Port:            8007,
				HealthStatus:    "healthy",
				Forwarded:       false,
				LastStreamUsage: now.Add(-4 * time.Minute),
			},
			activeStreams: 1,
		},
		{
			engine: engineState{
				ContainerID:     "nonempty-nonforwarded-2",
				Host:            "host8",
				Port:            8008,
				HealthStatus:    "healthy",
				Forwarded:       false,
				LastStreamUsage: now.Add(-6 * time.Minute),
			},
			activeStreams: 2,
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

	// Verify the priority order matches the requirements
	
	// First two should be empty forwarded engines (oldest usage first)
	if availableEngines[0].activeStreams != 0 || !availableEngines[0].engine.Forwarded {
		t.Errorf("Expected first engine to be empty forwarded, got streams=%d forwarded=%v",
			availableEngines[0].activeStreams, availableEngines[0].engine.Forwarded)
	}
	if availableEngines[0].engine.ContainerID != "empty-forwarded-2" {
		t.Errorf("Expected empty-forwarded-2 (oldest) first, got %s", availableEngines[0].engine.ContainerID)
	}
	
	if availableEngines[1].activeStreams != 0 || !availableEngines[1].engine.Forwarded {
		t.Errorf("Expected second engine to be empty forwarded, got streams=%d forwarded=%v",
			availableEngines[1].activeStreams, availableEngines[1].engine.Forwarded)
	}
	if availableEngines[1].engine.ContainerID != "empty-forwarded-1" {
		t.Errorf("Expected empty-forwarded-1 second, got %s", availableEngines[1].engine.ContainerID)
	}

	// Next two should be empty non-forwarded engines (oldest usage first)
	if availableEngines[2].activeStreams != 0 || availableEngines[2].engine.Forwarded {
		t.Errorf("Expected third engine to be empty non-forwarded, got streams=%d forwarded=%v",
			availableEngines[2].activeStreams, availableEngines[2].engine.Forwarded)
	}
	if availableEngines[2].engine.ContainerID != "empty-nonforwarded-2" {
		t.Errorf("Expected empty-nonforwarded-2 (oldest) third, got %s", availableEngines[2].engine.ContainerID)
	}
	
	if availableEngines[3].activeStreams != 0 || availableEngines[3].engine.Forwarded {
		t.Errorf("Expected fourth engine to be empty non-forwarded, got streams=%d forwarded=%v",
			availableEngines[3].activeStreams, availableEngines[3].engine.Forwarded)
	}
	if availableEngines[3].engine.ContainerID != "empty-nonforwarded-1" {
		t.Errorf("Expected empty-nonforwarded-1 fourth, got %s", availableEngines[3].engine.ContainerID)
	}

	// Next should be non-empty engines sorted by stream count, with forwarded preferred within same count
	// First: 1 stream forwarded
	if availableEngines[4].activeStreams != 1 || !availableEngines[4].engine.Forwarded {
		t.Errorf("Expected fifth engine to be non-empty (1 stream) forwarded, got streams=%d forwarded=%v",
			availableEngines[4].activeStreams, availableEngines[4].engine.Forwarded)
	}
	if availableEngines[4].engine.ContainerID != "nonempty-forwarded-1" {
		t.Errorf("Expected nonempty-forwarded-1 (1 stream) fifth, got %s",
			availableEngines[4].engine.ContainerID)
	}
	
	// Next: 1 stream non-forwarded
	if availableEngines[5].activeStreams != 1 || availableEngines[5].engine.Forwarded {
		t.Errorf("Expected sixth engine to be non-empty (1 stream) non-forwarded, got streams=%d forwarded=%v",
			availableEngines[5].activeStreams, availableEngines[5].engine.Forwarded)
	}
	if availableEngines[5].engine.ContainerID != "nonempty-nonforwarded-1" {
		t.Errorf("Expected nonempty-nonforwarded-1 (1 stream) sixth, got %s",
			availableEngines[5].engine.ContainerID)
	}

	// Next: 2 streams forwarded
	if availableEngines[6].activeStreams != 2 || !availableEngines[6].engine.Forwarded {
		t.Errorf("Expected seventh engine to be non-empty (2 streams) forwarded, got streams=%d forwarded=%v",
			availableEngines[6].activeStreams, availableEngines[6].engine.Forwarded)
	}
	if availableEngines[6].engine.ContainerID != "nonempty-forwarded-2" {
		t.Errorf("Expected nonempty-forwarded-2 (2 streams) seventh, got %s",
			availableEngines[6].engine.ContainerID)
	}
	
	// Last: 2 streams non-forwarded
	if availableEngines[7].activeStreams != 2 || availableEngines[7].engine.Forwarded {
		t.Errorf("Expected eighth engine to be non-empty (2 streams) non-forwarded, got streams=%d forwarded=%v",
			availableEngines[7].activeStreams, availableEngines[7].engine.Forwarded)
	}
	if availableEngines[7].engine.ContainerID != "nonempty-nonforwarded-2" {
		t.Errorf("Expected nonempty-nonforwarded-2 (2 streams) eighth, got %s",
			availableEngines[7].engine.ContainerID)
	}

	// Log the final order for debugging
	t.Log("Final engine selection order:")
	for i, eng := range availableEngines {
		t.Logf("%d. %s (streams=%d, forwarded=%v)", 
			i+1, eng.engine.ContainerID, eng.activeStreams, eng.engine.Forwarded)
	}
}
