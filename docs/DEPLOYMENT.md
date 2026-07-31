# tftp-os Deployment Guide

tftp-os is a pure Python library. It has no CLI, no REST API server, and no
daemon. You install it as a dependency, configure it with TOML files, and pair
it with an existing TFTP server. This guide covers everything needed to get a
working environment.

---

## Installation

tftp-os is published to PyPI. Install with pip:

```bash
pip install tftpos
```

Install with optional extras:

```bash
# TLS support (auto-generated self-signed certificates)
pip install "tftpos[tls]"

# PostgreSQL backend
pip install "tftpos[postgres]"

# MariaDB / MySQL backend
pip install "tftpos[mysql]"

# Multiple extras
pip install "tftpos[tls,postgres]"
```

### Extras reference

| Extra          | Adds              | Purpose                                    |
|----------------|-------------------|--------------------------------------------|
| `tls`          | `cryptography`    | Self-signed TLS certificate generation     |
| `postgres`     | `psycopg2-binary` | PostgreSQL database backend                |
| `mysql`        | `pymysql`         | MariaDB / MySQL database backend           |
| `power`        | --                | BMC/IPMI/Redfish power control             |
| `hypervisor`   | --                | libvirt, bhyve, Hyper-V, VMM backends      |
| `cloud`        | --                | cloud-init, cloud-image handling           |
| `cluster`      | --                | cluster provisioning, repo mirrors         |
| `observability`| --                | metrics, audit, webhooks, console          |
| `all`          | all of the above  | convenience: every extra                   |
| `dev`          | pytest, ruff, etc | development and testing tools              |

### Install from source

```bash
git clone https://github.com/FlossWare/tftp-os.git
cd tftp-os
pip install -e .
```

For development (linting, testing, type checking):

```bash
pip install -e ".[dev]"
```

---

## Directory Layout

The default paths used by tftp-os (all configurable in `tftpos.toml`):

| Path                    | Purpose                                        |
|-------------------------|-------------------------------------------------|
| `/etc/tftpos/`          | Configuration files and secrets                 |
| `/etc/tftpos/profiles/` | Provision profile TOML files                    |
| `/srv/tftp/`            | TFTP root directory (files served by TFTP)      |
| `/srv/tftpos/distros/`  | Firmware images staged for provisioning         |

Create the directories:

```bash
sudo mkdir -p /etc/tftpos/profiles /srv/tftp /srv/tftpos/distros
```

---

## Configuration

tftp-os reads its configuration from a TOML file (typically
`/etc/tftpos/tftpos.toml`). Below is a minimal example:

```toml
[paths]
tftp_root = "/srv/tftp"
distro_root = "/srv/tftpos/distros"
data_dir = "/etc/tftpos"

[database]
backend = "sqlite"
url = "sqlite:///tftpos.db"

[tls]
auto_generate = true

[logging]
level = "INFO"
json_format = false

[audit]
enabled = true
```

### Host rules

Define host-to-profile mappings in `hosts.toml`:

```toml
[[hosts]]
mac = "aa:bb:cc:dd:ee:ff"
profile = "openwrt-23"
```

### Provision profiles

Place profile files in the `profiles/` directory (e.g.,
`/etc/tftpos/profiles/openwrt-23.toml`). Each profile defines the firmware
image, boot parameters, and any device-specific settings.

---

## Database Setup

### SQLite (default)

No setup required. The database file is created automatically at the path
specified in the `[database]` section of your configuration (defaults to
`sqlite:///tftpos.db` in the working directory).

### PostgreSQL

Install tftp-os with the postgres extra:

```bash
pip install "tftpos[postgres]"
```

Create the database and user:

```bash
sudo -u postgres createuser tftpos
sudo -u postgres createdb -O tftpos tftpos
sudo -u postgres psql -c "ALTER USER tftpos PASSWORD 'changeme';"
```

Or with SQL:

```sql
CREATE USER tftpos WITH PASSWORD 'changeme';
CREATE DATABASE tftpos OWNER tftpos;
```

Set the database URL in `tftpos.toml`:

```toml
[database]
backend = "postgresql"
url = "postgresql://tftpos:changeme@localhost:5432/tftpos"
```

### MariaDB / MySQL

Install tftp-os with the mysql extra:

```bash
pip install "tftpos[mysql]"
```

Create the database and user:

```sql
CREATE DATABASE tftpos CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'tftpos'@'localhost' IDENTIFIED BY 'changeme';
GRANT ALL PRIVILEGES ON tftpos.* TO 'tftpos'@'localhost';
FLUSH PRIVILEGES;
```

Set the database URL in `tftpos.toml`:

```toml
[database]
backend = "mariadb"
url = "mysql+pymysql://tftpos:changeme@localhost:3306/tftpos"
```

---

## TFTP Server Integration

tftp-os does not implement a TFTP server. It manages the files in the TFTP root
directory. You need a separate TFTP server to serve those files to PXE clients.

### dnsmasq

dnsmasq can act as both DHCP and TFTP server:

```bash
sudo tee /etc/dnsmasq.d/tftpos.conf > /dev/null << 'EOF'
# DHCP range
dhcp-range=192.168.1.100,192.168.1.200,12h

# TFTP server
enable-tftp
tftp-root=/srv/tftp

# PXE boot
dhcp-boot=pxelinux.0
EOF
```

Restart dnsmasq:

```bash
sudo systemctl restart dnsmasq
```

### tftpd-hpa

Install tftpd-hpa:

```bash
sudo apt install tftpd-hpa            # Debian/Ubuntu
sudo dnf install tftp-server           # Fedora/RHEL
```

Edit `/etc/default/tftpd-hpa` (Debian/Ubuntu) or `/etc/sysconfig/tftp`
(Fedora/RHEL):

```bash
TFTP_USERNAME="tftp"
TFTP_DIRECTORY="/srv/tftp"
TFTP_ADDRESS="0.0.0.0:69"
TFTP_OPTIONS="--secure"
```

The TFTP server user needs read access to `/srv/tftp`. If tftp-os writes files
as a different user, set appropriate permissions:

```bash
sudo chmod -R a+r /srv/tftp
```

Start tftpd-hpa:

```bash
sudo systemctl enable tftpd-hpa
sudo systemctl start tftpd-hpa
```

### atftpd

Install atftpd:

```bash
sudo apt install atftpd                # Debian/Ubuntu
```

Configure `/etc/default/atftpd`:

```bash
USE_INETD=false
OPTIONS="--daemon --port 69 --tftpd-timeout 300 --retry-timeout 5 --maxthread 100 --verbose=5 /srv/tftp"
```

Start atftpd:

```bash
sudo systemctl enable atftpd
sudo systemctl start atftpd
```

### ISC DHCP (next-server directive)

If you use a standalone TFTP server, point DHCP clients to it by adding
`next-server` to your ISC DHCP configuration (`/etc/dhcp/dhcpd.conf`):

```conf
subnet 192.168.1.0 netmask 255.255.255.0 {
    range 192.168.1.100 192.168.1.200;
    option routers 192.168.1.1;

    next-server 192.168.1.10;          # IP of your TFTP server
    filename "pxelinux.0";
}
```

---

## Staging Firmware

tftp-os provides the `tftpos.staging` module to place resolved firmware files
under `tftp_root` where your TFTP server can serve them.

```python
from pathlib import Path
from tftpos.staging import stage, unstage, list_staged

# Stage a firmware file (copies or symlinks into tftp_root)
staged = stage(
    firmware_path=Path("/srv/tftpos/distros/openwrt/23.05/firmware.bin"),
    tftp_root=Path("/srv/tftp"),
    name="openwrt-23.05.bin",
    symlink=True,
)

# List all staged files
for path in list_staged(Path("/srv/tftp")):
    print(path)

# Remove a staged file
unstage(staged)
```

The `FirmwareEngine.stage()` convenience method combines `serve()` and
`stage()` in one call:

```python
staged_path = engine.stage(mac="aa:bb:cc:dd:ee:ff")
```

Staging uses atomic tmp-then-rename to avoid serving partial files.

---

## Backup

### SQLite

```bash
sqlite3 /path/to/tftpos.db ".backup '/backup/tftpos-$(date +%Y%m%d).db'"
```

### PostgreSQL

```bash
pg_dump -U tftpos -h localhost tftpos > /backup/tftpos-$(date +%Y%m%d).sql
```

### Configuration

```bash
sudo tar czf /backup/tftpos-config-$(date +%Y%m%d).tar.gz /etc/tftpos/
```

---

## Troubleshooting

### Permissions on /srv/tftp

If PXE clients cannot download files, check that the TFTP server user can read
what tftp-os has written:

```bash
ls -la /srv/tftp/
sudo chmod -R a+r /srv/tftp/
```

### Database connection failures

Check that the database is running and reachable:

```bash
# PostgreSQL
pg_isready -h localhost -p 5432

# MySQL / MariaDB
mysqladmin -u tftpos -p ping
```

Common mistakes:
- Wrong port or hostname in the connection URL
- Database or user not created yet
- Driver package not installed (`psycopg2-binary` or `pymysql`)

### TLS certificate generation fails

Auto-generated self-signed certificates require the `cryptography` package:

```bash
pip install "tftpos[tls]"
```

### Testing TFTP connectivity

```bash
tftp localhost -c get pxelinux.0
```

If this fails, check that your TFTP server (tftpd-hpa, dnsmasq, atftpd) is
running and that the expected file exists and is readable under `/srv/tftp/`.

---

## Web Application Deployment

tftp-os is a library only. For a deployable application with web, desktop, and
mobile frontends, see
[flossware-tftp-os](https://github.com/FlossWare/flossware-tftp-os). That
repository includes systemd service configuration, reverse proxy setup, and
full application deployment instructions.

---

## See Also

- [docs/HARDWARE_LAB.md](HARDWARE_LAB.md) -- hardware/system-lab checklist for real-device testing with system tftpd
