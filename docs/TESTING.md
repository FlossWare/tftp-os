# Testing

## Running Tests

```bash
# Full test suite with coverage
pytest

# Without coverage (faster)
pytest --no-cov

# Specific test file
pytest tests/test_engine.py

# Specific test class or method
pytest tests/test_auth.py::TestApiKeyStore::test_create_key

# Parallel execution
pytest -n auto --no-cov

# Stop on first failure
pytest -x --no-cov
```

## Test Structure

```
tests/
  test_audit.py           - AuditLogger, event logging, query
  test_auth.py            - ApiKeyStore, RBAC, role hierarchy
  test_benchmark.py       - Performance benchmarks
  test_cloud_image.py     - Image import, convert, resize, list
  test_cloud_init.py      - Cloud-init config generation
  test_cluster.py         - ClusterManager, ordered provisioning
  test_db.py              - StorageBackend implementations
  test_errors.py          - Exception hierarchy, format_error
  test_iso_detect.py      - ISO/image detection
  test_logging_config.py  - JSON/syslog logging, correlation IDs
  test_named_objects.py   - NamedObjectStore CRUD
  test_observability.py   - Metrics rendering, cache stats
  test_performance.py     - Latency and throughput tests
  test_power.py           - IPMI/Redfish drivers, PowerManager
  test_rate_limit.py      - Token bucket, middleware, endpoint groups
  test_registry.py        - PluginRegistry discovery and registration
  test_repo_mirror.py     - RepoManager, sync operations
  test_secrets.py         - SecretsProvider, secret resolution
  test_tls.py             - Certificate generation
  test_virtos_integration.py - VirtOS/hypervisor integration
  test_vm_client.py       - VirtBackend implementations
  test_webhooks.py        - WebhookManager, HMAC signatures
```

22 test files, 954 tests total.

## Coverage

Coverage is configured in `pyproject.toml`:

```toml
[tool.coverage.run]
source = ["tftpos"]
branch = true

[tool.coverage.report]
fail_under = 75
show_missing = true
```

| Target | Value |
|--------|-------|
| Minimum coverage | 75% |
| Branch coverage | Enabled |
| Coverage source | `tftpos/` |

View coverage report:

```bash
pytest --cov-report=html
open htmlcov/index.html
```

## Test Configuration

Pytest is configured in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
minversion = "7.0"
testpaths = ["tests"]
addopts = [
    "--verbose",
    "--strict-markers",
    "--strict-config",
    "--cov=tftpos",
    "--cov-report=term-missing:skip-covered",
    "--cov-branch",
    "--maxfail=3",
]
markers = [
    "slow: marks tests as slow",
    "integration: marks tests as integration tests",
]
timeout = 300
```

Run only fast tests:

```bash
pytest -m "not slow" --no-cov
```

## Writing New Tests

Tests use `pytest` with `unittest.mock` for mocking. Follow these patterns:

```python
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tftpos.config import TftpOSConfig
from tftpos.models import HostRule, ProvisionProfile
from tftpos.engine import FirmwareEngine
from tftpos.matcher import HostMatcher
from tftpos.registry import PluginRegistry


def _rule(**kwargs) -> HostRule:
    kwargs.setdefault("profile", "test")
    kwargs.setdefault("os_family", "openwrt")
    kwargs.setdefault("os_version", "23.05")
    return HostRule(**kwargs)


class TestFirmwareEngine:

    def test_serve_returns_firmware_path(self, tmp_path):
        plugin = MagicMock()
        plugin.validate_profile.return_value = []
        plugin.firmware_path.return_value = "/srv/tftp/firmware.bin"

        registry = MagicMock(spec=PluginRegistry)
        registry.get.return_value = plugin

        rule = _rule(mac="aa:bb:cc:dd:ee:ff")
        matcher = MagicMock(spec=HostMatcher)
        matcher.match.return_value = rule

        config = TftpOSConfig(data_dir=tmp_path)
        engine = FirmwareEngine(registry, matcher, config)

        result = engine.serve(mac="aa:bb:cc:dd:ee:ff")
        assert result == "/srv/tftp/firmware.bin"
```

## Linting

```bash
# Ruff (linting + formatting)
ruff check tftpos/ tests/
ruff format --check tftpos/ tests/

# Type checking
mypy tftpos/

# Security audit
bandit -r tftpos/ -ll
```

## What Is NOT Tested

- No integration tests against real TFTP servers
- No integration tests against real IPMI/Redfish BMCs
- No integration tests against real hypervisors (libvirt, bhyve, vmm, Hyper-V)
- No end-to-end tests serving firmware to actual devices
- Cloud image operations require `qemu-img` (not tested in CI)
