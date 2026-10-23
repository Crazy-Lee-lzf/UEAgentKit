"""UEAgentKit M1 layout preview broker (offline, opt-in, manual start).

Importing this package has no side effects: no listener, no descriptor, no
subprocess, no Editor connection.  Only :class:`~.server.LayoutBrokerServer`
owns a socket, and only when ``start()`` is called explicitly.
"""

from __future__ import annotations

from .client import BrokerClientError, LayoutBrokerClient
from .protocol import (
    ALGORITHM,
    BROKER_VERSION,
    PROTOCOL_SCHEMA_VERSION,
    BrokerProtocolError,
    BrokerRequest,
)
from .server import LayoutBrokerServer

__all__ = [
    "ALGORITHM",
    "BROKER_VERSION",
    "PROTOCOL_SCHEMA_VERSION",
    "BrokerClientError",
    "BrokerProtocolError",
    "BrokerRequest",
    "LayoutBrokerClient",
    "LayoutBrokerServer",
]
