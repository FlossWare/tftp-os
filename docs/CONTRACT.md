# tftp-os / pxe-os Contract

This document defines the boundary between **tftp-os** (foundation layer) and **pxe-os** (decorator layer). It is the authoritative reference for what each project owns, what tftp-os guarantees, and how consumers extend it.

## Dependency Direction

```
pxe-os  -->  tftp-os
```

pxe-os depends on tftp-os. The reverse dependency **never** exists. tftp-os must not import, reference, or assume the presence of pxe-os (or any other consumer).

## What tftp-os Provides

### Host Matching

`tftpos.matcher.HostMatcher` resolves a request's identity attributes to a `HostRule`. Matching is tiered by specificity:

| Tier | Attribute | Example |
|------|-----------|---------|
| 0 | Exact MAC | `52:54:00:ab:cd:ef` |
| 1 | MAC prefix | `52:54:00` |
| 2 | Hostname pattern | `web-*` |
| 3 | Subnet | `10.0.0.0/24` |
| 4 | Serial | `SN12345` |
| 5 | Group | `gpu-nodes` |
| 6 | Architecture | `aarch64` |
| 7 | Catch-all | (no criteria) |

The first match at the most-specific tier wins.

```python
from tftpos.matcher import HostMatcher

matcher = HostMatcher(rules)
rule = matcher.match(mac="52:54:00:ab:cd:ef", hostname="web-01")
# Returns Optional[HostRule]
```

### Profile Loading

`tftpos.config.load_profile()` parses a TOML file into a `ProvisionProfile` dataclass. Profiles live in `<data_dir>/profiles/` and are resolved by name from the matched `HostRule.profile` field.

### Firmware Path Resolution

`tftpos.engine.FirmwareEngine.serve()` is the primary entry point. Given a MAC address (and optional identity attributes), it returns the filesystem path to the correct firmware/boot file:

```python
from tftpos.engine import FirmwareEngine

engine = FirmwareEngine(registry, matcher, config, tracker)
firmware_path = engine.serve(mac="52:54:00:ab:cd:ef")
# Returns str -- path to firmware file
# Raises ValueError if no matching rule is found
```

Internally, `serve()` chains: match rule -> load profile -> look up plugin -> call `plugin.firmware_path(profile)`.

### Provisioning State

`tftpos.state.ProvisionTracker` tracks each host through a defined lifecycle:

```
REGISTERED -> BOOTING -> INSTALLING -> POST_INSTALL -> COMPLETE
                |            |              |
                +------------+--------------+--> FAILED
```

State transitions are enforced by `ProvisionTracker.transition()`. Callers can register callbacks via `on_state_change()`.

```python
from tftpos.state import ProvisionTracker, ProvisionState

tracker = ProvisionTracker(backend)
tracker.register(mac, profile, os_family, os_version)
tracker.transition(mac, ProvisionState.BOOTING)

record = tracker.get(mac)        # Optional[ProvisionRecord]
all_records = tracker.list_all() # List[ProvisionRecord]
```

Netboot control (`enable_netboot`, `disable_netboot`, `is_netboot_enabled`) is part of the tracker.

### Plugin Registry

`tftpos.registry.PluginRegistry` manages `FirmwarePlugin` implementations keyed by `os_family`.

```python
from tftpos.registry import PluginRegistry

registry = PluginRegistry(entry_point_group="tftpos.plugins")
registry.discover()       # Load plugins from installed entry points
plugin = registry.get("rhel")  # Returns FirmwarePlugin instance
registry.available        # Sorted list of registered os_family keys
```

### Configuration

`tftpos.config.TftpOSConfig` is the root configuration dataclass, loaded from TOML via `load_config()`. It includes server settings, TLS, auth, rate limiting, database, logging, audit, and webhooks. Hosts are loaded separately via `load_hosts()`.

### Models

All data structures are plain dataclasses in `tftpos.models`:

- `HostRule` -- match criteria plus BMC/deploy metadata
- `ProvisionProfile` -- OS, firmware type, install URLs, packages, scripts
- `BootFirmware` -- enum: `BIOS`, `UEFI`
- `DistroAssets` -- kernel, initrd, repo, bootloader, squashfs paths
- `CloudImage` -- cloud image metadata

### Error Types

`tftpos.errors` defines the exception hierarchy. Callers can rely on:

- `TftpOSError` -- base (has `message`, `suggestion`, `error_code`)
- `ConfigError` -- bad configuration
- `ValidationError` -- invalid input
- `ProvisionError` -- provisioning failures
- `PluginError` -- plugin lookup/execution failures

### Storage Backends

`tftpos.db.StorageBackend` is the abstract interface for provision state persistence. Built-in implementations:

- `SQLAlchemyBackend` -- SQLite, PostgreSQL, MariaDB via SQLAlchemy
- `SQLiteBackend` -- convenience wrapper for SQLite
- `JSONBackend` -- JSON file (not concurrent-safe)
- `MemoryBackend` -- in-memory (for tests)

## What tftp-os Does NOT Do

These are explicit non-goals. pxe-os or other consumers own them:

- **No iPXE script generation** -- tftp-os resolves firmware paths; pxe-os generates iPXE boot scripts
- **No autoinstall templates** -- kickstart, preseed, cloud-init *templates* belong in pxe-os; tftp-os provides raw `CloudInitConfig` generation but not OS-specific installer templates
- **No DHCP management** -- tftp-os does not configure or manage DHCP servers
- **No web UI** -- tftp-os is a pure library with no HTTP endpoints or frontend
- **No REST API** -- FastAPI integration lives in [flossware-tftp-os](https://github.com/FlossWare/flossware-tftp-os)
- **No OS-specific installer logic** -- distro-specific boot workflows belong in pxe-os plugins

## Extension Points

### FirmwarePlugin

Third-party or consumer packages extend tftp-os by implementing `tftpos.plugins.base.FirmwarePlugin`:

```python
from tftpos.plugins.base import FirmwarePlugin
from tftpos.models import ProvisionProfile, DistroAssets

class MyDistroPlugin(FirmwarePlugin):
    @property
    def os_family(self) -> str:
        return "mydistro"

    @property
    def supported_versions(self) -> list[str]:
        return ["1.0", "2.0"]

    def firmware_path(self, profile: ProvisionProfile) -> str:
        return f"/tftpboot/{profile.os_family}/{profile.os_version}/pxelinux.0"
```

Register via the `tftpos.plugins` entry point group in your package's `pyproject.toml`:

```toml
[project.entry-points."tftpos.plugins"]
mydistro = "my_package.plugins:MyDistroPlugin"
```

Plugins are discovered automatically when `PluginRegistry.discover()` is called.

Optional overrides:

- `validate_profile(profile) -> list[str]` -- return validation errors (empty = valid)
- `extract_from_image(image_path, dest) -> DistroAssets` -- extract boot assets from an ISO/image

Built-in helpers available to plugins:

- `_sanitize_context(context)` -- sanitizes hostnames, URLs, and packages
- `_render_template(template_name, context)` -- renders Jinja2 templates from a `templates/` directory

### HostMatcher Rules

Host matching is driven by `HostRule` dataclasses loaded from TOML. To add new matching behavior, define host rules with the appropriate tier attributes. The tier system is fixed (MAC > hostname > subnet > serial > group > arch > catch-all).

### Storage Backends

Implement `tftpos.db.StorageBackend` to add new persistence targets. The interface requires six methods: `save`, `get`, `list_all`, `delete`, `clear`, `close`.

## Public API

This section defines which modules constitute the public API surface.  It
is the authoritative list and must stay in sync with `tftpos/__init__.py`
(which declares the same surface via `__all__`) and `docs/SCOPE.md` (which
provides the rationale for each classification).

### Stable public modules (core)

These modules are listed in `tftpos.__all__` and form the foundation
required for firmware path resolution.  Breaking changes will be
documented in the changelog and reflected in the version number.

| Module | Key exports |
|--------|-------------|
| `tftpos.config` | `TftpOSConfig`, `load_config`, `load_hosts`, `load_profile` |
| `tftpos.engine` | `FirmwareEngine` |
| `tftpos.matcher` | `HostMatcher` |
| `tftpos.registry` | `PluginRegistry` |
| `tftpos.plugins.base` | `FirmwarePlugin` |
| `tftpos.models` | `HostRule`, `ProvisionProfile`, `BootFirmware`, `DistroAssets`, `CloudImage` |
| `tftpos.state` | `ProvisionTracker`, `ProvisionState`, `ProvisionRecord` |
| `tftpos.errors` | `TftpOSError`, `ConfigError`, `ValidationError`, `ProvisionError`, `PluginError`, `format_error` |
| `tftpos.db` | `StorageBackend`, `SQLAlchemyBackend`, `SQLiteBackend`, `JSONBackend`, `MemoryBackend` |
| `tftpos.validation` | input validation and sanitization helpers |
| `tftpos.logging_config` | logging setup used by core modules |
| `tftpos.staging` | `stage`, `unstage`, `list_staged` (tftp_root management) |
| `tftpos.plugins.static` | `StaticFirmwarePlugin` (built-in plugin for simple firmware layouts) |

### Extended modules (shipped, not part of the stable surface)

These modules ship with tftp-os but are **not** required for basic
firmware path resolution.  Their APIs may change without notice between
0.y releases.

| Module | Purpose |
|--------|---------|
| `tftpos.auth` | Authentication and RBAC |
| `tftpos.tls` | TLS certificate handling |
| `tftpos.secrets` | Secrets management |
| `tftpos.cache` | Response and object caching |
| `tftpos.rate_limit` | Request rate limiting |
| `tftpos.named_objects` | Named object registry |
| `tftpos.cloud_init` | Cloud-init config generation |
| `tftpos.cloud_image` | Cloud image handling |
| `tftpos.iso_detect` | ISO detection and distro identification |
| `tftpos.mnemonics` | Human-readable distro aliases |
| `tftpos.repo_mirror` | Repository mirror management |
| `tftpos.cluster` | Multi-host ordered provisioning |
| `tftpos.console` | Serial/VNC/SPICE console proxy |
| `tftpos.power` | BMC/IPMI/Redfish power control |
| `tftpos.client.*` | Hypervisor backends (libvirt, bhyve, Hyper-V, VMM) |

### App-layer modules (candidates for migration)

These modules are candidates for migration to **flossware-tftp-os** (the
application layer).  They remain in-tree for now but should not be
considered part of the library's long-term surface.

| Module | Purpose |
|--------|---------|
| `tftpos.webhooks` | Webhook event notifications |
| `tftpos.metrics` | Prometheus metrics export |
| `tftpos.audit` | Audit trail logging |

### Versioning policy

- **0.y (current):** The public API may change between minor releases.
  Breaking changes will be documented but are expected while the library
  matures.
- **1.0+:** The public API modules listed above become stable.  Breaking
  changes require a major version bump (semver).

## Configuration Evolution

### During 0.x (current)

Configuration may change between minor versions. Breaking changes are
documented in CHANGELOG.md and the release notes.

### Rules (effective now)

1. **New keys get defaults.** Adding a config key never breaks existing
   config files — omitted keys use sensible defaults.
2. **Removed keys are warned, not errored.** When a key is removed,
   `load_config()` logs a deprecation warning for at least one minor
   version before erroring.
3. **Type changes are breaking.** Changing a key's expected type
   (e.g. string → list) is a breaking change and requires a minor
   version bump with migration guidance.
4. **Profile schema is plugin-owned.** Profile TOML files are validated
   by `FirmwarePlugin.validate_profile()`, not by the core library.
   Plugins own their profile fields.

### Post-1.0 (future)

After 1.0, configuration will follow semver:
- Patch: no config changes
- Minor: additive only (new keys with defaults)
- Major: breaking changes with migration guide

### Validation

`load_config()` raises `ConfigError` for invalid configuration.
Unknown keys are ignored (forward-compatible). Missing required keys
raise `ConfigError` with a suggestion.


## How pxe-os Composes tftp-os

pxe-os is expected to:

1. Instantiate `FirmwareEngine` with its own `PluginRegistry` (populated with OS-specific plugins), a `HostMatcher` (loaded from host config), and a `TftpOSConfig`
2. Call `engine.serve(mac, ...)` to resolve firmware paths
3. Use the returned path to generate iPXE scripts, DHCP options, or installer configurations
4. Use `ProvisionTracker` to monitor and drive provisioning state
5. Register callbacks via `tracker.on_state_change()` for workflow automation
6. Extend `FirmwarePlugin` for each supported OS family

## App Bootstrap

The supported path from configuration to serving firmware:

```python
from tftpos.config import TftpOSConfig, load_config, load_hosts
from tftpos.engine import FirmwareEngine
from tftpos.matcher import HostMatcher
from tftpos.plugins.static import StaticFirmwarePlugin
from tftpos.registry import PluginRegistry

config = load_config("tftpos.toml")
rules = load_hosts("hosts.toml")

registry = PluginRegistry()
registry.register(
    StaticFirmwarePlugin,
    distro_root="/srv/tftpos/distros",
    os_family="openwrt",
    supported_versions=["23.05"],
)

matcher = HostMatcher(rules)
engine = FirmwareEngine(registry, matcher, config)

# Resolve and stage
path = engine.serve(mac="aa:bb:cc:dd:ee:ff")
staged = engine.stage(mac="aa:bb:cc:dd:ee:ff")
```

Note: `PluginRegistry.discover()` catalogs entry-point classes but cannot
auto-instantiate plugins that require configuration arguments.  The
application must call `registry.register(cls, **kwargs)` to provide
the necessary parameters (e.g. `distro_root`, `os_family`,
`supported_versions`).

## Recommended Imports for App v1

### Stable (safe to depend on)

These modules form the stable surface for app v1.  Breaking changes will
be documented and versioned.

- `config`, `engine`, `matcher`, `registry`, `models`, `state`, `errors`,
  `db`, `validation`, `logging_config`, `staging`, `plugins.base`,
  `plugins.static`

### Extended (may change)

These modules ship with tftp-os but their APIs may change without notice
between 0.y releases.

- `auth`, `tls`, `secrets`, `cache`, `rate_limit`, `named_objects`

### App-layer (will migrate to flossware-tftp-os)

These modules will move to the application layer and should not be
depended on for long-term library usage.

- `webhooks`, `metrics`, `audit`

### Do NOT depend on for v1

These modules are candidates for migration to downstream projects or are
not yet stable enough for app v1 consumers.

- `power`, `console`, `cluster`, `client.*`, `cloud_init`, `cloud_image`,
  `repo_mirror`, `mnemonics`, `iso_detect`
