package rediskeys

import "fmt"

// ─── Control-plane keys ───────────────────────────────────────────────────────

func CPEngineKey(containerID string) string {
	return fmt.Sprintf("cp:engine:%s", containerID)
}

const CPEnginesIndex = "cp:engines:all"
const CPVPNNodesIndex = "cp:vpn_nodes:all"

func CPVPNNodeKey(name string) string {
	return fmt.Sprintf("cp:vpn_node:%s", name)
}

const CPStateChanged = "cp:state_changed"
const CPStreamCounts = "cp:stream_counts"
const CPMonitorCounts = "cp:monitor_counts"
const CPDesiredReplicas = "cp:desired_replicas"

func CPForwardedPending(vpnContainer string) string {
	if vpnContainer == "" {
		return "cp:forwarded_pending:global"
	}
	return fmt.Sprintf("cp:forwarded_pending:%s", vpnContainer)
}

const CPTargetConfigHash = "cp:target_config_hash"
const CPTargetConfigGeneration = "cp:target_config_generation"

// ─── Proxy-plane keys ─────────────────────────────────────────────────────────

func StreamMetadata(contentID string) string {
	return fmt.Sprintf("ace_proxy:stream:%s:metadata", contentID)
}

func BufferIndex(contentID string) string {
	return fmt.Sprintf("ace_proxy:stream:%s:buffer:index", contentID)
}

func BufferChunk(contentID string, chunkIndex int64) string {
	return fmt.Sprintf("ace_proxy:stream:%s:buffer:chunk:%d", contentID, chunkIndex)
}

func BufferChunkPrefix(contentID string) string {
	return fmt.Sprintf("ace_proxy:stream:%s:buffer:chunk:", contentID)
}

func StreamStopping(contentID string) string {
	return fmt.Sprintf("ace_proxy:stream:%s:stopping", contentID)
}

func ClientStop(contentID, clientID string) string {
	return fmt.Sprintf("ace_proxy:stream:%s:client:%s:stop", contentID, clientID)
}

func EventsChannel(contentID string) string {
	return fmt.Sprintf("ace_proxy:events:%s", contentID)
}

func StreamOwner(contentID string) string {
	return fmt.Sprintf("ace_proxy:stream:%s:owner", contentID)
}

func Clients(contentID string) string {
	return fmt.Sprintf("ace_proxy:stream:%s:clients", contentID)
}

func LastClientDisconnect(contentID string) string {
	return fmt.Sprintf("ace_proxy:stream:%s:last_client_disconnect_time", contentID)
}

func ConnectionAttempt(contentID string) string {
	return fmt.Sprintf("ace_proxy:stream:%s:connection_attempt_time", contentID)
}

func LastData(contentID string) string {
	return fmt.Sprintf("ace_proxy:stream:%s:last_data", contentID)
}

func WorkerHeartbeat(workerID string) string {
	return fmt.Sprintf("ace_proxy:worker:%s:heartbeat", workerID)
}

func StreamInitTime(contentID string) string {
	return fmt.Sprintf("ace_proxy:stream:%s:init_time", contentID)
}

func ClientMetadata(contentID, clientID string) string {
	return fmt.Sprintf("ace_proxy:stream:%s:clients:%s", contentID, clientID)
}

func WorkerActivity(contentID, workerID string) string {
	return fmt.Sprintf("ace_proxy:stream:%s:worker:%s", contentID, workerID)
}

func StreamActivity(contentID string) string {
	return fmt.Sprintf("ace_proxy:stream:%s:activity", contentID)
}
