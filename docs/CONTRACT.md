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
- **No REST API** -- FastAPI integration lives in [flossware-tftpos-web](https://github.com/FlossWare/flossware-tftpos-web)
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

## Stability Intent

tftp-os is at version 0.x. The following modules are considered **stable public API** -- breaking changes will be documented and versioned:

- `tftpos.engine` -- `FirmwareEngine`
- `tftpos.matcher` -- `HostMatcher`
- `tftpos.registry` -- `PluginRegistry`
- `tftpos.config` -- `TftpOSConfig`, `load_config`, `load_hosts`, `load_profile`
- `tftpos.models` -- all dataclasses and enums
- `tftpos.state` -- `ProvisionTracker`, `ProvisionState`, `ProvisionRecord`
- `tftpos.plugins.base` -- `FirmwarePlugin`
- `tftpos.db` -- `StorageBackend` and built-in implementations
- `tftpos.errors` -- exception hierarchy

**Internals that may change** without notice:

- Cache key formats and TTL defaults (`tftpos.cache`)
- Logging internals (`tftpos.logging_config`)
- Metrics counter names (`tftpos.metrics`)
- Named object store schema (`tftpos.named_objects`)
- Console proxy implementation details (`tftpos.console`)
- Cluster provisioning orchestration (`tftpos.cluster`) -- still evolving

## How pxe-os Composes tftp-os

pxe-os is expected to:

1. Instantiate `FirmwareEngine` with its own `PluginRegistry` (populated with OS-specific plugins), a `HostMatcher` (loaded from host config), and a `TftpOSConfig`
2. Call `engine.serve(mac, ...)` to resolve firmware paths
3. Use the returned path to generate iPXE scripts, DHCP options, or installer configurations
4. Use `ProvisionTracker` to monitor and drive provisioning state
5. Register callbacks via `tracker.on_state_change()` for workflow automation
6. Extend `FirmwarePlugin` for each supported OS family
