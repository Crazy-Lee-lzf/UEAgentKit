"""Offline client fixture for the M1 layout preview broker.

This is deliberately **not** wired into the MCP server, the Editor menu, or any
production route.  It exists so the broker protocol can be exercised end-to-end
on a disposable host and so an Editor-side implementation has a reference for
the exact framing/validation rules.

The client re-validates every response independently: request id, schema,
algorithm, plan shape, node set, coordinate bounds, and the canonical
``layoutHash`` recomputation.  A malicious or buggy broker cannot get a plan
accepted by this client.
"""

from __future__ import annotations

import socket
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from . import descriptor as descriptor_module
from . import protocol


class BrokerClientError(protocol.BrokerProtocolError):
    """A client-side failure with a stable code."""


def _utc_now_text() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


class LayoutBrokerClient:
    def __init__(
        self,
        descriptor: Mapping[str, Any],
        *,
        project_name: str,
        project_path_hash: str,
        session_id: str,
        connect_timeout: float = 5.0,
    ) -> None:
        validated = descriptor_module.validate_descriptor(
            dict(descriptor),
            project_name=project_name,
            project_path_hash=project_path_hash,
        )
        if not protocol._SESSION_RE.match(session_id):  # noqa: SLF001 - shared format
            raise BrokerClientError(
                protocol.ERROR_REQUEST_INVALID, "session_id has an invalid format."
            )
        self._descriptor = validated
        self._project_name = project_name
        self._project_path_hash = project_path_hash
        self._session_id = session_id
        self._connect_timeout = connect_timeout

    @classmethod
    def from_descriptor_file(
        cls,
        path: str,
        *,
        project_name: str,
        project_path_hash: str,
        session_id: str,
        connect_timeout: float = 5.0,
    ) -> "LayoutBrokerClient":
        descriptor = descriptor_module.read_descriptor_file(
            path,
            project_name=project_name,
            project_path_hash=project_path_hash,
        )
        return cls(
            descriptor,
            project_name=project_name,
            project_path_hash=project_path_hash,
            session_id=session_id,
            connect_timeout=connect_timeout,
        )

    @property
    def port(self) -> int:
        return int(self._descriptor["port"])

    def build_request(
        self,
        preflight: Mapping[str, Any],
        context_envelope: Mapping[str, Any],
        *,
        request_id: str | None = None,
        mode: str = "full",
        spacing_x: int = 96,
        spacing_y: int = 64,
        target_width: int = 1800,
        seed: int = 0,
        deadline_ms: int = protocol.MAX_DEADLINE_MS,
    ) -> dict[str, Any]:
        return {
            "schemaVersion": protocol.PROTOCOL_SCHEMA_VERSION,
            "requestId": request_id or protocol.new_request_id(),
            "sessionId": self._session_id,
            "projectName": self._project_name,
            "projectPathHash": self._project_path_hash,
            "token": self._descriptor["token"],
            "sentAtUtc": _utc_now_text(),
            "deadlineMs": int(deadline_ms),
            "algorithm": dict(protocol.ALGORITHM),
            "options": {
                "mode": mode,
                "spacingX": int(spacing_x),
                "spacingY": int(spacing_y),
                "targetWidth": int(target_width),
                "seed": int(seed),
            },
            "snapshot": {
                "preflight": dict(preflight),
                "contextEnvelope": dict(context_envelope),
            },
        }

    def preview(
        self,
        preflight: Mapping[str, Any],
        context_envelope: Mapping[str, Any],
        **options: Any,
    ) -> dict[str, Any]:
        request = self.build_request(preflight, context_envelope, **options)
        return self.send(request)

    def send(self, request: Mapping[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        """Send one request and return the validated preview result."""

        try:
            validated = protocol.validate_request(
                dict(request),
                project_name=self._project_name,
                project_path_hash=self._project_path_hash,
                token=self._descriptor["token"],
                expected_session_id=self._session_id,
            )
            encoded = protocol.encode_message(dict(request))
        except protocol.BrokerProtocolError as exc:
            raise BrokerClientError(
                exc.code, exc.message, retryable=exc.retryable
            ) from exc
        wait_seconds = min(
            self._connect_timeout,
            max(validated.deadline_ms / 1000.0, 0.05) + 0.5,
        )
        if timeout is not None:
            wait_seconds = timeout
        try:
            with socket.create_connection(
                ("127.0.0.1", int(self._descriptor["port"])), timeout=wait_seconds
            ) as connection:
                connection.settimeout(wait_seconds)
                connection.sendall(encoded)
                raw = _read_one_line(connection)
        except (socket.timeout, TimeoutError) as exc:
            raise BrokerClientError(
                protocol.ERROR_TIMEOUT, "The broker did not answer before the deadline."
            ) from exc
        except ConnectionRefusedError as exc:
            raise BrokerClientError(
                protocol.ERROR_BROKER_UNAVAILABLE, "The broker refused the loopback connection."
            ) from exc
        except OSError as exc:
            raise BrokerClientError(
                protocol.ERROR_BROKER_UNAVAILABLE, "The broker connection failed."
            ) from exc

        payload = protocol.decode_message(raw)
        try:
            return protocol.validate_response(
                payload, validated, token=self._descriptor["token"]
            )
        except BrokerClientError:
            raise
        except protocol.BrokerProtocolError as exc:
            raise BrokerClientError(
                exc.code, exc.message, retryable=exc.retryable
            ) from exc


def _read_one_line(connection: socket.socket) -> bytes:
    buffer = bytearray()
    while True:
        chunk = connection.recv(65536)
        if not chunk:
            raise BrokerClientError(
                protocol.ERROR_RESPONSE_INVALID,
                "The broker closed the connection before a complete response line.",
            )
        buffer.extend(chunk)
        if len(buffer) > protocol.MAX_MESSAGE_BYTES:
            raise BrokerClientError(
                protocol.ERROR_RESPONSE_TOO_LARGE,
                "The broker response exceeds the 1 MiB limit.",
            )
        newline = buffer.find(b"\n")
        if newline >= 0:
            line = bytes(buffer[:newline])
            if len(line) > protocol.MAX_MESSAGE_BYTES:
                raise BrokerClientError(
                    protocol.ERROR_RESPONSE_TOO_LARGE,
                    "The broker response line exceeds the 1 MiB limit.",
                )
            if buffer[newline + 1 :].strip():
                raise BrokerClientError(
                    protocol.ERROR_RESPONSE_INVALID,
                    "The broker sent more than one response line.",
                )
            return line


__all__ = ["BrokerClientError", "LayoutBrokerClient"]
