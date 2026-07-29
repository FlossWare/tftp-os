"""Cloud-init config generation and config drive ISO creation."""

from __future__ import annotations

import os
import subprocess
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from jinja2 import Environment, PackageLoader

from tftpos.models import ProvisionProfile


@dataclass
class CloudInitConfig:
    user_data: str
    meta_data: str
    network_config: str = ""


_env = Environment(
    loader=PackageLoader("tftpos", "templates"),
    keep_trailing_newline=True,
    trim_blocks=True,
    lstrip_blocks=True,
)


def generate(profile: ProvisionProfile) -> CloudInitConfig:
    user_data = _render_user_data(profile)
    meta_data = _render_meta_data(profile)
    network_config = _render_network_config(profile)
    return CloudInitConfig(
        user_data=user_data,
        meta_data=meta_data,
        network_config=network_config,
    )


def _render_user_data(profile: ProvisionProfile) -> str:
    template = _env.get_template("cloud-init-userdata.j2")
    network = profile.network
    extra = profile.extra

    users = extra.get("users", [])
    if not users:
        default_user = extra.get("user", "admin")
        users = [{
            "name": default_user,
            "groups": extra.get("groups", ["sudo", "users"]),
            "shell": extra.get("shell", "/bin/bash"),
            "sudo": extra.get("sudo", "ALL=(ALL) NOPASSWD:ALL"),
            "lock_passwd": not extra.get("password"),
        }]
        ssh_keys = extra.get("ssh_authorized_keys", [])
        if ssh_keys:
            users[0]["ssh_authorized_keys"] = ssh_keys
        password = extra.get("password")
        if password:
            users[0]["passwd"] = _hash_password(password)

    return template.render(
        hostname=network.get("hostname", profile.name),
        fqdn=network.get("fqdn", f"{network.get('hostname', profile.name)}.local"),
        manage_etc_hosts=extra.get("manage_etc_hosts", True),
        users=users,
        packages=profile.packages,
        post_scripts=profile.post_scripts,
        write_files=extra.get("write_files", []),
        timezone=extra.get("timezone", "UTC"),
        locale=extra.get("locale", "en_US.UTF-8"),
        ssh_pwauth=extra.get("ssh_pwauth", False),
        disable_root=extra.get("disable_root", True),
        final_message=extra.get("final_message", "Cloud-init complete. System is ready."),
        extra_config=extra.get("cloud_config", {}),
    )


def _render_meta_data(profile: ProvisionProfile) -> str:
    template = _env.get_template("cloud-init-metadata.j2")
    network = profile.network
    extra = profile.extra
    hostname = network.get("hostname", profile.name)

    return template.render(
        instance_id=extra.get("instance_id", f"{hostname}-{profile.os_version}"),
        hostname=hostname,
        extra_meta=extra.get("meta_data", {}),
    )


def _render_network_config(profile: ProvisionProfile) -> str:
    network = profile.network
    template = _env.get_template("cloud-init-network.j2")

    if not network or network.get("method", "dhcp") == "dhcp":
        return template.render(
            method="dhcp",
            interface=network.get("device", "eth0"),
        )

    return template.render(
        method="static",
        interface=network.get("device", "eth0"),
        address=network.get("address", ""),
        gateway=network.get("gateway", ""),
        nameservers=network.get("nameservers", ["8.8.8.8", "8.8.4.4"]),
        search_domains=network.get("search_domains", []),
        mtu=network.get("mtu"),
    )


def generate_user_data(profile: ProvisionProfile) -> str:
    """Generate cloud-init user-data YAML from a provision profile."""
    return _render_user_data(profile)


def generate_meta_data(
    hostname: str,
    instance_id: Optional[str] = None,
) -> str:
    """Generate cloud-init meta-data YAML.

    Parameters
    ----------
    hostname:
        The hostname for the instance.
    instance_id:
        Optional instance ID; defaults to ``hostname``.
    """
    template = _env.get_template("cloud-init-metadata.j2")
    return template.render(
        instance_id=instance_id or hostname,
        hostname=hostname,
        extra_meta={},
    )


def generate_network_config(profile: ProvisionProfile) -> str:
    """Generate cloud-init v2 netplan network config from a profile."""
    return _render_network_config(profile)


def create_config_drive(
    profile: ProvisionProfile,
    output_path: Path,
) -> Path:
    config = generate(profile)
    return write_config_drive(config, output_path)


def write_config_drive(
    config: CloudInitConfig,
    output_path: Path,
) -> Path:
    work_dir = Path(tempfile.mkdtemp(prefix="tftpos_cidata_"))
    os.chmod(work_dir, 0o700)
    try:
        ud = work_dir / "user-data"
        ud.write_text(config.user_data)
        os.chmod(ud, 0o600)
        md = work_dir / "meta-data"
        md.write_text(config.meta_data)
        if config.network_config:
            (work_dir / "network-config").write_text(config.network_config)

        iso_tool = _find_iso_tool()
        if iso_tool is None:
            raise RuntimeError(
                "no ISO creation tool found "
                "(install genisoimage, mkisofs, or xorriso)"
            )

        output_path.parent.mkdir(parents=True, exist_ok=True)

        if "xorriso" in iso_tool:
            cmd = [
                iso_tool, "-as", "genisoimage",
                "-output", str(output_path),
                "-volid", "cidata",
                "-joliet", "-rock",
                str(work_dir),
            ]
        else:
            cmd = [
                iso_tool,
                "-output", str(output_path),
                "-volid", "cidata",
                "-joliet", "-rock",
                str(work_dir),
            ]

        subprocess.run(cmd, check=True, capture_output=True)
        return output_path
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _find_iso_tool() -> Optional[str]:
    for tool in ("genisoimage", "mkisofs", "xorriso"):
        if shutil.which(tool):
            return tool
    return None


def _hash_password(plaintext: str) -> str:
    import hashlib
    import secrets

    salt = secrets.token_hex(8)
    hashed = hashlib.sha512(
        (salt + plaintext).encode()
    ).hexdigest()
    return f"$6${salt}${hashed}"
