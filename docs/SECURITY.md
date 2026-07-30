# Security

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.1.x | Yes |

## Authentication and Authorization

tftp-os provides role-based access control (RBAC) with API keys. Auth is **disabled by default**.

### Roles

| Role | Access |
|------|--------|
| `VIEWER` | Read-only (list hosts, view status, read config) |
| `OPERATOR` | Read + write (manage hosts, trigger provisions, manage profiles) |
| `ADMIN` | Full access (create/revoke API keys, modify configuration, manage auth) |

Roles are hierarchical: `ADMIN` has all `OPERATOR` permissions, `OPERATOR` has all `VIEWER` permissions.

### Enabling auth

```toml
# tftpos.toml
[auth]
enabled = true
```

### Managing API keys

```python
from tftpos.auth import ApiKeyStore, Role

store = ApiKeyStore(Path("/etc/tftpos"))

# Create a key (returns raw key + ApiKey object)
raw_key, api_key = store.create_key("deploy-bot", Role.OPERATOR)
# raw_key = "tftpos_a1b2c3d4..." -- give this to the client

# Validate a key (timing-safe comparison)
validated = store.validate(raw_key)
if validated and validated.enabled:
    print(f"Role: {validated.role}")

# List, revoke, delete
keys = store.list_keys()
store.revoke("deploy-bot")   # Disable but keep record
store.delete("deploy-bot")   # Remove entirely
```

Keys are stored as SHA-256 hashes in `<data_dir>/auth_keys.json`. Raw keys are never stored.

### FastAPI integration

tftp-os is a pure library and does not include FastAPI glue code. The `require_role()` dependency and `RateLimitMiddleware` live in the separate [flossware-tftpos](https://github.com/FlossWare/flossware-tftpos) application repository (web frontend is in the `flossware-tftpos-web/` subdirectory). See that project for FastAPI auth examples.

## Rate Limiting

Token-bucket rate limiting protects against abuse and brute-force attacks. Three endpoint groups have independent limits:

| Group | Default RPM | Default burst | Purpose |
|-------|------------|---------------|---------|
| `TFTP` | 300 | 50 | Boot/firmware requests (machine traffic) |
| `API` | 60 | 20 | General API calls |
| `AUTH` | 10 | 5 | Login/key validation (brute-force protection) |

Enable in config:

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

The `RateLimiter` class tracks request rates per key and rejects requests that exceed the configured limits. Applications using this in HTTP middleware can return 429 responses with a `Retry-After` header.

## TLS

### Auto-generated self-signed certificates

The TLS module can generate a self-signed certificate when `generate_cert()` is called and `tls_auto_generate = true` (the default). Certificates are stored in `<data_dir>/tls/`.

### Custom certificates

```toml
[tls]
cert = "/etc/tftpos/tls/cert.pem"
key = "/etc/tftpos/tls/key.pem"
auto_generate = false
```

### Let's Encrypt

```bash
sudo certbot certonly --standalone -d tftpos.example.com
```

Then configure:

```toml
[tls]
cert = "/etc/letsencrypt/live/tftpos.example.com/fullchain.pem"
key = "/etc/letsencrypt/live/tftpos.example.com/privkey.pem"
auto_generate = false
```

### Reverse proxy

When deploying an application built on tftp-os behind a reverse proxy (nginx, HAProxy), terminate TLS at the proxy layer and disable auto-generation in the library configuration:

```toml
[tls]
auto_generate = false
```

## Input Validation

tftp-os validates and sanitizes all external input at the system boundary. The `tftpos.validation` module provides:

| Function | Purpose |
|----------|---------|
| `validate_mac(mac)` | Validates MAC address format |
| `normalize_mac(mac)` | Normalizes to lowercase colon-separated |
| `validate_hostname(hostname)` | RFC-compliant hostname validation |
| `sanitize_hostname(hostname)` | Validates and returns or raises ValueError |
| `validate_url(url)` | URL format and scheme validation |
| `sanitize_url(url)` | Validates and returns or raises ValueError |
| `is_shell_safe(value)` | Checks for shell metacharacters |
| `sanitize_shell_value(value)` | Rejects values with shell metacharacters |
| `escape_xml(value)` | Escapes XML special characters |
| `validate_safe_name(name)` | Rejects path traversal (`..`, `/`) |
| `validate_package_name(pkg)` | Validates package name format |
| `sanitize_packages(packages)` | Validates a list of package names |

Template rendering (`FirmwarePlugin._sanitize_context()`) automatically sanitizes hostnames, URLs, and package names before passing them to Jinja2 templates.

Jinja2 templates use `autoescape` for XML templates (`.xml`, `.xml.j2`) to prevent injection.

## Secrets Management

Secrets (`{{secret:KEY}}` references in profiles and host rules) are resolved at runtime. They are never stored in plain text in TOML files.

Two providers:

- **FileSecretsProvider** -- stores secrets in `<data_dir>/secrets.json` with file permissions restricted to the service user
- **EnvironmentSecretsProvider** -- reads `TFTPOS_SECRET_<KEY>` environment variables

```python
from tftpos.secrets import SecretsManager, FileSecretsProvider

provider = FileSecretsProvider(Path("/etc/tftpos"))
provider.set("bmc_password", "my-secret")

manager = SecretsManager(provider)
resolved = manager.resolve_profile(profile)
```

### Best practices

- Use `{{secret:KEY}}` references in host rules for BMC passwords
- Set restrictive file permissions on `secrets.json` (`chmod 600`)
- Prefer environment variable provider in containerized deployments
- Never commit secrets to version control

## Webhook Security

Webhook payloads are signed with HMAC-SHA256 when a secret is configured:

```python
from tftpos.webhooks import verify_signature

# Verify incoming webhook
is_valid = verify_signature(
    payload_bytes=request.body,
    secret="your-hmac-secret",
    signature=request.headers["X-TftpOS-Signature"],
)
```

Configure webhook secrets in `tftpos.toml`:

```toml
[[webhooks]]
url = "https://example.com/hook"
events = ["provision.complete"]
secret = "your-hmac-secret"
```

## Audit Trail

All security-relevant events are logged to the audit system:

- `AUTH_SUCCESS` / `AUTH_FAILURE` -- authentication attempts
- `API_KEY_CREATED` / `API_KEY_DELETED` -- key lifecycle
- `HOST_RULE_CHANGE` -- host rule modifications
- `PROFILE_CHANGE` -- profile modifications
- `NETBOOT_CHANGE` -- netboot enable/disable

Audit logs support file output, stdout, and syslog. See [DEPLOYMENT.md](DEPLOYMENT.md) for configuration.

## Vulnerability Reporting

Report security vulnerabilities by opening a private issue at [github.com/FlossWare/tftp-os/security](https://github.com/FlossWare/tftp-os/security/advisories).

Include:

- Description of the vulnerability
- Steps to reproduce
- Affected versions
- Suggested fix (if any)

## Dependency Auditing

```bash
pip install pip-audit
pip-audit
```

Update dependencies:

```bash
pip install --upgrade jinja2 pydantic sqlalchemy
```
