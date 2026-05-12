# Agent: Gluetun Wiki Sync

## Purpose

Keep the orchestrator's VPN provider support in sync with the upstream [gluetun-wiki](https://github.com/qdm12/gluetun-wiki) repository.

## Source of truth

The local `gluetun-wiki/` folder is a clone of `https://github.com/qdm12/gluetun-wiki.git`.  
Provider definitions live in `gluetun-wiki/setup/providers/` — one `.md` file per provider.

---

## Step 1 — Fetch latest wiki

```bash
cd gluetun-wiki
git pull origin main
cd ..
```

If the clone does not exist yet:

```bash
git clone https://github.com/qdm12/gluetun-wiki.git gluetun-wiki
```

---

## Step 2 — Audit provider list

List all provider files (excluding `custom.md`):

```bash
ls gluetun-wiki/setup/providers/*.md | xargs -I{} basename {} .md | grep -v custom | sort
```

Compare this against the `PROVIDER_OPTIONS` array in:

```
app/static/panel-react/src/pages/settings/VPNSettings.jsx
```

Every provider file must have a corresponding entry in `PROVIDER_OPTIONS` with:
- `value` = `VPN_SERVICE_PROVIDER` value used in the provider's docker example (lowercase, spaces allowed — e.g. `"vpn unlimited"`, `"perfect privacy"`)
- `label` = human-readable name

---

## Step 3 — Audit port-forwarding providers

Check which providers support the native `VPN_PORT_FORWARDING=on` mechanism:

```bash
grep -l "VPN_PORT_FORWARDING=on" gluetun-wiki/setup/providers/*.md | xargs -I{} basename {}
```

Compare against `portForwardingProviders` in:

```
app/orchestrator/internal/controlplane/vpn/gluetun.go
```

Each provider in that grep output must appear in the map (key = lowercase `VPN_SERVICE_PROVIDER` value).  
Providers that only support `FIREWALL_VPN_INPUT_PORTS` (manual) should **not** be in this map.

---

## Step 4 — Audit region key defaults

Some providers use `SERVER_REGIONS` instead of `SERVER_COUNTRIES` as their primary filter.
Detect them:

```bash
for f in gluetun-wiki/setup/providers/*.md; do
  provider=$(basename "$f" .md)
  first=$(grep -m1 "SERVER_REGIONS\|SERVER_COUNTRIES\|SERVER_CITIES" "$f" | grep -o "SERVER_[A-Z]*" | head -1)
  echo "$provider: $first"
done
```

Any provider where the first filter key is `SERVER_REGIONS` must be in the `regionDefaultKey` map in:

```
app/orchestrator/internal/controlplane/vpn/node_provisioner.go
```

Currently these providers default to `SERVER_REGIONS`: `private internet access`, `giganews`, `windscribe`, `vyprvpn`.

---

## Step 5 — Files to update

| File | What to check |
|------|--------------|
| `app/static/panel-react/src/pages/settings/VPNSettings.jsx` | `PROVIDER_OPTIONS` array — all providers present and sorted |
| `app/orchestrator/internal/controlplane/vpn/gluetun.go` | `portForwardingProviders` map — only `VPN_PORT_FORWARDING=on` providers |
| `app/orchestrator/internal/controlplane/vpn/node_provisioner.go` | `regionDefaultKey` map — providers that use `SERVER_REGIONS` as primary |

---

## Step 6 — Verify VPN_SERVICE_PROVIDER values

For each provider file, the canonical `VPN_SERVICE_PROVIDER` value is the one used in the `docker run` example near the top of the file:

```bash
grep -m1 "VPN_SERVICE_PROVIDER=" gluetun-wiki/setup/providers/<provider>.md
```

Make sure the `value` field in `PROVIDER_OPTIONS` matches this exactly (gluetun is case-sensitive).

---

## Checklist

- [ ] `git pull` run on `gluetun-wiki/`
- [ ] New providers added to `PROVIDER_OPTIONS` (sorted A-Z, `custom` last)
- [ ] Removed providers removed from `PROVIDER_OPTIONS`
- [ ] `portForwardingProviders` map matches providers with `VPN_PORT_FORWARDING=on` in wiki
- [ ] `regionDefaultKey` map matches providers whose TLDR uses `SERVER_REGIONS` as primary
- [ ] Frontend builds without error after changes
- [ ] Cleanup gluetun-wiki files
