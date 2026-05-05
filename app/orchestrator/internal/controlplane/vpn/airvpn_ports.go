package vpn

import (
	"strconv"
	"strings"
	"sync"
)

// AirVPNPortPool manages pre-allocated FIREWALL_VPN_INPUT_PORTS across all
// AirVPN credentials. Ports are gathered from every credential that carries a
// "firewall_vpn_input_ports" field; the union is deduplicated so duplicate
// entries across multiple credentials collapse into a single port slot.
//
// Each port may be claimed by at most one running gluetun container. When
// the container is destroyed the port is returned to the free pool.
type AirVPNPortPool struct {
	mu      sync.Mutex
	all     []int          // deduped ordered union of ports from all credentials
	claimed map[int]string // port -> containerName
	byCont  map[string]int // containerName -> port
}

func newAirVPNPortPool() *AirVPNPortPool {
	return &AirVPNPortPool{
		claimed: make(map[int]string),
		byCont:  make(map[string]int),
	}
}

// rebuild replaces the port universe from the current credential list.
// Claims for ports that no longer appear in any credential are evicted;
// all other claims are preserved so live containers keep their ports.
func (p *AirVPNPortPool) rebuild(credentials []map[string]interface{}) {
	p.mu.Lock()
	defer p.mu.Unlock()

	seen := make(map[int]bool)
	var all []int
	for _, cred := range credentials {
		if strings.ToLower(strings.TrimSpace(strVal(cred["provider"]))) != "airvpn" {
			continue
		}
		for _, port := range parseFirewallPorts(cred["firewall_vpn_input_ports"]) {
			if !seen[port] {
				seen[port] = true
				all = append(all, port)
			}
		}
	}
	p.all = all

	// Evict claims for ports removed from the universe.
	for port, container := range p.claimed {
		if !seen[port] {
			delete(p.claimed, port)
			delete(p.byCont, container)
		}
	}
}

// acquirePort claims and returns a free port for containerName.
// Returns 0 if the pool is exhausted. Calling again with the same
// containerName is idempotent — it returns the already-claimed port.
func (p *AirVPNPortPool) acquirePort(containerName string) int {
	p.mu.Lock()
	defer p.mu.Unlock()

	if port, ok := p.byCont[containerName]; ok {
		return port
	}
	for _, port := range p.all {
		if _, claimed := p.claimed[port]; !claimed {
			p.claimed[port] = containerName
			p.byCont[containerName] = port
			return port
		}
	}
	return 0
}

// releasePort frees the port held by containerName.
func (p *AirVPNPortPool) releasePort(containerName string) {
	p.mu.Lock()
	defer p.mu.Unlock()

	if port, ok := p.byCont[containerName]; ok {
		delete(p.claimed, port)
		delete(p.byCont, containerName)
	}
}

// summary returns pool statistics for observability.
func (p *AirVPNPortPool) summary() map[string]interface{} {
	p.mu.Lock()
	defer p.mu.Unlock()

	free := 0
	for _, port := range p.all {
		if _, ok := p.claimed[port]; !ok {
			free++
		}
	}
	claims := make(map[string]int, len(p.byCont))
	for container, port := range p.byCont {
		claims[container] = port
	}
	return map[string]interface{}{
		"total":   len(p.all),
		"claimed": len(p.claimed),
		"free":    free,
		"claims":  claims,
	}
}

// ── Port parsing helpers ──────────────────────────────────────────────────────

func parseFirewallPorts(v interface{}) []int {
	var ports []int
	switch val := v.(type) {
	case nil:
		return nil
	case []interface{}:
		for _, item := range val {
			if n := parsePortInt(strVal(item)); n > 0 {
				ports = append(ports, n)
			}
		}
	case string:
		for _, part := range strings.Split(val, ",") {
			if n := parsePortInt(part); n > 0 {
				ports = append(ports, n)
			}
		}
	case float64:
		if n := int(val); n > 0 && n <= 65535 {
			ports = append(ports, n)
		}
	}
	return ports
}

func parsePortInt(s string) int {
	s = strings.TrimSpace(s)
	if s == "" {
		return 0
	}
	n, err := strconv.Atoi(s)
	if err != nil || n <= 0 || n > 65535 {
		return 0
	}
	return n
}
