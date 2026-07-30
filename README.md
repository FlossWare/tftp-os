# tftp-os

TFTP-based firmware provisioning library — reusable base for network boot applications.

## Overview

tftp-os is a standalone firmware provisioning library. Given a device's MAC address (or hostname, subnet, serial number, or group), tftp-os resolves which firmware file to serve, tracks provisioning state, and provides supporting infrastructure: cloud-init config generation, cloud image management, BMC power control, console access, hypervisor backends, audit logging, webhooks, and RBAC.

tftp-os is a **reusable library** — it contains no CLI, no REST API routes, and no UI. It is designed as a foundation that anyone can build on. See [flossware-tftpos](https://github.com/FlossWare/flossware-tftpos) for a reference application with desktop, mobile, and web frontends.

tftp-os works on its own for scenarios where you need to serve firmware to devices -- router firmware (OpenWRT, DD-WRT, FreshTomato), embedded systems, IoT devices, or any TFTP-based provisioning workflow.

[pxe-os](https://github.com/FlossWare/pxe-os) builds on tftp-os as a decorator layer, adding iPXE script generation, autoinstall templates (kickstart, preseed, autoinstall, autoyast), OS installer plugins, DHCP configuration, and a web UI. If you need full PXE boot provisioning for operating systems, use pxe-os. If you need a firmware serving foundation, use tftp-os directly.

## Project Status

- **950 tests**, all passing
- **Python 3.10 -- 3.13**
- **Development Status: Alpha** (Development Status :: 3 - Alpha)

> **tftp-os is alpha software.** It has been extracted from PxeOS and has not been deployed in a production environment. The test suite validates logic correctness, but no real-world TFTP serving has been performed. See [Known Limitations](#known-limitations) for details.

## Quick Start

```bash
# Install
pip install -e .

# Create configuration directory
sudo mkdir -p /etc/tftpos/profiles

# Create a config file
cat > /etc/tftpos/tftpos.toml << 'EOF'
[server]
host = "0.0.0.0"
port = 8443

[paths]
tftp_root = "/srv/tftp"
distro_root = "/srv/tftpos/distros"
data_dir = "/etc/tftpos"

[database]
backend = "sqlite"
url = "sqlite:///tftpos.db"
EOF

# Create host rules
cat > /etc/tftpos/hosts.toml << 'EOF'
[[host]]
mac = "aa:bb:cc:dd:ee:ff"
profile = "my-router"
os_family = "openwrt"
os_version = "23.05"

[[host]]
mac_prefix = "52:54:00"
profile = "lab-device"
os_family = "openwrt"
os_version = "23.05"
EOF

# Create a profile
cat > /etc/tftpos/profiles/my-router.toml << 'EOF'
[profile]
name = "my-router"
os_family = "openwrt"
os_version = "23.05"
arch = "x86_64"
EOF
```

### Programmatic usage

```python
from tftpos.config import load_config, load_hosts
from tftpos.engine import FirmwareEngine
from tftpos.matcher import HostMatcher
from tftpos.registry import PluginRegistry

# Load configuration
config = load_config(Path("/etc/tftpos/tftpos.toml"))
rules = load_hosts(Path("/etc/tftpos/hosts.toml"))

# Build the engine
registry = PluginRegistry()
registry.load_builtins(["my_plugins.openwrt"])
matcher = HostMatcher(rules)
engine = FirmwareEngine(registry, matcher, config)

# Resolve a MAC to its firmware file
firmware_path = engine.serve(mac="aa:bb:cc:dd:ee:ff")
```

## Integration with TFTP servers

tftp-os resolves firmware paths — it does not speak the TFTP protocol.
Pair it with any TFTP server (dnsmasq, tftpd-hpa, atftpd):

1. tftp-os resolves MAC → firmware file path
2. Your TFTP server serves that file to the device
3. tftp-os tracks provisioning state

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for TFTP server setup.

## Architecture

```
                    +-----------+
                    | TOML      |
                    | Config    |
                    +-----------+
                         |
      +------------------+------------------+
      |                  |                  |
+-----------+    +-------------+    +---------------+
| hosts.toml|    | tftpos.toml |    | profiles/*.toml|
+-----------+    +-------------+    +---------------+
      |                  |                  |
      v                  v                  v
+----------+    +--------------+    +----------------+
|HostMatcher|    |TftpOSConfig  |    |ProvisionProfile|
+----------+    +--------------+    +----------------+
      |                  |                  |
      +------------------+------------------+
                         |
                  +------v------+
                  |FirmwareEngine|
                  +------+------+
                         |
                  +------v------+
                  |PluginRegistry|
                  +------+------+
                         |
                  +------v------+
                  |FirmwarePlugin|
                  +------+------+
                         |
                    firmware path
```

**Data flow:**

1. `HostMatcher` receives a MAC address (and optional hostname, subnet, serial, groups, arch) and finds the best-matching `HostRule`
2. `FirmwareEngine` loads the `ProvisionProfile` for that rule from a TOML file under `data_dir/profiles/`
3. The engine looks up the `FirmwarePlugin` for the rule's `os_family` in the `PluginRegistry`
4. The plugin validates the profile and returns the firmware file path

## Configuration

tftp-os uses TOML configuration files. There are three types:

### Server config (`tftpos.toml`)

```toml
[server]
host = "0.0.0.0"
port = 8443

[paths]
tftp_root = "/srv/tftp"
distro_root = "/srv/tftpos/distros"
data_dir = "/etc/tftpos"

[tls]
cert = "/etc/tftpos/tls/cert.pem"
key = "/etc/tftpos/tls/key.pem"
auto_generate = true

[auth]
enabled = false

[database]
backend = "sqlite"          # sqlite, postgresql, mariadb
url = "sqlite:///tftpos.db"

[rate_limit]
enabled = false
tftp_requests_per_minute = 300.0
tftp_burst = 50
api_requests_per_minute = 60.0
api_burst = 20
auth_requests_per_minute = 10.0
auth_burst = 5

[logging]
level = "INFO"
json_format = false
# log_file = "/var/log/tftpos/tftpos.log"
syslog_enabled = false
journald_enabled = false

[audit]
enabled = true
# log_file = "/var/log/tftpos/audit.log"
buffer_size = 1000

[discovery]
enabled = false
service_name = "tftpos"

# [[webhooks]]
# url = "https://example.com/webhook"
# events = ["provision.started", "provision.complete"]
# secret = "your-hmac-secret"
```

### Host rules (`hosts.toml`)

```toml
# Exact MAC match (highest priority)
[[host]]
mac = "aa:bb:cc:dd:ee:ff"
profile = "my-router"
os_family = "openwrt"
os_version = "23.05"

# MAC prefix match (e.g., all QEMU VMs)
[[host]]
mac_prefix = "52:54:00"
profile = "lab-device"
os_family = "openwrt"
os_version = "23.05"

# Hostname pattern match
[[host]]
hostname_pattern = "router-*.lab.example.com"
profile = "lab-router"
os_family = "ddwrt"
os_version = "2024"

# Subnet match
[[host]]
subnet = "10.0.0.0/24"
profile = "office-device"
os_family = "openwrt"
os_version = "23.05"

# Catch-all (lowest priority)
[[host]]
profile = "default"
os_family = "openwrt"
os_version = "23.05"

# With BMC power control
[[host]]
mac = "11:22:33:44:55:66"
profile = "managed-device"
os_family = "openwrt"
os_version = "23.05"
bmc_host = "192.168.1.100"
bmc_user = "admin"
bmc_password = "{{secret:bmc_password}}"
bmc_driver = "ipmi"
```

### Profiles (`profiles/*.toml`)

```toml
[profile]
name = "my-router"
os_family = "openwrt"
os_version = "23.05"
arch = "x86_64"
firmware = "bios"           # bios or uefi
vendor = "openwrt"
install_url = "https://downloads.openwrt.org/releases/23.05.5/targets/x86/64/"
packages = ["luci", "wireguard-tools"]
post_scripts = ["#!/bin/sh\necho 'Provisioning complete'"]

[profile.network]
hostname = "router-01.lab.example.com"
gateway = "10.0.0.1"
nameservers = ["10.0.0.1", "8.8.8.8"]

[profile.disk]
size = "1G"

[profile.extra]
custom_key = "custom_value"
```

## Host Matching

`HostMatcher` uses tiered priority matching. When multiple rules could match a request, the most specific match wins:

| Tier | Match type | Example |
|------|-----------|---------|
| 0 | Exact MAC | `mac = "aa:bb:cc:dd:ee:ff"` |
| 1 | MAC prefix | `mac_prefix = "52:54:00"` |
| 2 | Hostname pattern | `hostname_pattern = "router-*.lab"` |
| 3 | Subnet | `subnet = "10.0.0.0/24"` |
| 4 | Serial number | `serial = "SN123456"` |
| 5 | Group | `group = "lab-devices"` |
| 6 | Architecture | `arch = "aarch64"` |
| 7 | Catch-all | No match criteria set |

Within the same tier, rules are sorted by `priority` (lower number = higher priority, default 100).

Hostname patterns use `fnmatch` glob syntax. Subnet matching uses standard CIDR notation.

## Plugin Architecture

tftp-os uses a plugin system for firmware handling. Each plugin implements the `FirmwarePlugin` abstract base class:

```python
from tftpos.plugins.base import FirmwarePlugin
from tftpos.models import ProvisionProfile


class OpenWRTPlugin(FirmwarePlugin):

    @property
    def os_family(self) -> str:
        return "openwrt"

    @property
    def supported_versions(self) -> list[str]:
        return ["23.05", "23.05.5", "24.10"]

    def firmware_path(self, profile: ProvisionProfile) -> str:
        return f"/srv/tftpos/distros/openwrt/{profile.os_version}/firmware.bin"

    def validate_profile(self, profile: ProvisionProfile) -> list[str]:
        errors = super().validate_profile(profile)
        if not profile.install_url:
            errors.append("install_url is required for OpenWRT")
        return errors
```

### Registration

**Via entry points** (for installable packages):

```toml
# In your package's pyproject.toml
[project.entry-points."tftpos.plugins"]
openwrt = "my_plugins.openwrt:OpenWRTPlugin"
```

**Via `load_builtins()`** (for local plugins):

```python
registry = PluginRegistry()
registry.load_builtins(["my_plugins.openwrt", "my_plugins.ddwrt"])
```

**Manual registration:**

```python
registry = PluginRegistry()
registry.register(OpenWRTPlugin)
```

See [docs/PLUGIN_GUIDE.md](docs/PLUGIN_GUIDE.md) for a complete tutorial.

## Provisioning State Machine

tftp-os tracks each device through a provisioning lifecycle:

```
REGISTERED ──> BOOTING ──> INSTALLING ──> POST_INSTALL ──> COMPLETE
     │             │            │              │
     └─────────────┴────────────┴──────────────┴──────> FAILED
```

| State | Description |
|-------|-------------|
| `REGISTERED` | Host registered, waiting for boot request |
| `BOOTING` | Boot request served |
| `INSTALLING` | Installation config served |
| `POST_INSTALL` | Post-install scripts running |
| `COMPLETE` | Provisioning finished successfully |
| `FAILED` | Provisioning failed |

```python
from tftpos.state import ProvisionTracker, ProvisionState

tracker = ProvisionTracker()
tracker.register("aa:bb:cc:dd:ee:ff", "my-router", "openwrt", "23.05")
tracker.transition("aa:bb:cc:dd:ee:ff", ProvisionState.BOOTING)

# Boot-once: disable netboot after completion
tracker.disable_netboot("aa:bb:cc:dd:ee:ff")
assert not tracker.is_netboot_enabled("aa:bb:cc:dd:ee:ff")

# Callbacks
tracker.on_state_change(ProvisionState.COMPLETE, lambda record: print(f"Done: {record.mac}"))
```

## Storage Backends

tftp-os supports multiple storage backends for provisioning state:

| Backend | Config `url` | Use case |
|---------|-------------|----------|
| SQLite | `sqlite:///tftpos.db` | Single-node (default) |
| PostgreSQL | `postgresql://user:pass@host/db` | Multi-node / production |
| MySQL/MariaDB | `mysql+pymysql://user:pass@host/db` | Existing MySQL infrastructure |
| JSON | N/A (programmatic) | Development / testing |
| In-memory | N/A (programmatic) | Testing |

```toml
# tftpos.toml
[database]
backend = "postgresql"
url = "postgresql://tftpos:secret@db.example.com/tftpos"
```

Install the appropriate driver:

```bash
pip install tftpos[postgres]   # PostgreSQL
pip install tftpos[mysql]      # MySQL/MariaDB
```

## Cloud-Init

Generate cloud-init configuration for bare-metal or VM provisioning:

```python
from tftpos.cloud_init import generate, create_config_drive
from tftpos.models import ProvisionProfile

profile = ProvisionProfile(
    name="server-01",
    os_family="ubuntu",
    os_version="24.04",
    network={"hostname": "server-01.example.com"},
    packages=["docker.io", "nginx"],
    post_scripts=["#!/bin/bash\nsystemctl enable nginx"],
)

# Generate config files
config = generate(profile)
print(config.user_data)    # cloud-config YAML
print(config.meta_data)    # instance metadata JSON
print(config.network_config)  # network-config v2

# Create a config-drive ISO
iso_path = create_config_drive(profile, Path("/tmp/cloud-init.iso"))
```

## Cloud Images

Import and manage cloud images (qcow2, raw, vmdk, vhd, vhdx):

```python
from tftpos.cloud_image import import_cloud_image, list_images, convert_image

# Import from URL
image = import_cloud_image(
    source="https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img",
    os_family="ubuntu",
    vendor="ubuntu",
    version="24.04",
)

# Convert format
convert_image(Path("image.qcow2"), "raw", Path("image.raw"))

# List all images
for img in list_images():
    print(f"{img.name}: {img.format} ({img.size_bytes} bytes)")
```

## Cluster Provisioning

Define and provision groups of machines in coordinated order:

```toml
# cluster.toml
[cluster]
name = "lab-cluster"
provisioning_order = ["control-plane", "workers"]

[[cluster.hosts]]
hostname = "cp-01"
mac = "aa:bb:cc:00:00:01"
role = "control-plane"
profile = "k8s-control"

[[cluster.hosts]]
hostname = "worker-01"
mac = "aa:bb:cc:00:00:02"
role = "workers"
profile = "k8s-worker"

[[cluster.hosts]]
hostname = "worker-02"
mac = "aa:bb:cc:00:00:03"
role = "workers"
profile = "k8s-worker"
```

```python
from tftpos.cluster import ClusterManager, ClusterStore, parse_cluster_toml

store = ClusterStore(Path("/etc/tftpos"))
manager = ClusterManager(store)

# Parse and create
with open("cluster.toml") as f:
    definition = parse_cluster_toml(f.read())
manager.create_cluster(definition)

# Provision in order (control-plane first, then workers)
manager.start_provisioning("lab-cluster")
```

## BMC Power Control

Control server power via IPMI or Redfish:

```python
from tftpos.power import PowerManager, IPMIDriver, RedfishDriver

pm = PowerManager()

# Register IPMI-controlled device
pm.register("aa:bb:cc:dd:ee:ff", IPMIDriver(
    host="192.168.1.100",
    username="admin",
    password="secret",
))

# Register Redfish-controlled device
pm.register("11:22:33:44:55:66", RedfishDriver(
    host="192.168.1.101",
    username="admin",
    password="secret",
))

# Control power
pm.power_on("aa:bb:cc:dd:ee:ff")
pm.set_boot_device("aa:bb:cc:dd:ee:ff", device="pxe")
status = pm.power_status("aa:bb:cc:dd:ee:ff")  # "on", "off", "unknown"
```

BMC credentials in host rules support secret references (`{{secret:KEY}}`), resolved at runtime by `SecretsManager`.

## Console Access

WebSocket proxy for VNC, SPICE, and serial consoles:

```python
from tftpos.console import ConsoleConfig, ConsoleType, ConsoleProxy

config = ConsoleConfig(
    console_type=ConsoleType.VNC,
    host="192.168.1.100",
    port=5900,
)

proxy = ConsoleProxy(config)
await proxy.connect()
await proxy.send_to_backend(b"data")
response = await proxy.receive_from_backend()
await proxy.close()
```

Host rules can specify console access directly:

```toml
[[host]]
mac = "aa:bb:cc:dd:ee:ff"
profile = "my-server"
os_family = "openwrt"
os_version = "23.05"
console_type = "vnc"
console_endpoint = "192.168.1.100:5900"
```

## Hypervisor Backends

tftp-os can create and manage VMs on four hypervisor platforms:

| Backend | Platform | CLI tools |
|---------|----------|-----------|
| `LibvirtBackend` | Linux (KVM/QEMU) | `virsh`, `virt-install` |
| `BhyveBackend` | FreeBSD | `bhyvectl`, `bhyve` |
| `VmmBackend` | OpenBSD | `vmctl` |
| `HyperVBackend` | Windows | PowerShell Hyper-V |

```python
from tftpos.client import detect_hypervisor

# Auto-detect available hypervisor
backend = detect_hypervisor()
if backend:
    print(f"Found: {backend.hypervisor_name}")

    # Create and start a VM
    backend.create_vm(
        name="test-vm",
        mac="52:54:00:00:00:01",
        memory_mb=2048,
        vcpus=2,
        disk_gb=20,
    )
    backend.start_vm("test-vm")
```

## Observability

### Metrics

Prometheus-compatible metrics in text exposition format:

```python
from tftpos.metrics import render_metrics

print(render_metrics())
# tftpos_provisions_total 0
# tftpos_active_provisions 0
# tftpos_boot_requests_total 0
# ...
```

### Audit logging

Structured audit trail for all provisioning events:

```python
from tftpos.audit import init_audit, get_audit_logger

init_audit()
logger = get_audit_logger()
logger.log_boot_request(mac="aa:bb:cc:dd:ee:ff", profile="my-router")
logger.log_state_transition(
    mac="aa:bb:cc:dd:ee:ff",
    old_state="registered",
    new_state="booting",
)

# Query recent events
events = logger.query(mac="aa:bb:cc:dd:ee:ff", limit=10)
```

Audit supports file logging, stdout, and syslog output.

### Webhooks

Webhook delivery for provisioning lifecycle events:

```python
from tftpos.webhooks import WebhookManager, WebhookConfig

manager = WebhookManager(webhooks=[
    WebhookConfig(
        url="https://example.com/hook",
        events=["provision.started", "provision.complete"],
        secret="hmac-secret",
    ),
])

# Fire an event (async delivery)
manager.fire("provision.started", {"mac": "aa:bb:cc:dd:ee:ff"})
```

Supported events: `provision.started`, `provision.complete`, `provision.failed`, `netboot.disabled`.

Webhook payloads are signed with HMAC-SHA256 when a secret is configured. Verify with:

```python
from tftpos.webhooks import verify_signature
valid = verify_signature(payload_bytes, secret, signature_header)
```

## Security

### RBAC

Three roles with hierarchical access:

| Role | Permissions |
|------|------------|
| `VIEWER` | Read-only access |
| `OPERATOR` | Read + write (manage hosts, trigger provisions) |
| `ADMIN` | Full access (manage API keys, configuration) |

```python
from tftpos.auth import ApiKeyStore, Role

store = ApiKeyStore(Path("/etc/tftpos"))
raw_key, api_key = store.create_key("my-key", Role.OPERATOR)
print(f"API key: {raw_key}")  # tftpos_...

# Validate a key
validated = store.validate(raw_key)  # Returns ApiKey or None
```

Enable in config:

```toml
[auth]
enabled = true
```

### Rate limiting

Token-bucket rate limiting with three endpoint groups:

```toml
[rate_limit]
enabled = true
tftp_requests_per_minute = 300.0
tftp_burst = 50
api_requests_per_minute = 60.0
api_burst = 20
auth_requests_per_minute = 10.0
auth_burst = 5
```

### TLS

Auto-generated self-signed certificates by default. Configure custom certs:

```toml
[tls]
cert = "/etc/tftpos/tls/cert.pem"
key = "/etc/tftpos/tls/key.pem"
auto_generate = false
```

See [docs/SECURITY.md](docs/SECURITY.md) for details.

## Secrets Management

Two providers for resolving `{{secret:KEY}}` references in profiles and host rules:

**File-based** (default): Stores secrets in `<data_dir>/secrets.json`.

**Environment variables**: Reads `TFTPOS_SECRET_<KEY>` environment variables.

```python
from tftpos.secrets import SecretsManager, FileSecretsProvider

provider = FileSecretsProvider(Path("/etc/tftpos"))
provider.set("bmc_password", "my-secret")

manager = SecretsManager(provider)
resolved_profile = manager.resolve_profile(profile)  # {{secret:bmc_password}} -> my-secret
```

## Repository Mirrors

Manage local mirrors of package repositories:

```python
from tftpos.repo_mirror import RepoManager, RepoMirror

manager = RepoManager(Path("/etc/tftpos"))
manager.add_mirror(RepoMirror(
    name="openwrt-23.05",
    source_url="rsync://downloads.openwrt.org/releases/23.05.5/",
    local_path="/srv/tftpos/mirrors/openwrt-23.05",
    sync_interval=86400,
))

result = manager.sync_mirror("openwrt-23.05")
print(f"Sync {'succeeded' if result.success else 'failed'}")
```

## Named Objects

CRUD store for named distros and hosts, backed by JSON files:

```python
from tftpos.named_objects import NamedObjectStore, NamedDistro, NamedHost

store = NamedObjectStore(Path("/etc/tftpos"))

# Register a distro
store.add_distro(NamedDistro(
    name="openwrt-23.05",
    os_family="openwrt",
    vendor="openwrt",
    version="23.05",
    kernel_path="/srv/tftpos/distros/openwrt/23.05/vmlinuz",
))

# Register a host
store.add_host(NamedHost(
    name="router-01",
    mac="aa:bb:cc:dd:ee:ff",
    profile="my-router",
    hostname="router-01.lab.example.com",
))

# Look up
distro = store.get_distro("openwrt-23.05")
host = store.find_host_by_mac("aa:bb:cc:dd:ee:ff")
```

## ISO Detection

Detect OS family and version from a mounted ISO or image:

```python
from tftpos.iso_detect import detect_iso, is_live_iso

alias = detect_iso(Path("/mnt/iso"))
# DistroAlias(os_family="fedora", vendor="fedora", version="40")

live = is_live_iso(Path("/mnt/iso"))
# True if squashfs/live filesystem found
```

Supports detection via `.treeinfo`, `.discinfo`, `README.diskdefines`, Debian `dists/`, OpenBSD `MANIFEST`/`bsd.rd`, FreeBSD `base.txz`, and volume labels.

## Structured Logging

Configurable logging with JSON output, syslog, journald, and correlation IDs:

```python
from tftpos.logging_config import configure_logging, LoggingConfig
from tftpos.logging_config import set_correlation_id, get_correlation_id

configure_logging(LoggingConfig(
    level="INFO",
    json_format=True,       # JSON lines output
    syslog_enabled=False,
    journald_enabled=False,
))

# Correlation IDs for request tracing
cid = set_correlation_id()  # generates UUID
# All log records now include cid=<uuid>
```

Enable in config:

```toml
[logging]
level = "INFO"
json_format = true
syslog_enabled = false
journald_enabled = false
```

## Distro Mnemonics

Shorthand aliases for common distro identifiers:

| Alias | OS Family | Vendor | Version |
|-------|-----------|--------|---------|
| `fedora40` | fedora | fedora | 40 |
| `rhel9` | fedora | rhel | 9 |
| `ubuntu2404` | ubuntu | ubuntu | 24.04 |
| `noble` | ubuntu | ubuntu | 24.04 |
| `deb12` | debian | debian | 12 |
| `bookworm` | debian | debian | 12 |
| `fbsd14` | freebsd | freebsd | 14 |
| `obsd76` | openbsd | openbsd | 7.6 |
| `arch` | arch | arch | latest |
| `win11` | windows | windows | 11 |
| `tumbleweed` | suse | opensuse | tumbleweed |

```python
from tftpos.mnemonics import resolve_mnemonic

alias = resolve_mnemonic("rhel9")
# DistroAlias(os_family="fedora", vendor="rhel", version="9")
```

40+ built-in aliases are included. Register custom aliases:

```python
from tftpos.mnemonics import get_registry, DistroAlias

registry = get_registry()
registry.register("myrouter", DistroAlias("openwrt", "openwrt", "23.05"))
```

## Relationship to PxeOS

tftp-os is the **foundation layer**. PxeOS is the **decorator layer**.

| Concern | tftp-os | PxeOS |
|---------|--------|-------|
| Host matching | MAC, hostname, subnet, serial, group, arch | Same (delegates to tftp-os) |
| Profile loading | TOML-based profiles | Same (delegates to tftp-os) |
| Firmware serving | Generic firmware path | iPXE scripts, kernel + initrd + boot args |
| Autoinstall | Cloud-init only | Kickstart, preseed, autoinstall, autoyast |
| OS plugins | `FirmwarePlugin` (firmware path) | `OSPlugin` extends FirmwarePlugin (boot assets, autoinstall, ISO extraction) |
| Plugin registry | Entry point: `tftpos.plugins` | Entry point: `pxeos.plugins` |
| DHCP | Not managed | Generates dnsmasq/ISC DHCP config |
| Web UI | None | Dashboard with live status |

**Dependency direction:** PxeOS depends on tftp-os. tftp-os never imports from PxeOS.

```python
# PxeOS engine wraps tftp-os engine (has-a, not is-a)
from tftpos.engine import FirmwareEngine
from pxeos.engine import ProvisioningEngine

engine = ProvisioningEngine(registry, matcher, config)
# engine._firmware is a FirmwareEngine instance
# engine.render_ipxe_script() -- PXE-specific
# engine._firmware.serve()    -- tftp-os firmware resolution
```

## Known Limitations

- **No real-world deployment** -- tftp-os has not been used to serve firmware to actual devices ([#5](https://github.com/FlossWare/tftp-os/issues/5))
- **No REST API implementation** -- this is intentional; tftp-os is a pure library with no API routes
- **Plugin ecosystem** -- No built-in firmware plugins ship with tftp-os; you must write your own or use PxeOS's OS plugins ([#4](https://github.com/FlossWare/tftp-os/issues/4))
- **IPMI/Redfish** -- Power control shells out to `ipmitool` and makes HTTP requests to Redfish endpoints; not tested against real BMCs
- **Hypervisor backends** -- Shell out to platform-specific CLI tools; tested with mocks only
- **Cloud image import** -- Requires `qemu-img` and `wget`/`curl` on the host

## System Requirements

| Requirement | Value |
|-------------|-------|
| Python | >= 3.10 |
| OS | Linux, FreeBSD, OpenBSD, NetBSD |

### Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| jinja2 | >= 3.1 | Template rendering |
| pydantic | >= 2.0 | Data validation |
| sqlalchemy | >= 2.0 | Database ORM |
| tomli | >= 2.0 | TOML parsing (Python < 3.11) |

### Optional dependency groups

```bash
pip install tftpos[tls]       # cryptography (TLS cert generation)
pip install tftpos[postgres]  # psycopg2 (PostgreSQL)
pip install tftpos[mysql]     # pymysql (MySQL/MariaDB)
pip install tftpos[dev]       # pytest, ruff, mypy, bandit, coverage
```

## Development

```bash
git clone https://github.com/FlossWare/tftp-os.git
cd tftp-os
pip install -e ".[dev]"
pytest

# Lint and type-check
ruff check tftpos/
mypy tftpos/
```

### Try it locally

```python
import tempfile
from pathlib import Path
from tftpos.config import load_config, load_hosts
from tftpos.engine import FirmwareEngine
from tftpos.matcher import HostMatcher
from tftpos.registry import PluginRegistry

with tempfile.TemporaryDirectory() as tmp:
    tmp_path = Path(tmp)

    # Minimal config
    (tmp_path / "tftpos.toml").write_text("""
[server]
host = "127.0.0.1"
port = 8443
[paths]
tftp_root = "/tmp/tftp"
data_dir = "{tmp}"
[database]
backend = "sqlite"
url = "sqlite://"
""".format(tmp=tmp))

    # Host rule
    (tmp_path / "hosts.toml").write_text("""
[[host]]
mac = "aa:bb:cc:dd:ee:ff"
profile = "demo"
os_family = "openwrt"
os_version = "23.05"
""")

    config = load_config(tmp_path / "tftpos.toml")
    rules = load_hosts(tmp_path / "hosts.toml")
    engine = FirmwareEngine(PluginRegistry(), HostMatcher(rules), config)
    result = engine.serve(mac="aa:bb:cc:dd:ee:ff")
    print(result)
```

See [docs/TESTING.md](docs/TESTING.md) for test structure and coverage targets.

## Deployment

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for installation, systemd, firewall, TLS, and DHCP/TFTP server setup.

## Security

See [docs/SECURITY.md](docs/SECURITY.md) for RBAC, API keys, rate limiting, input validation, and vulnerability reporting.

## License

[GPL-3.0-or-later](LICENSE)
