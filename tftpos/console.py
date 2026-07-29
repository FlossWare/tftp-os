"""Console proxy support for browser-based VNC/SPICE/serial viewers.

Provides websocket-to-TCP proxying for VNC (via websockify) and
PTY-based serial console streaming via websocket (xterm.js).
"""

from __future__ import annotations

import asyncio
import enum
import logging
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

_VALID_CONSOLE_TYPES = frozenset({"vnc", "spice", "serial"})
_ENDPOINT_RE = re.compile(
    r"^(?P<host>[a-zA-Z0-9._-]+):(?P<port>[0-9]{1,5})$"
)


class ConsoleType(enum.Enum):
    VNC = "vnc"
    SPICE = "spice"
    SERIAL = "serial"


@dataclass(frozen=True)
class ConsoleConfig:
    """Per-host console configuration."""

    console_type: ConsoleType
    host: str
    port: int

    @classmethod
    def from_host_rule(
        cls,
        console_type: Optional[str],
        console_endpoint: Optional[str],
    ) -> Optional["ConsoleConfig"]:
        """Parse console config from host rule fields.

        Returns None if console is not configured.
        Raises ValueError for invalid configurations.
        """
        if not console_type and not console_endpoint:
            return None

        if not console_type:
            raise ValueError("console_endpoint set without console_type")
        if not console_endpoint:
            raise ValueError("console_type set without console_endpoint")

        ct = console_type.lower().strip()
        if ct not in _VALID_CONSOLE_TYPES:
            raise ValueError(
                f"invalid console_type {console_type!r}; "
                f"must be one of: {', '.join(sorted(_VALID_CONSOLE_TYPES))}"
            )

        match = _ENDPOINT_RE.match(console_endpoint.strip())
        if not match:
            raise ValueError(
                f"invalid console_endpoint {console_endpoint!r}; "
                f"expected host:port format"
            )

        port = int(match.group("port"))
        if port < 1 or port > 65535:
            raise ValueError(
                f"invalid port {port} in console_endpoint; "
                f"must be 1-65535"
            )

        return cls(
            console_type=ConsoleType(ct),
            host=match.group("host"),
            port=port,
        )


class ConsoleProxy:
    """Websocket-to-TCP proxy for VNC/SPICE connections.

    Bridges a websocket connection (from the browser) to a raw TCP
    socket (VNC/SPICE server).  Each call to ``proxy()`` runs until
    the websocket or the backend closes.
    """

    def __init__(self, config: ConsoleConfig) -> None:
        if config.console_type not in (ConsoleType.VNC, ConsoleType.SPICE):
            raise ValueError(
                f"ConsoleProxy requires vnc or spice, got {config.console_type.value}"
            )
        self.config = config
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None

    async def connect(self) -> None:
        """Open TCP connection to the backend console server."""
        self._reader, self._writer = await asyncio.open_connection(
            self.config.host, self.config.port,
        )
        logger.info(
            "Console proxy connected to %s:%d",
            self.config.host,
            self.config.port,
        )

    async def close(self) -> None:
        """Close the backend TCP connection."""
        if self._writer:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
            self._reader = None

    async def send_to_backend(self, data: bytes) -> None:
        """Forward data from the websocket to the backend."""
        if self._writer:
            self._writer.write(data)
            await self._writer.drain()

    async def receive_from_backend(self, max_bytes: int = 65536) -> bytes:
        """Read data from the backend (for forwarding to websocket).

        Returns empty bytes when the backend connection is closed.
        """
        if not self._reader:
            return b""
        data = await self._reader.read(max_bytes)
        return data


class SerialConsoleProxy:
    """Websocket-to-TCP proxy for serial console connections.

    Similar to ConsoleProxy but intended for serial/SOL endpoints
    exposed as TCP sockets (e.g., ``virsh console`` forwarded via
    ``socat`` or IPMI SOL via ``ipmitool sol activate``).
    """

    def __init__(self, config: ConsoleConfig) -> None:
        if config.console_type != ConsoleType.SERIAL:
            raise ValueError(
                f"SerialConsoleProxy requires serial, got {config.console_type.value}"
            )
        self.config = config
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None

    async def connect(self) -> None:
        """Open TCP connection to the serial console endpoint."""
        self._reader, self._writer = await asyncio.open_connection(
            self.config.host, self.config.port,
        )
        logger.info(
            "Serial console connected to %s:%d",
            self.config.host,
            self.config.port,
        )

    async def close(self) -> None:
        """Close the backend TCP connection."""
        if self._writer:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
            self._reader = None

    async def send_to_backend(self, data: bytes) -> None:
        """Forward keystrokes from the websocket to the serial backend."""
        if self._writer:
            self._writer.write(data)
            await self._writer.drain()

    async def receive_from_backend(self, max_bytes: int = 4096) -> bytes:
        """Read output from the serial backend.

        Returns empty bytes when the connection is closed.
        """
        if not self._reader:
            return b""
        data = await self._reader.read(max_bytes)
        return data
