package vpn

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net"
	"strings"
	"time"

	dockerclient "github.com/docker/docker/client"
	"github.com/docker/docker/api/types/container"
	"github.com/docker/docker/api/types/filters"
	dockerimage "github.com/docker/docker/api/types/image"
	"github.com/docker/docker/api/types/mount"
	"github.com/docker/docker/api/types/network"

	"github.com/acestream/acestream/internal/config"
	"github.com/acestream/acestream/internal/state"
)

// regionDefaultKey maps providers that use SERVER_REGIONS instead of SERVER_COUNTRIES.
var regionDefaultKey = map[string]string{
	"private internet access": "SERVER_REGIONS",
	"giganews":                "SERVER_REGIONS",
	"windscribe":              "SERVER_REGIONS",
	"vyprvpn":                 "SERVER_REGIONS",
}

// ProvisionResult is returned after a successful VPN container start.
type ProvisionResult struct {
	ContainerID             string
	ContainerName           string
	Provider                string
	Protocol                string
	CredentialID            string
	AssignedHostname        string
	PortForwardingSupported bool
	ControlServerURL        string
}

// Provisioner creates and destroys Gluetun VPN containers.
type Provisioner struct {
	creds *CredentialManager
	rep   *ReputationManager
}

func NewProvisioner(creds *CredentialManager, rep *ReputationManager) *Provisioner {
	return &Provisioner{creds: creds, rep: rep}
}

// ProvisionNode leases a credential, builds the Gluetun env, and starts a
// new VPN container. On any failure after leasing, the lease is released.
func (p *Provisioner) ProvisionNode(ctx context.Context) (*ProvisionResult, error) {
	cfg := config.C.Load()
	containerName := generateContainerName("gluetun-dyn")

	lease, err := p.creds.AcquireLease(containerName)
	if err != nil {
		return nil, fmt.Errorf("credential acquire: %w", err)
	}
	if lease == nil {
		return nil, fmt.Errorf("no available VPN credentials")
	}

	cred := lease.Credential

	provider := resolveProvider("", map[string]interface{}{}, cred, cfg.VPNProvider)
	protocol, err := resolveProtocol(map[string]interface{}{}, cred, cfg.VPNProtocol)
	if err != nil {
		p.creds.ReleaseLease(containerName)
		return nil, err
	}
	regions := resolveRegions(nil, map[string]interface{}{}, cred, cfg.VPNRegions)

	providerSupportsPF := ProviderSupportsForwarding(provider)
	credSupportsPF := credentialSupportsPF(cred)
	pfSupported := providerSupportsPF && credSupportsPF

	env, err := buildGluetunEnv(provider, protocol, regions, cred, map[string]interface{}{}, pfSupported, true)
	if err != nil {
		p.creds.ReleaseLease(containerName)
		return nil, fmt.Errorf("building env: %w", err)
	}

	// Choose a safe hostname from the catalog unless the credential already pins one.
	hasExplicitPin := env["SERVER_HOSTNAMES"] != "" || env["WIREGUARD_ENDPOINTS"] != ""
	requirePF := env["VPN_PORT_FORWARDING"] == "on"
	catalogFile := effectiveCatalogFile(map[string]interface{}{})

	if !hasExplicitPin {
		hn := p.rep.GetSafeHostname(ctx, provider, regions, protocol, requirePF, catalogFile)
		if hn != "" {
			env["SERVER_HOSTNAMES"] = hn
		}
	}

	// Apply port-forwarding filter guard — may drop incompatible server pins.
	applyPFFilterGuard(env, provider, protocol, catalogFile, p.rep)

	assignedHostname := ""
	if hn, ok := env["SERVER_HOSTNAMES"]; ok {
		if idx := strings.IndexByte(hn, ','); idx >= 0 {
			assignedHostname = strings.ToLower(strings.TrimSpace(hn[:idx]))
		} else {
			assignedHostname = strings.ToLower(strings.TrimSpace(hn))
		}
	}

	labels := buildLabels(provider, protocol, lease.CredentialID, pfSupported)
	image := cfg.VPNImage
	if image == "" {
		image = "qmcgaw/gluetun"
	}

	containerID, controlHost, err := p.startContainer(ctx, image, containerName, env, labels)
	if err != nil {
		p.creds.ReleaseLease(containerName)
		return nil, fmt.Errorf("starting container: %w", err)
	}

	// Register in global state.
	now := time.Now().UTC()
	state.Global.UpsertVPNNode(&state.VPNNode{
		ContainerName:           containerName,
		ContainerID:             containerID,
		Status:                  "running",
		Provider:                provider,
		Protocol:                protocol,
		CredentialID:            lease.CredentialID,
		ManagedDynamic:          true,
		AssignedHostname:        assignedHostname,
		PortForwardingSupported: pfSupported,
		ControlHost:             controlHost,
		FirstSeen:               now,
		LastSeen:                now,
	})

	slog.Info("Dynamic VPN node provisioned",
		"name", containerName,
		"provider", provider,
		"protocol", protocol,
		"hostname", assignedHostname,
	)

	return &ProvisionResult{
		ContainerID:             containerID,
		ContainerName:           containerName,
		Provider:                provider,
		Protocol:                protocol,
		CredentialID:            lease.CredentialID,
		AssignedHostname:        assignedHostname,
		PortForwardingSupported: pfSupported,
		ControlServerURL:        fmt.Sprintf("http://%s:%d", containerName, cfg.GluetunAPIPort),
	}, nil
}

// DestroyNode stops the VPN container, releases the credential lease, and
// removes the node from global state.
func (p *Provisioner) DestroyNode(ctx context.Context, containerName string) error {
	cli, err := dockerclient.NewClientWithOpts(
		dockerclient.FromEnv,
		dockerclient.WithAPIVersionNegotiation(),
	)
	if err != nil {
		return err
	}
	defer cli.Close()

	containers, err := cli.ContainerList(ctx, container.ListOptions{
		All:     true,
		Filters: filters.NewArgs(filters.Arg("name", containerName)),
	})
	if err != nil {
		slog.Warn("VPN destroy: container list failed", "name", containerName, "err", err)
	}

	for _, c := range containers {
		if err := cli.ContainerRemove(ctx, c.ID, container.RemoveOptions{Force: true}); err != nil {
			slog.Warn("VPN destroy: remove failed", "id", c.ID[:12], "err", err)
		}
	}

	p.creds.ReleaseLease(containerName)
	state.Global.RemoveVPNNode(containerName)
	slog.Info("Dynamic VPN node destroyed", "name", containerName)
	return nil
}

// ListManagedNodes returns running dynamic VPN containers from Docker.
func (p *Provisioner) ListManagedNodes(ctx context.Context, includeStopped bool) ([]map[string]interface{}, error) {
	cli, err := dockerclient.NewClientWithOpts(
		dockerclient.FromEnv,
		dockerclient.WithAPIVersionNegotiation(),
	)
	if err != nil {
		return nil, err
	}
	defer cli.Close()

	f := filters.NewArgs(
		filters.Arg("label", "acestream-orchestrator.managed=true"),
		filters.Arg("label", "role=vpn_node"),
	)
	containers, err := cli.ContainerList(ctx, container.ListOptions{
		All:     includeStopped,
		Filters: f,
	})
	if err != nil {
		return nil, err
	}

	var nodes []map[string]interface{}
	for _, c := range containers {
		labels := c.Labels
		name := ""
		for _, n := range c.Names {
			name = strings.TrimPrefix(n, "/")
			break
		}
		nodes = append(nodes, map[string]interface{}{
			"container_id":             c.ID,
			"container_name":           name,
			"status":                   c.State,
			"provider":                 labels["acestream.vpn.provider"],
			"protocol":                 labels["acestream.vpn.protocol"],
			"credential_id":            labels["acestream.vpn.credential_id"],
			"port_forwarding_supported": labels["acestream.vpn.port_forwarding_supported"] == "true",
		})
	}
	return nodes, nil
}

// startContainer creates and starts a Gluetun container. It returns the
// container ID and the resolved internal IP address (for cross-network API
// reachability). If the IP cannot be determined, an empty string is returned
// and the caller should fall back to the container name.
func (p *Provisioner) startContainer(
	ctx context.Context,
	image, name string,
	envMap map[string]string,
	labels map[string]string,
) (containerID, controlHost string, err error) {
	cli, err := dockerclient.NewClientWithOpts(
		dockerclient.FromEnv,
		dockerclient.WithAPIVersionNegotiation(),
	)
	if err != nil {
		return "", "", err
	}
	defer cli.Close()

	envList := make([]string, 0, len(envMap))
	for k, v := range envMap {
		envList = append(envList, k+"="+v)
	}

	hostCfg := &container.HostConfig{
		CapAdd: []string{"NET_ADMIN"},
		Resources: container.Resources{
			Devices: []container.DeviceMapping{
				{PathOnHost: "/dev/net/tun", PathInContainer: "/dev/net/tun", CgroupPermissions: "rwm"},
			},
		},
		Mounts: []mount.Mount{
			{Type: mount.TypeVolume, Source: GluetunVolumeName, Target: "/gluetun"},
		},
		RestartPolicy: container.RestartPolicy{Name: "unless-stopped"},
	}

	netCfg := &network.NetworkingConfig{}
	dockerNet := config.C.Load().DockerNetwork
	if dockerNet != "" {
		hostCfg.NetworkMode = container.NetworkMode(dockerNet)
		if dockerNet != "host" && dockerNet != "none" {
			netCfg.EndpointsConfig = map[string]*network.EndpointSettings{
				dockerNet: {},
			}
		}
	}

	if err := ensureImage(ctx, cli, image); err != nil {
		return "", "", fmt.Errorf("image pull %s: %w", image, err)
	}

	containerCfg := &container.Config{
		Image:  image,
		Env:    envList,
		Labels: labels,
	}

	resp, err := cli.ContainerCreate(ctx, containerCfg, hostCfg, netCfg, nil, name)
	if err != nil {
		return "", "", err
	}

	if err := cli.ContainerStart(ctx, resp.ID, container.StartOptions{}); err != nil {
		_ = cli.ContainerRemove(ctx, resp.ID, container.RemoveOptions{Force: true})
		return "", "", err
	}

	// Inspect to resolve the container's internal IP for cross-network API access.
	if info, inspErr := cli.ContainerInspect(ctx, resp.ID); inspErr == nil {
		if ns := info.NetworkSettings; ns != nil {
			for _, ep := range ns.Networks {
				if ep != nil && ep.IPAddress != "" {
					controlHost = ep.IPAddress
					break
				}
			}
		}
	}

	return resp.ID, controlHost, nil
}

// ── Env building ──────────────────────────────────────────────────────────────

func buildGluetunEnv(
	provider, protocol string,
	regions []string,
	cred, settings map[string]interface{},
	pfSupported bool,
	ignoreEndpoint bool,
) (map[string]string, error) {
	cfg := config.C.Load()
	env := map[string]string{
		"VPN_SERVICE_PROVIDER":           provider,
		"VPN_TYPE":                       protocol,
		"HTTP_CONTROL_SERVER_ADDRESS":    fmt.Sprintf(":%d", cfg.GluetunAPIPort),
		"GLUETUN_SERVERS_JSON_PATH":      "/gluetun/servers.json",
		"HTTP_CONTROL_SERVER_AUTH_DEFAULT_ROLE": `{"auth":"none"}`,
		"HTTP_CONTROL_SERVER_LOG":        "off",
	}

	// TZ
	if tz := strValM(cred, "tz", settings, "tz"); tz == "" {
		env["TZ"] = "UTC"
	} else {
		env["TZ"] = tz
	}

	// DOT
	if coerceBool(settings["disable_dot"]) {
		env["DOT"] = "off"
	}

	// WireGuard IPv6 flag.
	allowIPv6 := coerceBool(firstNonNil(cred["wireguard_allow_ipv6"], settings["wireguard_allow_ipv6"]))

	// Credential-specific env.
	if err := applyCredentialEnv(env, protocol, cred, allowIPv6, ignoreEndpoint); err != nil {
		return nil, err
	}

	// Region env.
	applyRegionEnv(env, provider, regions, cred)

	// Port-forwarding env.
	applyPortForwardingEnv(env, provider, settings, cred, pfSupported)

	// Optional credential env.
	applyOptionalCredentialEnv(env, protocol, cred)

	// Extra env from settings and credential.
	for _, source := range []map[string]interface{}{settings, cred} {
		if extra, ok := source["extra_env"].(map[string]interface{}); ok {
			for k, v := range extra {
				if k = strings.TrimSpace(k); k != "" {
					env[k] = fmt.Sprintf("%v", v)
				}
			}
		}
	}

	return env, nil
}

func applyCredentialEnv(env map[string]string, protocol string, cred map[string]interface{}, allowIPv6, ignoreEndpoint bool) error {
	if protocol == "wireguard" {
		pk := firstStrOf(cred, "wireguard_private_key", "private_key", "wg_private_key", "PrivateKey")
		if pk == "" {
			return fmt.Errorf("wireguard credential missing private key")
		}
		env["WIREGUARD_PRIVATE_KEY"] = pk

		rawAddr := firstNonNil(cred["wireguard_addresses"], cred["addresses"], cred["Address"])
		if rawAddr != nil {
			addrs := normalizeWireGuardAddresses(rawAddr)
			if len(addrs) == 0 {
				return fmt.Errorf("wireguard credential has empty addresses")
			}
			if allowIPv6 {
				env["WIREGUARD_ADDRESSES"] = strings.Join(addrs, ",")
			} else {
				var v4 []string
				for _, a := range addrs {
					if isIPv4CIDR(a) {
						v4 = append(v4, a)
					}
				}
				if len(v4) == 0 {
					return fmt.Errorf("wireguard addresses are IPv6-only; enable wireguard_allow_ipv6")
				}
				env["WIREGUARD_ADDRESSES"] = strings.Join(v4, ",")
			}
		}

		if !ignoreEndpoint {
			ep := firstStrOf(cred, "wireguard_endpoints", "endpoints", "endpoint", "Endpoint")
			if ep != "" {
				env["WIREGUARD_ENDPOINTS"] = ep
			}
		}
	} else {
		user := firstStrOf(cred, "openvpn_user", "username", "user")
		pass := firstStrOf(cred, "openvpn_password", "password", "pass")
		if user == "" || pass == "" {
			return fmt.Errorf("openvpn credential missing username/password")
		}
		env["OPENVPN_USER"] = user
		env["OPENVPN_PASSWORD"] = pass

		if !ignoreEndpoint {
			if ip := firstStrOf(cred, "openvpn_endpoint_ip", "endpoint_ip", "ip"); ip != "" {
				env["OPENVPN_ENDPOINT_IP"] = ip
			}
			if port := firstStrOf(cred, "openvpn_endpoint_port", "endpoint_port", "port"); port != "" {
				env["OPENVPN_ENDPOINT_PORT"] = port
			}
		}
	}
	return nil
}

func applyRegionEnv(env map[string]string, provider string, regions []string, cred map[string]interface{}) {
	var countries, cities, serverRegions, hostnames []string

	countries = append(countries, normalizeList(cred["server_countries"])...)
	cities = append(cities, normalizeList(cred["server_cities"])...)
	serverRegions = append(serverRegions, normalizeList(cred["server_regions"])...)
	hostnames = append(hostnames, normalizeList(cred["server_hostnames"])...)

	var unqualified []string
	for _, r := range regions {
		if !strings.Contains(r, ":") {
			unqualified = append(unqualified, r)
			continue
		}
		idx := strings.IndexByte(r, ':')
		tag := strings.ToLower(strings.TrimSpace(r[:idx]))
		val := strings.TrimSpace(r[idx+1:])
		if val == "" {
			continue
		}
		switch tag {
		case "country", "countries":
			countries = append(countries, val)
		case "city", "cities":
			cities = append(cities, val)
		case "region", "regions":
			serverRegions = append(serverRegions, val)
		case "hostname", "hostnames", "server":
			hostnames = append(hostnames, val)
		default:
			unqualified = append(unqualified, r)
		}
	}

	if len(unqualified) > 0 {
		preferred := regionDefaultKey[provider]
		if preferred == "SERVER_REGIONS" {
			serverRegions = append(serverRegions, unqualified...)
		} else {
			countries = append(countries, unqualified...)
		}
	}

	if len(countries) > 0 {
		env["SERVER_COUNTRIES"] = strings.Join(dedupe(countries), ",")
	}
	if len(cities) > 0 {
		env["SERVER_CITIES"] = strings.Join(dedupe(cities), ",")
	}
	if len(serverRegions) > 0 {
		env["SERVER_REGIONS"] = strings.Join(dedupe(serverRegions), ",")
	}
	if len(hostnames) > 0 {
		env["SERVER_HOSTNAMES"] = strings.Join(dedupe(hostnames), ",")
	}
}

func applyPortForwardingEnv(
	env map[string]string,
	provider string,
	settings, cred map[string]interface{},
	pfSupported bool,
) {
	explicitPref := firstNonNil(cred["vpn_port_forwarding"], settings["vpn_port_forwarding"])
	credSupportsPFVal := credentialSupportsPF(cred)
	providerSupportsPFVal := ProviderSupportsForwarding(provider)
	normalizedSupported := pfSupported && credSupportsPFVal && providerSupportsPFVal

	var shouldEnable bool
	if explicitPref != nil {
		shouldEnable = coerceBool(explicitPref) && normalizedSupported
	} else {
		p2pEnabled := coerceBool(firstNonNil(cred["p2p_forwarding_enabled"], settings["p2p_forwarding_enabled"]))
		shouldEnable = (p2pEnabled || normalizedSupported) && normalizedSupported
	}

	if shouldEnable {
		env["VPN_PORT_FORWARDING"] = "on"
		env["VPN_PORT_FORWARDING_PROVIDER"] = provider

		if custom := firstStrOf(cred, "vpn_port_forwarding_provider"); custom == "" {
			if custom2 := strVal(settings["vpn_port_forwarding_provider"]); custom2 != "" {
				env["VPN_PORT_FORWARDING_PROVIDER"] = strings.ToLower(strings.TrimSpace(custom2))
			}
		} else {
			env["VPN_PORT_FORWARDING_PROVIDER"] = strings.ToLower(strings.TrimSpace(custom))
		}

		switch provider {
		case "private internet access":
			if user := firstStrOf(cred, "vpn_port_forwarding_username", "openvpn_user", "username"); user != "" {
				env["VPN_PORT_FORWARDING_USERNAME"] = user
			}
			if pass := firstStrOf(cred, "vpn_port_forwarding_password", "openvpn_password", "password"); pass != "" {
				env["VPN_PORT_FORWARDING_PASSWORD"] = pass
			}
			if env["SERVER_HOSTNAMES"] == "" && env["WIREGUARD_ENDPOINTS"] == "" {
				env["PORT_FORWARD_ONLY"] = "true"
			}
		case "protonvpn":
			if env["SERVER_HOSTNAMES"] == "" && env["WIREGUARD_ENDPOINTS"] == "" {
				env["PORT_FORWARD_ONLY"] = "on"
			}
		}
	} else {
		env["VPN_PORT_FORWARDING"] = "off"
	}
}

func applyOptionalCredentialEnv(env map[string]string, protocol string, cred map[string]interface{}) {
	var optionalMap map[string]string
	if protocol == "wireguard" {
		optionalMap = map[string]string{
			"wireguard_public_key":                   "WIREGUARD_PUBLIC_KEY",
			"wireguard_preshared_key":                "WIREGUARD_PRESHARED_KEY",
			"wireguard_endpoint_ip":                  "WIREGUARD_ENDPOINT_IP",
			"endpoint_ip":                            "WIREGUARD_ENDPOINT_IP",
			"wireguard_endpoint_port":                "WIREGUARD_ENDPOINT_PORT",
			"endpoint_port":                          "WIREGUARD_ENDPOINT_PORT",
			"wireguard_allowed_ips":                  "WIREGUARD_ALLOWED_IPS",
			"wireguard_implementation":               "WIREGUARD_IMPLEMENTATION",
			"wireguard_mtu":                          "WIREGUARD_MTU",
			"wireguard_persistent_keepalive_interval": "WIREGUARD_PERSISTENT_KEEPALIVE_INTERVAL",
		}
		// Default MTU for Wireguard to avoid slow path discovery (6 seconds).
		env["WIREGUARD_MTU"] = "1400"
	} else {
		optionalMap = map[string]string{
			"openvpn_protocol":      "OPENVPN_PROTOCOL",
			"openvpn_endpoint_ip":   "OPENVPN_ENDPOINT_IP",
			"endpoint_ip":           "OPENVPN_ENDPOINT_IP",
			"openvpn_endpoint_port": "OPENVPN_ENDPOINT_PORT",
			"endpoint_port":         "OPENVPN_ENDPOINT_PORT",
			"openvpn_version":       "OPENVPN_VERSION",
			"openvpn_ciphers":       "OPENVPN_CIPHERS",
			"openvpn_auth":          "OPENVPN_AUTH",
		}
	}
	for credKey, envKey := range optionalMap {
		val := strings.TrimSpace(strVal(cred[credKey]))
		if val != "" {
			env[envKey] = val
		}
	}
}

// applyPFFilterGuard drops or resolves explicit server pins that are
// incompatible with VPN_PORT_FORWARDING=on.
func applyPFFilterGuard(
	env map[string]string,
	provider, protocol, catalogFile string,
	rep *ReputationManager,
) {
	if env["VPN_PORT_FORWARDING"] != "on" {
		return
	}
	if !ProviderSupportsForwarding(provider) {
		return
	}

	explicit := extractExplicitHostnames(env, protocol)
	if len(explicit) == 0 {
		return
	}

	servers := rep.ProviderServers(provider, catalogFile)
	normalProto := normalizeProtocol(protocol)

	var compatible []string
	for _, pin := range explicit {
		found := false
		isCompatible := false
		for _, s := range servers {
			hn := strings.ToLower(strings.TrimSpace(strVal(s["hostname"])))
			if hn == "" {
				continue
			}
			entryIP := strings.TrimSpace(strVal(s["entry_ip"]))
			sIPs, _ := s["ips"].([]interface{})

			matchedIP := false
			for _, ip := range sIPs {
				if strings.TrimSpace(strVal(ip)) == pin {
					matchedIP = true
					break
				}
			}

			if hn != pin && entryIP != pin && !matchedIP {
				continue
			}
			found = true

			sProto := normalizeProtocol(strVal(s["vpn"]))
			if normalProto != "" && sProto != "" && sProto != normalProto {
				continue
			}
			if normalProto != "" && sProto == "" {
				continue
			}
			if !serverSupportsPF(s) {
				continue
			}
			isCompatible = true
			compatible = append(compatible, hn)
			break
		}

		if !found && !isPotentialIP(pin) {
			compatible = append(compatible, pin)
		} else if found && !isCompatible {
			slog.Warn("VPN pin incompatible with port-forwarding; dropping",
				"pin", pin, "provider", provider)
		}
	}

	// Clear endpoint env vars; set compatible hostnames if any found.
	clearKeys := []string{"SERVER_HOSTNAMES"}
	if protocol == "wireguard" {
		clearKeys = append(clearKeys, "WIREGUARD_ENDPOINTS", "WIREGUARD_ENDPOINT_IP", "WIREGUARD_ENDPOINT_PORT")
	} else {
		clearKeys = append(clearKeys, "OPENVPN_ENDPOINT_IP", "OPENVPN_ENDPOINT_PORT")
	}
	for _, k := range clearKeys {
		delete(env, k)
	}

	if len(compatible) > 0 {
		env["SERVER_HOSTNAMES"] = strings.Join(dedupe(compatible), ",")
		slog.Info("Resolved VPN pin to PF-capable hostname",
			"provider", provider,
			"hostnames", env["SERVER_HOSTNAMES"],
		)
	}
}

// ── Label building ────────────────────────────────────────────────────────────

func buildLabels(provider, protocol, credentialID string, pfSupported bool) map[string]string {
	labels := map[string]string{
		"acestream-orchestrator.managed":       "true",
		"role":                                 "vpn_node",
		"acestream.vpn.provider":               provider,
		"acestream.vpn.protocol":               protocol,
		"acestream.vpn.port_forwarding_supported": boolStr(pfSupported),
	}
	if credentialID != "" {
		labels["acestream.vpn.credential_id"] = credentialID
	}
	return labels
}

// ── Resolution helpers ────────────────────────────────────────────────────────

var providerAliases = map[string]string{
	"pia":                    "private internet access",
	"privateinternetaccess":  "private internet access",
	"private_internet_access": "private internet access",
}

func resolveProvider(requested string, settings, cred map[string]interface{}, cfgDefault string) string {
	providers, _ := settings["providers"].([]interface{})
	firstProvider := ""
	if len(providers) > 0 {
		firstProvider = strVal(providers[0])
	}
	raw := firstNonEmptyStr(
		requested,
		strVal(cred["provider"]),
		strVal(cred["vpn_service_provider"]),
		strVal(settings["provider"]),
		firstProvider,
		cfgDefault,
		"protonvpn",
	)
	n := strings.ToLower(strings.TrimSpace(raw))
	if v, ok := providerAliases[n]; ok {
		return v
	}
	return n
}

func resolveProtocol(settings, cred map[string]interface{}, cfgDefault string) (string, error) {
	raw := firstNonEmptyStr(
		strVal(cred["protocol"]),
		strVal(cred["vpn_type"]),
		strVal(settings["protocol"]),
		cfgDefault,
		"wireguard",
	)
	n := strings.ToLower(strings.TrimSpace(raw))
	if n != "wireguard" && n != "openvpn" {
		return "", fmt.Errorf("unsupported VPN protocol: %s", raw)
	}
	return n, nil
}

func resolveRegions(requested []string, settings, cred map[string]interface{}, cfgDefault []string) []string {
	if len(requested) > 0 {
		return requested
	}
	if r, ok := cred["regions"].([]interface{}); ok && len(r) > 0 {
		var out []string
		for _, item := range r {
			if s := strings.TrimSpace(strVal(item)); s != "" {
				out = append(out, s)
			}
		}
		return out
	}
	if r, ok := settings["regions"].([]interface{}); ok && len(r) > 0 {
		var out []string
		for _, item := range r {
			if s := strings.TrimSpace(strVal(item)); s != "" {
				out = append(out, s)
			}
		}
		return out
	}
	return cfgDefault
}

func credentialSupportsPF(cred map[string]interface{}) bool {
	pf := cred["port_forwarding"]
	if pf == nil {
		return true // default: allowed
	}
	return coerceBool(pf)
}

func effectiveCatalogFile(settings map[string]interface{}) string {
	src := strings.ToLower(strings.TrimSpace(strVal(settings["vpn_servers_refresh_source"])))
	switch src {
	case "gluetun_official":
		return "servers-official.json"
	case "proton_paid":
		return "servers-proton.json"
	}
	return "servers.json"
}

// ── WireGuard address helpers ─────────────────────────────────────────────────

func normalizeWireGuardAddresses(raw interface{}) []string {
	var tokens []string
	var extend func(v interface{})
	extend = func(v interface{}) {
		if v == nil {
			return
		}
		switch val := v.(type) {
		case []interface{}:
			for _, item := range val {
				extend(item)
			}
		case string:
			// Handle Python-list-like strings: "['10.2.0.2/32', '2a07:.../128']"
			cleaned := strings.NewReplacer("[", "", "]", "", "\"", "", "'", "").Replace(val)
			for _, part := range strings.Split(cleaned, ",") {
				if s := strings.TrimSpace(part); s != "" {
					tokens = append(tokens, s)
				}
			}
		default:
			if s := strings.TrimSpace(fmt.Sprintf("%v", v)); s != "" {
				tokens = append(tokens, s)
			}
		}
	}
	extend(raw)
	return dedupe(tokens)
}

func isIPv4CIDR(s string) bool {
	host := s
	if idx := strings.IndexByte(s, '/'); idx >= 0 {
		host = s[:idx]
	}
	ip := net.ParseIP(host)
	return ip != nil && ip.To4() != nil
}

func extractExplicitHostnames(env map[string]string, protocol string) []string {
	var hostnames []string
	hostnames = append(hostnames, normalizeList(env["SERVER_HOSTNAMES"])...)

	if protocol == "wireguard" {
		for _, ep := range normalizeList(env["WIREGUARD_ENDPOINTS"]) {
			token := strings.ToLower(strings.TrimSpace(ep))
			if strings.HasPrefix(token, "[") {
				if end := strings.Index(token, "]"); end > 1 {
					hostnames = append(hostnames, token[1:end])
					continue
				}
			}
			if count := strings.Count(token, ":"); count == 1 {
				host, port, err := net.SplitHostPort(token)
				if err == nil && port != "" {
					hostnames = append(hostnames, host)
					continue
				}
			}
			hostnames = append(hostnames, token)
		}
	}

	var out []string
	seen := map[string]bool{}
	for _, h := range hostnames {
		h = strings.ToLower(strings.TrimSpace(h))
		if h != "" && !seen[h] {
			seen[h] = true
			out = append(out, h)
		}
	}
	return out
}

func isPotentialIP(s string) bool {
	parts := strings.Split(s, ".")
	if len(parts) != 4 {
		return false
	}
	for _, p := range parts {
		if len(p) == 0 || len(p) > 3 {
			return false
		}
		for _, c := range p {
			if c < '0' || c > '9' {
				return false
			}
		}
	}
	return true
}

// ── Generic helpers ───────────────────────────────────────────────────────────

func firstStrOf(m map[string]interface{}, keys ...string) string {
	for _, k := range keys {
		if s := strings.TrimSpace(strVal(m[k])); s != "" {
			return s
		}
	}
	return ""
}

func firstNonEmptyStr(vals ...string) string {
	for _, v := range vals {
		if v = strings.TrimSpace(v); v != "" {
			return v
		}
	}
	return ""
}

func firstNonNil(vals ...interface{}) interface{} {
	for _, v := range vals {
		if v != nil {
			return v
		}
	}
	return nil
}

func strValM(a map[string]interface{}, keyA string, b map[string]interface{}, keyB string) string {
	if s := strings.TrimSpace(strVal(a[keyA])); s != "" {
		return s
	}
	return strings.TrimSpace(strVal(b[keyB]))
}

func normalizeList(v interface{}) []string {
	if v == nil {
		return nil
	}
	switch val := v.(type) {
	case string:
		var out []string
		for _, item := range strings.Split(val, ",") {
			if s := strings.TrimSpace(item); s != "" {
				out = append(out, s)
			}
		}
		return out
	case []interface{}:
		var out []string
		for _, item := range val {
			if s := strings.TrimSpace(strVal(item)); s != "" {
				out = append(out, s)
			}
		}
		return out
	}
	return nil
}

func dedupe(in []string) []string {
	seen := make(map[string]bool, len(in))
	out := make([]string, 0, len(in))
	for _, s := range in {
		if !seen[s] {
			seen[s] = true
			out = append(out, s)
		}
	}
	return out
}

func boolStr(b bool) string {
	if b {
		return "true"
	}
	return "false"
}

func generateContainerName(prefix string) string {
	// Use a short timestamp-based suffix for uniqueness without uuid dependency.
	return fmt.Sprintf("%s-%x", prefix, time.Now().UnixNano()&0xffffffff)
}

func ensureImage(ctx context.Context, cli *dockerclient.Client, ref string) error {
	imgs, err := cli.ImageList(ctx, dockerimage.ListOptions{
		Filters: filters.NewArgs(filters.Arg("reference", ref)),
	})
	if err != nil {
		return err
	}
	if len(imgs) > 0 {
		return nil
	}
	slog.Info("pulling image", "image", ref)
	rc, err := cli.ImagePull(ctx, ref, dockerimage.PullOptions{})
	if err != nil {
		return err
	}
	defer rc.Close()
	dec := json.NewDecoder(rc)
	for {
		var msg struct {
			Error string `json:"error"`
		}
		if err := dec.Decode(&msg); err != nil {
			if err == io.EOF {
				break
			}
			return err
		}
		if msg.Error != "" {
			return fmt.Errorf("%s", msg.Error)
		}
	}
	slog.Info("image pulled", "image", ref)
	return nil
}
