package engine

import (
	"fmt"
	"strings"
	"sync"

	"github.com/acestream/acestream/internal/config"
)

// PortAllocator is the exact Go equivalent of the Python PortAllocator.
// All operations are safe for concurrent use.
type PortAllocator struct {
	mu sync.Mutex

	hostMin, hostMax int
	httpMin, httpMax int
	httpsMin, httpsMax int

	hostNext  int
	httpNext  int
	httpsNext int

	usedHost  map[int]struct{}
	usedHTTP  map[int]struct{}
	usedHTTPS map[int]struct{}

	// Gluetun global fallback range (starts at 19000, capped to host range)
	gluetunMin, gluetunMax int
	gluetunNext            int
	usedGluetun            map[int]struct{}

	// Per-VPN port ranges from GLUETUN_PORT_RANGE_1/2 env vars
	vpnRangeSlots       []portRange          // ordered slot list
	vpnRangeAssignments map[string]int        // vpnContainer -> slot index
	vpnPortRanges       map[string]*vpnRange  // vpnContainer -> range state

	// Per-VPN internal P2P ports (starting at 62062)
	vpnP2PUsed map[string]map[int]struct{}
}

type portRange struct{ min, max int }

type vpnRange struct {
	min, max int
	next     int
	used     map[int]struct{}
}

var Alloc = newPortAllocator()

func newPortAllocator() *PortAllocator {
	cfg := config.C.Load()

	a := &PortAllocator{
		hostMin: cfg.PortRangeHost.Min,
		hostMax: cfg.PortRangeHost.Max,
		httpMin: cfg.ACEHTTPRange.Min,
		httpMax: cfg.ACEHTTPRange.Max,
		httpsMin: cfg.ACEHTTPSRange.Min,
		httpsMax: cfg.ACEHTTPSRange.Max,

		usedHost:  make(map[int]struct{}),
		usedHTTP:  make(map[int]struct{}),
		usedHTTPS: make(map[int]struct{}),
		usedGluetun: make(map[int]struct{}),

		vpnRangeSlots:       nil,
		vpnRangeAssignments: make(map[string]int),
		vpnPortRanges:       make(map[string]*vpnRange),
		vpnP2PUsed:          make(map[string]map[int]struct{}),
	}

	a.hostNext = a.hostMin
	a.httpNext = a.httpMin
	a.httpsNext = a.httpsMin

	gluetunBase := 19000
	a.gluetunMin = max(gluetunBase, a.hostMin)
	a.gluetunMax = a.hostMax
	a.gluetunNext = a.gluetunMin

	// Parse optional VPN-specific port ranges
	for _, raw := range []string{cfg.GluetunPortRange1, cfg.GluetunPortRange2} {
		if raw == "" {
			continue
		}
		parts := strings.SplitN(raw, "-", 2)
		if len(parts) != 2 {
			continue
		}
		var lo, hi int
		fmt.Sscanf(parts[0], "%d", &lo)
		fmt.Sscanf(parts[1], "%d", &hi)
		if lo > 0 && hi >= lo {
			a.vpnRangeSlots = append(a.vpnRangeSlots, portRange{lo, hi})
		}
	}

	return a
}



func (a *PortAllocator) nextIn(cur, lo, hi int, used map[int]struct{}) (int, error) {
	p := cur
	for range hi - lo + 1 {
		if p > hi {
			p = lo
		}
		if _, inUse := used[p]; !inUse {
			return p, nil
		}
		p++
	}
	return 0, fmt.Errorf("no free ports in range %d-%d", lo, hi)
}

func (a *PortAllocator) AllocHost() (int, error) {
	a.mu.Lock()
	defer a.mu.Unlock()
	p, err := a.nextIn(a.hostNext, a.hostMin, a.hostMax, a.usedHost)
	if err != nil {
		return 0, err
	}
	a.usedHost[p] = struct{}{}
	a.hostNext = p + 1
	return p, nil
}

func (a *PortAllocator) AllocHTTP() (int, error) {
	a.mu.Lock()
	defer a.mu.Unlock()
	p, err := a.nextIn(a.httpNext, a.httpMin, a.httpMax, a.usedHTTP)
	if err != nil {
		return 0, err
	}
	a.usedHTTP[p] = struct{}{}
	a.httpNext = p + 1
	return p, nil
}

func (a *PortAllocator) AllocHTTPS(avoid int) (int, error) {
	a.mu.Lock()
	defer a.mu.Unlock()
	for {
		p, err := a.nextIn(a.httpsNext, a.httpsMin, a.httpsMax, a.usedHTTPS)
		if err != nil {
			return 0, err
		}
		if avoid == 0 || p != avoid {
			a.usedHTTPS[p] = struct{}{}
			a.httpsNext = p + 1
			return p, nil
		}
		a.httpsNext = p + 1
	}
}

func (a *PortAllocator) ReserveHost(p int) {
	a.mu.Lock()
	defer a.mu.Unlock()
	a.usedHost[p] = struct{}{}
}

func (a *PortAllocator) ReserveHTTP(p int) {
	a.mu.Lock()
	defer a.mu.Unlock()
	a.usedHTTP[p] = struct{}{}
}

func (a *PortAllocator) ReserveHTTPS(p int) {
	a.mu.Lock()
	defer a.mu.Unlock()
	a.usedHTTPS[p] = struct{}{}
}

func (a *PortAllocator) FreeHost(p int) {
	if p == 0 {
		return
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	delete(a.usedHost, p)
}

func (a *PortAllocator) FreeHTTP(p int) {
	if p == 0 {
		return
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	delete(a.usedHTTP, p)
}

func (a *PortAllocator) FreeHTTPS(p int) {
	if p == 0 {
		return
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	delete(a.usedHTTPS, p)
}

// ensureVPNRangeAssigned assigns a port-range slot to a VPN on first use.
// Must be called with a.mu held.
func (a *PortAllocator) ensureVPNRangeAssigned(vpn string) {
	if vpn == "" || len(a.vpnRangeSlots) == 0 {
		return
	}
	if _, ok := a.vpnPortRanges[vpn]; ok {
		return
	}
	// Find first unassigned slot
	used := make(map[int]struct{}, len(a.vpnRangeAssignments))
	for _, idx := range a.vpnRangeAssignments {
		used[idx] = struct{}{}
	}
	for idx, slot := range a.vpnRangeSlots {
		if _, taken := used[idx]; !taken {
			a.vpnRangeAssignments[vpn] = idx
			a.vpnPortRanges[vpn] = &vpnRange{
				min:  slot.min,
				max:  slot.max,
				next: slot.min,
				used: make(map[int]struct{}),
			}
			return
		}
	}
}

func (a *PortAllocator) AllocGluetunPort(vpn string) (int, error) {
	a.mu.Lock()
	defer a.mu.Unlock()

	a.ensureVPNRangeAssigned(vpn)

	if vr, ok := a.vpnPortRanges[vpn]; ok {
		p, err := a.nextIn(vr.next, vr.min, vr.max, vr.used)
		if err != nil {
			return 0, err
		}
		vr.used[p] = struct{}{}
		vr.next = p + 1
		return p, nil
	}

	// Global fallback
	p, err := a.nextIn(a.gluetunNext, a.gluetunMin, a.gluetunMax, a.usedGluetun)
	if err != nil {
		return 0, err
	}
	a.usedGluetun[p] = struct{}{}
	a.gluetunNext = p + 1
	return p, nil
}

func (a *PortAllocator) ReserveGluetunPort(p int, vpn string) {
	a.mu.Lock()
	defer a.mu.Unlock()
	a.ensureVPNRangeAssigned(vpn)
	if vr, ok := a.vpnPortRanges[vpn]; ok {
		vr.used[p] = struct{}{}
	} else {
		a.usedGluetun[p] = struct{}{}
	}
}

func (a *PortAllocator) FreeGluetunPort(p int, vpn string) {
	if p == 0 {
		return
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	if vr, ok := a.vpnPortRanges[vpn]; ok {
		delete(vr.used, p)
	} else {
		delete(a.usedGluetun, p)
	}
}

// PortsInfo is the result of AllocateEnginePorts.
type PortsInfo struct {
	ContainerHTTPPort  int
	ContainerHTTPSPort int
	ContainerAPIPort   int
	HostHTTPPort       int
	HostAPIPort        int
	HostHTTPSPort      int // 0 if not mapped
}

// AllocateEnginePorts atomically reserves all ports for a new engine.
// Mirrors Python's PortAllocator.allocate_engine_ports exactly.
func (a *PortAllocator) AllocateEnginePorts(
	useGluetun bool,
	vpnContainer string,
	requestedHostPort int,
	userHTTPPort, userHTTPSPort, userAPIPort int,
	mapHTTPS bool,
) (PortsInfo, error) {
	a.mu.Lock()
	defer a.mu.Unlock()

	type reservation struct {
		kind string
		port int
		vpn  string
	}
	var reservations []reservation

	rollback := func() {
		for i := len(reservations) - 1; i >= 0; i-- {
			r := reservations[i]
			switch r.kind {
			case "host":
				delete(a.usedHost, r.port)
			case "http":
				delete(a.usedHTTP, r.port)
			case "https":
				delete(a.usedHTTPS, r.port)
			case "gluetun":
				if vr, ok := a.vpnPortRanges[r.vpn]; ok {
					delete(vr.used, r.port)
				} else {
					delete(a.usedGluetun, r.port)
				}
			}
		}
	}

	track := func(kind string, port int, vpn string) {
		reservations = append(reservations, reservation{kind, port, vpn})
	}

	a.ensureVPNRangeAssigned(vpnContainer)

	allocGluetun := func() (int, error) {
		if vr, ok := a.vpnPortRanges[vpnContainer]; ok {
			p, err := a.nextIn(vr.next, vr.min, vr.max, vr.used)
			if err != nil {
				return 0, err
			}
			vr.used[p] = struct{}{}
			vr.next = p + 1
			return p, nil
		}
		p, err := a.nextIn(a.gluetunNext, a.gluetunMin, a.gluetunMax, a.usedGluetun)
		if err != nil {
			return 0, err
		}
		a.usedGluetun[p] = struct{}{}
		a.gluetunNext = p + 1
		return p, nil
	}

	var info PortsInfo
	var err error

	// HTTP port
	if userHTTPPort != 0 {
		info.ContainerHTTPPort = userHTTPPort
		info.HostHTTPPort = requestedHostPort
		if info.HostHTTPPort == 0 {
			info.HostHTTPPort = userHTTPPort
		}
		if useGluetun {
			if vr, ok := a.vpnPortRanges[vpnContainer]; ok {
				vr.used[info.ContainerHTTPPort] = struct{}{}
			} else {
				a.usedGluetun[info.ContainerHTTPPort] = struct{}{}
			}
			track("gluetun", info.ContainerHTTPPort, vpnContainer)
		} else {
			a.usedHTTP[info.ContainerHTTPPort] = struct{}{}
			track("http", info.ContainerHTTPPort, "")
		}
	} else {
		if useGluetun {
			info.HostHTTPPort, err = allocGluetun()
			if err != nil {
				rollback()
				return PortsInfo{}, err
			}
			track("gluetun", info.HostHTTPPort, vpnContainer)
			info.ContainerHTTPPort = info.HostHTTPPort
		} else {
			if requestedHostPort != 0 {
				info.HostHTTPPort = requestedHostPort
			} else {
				info.HostHTTPPort, err = a.nextIn(a.hostNext, a.hostMin, a.hostMax, a.usedHost)
				if err != nil {
					rollback()
					return PortsInfo{}, err
				}
				a.usedHost[info.HostHTTPPort] = struct{}{}
				a.hostNext = info.HostHTTPPort + 1
				track("host", info.HostHTTPPort, "")
			}
			info.ContainerHTTPPort = info.HostHTTPPort
			a.usedHTTP[info.ContainerHTTPPort] = struct{}{}
			track("http", info.ContainerHTTPPort, "")
		}
	}

	// HTTPS port
	if userHTTPSPort != 0 {
		info.ContainerHTTPSPort = userHTTPSPort
		a.usedHTTPS[info.ContainerHTTPSPort] = struct{}{}
		track("https", info.ContainerHTTPSPort, "")
	} else {
		for {
			p, err2 := a.nextIn(a.httpsNext, a.httpsMin, a.httpsMax, a.usedHTTPS)
			if err2 != nil {
				rollback()
				return PortsInfo{}, err2
			}
			if p != info.ContainerHTTPPort {
				info.ContainerHTTPSPort = p
				a.usedHTTPS[p] = struct{}{}
				a.httpsNext = p + 1
				track("https", p, "")
				break
			}
			a.httpsNext = p + 1
		}
	}

	// API port
	if userAPIPort != 0 {
		info.ContainerAPIPort = userAPIPort
		info.HostAPIPort = userAPIPort
		if useGluetun {
			if vr, ok := a.vpnPortRanges[vpnContainer]; ok {
				vr.used[info.HostAPIPort] = struct{}{}
			} else {
				a.usedGluetun[info.HostAPIPort] = struct{}{}
			}
			track("gluetun", info.HostAPIPort, vpnContainer)
		} else {
			a.usedHost[info.HostAPIPort] = struct{}{}
			track("host", info.HostAPIPort, "")
		}
	} else {
		if useGluetun {
			info.HostAPIPort, err = allocGluetun()
			if err != nil {
				rollback()
				return PortsInfo{}, err
			}
			track("gluetun", info.HostAPIPort, vpnContainer)
		} else {
			info.HostAPIPort, err = a.nextIn(a.hostNext, a.hostMin, a.hostMax, a.usedHost)
			if err != nil {
				rollback()
				return PortsInfo{}, err
			}
			a.usedHost[info.HostAPIPort] = struct{}{}
			a.hostNext = info.HostAPIPort + 1
			track("host", info.HostAPIPort, "")
		}
		info.ContainerAPIPort = info.HostAPIPort
	}

	// Optional host-side HTTPS mapping
	if !useGluetun && mapHTTPS {
		info.HostHTTPSPort, err = a.nextIn(a.hostNext, a.hostMin, a.hostMax, a.usedHost)
		if err != nil {
			rollback()
			return PortsInfo{}, err
		}
		a.usedHost[info.HostHTTPSPort] = struct{}{}
		a.hostNext = info.HostHTTPSPort + 1
		track("host", info.HostHTTPSPort, "")
	}

	return info, nil
}

// ReserveFromLabels marks ports stored in container labels as in-use.
// Called on engine start events so allocator state stays consistent with running containers.
func (a *PortAllocator) ReserveFromLabels(labels map[string]string) {
	vpn := labels["acestream.vpn_container"]
	hp := parseInt(labels["host.http_port"])
	ap := parseInt(labels["host.api_port"])
	hsp := parseInt(labels["host.https_port"])
	cp := parseInt(labels["acestream.http_port"])
	sp := parseInt(labels["acestream.https_port"])

	a.mu.Lock()
	defer a.mu.Unlock()
	if hp != 0 {
		a.usedHost[hp] = struct{}{}
	}
	if cp != 0 {
		a.usedHTTP[cp] = struct{}{}
	}
	if sp != 0 {
		a.usedHTTPS[sp] = struct{}{}
	}
	if hsp != 0 {
		a.usedHost[hsp] = struct{}{}
	}
	if vpn != "" {
		// Reserve in per-VPN slot range when available, otherwise global gluetun pool.
		// Mirrors AllocateEnginePorts / ReserveGluetunPort logic (inline to avoid re-locking).
		a.ensureVPNRangeAssigned(vpn)
		reserveVPN := func(p int) {
			if p == 0 {
				return
			}
			if vr, ok := a.vpnPortRanges[vpn]; ok {
				vr.used[p] = struct{}{}
			} else {
				a.usedGluetun[p] = struct{}{}
			}
		}
		reserveVPN(hp)
		if ap != hp {
			reserveVPN(ap)
		}
	} else if ap != 0 && ap != hp {
		a.usedHost[ap] = struct{}{}
	}
}

// ReleaseFromLabels frees all ports stored in container labels (called on container die).
func (a *PortAllocator) ReleaseFromLabels(labels map[string]string) {
	vpn := labels["acestream.vpn_container"]
	hp := parseInt(labels["host.http_port"])
	ap := parseInt(labels["host.api_port"])
	hsp := parseInt(labels["host.https_port"])
	cp := parseInt(labels["acestream.http_port"])
	sp := parseInt(labels["acestream.https_port"])

	a.FreeHost(hp)
	a.FreeHTTP(cp)
	a.FreeHTTPS(sp)
	if hsp != 0 {
		a.FreeHost(hsp)
	}
	if vpn != "" {
		a.FreeGluetunPort(hp, vpn)
		if ap != 0 && ap != hp {
			a.FreeGluetunPort(ap, vpn)
		}
	} else {
		a.FreeHost(ap)
	}
}

// AllocInternalP2PPort allocates a unique internal P2P port per VPN container.
// Mirrors Python's alloc_internal_p2p_port.
func (a *PortAllocator) AllocInternalP2PPort(vpn string) int {
	a.mu.Lock()
	defer a.mu.Unlock()
	used := a.vpnP2PUsed[vpn]
	if used == nil {
		used = make(map[int]struct{})
		a.vpnP2PUsed[vpn] = used
	}
	p := 62062
	for {
		if _, taken := used[p]; !taken {
			break
		}
		p++
	}
	used[p] = struct{}{}
	return p
}

func (a *PortAllocator) FreeInternalP2PPort(port int, vpn string) {
	if port == 0 || vpn == "" {
		return
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	if used := a.vpnP2PUsed[vpn]; used != nil {
		delete(used, port)
	}
}

func (a *PortAllocator) ClearAll() {
	a.mu.Lock()
	defer a.mu.Unlock()
	a.usedHost = make(map[int]struct{})
	a.usedHTTP = make(map[int]struct{})
	a.usedHTTPS = make(map[int]struct{})
	a.usedGluetun = make(map[int]struct{})
	a.gluetunNext = a.gluetunMin
	a.vpnPortRanges = make(map[string]*vpnRange)
	a.vpnRangeAssignments = make(map[string]int)
	a.vpnP2PUsed = make(map[string]map[int]struct{})
}

func parseInt(s string) int {
	var n int
	fmt.Sscanf(s, "%d", &n)
	return n
}
