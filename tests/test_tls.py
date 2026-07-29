"""Tests for tftpos.tls -- TLS certificate generation and CLI integration."""

from __future__ import annotations

import ssl
from pathlib import Path

import pytest

from tftpos.tls import (
    DEFAULT_CERT_PATH,
    DEFAULT_KEY_PATH,
    ensure_tls_certs,
    generate_self_signed_cert,
)


# ---------------------------------------------------------------------------
# generate_self_signed_cert
# ---------------------------------------------------------------------------


class TestGenerateSelfSignedCert:
    """Tests for self-signed certificate generation."""

    def test_creates_cert_and_key_files(self, tmp_path):
        """Certificate and key PEM files are created on disk."""
        cert_path = tmp_path / "cert.pem"
        key_path = tmp_path / "key.pem"

        result_cert, result_key = generate_self_signed_cert(
            cert_path, key_path
        )

        assert result_cert == cert_path
        assert result_key == key_path
        assert cert_path.exists()
        assert key_path.exists()

    def test_cert_is_valid_pem(self, tmp_path):
        """Generated certificate is valid PEM that OpenSSL can parse."""
        cert_path = tmp_path / "cert.pem"
        key_path = tmp_path / "key.pem"

        generate_self_signed_cert(cert_path, key_path)

        cert_data = cert_path.read_text()
        assert "-----BEGIN CERTIFICATE-----" in cert_data
        assert "-----END CERTIFICATE-----" in cert_data

    def test_key_is_valid_pem(self, tmp_path):
        """Generated key is valid PEM format."""
        cert_path = tmp_path / "cert.pem"
        key_path = tmp_path / "key.pem"

        generate_self_signed_cert(cert_path, key_path)

        key_data = key_path.read_text()
        assert "-----BEGIN RSA PRIVATE KEY-----" in key_data
        assert "-----END RSA PRIVATE KEY-----" in key_data

    def test_key_file_permissions(self, tmp_path):
        """Private key file has restrictive permissions (0o600)."""
        cert_path = tmp_path / "cert.pem"
        key_path = tmp_path / "key.pem"

        generate_self_signed_cert(cert_path, key_path)

        mode = key_path.stat().st_mode & 0o777
        assert mode == 0o600

    def test_creates_parent_directories(self, tmp_path):
        """Parent directories are created if they do not exist."""
        cert_path = tmp_path / "subdir" / "nested" / "cert.pem"
        key_path = tmp_path / "subdir" / "nested" / "key.pem"

        generate_self_signed_cert(cert_path, key_path)

        assert cert_path.exists()
        assert key_path.exists()

    def test_custom_common_name(self, tmp_path):
        """Custom common name is accepted without error."""
        cert_path = tmp_path / "cert.pem"
        key_path = tmp_path / "key.pem"

        # Should not raise
        generate_self_signed_cert(
            cert_path, key_path, common_name="test.example.com"
        )

        assert cert_path.exists()

    def test_custom_validity_days(self, tmp_path):
        """Custom validity period is accepted."""
        cert_path = tmp_path / "cert.pem"
        key_path = tmp_path / "key.pem"

        generate_self_signed_cert(
            cert_path, key_path, validity_days=30
        )

        assert cert_path.exists()

    def test_cert_and_key_match(self, tmp_path):
        """Certificate and key form a valid pair (SSLContext accepts them)."""
        cert_path = tmp_path / "cert.pem"
        key_path = tmp_path / "key.pem"

        generate_self_signed_cert(cert_path, key_path)

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        # This will raise if cert/key don't match
        ctx.load_cert_chain(str(cert_path), str(key_path))

    def test_cert_has_san_extension(self, tmp_path):
        """Certificate includes SAN with localhost and 127.0.0.1."""
        from cryptography import x509

        cert_path = tmp_path / "cert.pem"
        key_path = tmp_path / "key.pem"

        generate_self_signed_cert(cert_path, key_path)

        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        san = cert.extensions.get_extension_for_class(
            x509.SubjectAlternativeName
        )
        dns_names = san.value.get_values_for_type(x509.DNSName)
        assert "localhost" in dns_names

    def test_default_paths(self):
        """Default paths point to /etc/tftpos/tls/."""
        assert DEFAULT_CERT_PATH == Path("/etc/tftpos/tls/cert.pem")
        assert DEFAULT_KEY_PATH == Path("/etc/tftpos/tls/key.pem")


# ---------------------------------------------------------------------------
# ensure_tls_certs
# ---------------------------------------------------------------------------


class TestEnsureTlsCerts:
    """Tests for the ensure_tls_certs convenience function."""

    def test_returns_provided_paths_when_both_exist(self, tmp_path):
        """If user provides existing cert and key, they are returned."""
        cert_path = tmp_path / "user-cert.pem"
        key_path = tmp_path / "user-key.pem"

        # Create dummy files
        cert_path.write_text("cert")
        key_path.write_text("key")

        result_cert, result_key = ensure_tls_certs(
            cert_path=cert_path, key_path=key_path
        )

        assert result_cert == cert_path
        assert result_key == key_path

    def test_raises_when_cert_missing(self, tmp_path):
        """FileNotFoundError if user-provided cert does not exist."""
        cert_path = tmp_path / "missing-cert.pem"
        key_path = tmp_path / "key.pem"
        key_path.write_text("key")

        with pytest.raises(FileNotFoundError, match="certificate"):
            ensure_tls_certs(cert_path=cert_path, key_path=key_path)

    def test_raises_when_key_missing(self, tmp_path):
        """FileNotFoundError if user-provided key does not exist."""
        cert_path = tmp_path / "cert.pem"
        key_path = tmp_path / "missing-key.pem"
        cert_path.write_text("cert")

        with pytest.raises(FileNotFoundError, match="key"):
            ensure_tls_certs(cert_path=cert_path, key_path=key_path)

    def test_auto_generates_under_data_dir(self, tmp_path):
        """With no user paths, generates certs under data_dir/tls/."""
        cert, key = ensure_tls_certs(data_dir=tmp_path)

        assert cert == tmp_path / "tls" / "cert.pem"
        assert key == tmp_path / "tls" / "key.pem"
        assert cert.exists()
        assert key.exists()

    def test_reuses_existing_auto_generated(self, tmp_path):
        """If auto-generated certs already exist, they are reused."""
        tls_dir = tmp_path / "tls"
        tls_dir.mkdir()
        cert_path = tls_dir / "cert.pem"
        key_path = tls_dir / "key.pem"

        # Create dummy existing files
        cert_path.write_text("existing-cert")
        key_path.write_text("existing-key")

        result_cert, result_key = ensure_tls_certs(data_dir=tmp_path)

        assert result_cert == cert_path
        assert result_key == key_path
        # Should not have overwritten
        assert cert_path.read_text() == "existing-cert"

    def test_generates_when_no_args(self, tmp_path):
        """With only data_dir, new certs are generated."""
        cert, key = ensure_tls_certs(data_dir=tmp_path)

        assert cert.exists()
        assert key.exists()
        # Should be proper PEM
        assert "BEGIN CERTIFICATE" in cert.read_text()


# ---------------------------------------------------------------------------
# Config: [tls] section loading
# ---------------------------------------------------------------------------


class TestTlsConfig:
    """Tests for [tls] section in TOML config."""

    def test_tls_section_cert_and_key(self, tmp_path):
        """[tls] section cert/key are loaded into TftpOSConfig."""
        from tftpos.config import load_config

        config_file = tmp_path / "tftpos.toml"
        config_file.write_text(
            '[server]\nhost = "0.0.0.0"\nport = 8443\n\n'
            '[tls]\ncert = "/etc/tftpos/tls/cert.pem"\n'
            'key = "/etc/tftpos/tls/key.pem"\n'
        )

        config = load_config(config_file)
        assert config.tls_cert == Path("/etc/tftpos/tls/cert.pem")
        assert config.tls_key == Path("/etc/tftpos/tls/key.pem")

    def test_tls_auto_generate_default_true(self, tmp_path):
        """tls_auto_generate defaults to True."""
        from tftpos.config import load_config

        config_file = tmp_path / "tftpos.toml"
        config_file.write_text(
            '[server]\nhost = "0.0.0.0"\nport = 8443\n'
        )

        config = load_config(config_file)
        assert config.tls_auto_generate is True

    def test_tls_auto_generate_false(self, tmp_path):
        """[tls] auto_generate = false is respected."""
        from tftpos.config import load_config

        config_file = tmp_path / "tftpos.toml"
        config_file.write_text(
            '[server]\nhost = "0.0.0.0"\nport = 8443\n\n'
            '[tls]\nauto_generate = false\n'
        )

        config = load_config(config_file)
        assert config.tls_auto_generate is False

    def test_tls_section_overrides_server_keys(self, tmp_path):
        """[tls] section takes precedence over server.tls_cert/tls_key."""
        from tftpos.config import load_config

        config_file = tmp_path / "tftpos.toml"
        config_file.write_text(
            '[server]\nhost = "0.0.0.0"\nport = 8443\n'
            'tls_cert = "/old/cert.pem"\n'
            'tls_key = "/old/key.pem"\n\n'
            '[tls]\ncert = "/new/cert.pem"\n'
            'key = "/new/key.pem"\n'
        )

        config = load_config(config_file)
        assert config.tls_cert == Path("/new/cert.pem")
        assert config.tls_key == Path("/new/key.pem")

    def test_backward_compat_server_tls_keys(self, tmp_path):
        """server.tls_cert/tls_key still work without [tls] section."""
        from tftpos.config import load_config

        config_file = tmp_path / "tftpos.toml"
        config_file.write_text(
            '[server]\nhost = "0.0.0.0"\nport = 8443\n'
            'tls_cert = "/etc/tftpos/cert.pem"\n'
            'tls_key = "/etc/tftpos/key.pem"\n'
        )

        config = load_config(config_file)
        assert config.tls_cert == Path("/etc/tftpos/cert.pem")
        assert config.tls_key == Path("/etc/tftpos/key.pem")

    def test_default_config_has_auto_generate_true(self):
        """TftpOSConfig() defaults tls_auto_generate to True."""
        from tftpos.config import TftpOSConfig

        config = TftpOSConfig()
        assert config.tls_auto_generate is True
        assert config.tls_cert is None
        assert config.tls_key is None


