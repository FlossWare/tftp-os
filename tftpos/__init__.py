"""TftpOS -- TFTP-based firmware provisioning library.

Public API surface
==================

The modules listed in ``__all__`` form the stable public API.  Breaking
changes to these modules will be documented and versioned.  During the
0.y series the API may still evolve, but we will minimise churn.

Core modules (always required for firmware path resolution)::

    tftpos.config       -- TftpOSConfig, load_config, load_hosts, load_profile
    tftpos.engine       -- FirmwareEngine
    tftpos.matcher      -- HostMatcher
    tftpos.registry     -- PluginRegistry
    tftpos.plugins.base -- FirmwarePlugin (abstract base class)
    tftpos.plugins.static -- StaticFirmwarePlugin (built-in plugin)
    tftpos.models       -- HostRule, ProvisionProfile, BootFirmware,
                           DistroAssets, CloudImage
    tftpos.state        -- ProvisionTracker, ProvisionState, ProvisionRecord
    tftpos.errors       -- TftpOSError, ConfigError, ValidationError,
                           ProvisionError, PluginError, format_error
    tftpos.db           -- StorageBackend, SQLAlchemyBackend, SQLiteBackend,
                           JSONBackend, MemoryBackend
    tftpos.validation   -- input validation and sanitization helpers
    tftpos.logging_config -- logging setup used by core modules
    tftpos.staging      -- stage, unstage, list_staged (tftp_root management)

Extended modules (shipped but not required for basic usage)::

    tftpos.auth,
    tftpos.tls, tftpos.secrets, tftpos.cache,
    tftpos.rate_limit, tftpos.named_objects,
    tftpos.cloud_init, tftpos.cloud_image, tftpos.iso_detect,
    tftpos.mnemonics, tftpos.repo_mirror, tftpos.cluster,
    tftpos.console, tftpos.power, tftpos.client.*

App-layer modules (candidates for migration to flossware-tftp-os)::

    tftpos.webhooks, tftpos.metrics, tftpos.audit

See ``docs/CONTRACT.md`` for the full contract and stability guarantees.
"""

from importlib.metadata import version

__version__ = version("tftpos")

# Public API submodules -- these are the stable surface.
# Users should import directly from these submodules
# (e.g. ``from tftpos.engine import FirmwareEngine``).
__all__ = [
    "config",
    "engine",
    "matcher",
    "registry",
    "models",
    "state",
    "errors",
    "db",
    "validation",
    "logging_config",
    "staging",
    "plugins",
]
