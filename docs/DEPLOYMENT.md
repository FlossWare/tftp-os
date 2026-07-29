# TftpOS Deployment Runbook

**Status: Alpha software. Never deployed in production.**

This document covers installation, configuration, and operation of TftpOS. Commands are written to be copy-pasted directly. Adjust paths and values to match your environment.

---

## Installation

TftpOS is not yet published to PyPI. Install from a local checkout.

Basic install:

```bash
git clone https://github.com/FlossWare/TftpOS.git
cd TftpOS
pip install -e .
```

Install with optional dependencies (API server, TLS support, PostgreSQL backend):

```bash
pip install -e ".[api,tls,postgres]"
```

- `api` — pulls in uvicorn and fastapi for the REST API server
- `tls` — pulls in cryptography for auto-generated self-signed certificates
- `postgres` — pulls in psycopg2 for PostgreSQL database support

---

## Directory Layout

- `/etc/tftpos/` — configuration files, secrets, auth keys
- `/etc/tftpos/profiles/` — provision profile TOML files
- `/srv/tftp/` — TFTP root directory (files served by the TFTP server)
- `/srv/tftpos/distros/` — firmware images staged for provisioning

Create the directory tree:

```bash
sudo mkdir -p /etc/tftpos/profiles /srv/tftp /srv/tftpos/distros
```

Set ownership so the service user can write to the TFTP root and distros directory:

```bash
sudo chown -R tftpos:tftpos /srv/tftp /srv/tftpos
```

If you do not have a dedicated `tftpos` user, create one:

```bash
sudo useradd -r -s /usr/sbin/nologin -d /srv/tftpos tftpos
```

---

## systemd Service

> **Note:** Neither the TftpOS CLI (`tftpos`) nor a REST API server are implemented yet. The `pyproject.toml` declares a `tftpos` entry point, and FastAPI is available as an optional dependency, but no `tftpos/cli.py` or `tftpos/api.py` module exists. The systemd unit below is a **placeholder** for when these are implemented.

```bash
sudo tee /etc/systemd/system/tftpos.service > /dev/null << 'EOF'
[Unit]
Description=TftpOS Firmware Provisioning
After=network.target

[Service]
Type=simple
User=tftpos
Group=tftpos
WorkingDirectory=/srv/tftpos
# Placeholder -- update ExecStart when CLI or API server is implemented
# ExecStart=/usr/local/bin/tftpos --config /etc/tftpos/tftpos.toml
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable tftpos.service
sudo systemctl start tftpos.service
```

Check status:

```bash
sudo systemctl status tftpos.service
```

View logs:

```bash
sudo journalctl -u tftpos.service -f
```

---

## Firewall

TftpOS itself does not serve TFTP traffic. Ports to open depend on your setup:

- **UDP 69** — for whatever TFTP server you run (tftpd-hpa, dnsmasq, etc.)
- **TCP 8443** — for the TftpOS API server (when implemented; not yet available)
- **TCP 80** — if using Let's Encrypt standalone mode for certificate issuance
- **TCP 443** — if using a reverse proxy with TLS termination

### firewalld

```bash
sudo firewall-cmd --permanent --add-port=69/udp
sudo firewall-cmd --permanent --add-port=8443/tcp
sudo firewall-cmd --reload
```

### iptables

```bash
sudo iptables -A INPUT -p udp --dport 69 -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 8443 -j ACCEPT
sudo iptables-save | sudo tee /etc/iptables/rules.v4
```

### nftables

```nft
table inet filter {
    chain input {
        udp dport 69 accept
        tcp dport 8443 accept
    }
}
```

Apply:

```bash
sudo nft -f /etc/nftables.conf
```

---

## TLS

### Auto-generated self-signed certificate (default)

When `tls_auto_generate = true` in your configuration, TftpOS generates a self-signed certificate on first start. This requires the `cryptography` package (installed by the `tls` extra).

This is fine for testing. Do not use self-signed certificates for anything beyond a lab environment.

### Custom certificates

Place your certificate and key in the config directory:

```bash
sudo cp your-cert.pem /etc/tftpos/tls.cert
sudo cp your-key.pem /etc/tftpos/tls.key
sudo chmod 600 /etc/tftpos/tls.key
sudo chown tftpos:tftpos /etc/tftpos/tls.cert /etc/tftpos/tls.key
```

Set `tls_auto_generate = false` in your configuration and point the cert/key paths to these files.

### Let's Encrypt with certbot

Install certbot and obtain a certificate:

```bash
sudo apt install certbot       # Debian/Ubuntu
sudo dnf install certbot       # Fedora/RHEL

sudo certbot certonly --standalone -d tftpos.example.com
```

Symlink or copy the certs:

```bash
sudo ln -sf /etc/letsencrypt/live/tftpos.example.com/fullchain.pem /etc/tftpos/tls.cert
sudo ln -sf /etc/letsencrypt/live/tftpos.example.com/privkey.pem /etc/tftpos/tls.key
```

Set up auto-renewal with a post-hook to restart TftpOS:

```bash
sudo tee /etc/letsencrypt/renewal-hooks/post/restart-tftpos.sh > /dev/null << 'EOF'
#!/bin/bash
systemctl restart tftpos.service
EOF
sudo chmod +x /etc/letsencrypt/renewal-hooks/post/restart-tftpos.sh
```

### Reverse proxy

> **Note:** The reverse proxy examples below are **placeholders** for when the REST API server is implemented. They are included for planning purposes.

If you terminate TLS at a reverse proxy, run the TftpOS API on plain HTTP and let the proxy handle certificates.

**nginx:**

```nginx
server {
    listen 443 ssl;
    server_name tftpos.example.com;

    ssl_certificate     /etc/letsencrypt/live/tftpos.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/tftpos.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

**HAProxy:**

```haproxy
frontend tftpos_https
    bind *:443 ssl crt /etc/haproxy/certs/tftpos.example.com.pem
    default_backend tftpos_api

backend tftpos_api
    server tftpos 127.0.0.1:8080 check
```

---

## DHCP/TFTP Server Integration

TftpOS does not implement a TFTP server. It manages the files in the TFTP root directory. You need a separate TFTP server to actually serve those files to PXE clients.

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

### ISC DHCP

Add TFTP options to your ISC DHCP configuration (`/etc/dhcp/dhcpd.conf`):

```conf
subnet 192.168.1.0 netmask 255.255.255.0 {
    range 192.168.1.100 192.168.1.200;
    option routers 192.168.1.1;

    next-server 192.168.1.10;          # IP of your TFTP server
    filename "pxelinux.0";
}
```

Restart dhcpd:

```bash
sudo systemctl restart dhcpd
```

### tftpd-hpa

Install and configure tftpd-hpa to serve from the TftpOS-managed directory:

```bash
sudo apt install tftpd-hpa            # Debian/Ubuntu
sudo dnf install tftp-server           # Fedora/RHEL
```

Edit `/etc/default/tftpd-hpa` (Debian/Ubuntu) or `/etc/sysconfig/tftp` (Fedora/RHEL):

```bash
TFTP_USERNAME="tftp"
TFTP_DIRECTORY="/srv/tftp"
TFTP_ADDRESS="0.0.0.0:69"
TFTP_OPTIONS="--secure"
```

The `tftp` user needs read access to `/srv/tftp`. If TftpOS writes files as the `tftpos` user, make sure both users share a group or set appropriate permissions:

```bash
sudo usermod -aG tftpos tftp
sudo chmod -R g+r /srv/tftp
```

Start tftpd-hpa:

```bash
sudo systemctl enable tftpd-hpa
sudo systemctl start tftpd-hpa
```

---

## Database Setup

### SQLite (default)

SQLite is the default backend. No configuration required. The database file is created automatically on first run (typically at `/srv/tftpos/tftpos.db` or wherever the working directory is set).

### PostgreSQL

Install the PostgreSQL driver:

```bash
pip install psycopg2-binary
```

Or install TftpOS with the postgres extra:

```bash
pip install -e ".[postgres]"
```

Create the database and user:

```sql
CREATE USER tftpos WITH PASSWORD 'changeme';
CREATE DATABASE tftpos OWNER tftpos;
```

Or from the shell:

```bash
sudo -u postgres createuser tftpos
sudo -u postgres createdb -O tftpos tftpos
sudo -u postgres psql -c "ALTER USER tftpos PASSWORD 'changeme';"
```

Set the database URL in your TftpOS configuration:

```
database_url = "postgresql://tftpos:changeme@localhost:5432/tftpos"
```

### MySQL / MariaDB

Install the MySQL driver:

```bash
pip install pymysql
```

Create the database and user:

```sql
CREATE DATABASE tftpos CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'tftpos'@'localhost' IDENTIFIED BY 'changeme';
GRANT ALL PRIVILEGES ON tftpos.* TO 'tftpos'@'localhost';
FLUSH PRIVILEGES;
```

Set the database URL in your TftpOS configuration:

```
database_url = "mysql+pymysql://tftpos:changeme@localhost:3306/tftpos"
```

---

## Backup

### SQLite

Copy the database file:

```bash
cp /srv/tftpos/tftpos.db /backup/tftpos-$(date +%Y%m%d).db
```

For a consistent backup while the service is running, use the SQLite backup command:

```bash
sqlite3 /srv/tftpos/tftpos.db ".backup '/backup/tftpos-$(date +%Y%m%d).db'"
```

### PostgreSQL

```bash
pg_dump -U tftpos -h localhost tftpos > /backup/tftpos-$(date +%Y%m%d).sql
```

Or as a compressed custom-format dump:

```bash
pg_dump -U tftpos -h localhost -Fc tftpos > /backup/tftpos-$(date +%Y%m%d).dump
```

### Configuration

Back up the entire config directory:

```bash
sudo tar czf /backup/tftpos-config-$(date +%Y%m%d).tar.gz /etc/tftpos/
```

---

## Troubleshooting

### Permissions on /srv/tftp

If PXE clients cannot download files, check permissions. The TFTP server user (typically `tftp` or `nobody`) must be able to read the files TftpOS writes:

```bash
ls -la /srv/tftp/
sudo chmod -R a+r /srv/tftp/
```

### Database connection failures

Check that the database is running and reachable:

```bash
# PostgreSQL
pg_isready -h localhost -p 5432

# MySQL/MariaDB
mysqladmin -u tftpos -p ping
```

Check the connection URL in your configuration. Common mistakes:
- Wrong port
- Missing password
- Database not created yet
- Driver package not installed (`psycopg2`, `pymysql`)

### TLS certificate generation fails

Auto-generated self-signed certificates require the `cryptography` Python package:

```bash
pip install cryptography
```

Or install TftpOS with the `tls` extra:

```bash
pip install -e ".[tls]"
```

If you see permission errors, make sure the service user can write to `/etc/tftpos/`:

```bash
sudo chown tftpos:tftpos /etc/tftpos/
```

### Checking logs

Service logs:

```bash
sudo journalctl -u tftpos.service -f
sudo journalctl -u tftpos.service --since "1 hour ago"
```

### Testing API connectivity

> **Not yet available.** No REST API server is implemented. This section will be updated when one exists.

### Testing TFTP connectivity

```bash
tftp localhost -c get pxelinux.0
```

If this fails, check that your TFTP server (tftpd-hpa, dnsmasq, etc.) is running and that `/srv/tftp/pxelinux.0` exists and is readable.

---

**This is alpha software.** Expect rough edges, missing features, and breaking changes. File issues at https://github.com/FlossWare/TftpOS/issues.
