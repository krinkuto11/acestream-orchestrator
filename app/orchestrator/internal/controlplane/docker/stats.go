package docker

import (
	"context"
	"encoding/json"
	"io"
	"sync"

	"github.com/docker/docker/api/types/container"

	"github.com/acestream/acestream/internal/controlplane/engine"
)

type ContainerStats struct {
	ContainerID     string  `json:"container_id"`
	CPUPercent      float64 `json:"cpu_percent"`
	MemoryUsage     int64   `json:"memory_usage"`
	MemoryLimit     int64   `json:"memory_limit"`
	MemoryPercent   float64 `json:"memory_percent"`
	NetworkRxBytes  int64   `json:"network_rx_bytes"`
	NetworkTxBytes  int64   `json:"network_tx_bytes"`
	BlockReadBytes  int64   `json:"block_read_bytes"`
	BlockWriteBytes int64   `json:"block_write_bytes"`
}

// GetContainerStats fetches a one-shot stats snapshot for a single container.
func GetContainerStats(ctx context.Context, containerID string) (*ContainerStats, error) {
	cli, err := engine.NewDockerClientExported()
	if err != nil {
		return nil, err
	}
	defer cli.Close()

	resp, err := cli.ContainerStats(ctx, containerID, false)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}

	var raw container.StatsResponse
	if err := json.Unmarshal(body, &raw); err != nil {
		return nil, err
	}

	return parseStats(containerID, &raw), nil
}

// GetAllContainerStats fetches stats for a list of container IDs in parallel.
func GetAllContainerStats(ctx context.Context, containerIDs []string) map[string]*ContainerStats {
	result := make(map[string]*ContainerStats, len(containerIDs))
	var mu sync.Mutex
	var wg sync.WaitGroup

	for _, id := range containerIDs {
		wg.Add(1)
		go func(cid string) {
			defer wg.Done()
			s, err := GetContainerStats(ctx, cid)
			if err != nil {
				return
			}
			mu.Lock()
			result[cid] = s
			mu.Unlock()
		}(id)
	}
	wg.Wait()
	return result
}

func parseStats(id string, raw *container.StatsResponse) *ContainerStats {
	s := &ContainerStats{ContainerID: id}

	// CPU
	cpuDelta := float64(raw.CPUStats.CPUUsage.TotalUsage) - float64(raw.PreCPUStats.CPUUsage.TotalUsage)
	sysDelta := float64(raw.CPUStats.SystemUsage) - float64(raw.PreCPUStats.SystemUsage)
	numCPU := float64(raw.CPUStats.OnlineCPUs)
	if numCPU == 0 {
		numCPU = float64(len(raw.CPUStats.CPUUsage.PercpuUsage))
	}
	if sysDelta > 0 && cpuDelta > 0 {
		s.CPUPercent = (cpuDelta / sysDelta) * numCPU * 100.0
	}

	// Memory
	s.MemoryUsage = int64(raw.MemoryStats.Usage)
	s.MemoryLimit = int64(raw.MemoryStats.Limit)
	if s.MemoryLimit > 0 {
		s.MemoryPercent = float64(s.MemoryUsage) / float64(s.MemoryLimit) * 100.0
	}

	// Network
	for _, n := range raw.Networks {
		s.NetworkRxBytes += int64(n.RxBytes)
		s.NetworkTxBytes += int64(n.TxBytes)
	}

	// Block I/O
	for _, bio := range raw.BlkioStats.IoServiceBytesRecursive {
		switch bio.Op {
		case "Read":
			s.BlockReadBytes += int64(bio.Value)
		case "Write":
			s.BlockWriteBytes += int64(bio.Value)
		}
	}

	return s
}
