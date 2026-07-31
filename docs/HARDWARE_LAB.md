# Hardware / System-Lab Checklist

## Purpose

tftp-os has two levels of TFTP testing:

1. **tftpy lab proof** (`tests/test_lab_tftp.py`) -- uses tftpy, a pure-Python
   TFTP implementation, to prove the full MAC-resolve-stage-transfer-checksum
   loop entirely in user space on localhost. No root, no system daemon, no real
   device. This is what CI runs.

2. **System tftpd + real device** -- uses a production TFTP daemon (tftpd-hpa,
   dnsmasq, atftpd) serving files to a physical device or VM that PXE boots.
   This is what this checklist covers.

The tftpy lab proof validates library correctness. The system-lab test validates
that tftp-os works end-to-end in a real deployment scenario where the TFTP
daemon, network stack, and client firmware are all involved.

---

## Prerequisites

| Requirement | Purpose |
|-------------|---------|
| System TFTP daemon (tftpd-hpa, dnsmasq, or atftpd) | Serve files over the network |
| Real firmware file (sysupgrade.bin, vmlinuz, pxeboot, etc.) | Content to serve |
| Device or VM that PXE boots | Client that fetches firmware via TFTP |
| Network connectivity between TFTP server and client | Layer 2/3 path |
| tftp-os installed (`pip install -e .`) | Library under test |

See [DEPLOYMENT.md](DEPLOYMENT.md) for TFTP server installation and
configuration.

---

## Checklist

### Step 1: Place firmware under the distro layout

```bash
mkdir -p /srv/tftpos/distros/openwrt/23.05
cp sysupgrade.bin /srv/tftpos/distros/openwrt/23.05/firmware.bin
```

Record the source checksum:

```bash
sha256sum /srv/tftpos/distros/openwrt/23.05/firmware.bin
```

### Step 2: Configure plugin and engine

```python
python3 -c "
from tftpos.registry import PluginRegistry
from tftpos.plugins.static import StaticFirmwarePlugin
from tftpos.matcher import HostMatcher
from tftpos.models import HostRule
from tftpos.config import TftpOSConfig
from tftpos.engine import FirmwareEngine
from pathlib import Path

registry = PluginRegistry()
registry.register(
    StaticFirmwarePlugin,
    distro_root='/srv/tftpos/distros',
    os_family='openwrt',
    supported_versions=['23.05'],
)
rules = [
    HostRule(
        profile='lab-router',
        os_family='openwrt',
        os_version='23.05',
        mac='aa:bb:cc:dd:ee:ff',
    ),
]
matcher = HostMatcher(rules)
config = TftpOSConfig(
    tftp_root=Path('/srv/tftp'),
    data_dir=Path('/var/lib/tftpos'),
)
engine = FirmwareEngine(registry, matcher, config)

staged = engine.stage(mac='aa:bb:cc:dd:ee:ff')
print(f'Staged: {staged}')
"
```

### Step 3: Configure system tftpd

**tftpd-hpa:** Set `TFTP_DIRECTORY="/srv/tftp"` in `/etc/default/tftpd-hpa`
(Debian/Ubuntu) or `/etc/sysconfig/tftp` (Fedora/RHEL), then restart:

```bash
sudo systemctl restart tftpd-hpa
```

**dnsmasq:** Add to `/etc/dnsmasq.d/tftpos.conf`:

```
enable-tftp
tftp-root=/srv/tftp
```

Then restart:

```bash
sudo systemctl restart dnsmasq
```

See [DEPLOYMENT.md](DEPLOYMENT.md) for full TFTP server setup.

### Step 4: Client fetch

From the TFTP server host (smoke test):

```bash
tftp localhost -c get <staged_filename>
```

From a PXE client: point the device's DHCP `next-server` at this TFTP server
and boot via network.

### Step 5: Verify

```bash
sha256sum <downloaded_file>
sha256sum /srv/tftpos/distros/openwrt/23.05/firmware.bin
# checksums must match
```

### Step 6: Record result

Note the following for each test run:

- OS family and version of the firmware
- Device or VM model
- Firmware file size
- TFTP daemon used (tftpd-hpa, dnsmasq, atftpd)
- Pass or fail
- Date

Add the result to the Lab Proof Status table below.

---

## Lab Proof Status

| Test Type | Date | Result | Notes |
|-----------|------|--------|-------|
| tftpy localhost (random bytes) | 2026-07-30 | PASS | test_lab_tftp.py |
| tftpy localhost (DD-WRT R9000) | 2026-07-31 | PASS | 42 MB, SHA-256 verified |
| tftpy localhost (Fedora 44 vmlinuz) | 2026-07-31 | PASS | 17.6 MB, SHA-256 verified |
| tftpy localhost (FreeBSD 14.4 pxeboot) | 2026-07-31 | PASS | 0.4 MB, SHA-256 verified |
| System tftpd + real device | -- | PENDING | -- |
