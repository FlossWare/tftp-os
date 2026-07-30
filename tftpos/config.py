"""Configuration loading from TOML files."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from tftpos.logging_config import LoggingConfig
from tftpos.models import (
    AuditConfig,
    BootFirmware,
    HostRule,
    ProvisionProfile,
    WebhookConfig,
)

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib  # type: ignore[no-redef]


@dataclass
class DatabaseConfig:
    """Database backend configuration.

    Supports ``sqlite`` (default), ``postgresql``, and ``mariadb``.
    The *url* is a SQLAlchemy connection string.
    """

    backend: str = "sqlite"
    url: str = "sqlite:///tftpos.db"


@dataclass
class RateLimitSettings:
    """Rate-limiting configuration (disabled by default)."""

    enabled: bool = False
    # TFTP endpoints (boot/autoinstall) -- higher limits for machine traffic
    tftp_requests_per_minute: float = 300.0
    tftp_burst: int = 50
    # General API endpoints
    api_requests_per_minute: float = 60.0
    api_burst: int = 20
    # Auth endpoints -- stricter to prevent brute force
    auth_requests_per_minute: float = 10.0
    auth_burst: int = 5


@dataclass
class TftpOSConfig:
    server_host: str = "0.0.0.0"
    server_port: int = 8443
    tls_cert: Optional[Path] = None
    tls_key: Optional[Path] = None
    tls_auto_generate: bool = True
    tftp_root: Path = field(
        default_factory=lambda: Path("/srv/tftp")
    )
    distro_root: Path = field(
        default_factory=lambda: Path("/srv/tftpos/distros")
    )
    data_dir: Path = field(
        default_factory=lambda: Path("/etc/tftpos")
    )
    auth_enabled: bool = False
    service_name: str = "tftpos"
    enable_discovery: bool = False
    rate_limit: RateLimitSettings = field(
        default_factory=RateLimitSettings
    )
    logging: LoggingConfig = field(
        default_factory=LoggingConfig
    )
    audit: AuditConfig = field(
        default_factory=AuditConfig
    )
    database: DatabaseConfig = field(
        default_factory=DatabaseConfig
    )
    webhooks: List[WebhookConfig] = field(
        default_factory=list
    )


def load_config(path: Path) -> TftpOSConfig:
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(
            f"malformed config file {path}: {exc}"
        ) from exc
    except OSError as exc:
        raise ValueError(
            f"cannot read config file {path}: {exc}"
        ) from exc

    server = data.get("server", {})
    paths = data.get("paths", {})
    auth = data.get("auth", {})
    discovery = data.get("discovery", {})
    rl = data.get("rate_limit", {})
    log = data.get("logging", {})
    audit_data = data.get("audit", {})
    tls_section = data.get("tls", {})
    db_section = data.get("database", {})

    # [tls] section takes precedence; fall back to server.tls_*
    tls_cert = tls_section.get("cert") or server.get("tls_cert")
    tls_key = tls_section.get("key") or server.get("tls_key")
    tls_auto_generate = tls_section.get("auto_generate", True)

    rate_limit = RateLimitSettings(
        enabled=rl.get("enabled", False),
        tftp_requests_per_minute=float(
            rl.get("tftp_requests_per_minute", 300)
        ),
        tftp_burst=int(rl.get("tftp_burst", 50)),
        api_requests_per_minute=float(
            rl.get("api_requests_per_minute", 60)
        ),
        api_burst=int(rl.get("api_burst", 20)),
        auth_requests_per_minute=float(
            rl.get("auth_requests_per_minute", 10)
        ),
        auth_burst=int(rl.get("auth_burst", 5)),
    )

    log_file_raw = log.get("log_file")
    logging_config = LoggingConfig(
        level=log.get("level", "INFO"),
        json_format=log.get("json_format", False),
        log_file=Path(log_file_raw) if log_file_raw else None,
        max_bytes=int(log.get("max_bytes", 10_485_760)),
        backup_count=int(log.get("backup_count", 5)),
        syslog_enabled=log.get("syslog_enabled", False),
        syslog_address=log.get("syslog_address", "/dev/log"),
        journald_enabled=log.get("journald_enabled", False),
    )

    audit_log_file_raw = audit_data.get("log_file")
    audit_config = AuditConfig(
        enabled=audit_data.get("enabled", True),
        log_file=(
            Path(audit_log_file_raw)
            if audit_log_file_raw
            else None
        ),
        max_bytes=int(
            audit_data.get("max_bytes", 52_428_800)
        ),
        backup_count=int(
            audit_data.get("backup_count", 10)
        ),
        log_to_stdout=audit_data.get("log_to_stdout", False),
        buffer_size=int(
            audit_data.get("buffer_size", 1000)
        ),
        syslog_enabled=audit_data.get(
            "syslog_enabled", False
        ),
        syslog_address=audit_data.get(
            "syslog_address", "/dev/log"
        ),
    )

    database_config = DatabaseConfig(
        backend=db_section.get("backend", "sqlite"),
        url=db_section.get("url", "sqlite:///tftpos.db"),
    )

    webhooks_data = data.get("webhooks", [])
    webhooks: List[WebhookConfig] = []
    for wh in webhooks_data:
        webhooks.append(
            WebhookConfig(
                url=wh["url"],
                events=wh.get("events", []),
                secret=wh.get("secret", ""),
                retry_count=int(wh.get("retry_count", 3)),
                timeout=float(wh.get("timeout", 10.0)),
            )
        )

    return TftpOSConfig(
        server_host=server.get("host", "0.0.0.0"),
        server_port=server.get("port", 8443),
        tls_cert=Path(tls_cert) if tls_cert else None,
        tls_key=Path(tls_key) if tls_key else None,
        tls_auto_generate=tls_auto_generate,
        tftp_root=Path(paths.get("tftp_root", "/srv/tftp")),
        distro_root=Path(
            paths.get("distro_root", "/srv/tftpos/distros")
        ),
        data_dir=Path(paths.get("data_dir", "/etc/tftpos")),
        auth_enabled=auth.get("enabled", False),
        service_name=discovery.get("service_name", "tftpos"),
        enable_discovery=discovery.get("enabled", False),
        rate_limit=rate_limit,
        logging=logging_config,
        audit=audit_config,
        database=database_config,
        webhooks=webhooks,
    )


def load_hosts(path: Path) -> List[HostRule]:
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(
            f"malformed hosts file {path}: {exc}"
        ) from exc

    rules: List[HostRule] = []
    for entry in data.get("host", []):
        rules.append(
            HostRule(
                profile=entry["profile"],
                os_family=entry["os_family"],
                os_version=entry["os_version"],
                vendor=entry.get("vendor", ""),
                priority=entry.get("priority", 100),
                mac=entry.get("mac"),
                mac_prefix=entry.get("mac_prefix"),
                hostname_pattern=entry.get("hostname_pattern"),
                subnet=entry.get("subnet"),
                serial=entry.get("serial"),
                group=entry.get("group"),
                arch=entry.get("arch"),
                bmc_host=entry.get("bmc_host"),
                bmc_user=entry.get("bmc_user"),
                bmc_password=entry.get("bmc_password"),
                bmc_driver=entry.get("bmc_driver"),
                deploy_mode=entry.get("deploy_mode", "tftp"),
                console_type=entry.get("console_type"),
                console_endpoint=entry.get("console_endpoint"),
            )
        )
    return rules


def load_profile(path: Path) -> ProvisionProfile:
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(
            f"malformed profile file {path}: {exc}"
        ) from exc

    profile = data.get("profile", data)
    firmware_str = profile.get("firmware", "bios").lower()
    firmware = (
        BootFirmware.UEFI
        if firmware_str == "uefi"
        else BootFirmware.BIOS
    )

    return ProvisionProfile(
        name=profile["name"],
        os_family=profile["os_family"],
        os_version=profile["os_version"],
        vendor=profile.get("vendor", ""),
        arch=profile.get("arch", "x86_64"),
        firmware=firmware,
        install_url=profile.get("install_url", ""),
        autoinstall_url=profile.get("autoinstall_url", ""),
        network=profile.get("network", {}),
        disk=profile.get("disk", {}),
        packages=profile.get("packages", []),
        post_scripts=profile.get("post_scripts", []),
        extra=profile.get("extra", {}),
        ipxe_commands=profile.get("ipxe_commands", []),
        dhcp_options=profile.get("dhcp_options", {}),
    )
