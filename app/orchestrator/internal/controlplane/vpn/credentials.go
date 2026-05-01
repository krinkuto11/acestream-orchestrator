package vpn

import (
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"os"
	"sync"
	"time"
)

// Lease represents an active credential assignment to a VPN container.
type Lease struct {
	ContainerID  string
	CredentialID string
	LeasedAt     time.Time
	Credential   map[string]interface{}
}

// CredentialManager manages a finite VPN credential pool with strict lease semantics.
// Credentials are loaded from a JSON file; leases are tracked in-memory and
// restored from Docker container labels on startup.
type CredentialManager struct {
	mu         sync.Mutex
	byID       map[string]map[string]interface{} // credentialID -> raw credential
	available  []string                          // FIFO queue of available IDs
	leases     map[string]string                 // containerID -> credentialID
	leaseTimes map[string]time.Time
}

func NewCredentialManager() *CredentialManager {
	return &CredentialManager{
		byID:       make(map[string]map[string]interface{}),
		leases:     make(map[string]string),
		leaseTimes: make(map[string]time.Time),
	}
}

// LoadFromFile parses a JSON credential list from a file and calls Configure.
func (cm *CredentialManager) LoadFromFile(path string) error {
	data, err := os.ReadFile(path)
	if err != nil {
		return fmt.Errorf("reading credentials file %s: %w", path, err)
	}
	return cm.LoadJSON(data)
}

// LoadJSON parses a JSON-encoded credential list.
func (cm *CredentialManager) LoadJSON(data []byte) error {
	var raw []map[string]interface{}
	if err := json.Unmarshal(data, &raw); err != nil {
		return fmt.Errorf("parsing credentials JSON: %w", err)
	}
	return cm.Configure(raw)
}

// Configure atomically replaces the credential pool, preserving leases for
// credentials that still exist in the new pool.
func (cm *CredentialManager) Configure(credentials []map[string]interface{}) error {
	cm.mu.Lock()
	defer cm.mu.Unlock()

	newByID := make(map[string]map[string]interface{}, len(credentials))
	for i, raw := range credentials {
		id := buildCredentialID(i, raw)
		cred := make(map[string]interface{}, len(raw)+1)
		for k, v := range raw {
			cred[k] = v
		}
		cred["id"] = id
		newByID[id] = cred
	}

	// Determine which IDs are currently leased to valid credentials.
	activeIDs := make(map[string]bool)
	for _, credID := range cm.leases {
		if _, ok := newByID[credID]; ok {
			activeIDs[credID] = true
		}
	}

	// Evict leases for credentials that no longer exist.
	for containerID, credID := range cm.leases {
		if _, ok := newByID[credID]; !ok {
			delete(cm.leases, containerID)
			delete(cm.leaseTimes, containerID)
		}
	}

	// Rebuild available list preserving insertion order.
	var available []string
	for id := range newByID {
		if !activeIDs[id] {
			available = append(available, id)
		}
	}

	cm.byID = newByID
	cm.available = available
	return nil
}

// AcquireLease atomically reserves one credential for containerID.
// Returns nil (no error) when no credentials are available.
// If containerID already has a lease, the existing lease is returned.
func (cm *CredentialManager) AcquireLease(containerID string) (*Lease, error) {
	cm.mu.Lock()
	defer cm.mu.Unlock()

	// Re-use an existing lease for this container (idempotent).
	if credID, ok := cm.leases[containerID]; ok {
		cred := cm.byID[credID]
		return &Lease{
			ContainerID:  containerID,
			CredentialID: credID,
			LeasedAt:     cm.leaseTimes[containerID],
			Credential:   cred,
		}, nil
	}

	if len(cm.available) == 0 {
		return nil, nil
	}

	credID := cm.available[0]
	cm.available = cm.available[1:]
	cm.leases[containerID] = credID
	now := time.Now().UTC()
	cm.leaseTimes[containerID] = now

	return &Lease{
		ContainerID:  containerID,
		CredentialID: credID,
		LeasedAt:     now,
		Credential:   cm.byID[credID],
	}, nil
}

// ReleaseLease returns the credential associated with containerID back to the pool.
func (cm *CredentialManager) ReleaseLease(containerID string) bool {
	cm.mu.Lock()
	defer cm.mu.Unlock()

	credID, ok := cm.leases[containerID]
	if !ok {
		return false
	}
	delete(cm.leases, containerID)
	delete(cm.leaseTimes, containerID)

	if _, exists := cm.byID[credID]; exists {
		cm.available = append(cm.available, credID)
	}
	return true
}

// RestoreLeases rebuilds lease state from running Docker containers so that
// credentials already bound to live containers are not re-allocated on restart.
// nodes is a slice of maps containing "container_name"/"container_id" and "credential_id".
func (cm *CredentialManager) RestoreLeases(nodes []map[string]interface{}) {
	cm.mu.Lock()
	defer cm.mu.Unlock()

	usedIDs := make(map[string]bool)
	newLeases := make(map[string]string)
	newTimes := make(map[string]time.Time)

	for _, node := range nodes {
		credID, _ := node["credential_id"].(string)
		if credID == "" {
			continue
		}
		if _, ok := cm.byID[credID]; !ok {
			continue
		}
		if usedIDs[credID] {
			continue
		}

		containerKey := ""
		if cn, ok := node["container_name"].(string); ok && cn != "" {
			containerKey = cn
		} else if ci, ok := node["container_id"].(string); ok && ci != "" {
			containerKey = ci
		}
		if containerKey == "" {
			continue
		}

		usedIDs[credID] = true
		newLeases[containerKey] = credID
		newTimes[containerKey] = time.Now().UTC()
	}

	cm.leases = newLeases
	cm.leaseTimes = newTimes

	var available []string
	for id := range cm.byID {
		if !usedIDs[id] {
			available = append(available, id)
		}
	}
	cm.available = available
}

// AvailableCount returns the number of credentials currently available for leasing.
func (cm *CredentialManager) AvailableCount() int {
	cm.mu.Lock()
	defer cm.mu.Unlock()
	return len(cm.available)
}

// TotalCount returns the total number of credentials in the pool.
func (cm *CredentialManager) TotalCount() int {
	cm.mu.Lock()
	defer cm.mu.Unlock()
	return len(cm.byID)
}

// Summary returns a snapshot of pool state for observability.
func (cm *CredentialManager) Summary() map[string]interface{} {
	cm.mu.Lock()
	defer cm.mu.Unlock()
	leases := make([]map[string]interface{}, 0, len(cm.leases))
	for containerID, credID := range cm.leases {
		leases = append(leases, map[string]interface{}{
			"container_id":  containerID,
			"credential_id": credID,
			"leased_at":     cm.leaseTimes[containerID],
		})
	}
	return map[string]interface{}{
		"total":     len(cm.byID),
		"available": len(cm.available),
		"leased":    len(cm.leases),
		"leases":    leases,
	}
}

// buildCredentialID returns a deterministic ID for a credential entry.
// Prefers an explicit "id" field; falls back to a SHA256 prefix.
func buildCredentialID(index int, cred map[string]interface{}) string {
	if id, ok := cred["id"]; ok {
		if s, ok := id.(string); ok && s != "" {
			return s
		}
	}
	data, _ := json.Marshal(cred)
	h := sha256.Sum256(data)
	return fmt.Sprintf("cred-%d-%x", index, h[:8])
}
