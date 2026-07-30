"""TLS certificate generation and management for TftpOS.

Provides self-signed certificate auto-generation using the
``cryptography`` library so that autoinstall configs (which may
contain password hashes and SSH keys) are served over HTTPS by
default.
"""

from __future__ import annotations

import datetime
import logging
from pathlib import Path
from typing import Tuple

logger = logging.getLogger("tftpos.tls")

# Default location for auto-generated certificates
DEFAULT_CERT_DIR = Path("/etc/tftpos/tls")
DEFAULT_CERT_PATH = DEFAULT_CERT_DIR / "cert.pem"
DEFAULT_KEY_PATH = DEFAULT_CERT_DIR / "key.pem"

# Certificate parameters
_CERT_COMMON_NAME = "TftpOS Autoinstall Server"
_CERT_ORG = "TftpOS"
_CERT_VALIDITY_DAYS = 365
_KEY_SIZE = 4096


def generate_self_signed_cert(
    cert_path: Path = DEFAULT_CERT_PATH,
    key_path: Path = DEFAULT_KEY_PATH,
    common_name: str = _CERT_COMMON_NAME,
    validity_days: int = _CERT_VALIDITY_DAYS,
) -> Tuple[Path, Path]:
    """Generate a self-signed TLS certificate and private key.

    Creates the parent directories if they do not exist, writes
    PEM-encoded certificate and key files, and returns the paths.

    Parameters
    ----------
    cert_path:
        Where to write the certificate PEM file.
    key_path:
        Where to write the private key PEM file.
    common_name:
        Subject CN for the certificate.
    validity_days:
        How many days the certificate is valid.

    Returns
    -------
    tuple[Path, Path]
        ``(cert_path, key_path)`` after writing.

    Raises
    ------
    ImportError
        If the ``cryptography`` package is not installed.
    OSError
        If the files cannot be written.
    """
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except ImportError as exc:
        raise ImportError(
            "The 'cryptography' package is required for TLS certificate "
            "generation. Install it with: pip install cryptography"
        ) from exc

    # Generate RSA key pair
    key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=_KEY_SIZE,
    )

    # Build the self-signed certificate
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, _CERT_ORG),
        x509.NameAttribute(NameOID.COMMON_NAME, common_name),
    ])

    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=validity_days))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("localhost"),
                x509.IPAddress(
                    __import__("ipaddress").IPv4Address("127.0.0.1")
                ),
            ]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    # Ensure parent directories exist
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.parent.mkdir(parents=True, exist_ok=True)

    # Write PEM files
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    # Restrict key file permissions (owner-only read/write)
    key_path.chmod(0o600)

    cert_path.write_bytes(
        cert.public_bytes(serialization.Encoding.PEM)
    )

    logger.info(
        "generated self-signed TLS certificate: %s (valid %d days)",
        cert_path,
        validity_days,
    )
    return cert_path, key_path


def ensure_tls_certs(
    cert_path: Path | None = None,
    key_path: Path | None = None,
    data_dir: Path | None = None,
) -> Tuple[Path, Path]:
    """Return existing TLS cert/key paths or generate new ones.

    If *cert_path* and *key_path* are both provided and exist, they
    are returned as-is.  Otherwise a new self-signed certificate is
    generated under *data_dir*/tls/ (or the default location).

    Parameters
    ----------
    cert_path:
        User-supplied certificate path (may be ``None``).
    key_path:
        User-supplied key path (may be ``None``).
    data_dir:
        Base data directory; auto-generated certs go under
        ``data_dir / "tls"``.

    Returns
    -------
    tuple[Path, Path]
        ``(cert_path, key_path)`` ready for use with a TLS-capable server.
    """
    # User provided both paths -- use them directly
    if cert_path and key_path:
        if not cert_path.exists():
            raise FileNotFoundError(
                f"TLS certificate not found: {cert_path}"
            )
        if not key_path.exists():
            raise FileNotFoundError(
                f"TLS private key not found: {key_path}"
            )
        logger.info("using provided TLS certificate: %s", cert_path)
        return cert_path, key_path

    # Auto-generate under data_dir/tls or the default location
    if data_dir:
        gen_cert = data_dir / "tls" / "cert.pem"
        gen_key = data_dir / "tls" / "key.pem"
    else:
        gen_cert = DEFAULT_CERT_PATH
        gen_key = DEFAULT_KEY_PATH

    # Reuse existing auto-generated certs if they exist
    if gen_cert.exists() and gen_key.exists():
        logger.info(
            "reusing auto-generated TLS certificate: %s", gen_cert
        )
        return gen_cert, gen_key

    logger.info(
        "no TLS certificate configured; generating self-signed certificate"
    )
    return generate_self_signed_cert(gen_cert, gen_key)
