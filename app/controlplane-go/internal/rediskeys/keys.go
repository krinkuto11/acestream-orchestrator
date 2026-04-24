package rediskeys

import "fmt"

// Control plane engine state keys (written by Go, read by Python bridge)
func CPEngineKey(containerID string) string {
	return fmt.Sprintf("cp:engine:%s", containerID)
}

const CPEnginesIndex = "cp:engines:all"
const CPVPNNodesIndex = "cp:vpn_nodes:all"

func CPVPNNodeKey(name string) string {
	return fmt.Sprintf("cp:vpn_node:%s", name)
}

// Published by Go on any state change (Python subscribes for bridge updates)
const CPStateChanged = "cp:state_changed"

// Stream counts written by Python for use by Go autoscaler
const CPStreamCounts = "cp:stream_counts"

// Monitor session counts written by Python for use by Go autoscaler
const CPMonitorCounts = "cp:monitor_counts"

// Desired replica count negotiated between Go and Python
const CPDesiredReplicas = "cp:desired_replicas"

// Forwarded engine pending election flags (per-VPN and global)
func CPForwardedPending(vpnContainer string) string {
	if vpnContainer == "" {
		return "cp:forwarded_pending:global"
	}
	return fmt.Sprintf("cp:forwarded_pending:%s", vpnContainer)
}

// Target engine config hash (for rolling updates)
const CPTargetConfigHash = "cp:target_config_hash"
const CPTargetConfigGeneration = "cp:target_config_generation"

// Data plane proxy keys (written by data plane, read-only for control plane)
func ProxyStreamOwner(contentID string) string {
	return fmt.Sprintf("ace_proxy:stream:%s:owner", contentID)
}
