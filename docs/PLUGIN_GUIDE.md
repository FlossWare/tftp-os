# Writing TftpOS Firmware Plugins

A developer tutorial for writing plugins that tell TftpOS how to resolve firmware paths for different device types.

## Overview

TftpOS uses plugins to map a provisioning request to a firmware file on disk. When TftpOS receives a boot request, the engine looks up the device's profile, finds the plugin registered for that profile's `os_family`, validates the profile, and asks the plugin for the firmware file path. That path is what gets served over TFTP.

You need a plugin when:

- You are provisioning a device type that TftpOS does not already have a plugin for
- The firmware path layout for your devices does not match an existing plugin
- You need custom validation logic (required fields, version constraints, architecture checks)
- You need to extract firmware from archive files (`.tar.gz`, `.img`, etc.)

TftpOS ships with no built-in firmware plugins. You write them yourself, or use PxeOS's OS plugins if you are doing full PXE boot provisioning.

## FirmwarePlugin ABC

All plugins extend `FirmwarePlugin` from `tftpos/plugins/base.py`.

| Member | Kind | Abstract | Description |
|--------|------|----------|-------------|
| `os_family` | property | yes | String identifier for this OS/firmware family (e.g. `"openwrt"`, `"ddwrt"`) |
| `supported_versions` | property | yes | List of version strings this plugin handles |
| `firmware_path(profile)` | method | yes | Returns the filesystem path to the firmware file for the given profile |
| `validate_profile(profile)` | method | no | Returns a list of error strings (empty list = valid). Base implementation checks name, os_family match, and version support |
| `extract_from_image(image_path, dest)` | method | no | Extracts firmware from an archive. Default raises `NotImplementedError` |
| `_render_template(template_name, context)` | method | no | Renders a Jinja2 template from `tftpos/templates/` |
| `_sanitize_context(context)` | method | no | Sanitizes `hostname`, `install_url`, and `packages` in a template context dict |

### os_family

A lowercase string that uniquely identifies this firmware family. This is the key used for plugin registry lookup. It must match the `os_family` field in your `ProvisionProfile` and host rules.

```python
@property
def os_family(self) -> str:
    return "openwrt"
```

### supported_versions

A list of version strings this plugin knows how to handle. The base `validate_profile()` checks the profile's `os_version` against this list and rejects unknown versions. Return an empty list to accept any version (no version checking).

```python
@property
def supported_versions(self) -> list[str]:
    return ["23.05", "23.05.5", "24.10"]
```

### firmware_path(profile)

The core method. Given a `ProvisionProfile`, return the absolute filesystem path to the firmware file that should be served. This path should point to a real file under your TFTP root or distro storage directory.

```python
def firmware_path(self, profile: ProvisionProfile) -> str:
    return f"/srv/tftpos/distros/openwrt/{profile.os_version}/{profile.arch}/firmware.bin"
```

The profile gives you access to `os_version`, `arch`, `vendor`, `firmware` (BIOS/UEFI enum), `extra` (arbitrary dict), and everything else defined in `ProvisionProfile`. Use whatever fields you need to construct the path.

### validate_profile(profile)

Returns a list of error strings. An empty list means the profile is valid. The base implementation already checks three things:

- `profile.name` is not empty
- `profile.os_family` matches `self.os_family`
- `profile.os_version` is in `self.supported_versions` (if `supported_versions` is non-empty)

Override this to add your own checks. Always call `super().validate_profile(profile)` first to keep the base checks.

### extract_from_image(image_path, dest)

Optional. Override this if your firmware ships inside archives (`.tar.gz`, `.img`, `.zip`) and you need to extract the actual firmware binary. Returns a `DistroAssets` dataclass. The default raises `NotImplementedError`.

### _render_template(template_name, context)

Renders a Jinja2 template from the `tftpos/templates/` directory. The environment has `trim_blocks` and `lstrip_blocks` enabled, autoescapes `.xml` and `.xml.j2` files, and preserves trailing newlines. You do not need to override this -- just call it when you need templated output.

### _sanitize_context(context)

Sanitizes template context values before rendering. Validates `hostname` against RFC 952/1123, validates `install_url` against allowed URL schemes (`http`, `https`, `ftp`, `nfs`, `tftp`), and validates `packages` entries against a safe package name pattern. Raises `ValueError` on invalid input. Call this before `_render_template()` if your context includes user-supplied values.

## Step-by-Step: OpenWRT Plugin

A complete working plugin for serving OpenWRT firmware images.

```python
"""OpenWRT firmware plugin for TftpOS."""

from pathlib import Path

from tftpos.models import DistroAssets, ProvisionProfile
from tftpos.plugins.base import FirmwarePlugin


class OpenWRTPlugin(FirmwarePlugin):

    # OpenWRT organizes firmware by release version and target architecture.
    # Example layout on disk:
    #   /srv/tftpos/distros/openwrt/23.05/x86_64/firmware.bin
    #   /srv/tftpos/distros/openwrt/24.10/aarch64/firmware.bin

    DISTRO_ROOT = "/srv/tftpos/distros"

    @property
    def os_family(self) -> str:
        return "openwrt"

    @property
    def supported_versions(self) -> list[str]:
        return ["23.05", "23.05.5", "24.10"]

    def firmware_path(self, profile: ProvisionProfile) -> str:
        # Build the path from version and architecture.
        # The 'extra' dict can override the filename if a profile
        # needs a non-default image (e.g. sysupgrade vs factory).
        filename = profile.extra.get("firmware_filename", "firmware.bin")
        return (
            f"{self.DISTRO_ROOT}/openwrt/"
            f"{profile.os_version}/{profile.arch}/{filename}"
        )

    def validate_profile(self, profile: ProvisionProfile) -> list[str]:
        # Keep base checks (name, os_family match, version support).
        errors = super().validate_profile(profile)

        # OpenWRT downloads require an install_url pointing to the
        # release mirror so the engine knows where to fetch images.
        if not profile.install_url:
            errors.append("install_url is required for OpenWRT")

        # Guard against unsupported architectures.
        allowed_arches = {"x86_64", "aarch64", "mips", "mipsel", "armv7"}
        if profile.arch not in allowed_arches:
            errors.append(
                f"unsupported arch {profile.arch!r}; "
                f"allowed: {sorted(allowed_arches)}"
            )

        return errors

    def extract_from_image(
        self, image_path: Path, dest: Path
    ) -> DistroAssets:
        # OpenWRT release tarballs contain the firmware binary and
        # a sha256sums file. Extract and return the paths.
        import tarfile

        dest.mkdir(parents=True, exist_ok=True)
        with tarfile.open(image_path, "r:gz") as tar:
            tar.extractall(path=dest)

        kernel = dest / "openwrt-firmware.bin"
        if not kernel.exists():
            raise FileNotFoundError(
                f"firmware binary not found in {image_path}"
            )
        return DistroAssets(kernel_path=kernel)
```

### What each method does here

- **os_family** -- returns `"openwrt"`, which becomes the registry key. Profiles with `os_family = "openwrt"` will route to this plugin.
- **supported_versions** -- pins the plugin to specific OpenWRT releases. A profile requesting `os_version = "22.03"` will fail validation.
- **firmware_path** -- constructs the path using version and architecture from the profile. Supports an optional `firmware_filename` override in `profile.extra` for cases where you need sysupgrade images instead of factory images.
- **validate_profile** -- calls super for the three base checks, then adds two OpenWRT-specific rules: install_url is required, and architecture must be in the allowed set.
- **extract_from_image** -- handles `.tar.gz` archives by extracting them and returning the kernel path as a `DistroAssets` instance.

## Step-by-Step: DD-WRT Plugin

A simpler plugin showing different patterns -- flat firmware layout and stricter version validation.

```python
"""DD-WRT firmware plugin for TftpOS."""

import re
from pathlib import Path

from tftpos.models import ProvisionProfile
from tftpos.plugins.base import FirmwarePlugin


class DDWRTPlugin(FirmwarePlugin):

    # DD-WRT uses a flat layout with vendor-specific firmware filenames.
    # Example:
    #   /srv/tftpos/distros/ddwrt/2024/linksys-ea8300.bin
    #   /srv/tftpos/distros/ddwrt/2024/netgear-r7800.bin

    DISTRO_ROOT = "/srv/tftpos/distros"

    # DD-WRT uses year-based releases.
    _YEAR_RE = re.compile(r"^\d{4}$")

    @property
    def os_family(self) -> str:
        return "ddwrt"

    @property
    def supported_versions(self) -> list[str]:
        # Accept any year-format version instead of pinning to
        # a fixed list. Validation happens in validate_profile().
        return []

    def firmware_path(self, profile: ProvisionProfile) -> str:
        # DD-WRT firmware is vendor-specific. The profile's vendor
        # field identifies the target hardware.
        vendor = profile.vendor or "generic"
        return (
            f"{self.DISTRO_ROOT}/ddwrt/"
            f"{profile.os_version}/{vendor}.bin"
        )

    def validate_profile(self, profile: ProvisionProfile) -> list[str]:
        errors = super().validate_profile(profile)

        # Enforce year-based version format since supported_versions
        # is empty (base class skips the version check).
        if not self._YEAR_RE.match(profile.os_version):
            errors.append(
                f"os_version must be a 4-digit year, "
                f"got {profile.os_version!r}"
            )

        # DD-WRT firmware is always vendor-specific.
        if not profile.vendor:
            errors.append("vendor is required for DD-WRT")

        return errors
```

### How this differs from the OpenWRT plugin

- **supported_versions is empty** -- instead of listing every valid release, it returns an empty list so the base class skips its version check. Custom validation in `validate_profile()` enforces the year format instead. This is useful when versions follow a pattern rather than a fixed set.
- **firmware_path uses vendor** -- DD-WRT firmware files are per-device, so the path is built from `profile.vendor` rather than `profile.arch`.
- **No extract_from_image** -- DD-WRT firmware ships as standalone `.bin` files, not archives. The default `NotImplementedError` is fine.

## Registration

Three ways to make TftpOS find your plugin.

### Entry points (recommended for installable packages)

Add an entry point in your package's `pyproject.toml`:

```toml
[project.entry-points."tftpos.plugins"]
openwrt = "my_plugins.openwrt:OpenWRTPlugin"
ddwrt = "my_plugins.ddwrt:DDWRTPlugin"
```

Then call `discover()` on the registry:

```python
from tftpos.registry import PluginRegistry

registry = PluginRegistry()
registry.discover()  # Finds all tftpos.plugins entry points

plugin = registry.get("openwrt")
```

The entry point group must be `tftpos.plugins`. The key (left side) is arbitrary -- the registry uses the plugin's `os_family` property as the actual lookup key.

`discover()` silently skips entry points that fail to load, are not `FirmwarePlugin` subclasses, or are the abstract base class itself.

### load_builtins (for local plugins)

Pass a list of module names containing `FirmwarePlugin` subclasses:

```python
registry = PluginRegistry()
registry.load_builtins(["my_plugins.openwrt", "my_plugins.ddwrt"])
```

`load_builtins()` imports each module, scans it for concrete `FirmwarePlugin` subclasses (skips abstract classes), and registers them. Import failures are silently skipped.

This is the simplest approach when your plugins live in the same codebase and are not packaged separately.

### Manual register()

Register a specific class directly:

```python
from my_plugins.openwrt import OpenWRTPlugin

registry = PluginRegistry()
registry.register(OpenWRTPlugin)
```

`register()` instantiates the class and stores it by `os_family.lower()`. If you register two plugins with the same `os_family`, the second replaces the first.

### Checking what is registered

```python
print(registry.available)
# ['ddwrt', 'openwrt']  -- sorted list of os_family keys

plugin = registry.get("openwrt")
# Returns the OpenWRTPlugin instance, or raises ValueError
```

`get()` is case-insensitive: `registry.get("OpenWRT")` works the same as `registry.get("openwrt")`.

## Template Rendering

Plugins can render Jinja2 templates stored in `tftpos/templates/`. This is useful for generating configuration files, boot scripts, or provisioning manifests.

### Creating a template

Place your template in the `tftpos/templates/` directory with a `.j2` extension:

```jinja
{# tftpos/templates/openwrt-uci.j2 #}
{% if hostname %}
uci set system.@system[0].hostname='{{ hostname }}'
{% endif %}
{% if nameservers %}
{% for ns in nameservers %}
uci add_list dhcp.@dnsmasq[0].server='{{ ns }}'
{% endfor %}
{% endif %}
uci commit
```

### Rendering from your plugin

```python
def generate_config(self, profile: ProvisionProfile) -> str:
    context = {
        "hostname": profile.network.get("hostname", ""),
        "nameservers": profile.network.get("nameservers", []),
    }
    # Sanitize user-supplied values before rendering.
    context = self._sanitize_context(context)
    return self._render_template("openwrt-uci.j2", context)
```

### Sanitization

Always call `_sanitize_context()` before rendering templates that include user-supplied values. It validates:

- **hostname** -- must conform to RFC 952/1123 (alphanumeric and hyphens, labels 1-63 chars, no leading/trailing hyphens). Raises `ValueError` if invalid.
- **install_url** -- must use an allowed scheme (`http`, `https`, `ftp`, `nfs`, `tftp`). Raises `ValueError` if invalid.
- **packages** -- each entry must match `^[a-zA-Z0-9][a-zA-Z0-9._+:\-]*$`. Raises `ValueError` on the first invalid name.

Values not in these three keys pass through unchanged. If your context contains other user-supplied strings, validate them separately.

### XML autoescaping

Templates with `.xml` or `.xml.j2` extensions get automatic XML escaping of variables. Other template extensions render without autoescaping (plain text). This matches the Jinja2 `select_autoescape` configuration in the base class.

## Validation

### Extending validate_profile()

The base `validate_profile()` checks three things:

1. `profile.name` is not empty
2. `profile.os_family` matches `self.os_family`
3. `profile.os_version` is in `self.supported_versions` (if `supported_versions` is non-empty)

To add your own checks, override the method and call `super()` first:

```python
def validate_profile(self, profile: ProvisionProfile) -> list[str]:
    errors = super().validate_profile(profile)

    # Require a network configuration for this firmware type.
    if not profile.network:
        errors.append("network configuration is required")

    # Require at least one post-install script.
    if not profile.post_scripts:
        errors.append("at least one post_script is required")

    # Validate a custom extra field.
    if "flash_size" in profile.extra:
        try:
            size = int(profile.extra["flash_size"])
            if size < 4:
                errors.append("flash_size must be at least 4 (MB)")
        except (ValueError, TypeError):
            errors.append("flash_size must be an integer")

    return errors
```

### Error format

Each error is a plain string describing what is wrong. The engine collects all errors and reports them together, so return all errors you find rather than stopping at the first one.

### Calling validation

```python
plugin = registry.get("openwrt")
errors = plugin.validate_profile(profile)
if errors:
    for err in errors:
        print(f"  - {err}")
else:
    path = plugin.firmware_path(profile)
```

## Image Extraction

Override `extract_from_image()` when your firmware ships inside archives and you need to unpack it before serving.

```python
def extract_from_image(
    self, image_path: Path, dest: Path
) -> DistroAssets:
    import zipfile

    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(image_path, "r") as zf:
        zf.extractall(path=dest)

    kernel = dest / "firmware.bin"
    if not kernel.exists():
        raise FileNotFoundError(
            f"firmware.bin not found in {image_path}"
        )

    return DistroAssets(
        kernel_path=kernel,
        # initrd_path, repo_path, boot_loader_path, squashfs_path
        # are optional -- set them if the archive contains them.
    )
```

The method receives:

- **image_path** -- `Path` to the archive file on disk
- **dest** -- `Path` to the directory where extracted files should go

It returns a `DistroAssets` dataclass with these fields:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `kernel_path` | `Path` | yes | Path to the primary firmware/kernel binary |
| `initrd_path` | `Path` | no | Path to an initrd/initramfs, if applicable |
| `repo_path` | `Path` | no | Path to a local package repository |
| `boot_loader_path` | `Path` | no | Path to a bootloader binary |
| `squashfs_path` | `Path` | no | Path to a squashfs filesystem image |

If your firmware does not ship in archives, leave the default implementation alone. Calling it will raise `NotImplementedError` with a message naming the `os_family`.

## Testing Your Plugin

A pytest example covering the main plugin behaviors.

```python
"""Tests for the OpenWRT firmware plugin."""

import pytest

from tftpos.models import BootFirmware, ProvisionProfile
from tftpos.plugins.base import FirmwarePlugin
from tftpos.registry import PluginRegistry

# Import your plugin.
from my_plugins.openwrt import OpenWRTPlugin


class TestOpenWRTPlugin:

    def test_os_family(self):
        plugin = OpenWRTPlugin()
        assert plugin.os_family == "openwrt"

    def test_supported_versions(self):
        plugin = OpenWRTPlugin()
        assert "23.05" in plugin.supported_versions
        assert "24.10" in plugin.supported_versions

    def test_firmware_path_default(self):
        plugin = OpenWRTPlugin()
        profile = ProvisionProfile(
            name="router-01",
            os_family="openwrt",
            os_version="23.05",
            arch="x86_64",
            install_url="https://downloads.openwrt.org/releases/23.05.5/",
        )
        path = plugin.firmware_path(profile)
        assert path == (
            "/srv/tftpos/distros/openwrt/23.05/x86_64/firmware.bin"
        )

    def test_firmware_path_custom_filename(self):
        plugin = OpenWRTPlugin()
        profile = ProvisionProfile(
            name="router-02",
            os_family="openwrt",
            os_version="24.10",
            arch="aarch64",
            install_url="https://downloads.openwrt.org/releases/24.10/",
            extra={"firmware_filename": "sysupgrade.bin"},
        )
        path = plugin.firmware_path(profile)
        assert "sysupgrade.bin" in path

    def test_validate_profile_valid(self):
        plugin = OpenWRTPlugin()
        profile = ProvisionProfile(
            name="router-01",
            os_family="openwrt",
            os_version="23.05",
            arch="x86_64",
            install_url="https://downloads.openwrt.org/releases/23.05.5/",
        )
        errors = plugin.validate_profile(profile)
        assert errors == []

    def test_validate_profile_missing_install_url(self):
        plugin = OpenWRTPlugin()
        profile = ProvisionProfile(
            name="router-01",
            os_family="openwrt",
            os_version="23.05",
            arch="x86_64",
        )
        errors = plugin.validate_profile(profile)
        assert any("install_url" in e for e in errors)

    def test_validate_profile_bad_version(self):
        plugin = OpenWRTPlugin()
        profile = ProvisionProfile(
            name="router-01",
            os_family="openwrt",
            os_version="19.07",
            arch="x86_64",
            install_url="https://downloads.openwrt.org/",
        )
        errors = plugin.validate_profile(profile)
        assert any("unsupported version" in e for e in errors)

    def test_validate_profile_wrong_os_family(self):
        plugin = OpenWRTPlugin()
        profile = ProvisionProfile(
            name="router-01",
            os_family="ddwrt",
            os_version="23.05",
            arch="x86_64",
            install_url="https://downloads.openwrt.org/",
        )
        errors = plugin.validate_profile(profile)
        assert any("os_family mismatch" in e for e in errors)

    def test_validate_profile_bad_arch(self):
        plugin = OpenWRTPlugin()
        profile = ProvisionProfile(
            name="router-01",
            os_family="openwrt",
            os_version="23.05",
            arch="sparc64",
            install_url="https://downloads.openwrt.org/",
        )
        errors = plugin.validate_profile(profile)
        assert any("unsupported arch" in e for e in errors)


class TestPluginRegistration:

    def test_register_and_lookup(self):
        registry = PluginRegistry()
        registry.register(OpenWRTPlugin)
        assert "openwrt" in registry.available
        plugin = registry.get("openwrt")
        assert isinstance(plugin, OpenWRTPlugin)

    def test_case_insensitive_lookup(self):
        registry = PluginRegistry()
        registry.register(OpenWRTPlugin)
        assert registry.get("openwrt") is registry.get("OpenWRT")

    def test_unknown_os_family_raises(self):
        registry = PluginRegistry()
        with pytest.raises(ValueError, match="unknown os_family"):
            registry.get("nonexistent")

    def test_is_firmware_plugin_subclass(self):
        assert issubclass(OpenWRTPlugin, FirmwarePlugin)
```

Run with:

```bash
pytest tests/test_openwrt_plugin.py -v
```

## FirmwarePlugin vs OSPlugin

TftpOS defines `FirmwarePlugin`. PxeOS defines `OSPlugin`, which extends `FirmwarePlugin`. They serve different purposes.

### FirmwarePlugin (TftpOS -- `tftpos/plugins/base.py`)

For serving firmware files. Routers, embedded devices, IoT hardware -- anything where provisioning means "serve this binary over TFTP."

- `os_family` -- identifies the firmware family
- `supported_versions` -- version whitelist
- `firmware_path(profile)` -- returns the path to the firmware file
- `validate_profile(profile)` -- validates the profile
- `extract_from_image(image_path, dest)` -- optional archive extraction

### OSPlugin (PxeOS -- `pxeos/plugins/base.py`)

Extends `FirmwarePlugin` with PXE boot methods for full OS installations. Operating systems that boot via iPXE with kernel + initrd + autoinstall templates.

OSPlugin adds these abstract methods:

- `boot_assets(profile)` -- returns kernel, initrd, and boot arguments for iPXE script generation
- `generate_autoinstall(profile)` -- renders the autoinstall template (kickstart, preseed, autoinstall, autoyast)
- `autoinstall_filename()` -- returns the filename for the autoinstall file (e.g. `"ks.cfg"`, `"preseed.cfg"`)
- `extract_from_iso(mount_path, dest)` -- extracts boot assets from a mounted ISO

OSPlugin also provides a default `firmware_path()` that delegates to `boot_assets().kernel`, so you typically do not override `firmware_path()` in an OSPlugin subclass.

### Which one to use

| Scenario | Use |
|----------|-----|
| Router firmware (OpenWRT, DD-WRT, FreshTomato) | `FirmwarePlugin` (TftpOS) |
| Embedded device firmware | `FirmwarePlugin` (TftpOS) |
| IoT device images | `FirmwarePlugin` (TftpOS) |
| Linux OS installer (Fedora, Ubuntu, Debian) | `OSPlugin` (PxeOS) |
| Windows installer via iPXE | `OSPlugin` (PxeOS) |
| BSD installer via iPXE | `OSPlugin` (PxeOS) |
| Anything with kernel + initrd + autoinstall | `OSPlugin` (PxeOS) |

If you are writing a plugin for TftpOS (this project), implement `FirmwarePlugin`. If you need iPXE scripts, autoinstall templates, or ISO extraction, you need PxeOS and its `OSPlugin`.
