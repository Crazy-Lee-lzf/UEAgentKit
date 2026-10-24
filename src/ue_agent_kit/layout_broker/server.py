"""The opt-in, manual-start M1 layout preview broker daemon.

Properties enforced here:

* listens on ``127.0.0.1`` with an OS-assigned ephemeral port only;
* one line in, one line out, both bounded to 1 MiB, with a hard 5 s ceiling;
* token + project + session + algorithm identity checks before any work;
* one in-flight job, request-id dedup/replay rejection, per-session rate limit;
* calls only the existing pure ``build_preview`` function - no files are read
  or written from request content, no Editor connection, no subprocess;
* every failure becomes a stable ``ok=false`` response; the process survives;
* the token is never logged, included in a response, or written to a receipt.
"""

from __future__ import annotations

import logging
import secrets
import socket
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import datetime
from typing import Any

from ..graph_layout_preview import build_preview
from . import descriptor as descriptor_module
from . import protocol
from .security import FileSecurityStatus

LOGGER = logging.getLogger("ueak.layout_broker")

PreviewFunction = Callable[..., dict[str, Any]]

_CLIENT_READ_TIMEOUT_SECONDS = protocol.MAX_DEADLINE_MS / 1000.0
_TOTAL_REQUEST_SECONDS = protocol.MAX_DEADLINE_MS / 1000.0
_MAX_RATE_SESSIONS = 256
_MAX_SEEN_REQUESTS = protocol.MAX_DEDUP_ENTRIES


class LayoutBrokerServer:
    """One-process M1 broker.  Create, :meth:`start`, later :meth:`stop`."""

    def __init__(
        self,
        *,
        descriptor_path: str,
        project_name: str,
        project_path_hash: str,
        token: str | None = None,
        expect_session_id: str | None = None,
        preview_fn: PreviewFunction | None = None,
        rate_limit: int = protocol.DEFAULT_RATE_LIMIT,
        allow_unverified_acl: bool = False,
        started_at: datetime | None = None,
        read_timeout: float = _CLIENT_READ_TIMEOUT_SECONDS,
        total_deadline: float = _TOTAL_REQUEST_SECONDS,
    ) -> None:
        if not project_name:
            raise ValueError("project_name is required")
        if not protocol._PROJECT_HASH_RE.match(project_path_hash):  # noqa: SLF001
            raise ValueError("project_path_hash must be sha1:<40 lowercase hex>")
        if rate_limit <= 0:
            raise ValueError("rate_limit must be positive")
        self._descriptor_path = descriptor_path
        self._project_name = project_name
        self._project_path_hash = project_path_hash
        self._token = token or secrets.token_hex(32)
        if not protocol._TOKEN_RE.match(self._token):  # noqa: SLF001
            raise ValueError("token must be 32-128 lowercase hex characters")
        self._expect_session_id = expect_session_id
        self._preview_fn: PreviewFunction = preview_fn or build_preview
        self._rate_limit = rate_limit
        self._allow_unverified_acl = allow_unverified_acl
        self._started_at = started_at
        if not 0.05 <= read_timeout <= protocol.MAX_DEADLINE_MS / 1000.0:
            raise ValueError("read_timeout must be 0.05-5.0 seconds")
        self._read_timeout = float(read_timeout)
        if not 0.1 <= total_deadline <= _TOTAL_REQUEST_SECONDS:
            raise ValueError("total_deadline must be 0.1-5.0 seconds")
        self._total_deadline = float(total_deadline)

        self._listener: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._state_lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ueak-layout-job")
        self._active: Future[dict[str, Any]] | None = None
        self._seen_requests: OrderedDict[str, None] = OrderedDict()
        self._rate: OrderedDict[str, deque[float]] = OrderedDict()

        self._port = 0
        self._descriptor: dict[str, Any] | None = None
        self._security: FileSecurityStatus | None = None
        self._stats: dict[str, int] = {
            "accepted": 0,
            "rejected": 0,
            "completed": 0,
            "timedOut": 0,
            "cancelled": 0,
            "duplicates": 0,
            "rateLimited": 0,
            "busy": 0,
        }
        self._last_error_code: str | None = None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> dict[str, Any]:
        """Bind loopback, publish the descriptor, and start the accept thread."""

        if self._listener is not None:
            return dict(self._descriptor or {})
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind(("127.0.0.1", 0))
            bound = listener.getsockname()
            if bound[0] != "127.0.0.1":
                raise RuntimeError(f"refusing non-loopback bind: {bound[0]!r}")
            listener.listen(1)
            listener.settimeout(0.2)
        except BaseException:
            listener.close()
            raise

        descriptor = descriptor_module.build_descriptor(
            port=int(bound[1]),
            token=self._token,
            project_name=self._project_name,
            project_path_hash=self._project_path_hash,
            started_at=self._started_at,
        )
        try:
            written = descriptor_module.write_descriptor_atomic(
                self._descriptor_path,
                descriptor,
                allow_unverified_acl=self._allow_unverified_acl,
            )
        except BaseException:
            listener.close()
            raise

        self._listener = listener
        self._port = int(bound[1])
        self._descriptor = descriptor
        self._security = written.security
        self._thread = threading.Thread(
            target=self._serve_loop, name="ueak-layout-broker", daemon=True
        )
        self._thread.start()
        LOGGER.debug(
            "layout broker started port=%s security=%s algorithm=%s/%s",
            self._port,
            written.security.state,
            protocol.ALGORITHM_ID,
            protocol.ALGORITHM_VERSION,
        )
        return dict(descriptor)

    def stop(self) -> None:
        self._stop_event.set()
        listener, self._listener = self._listener, None
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._executor.shutdown(wait=False, cancel_futures=True)
        with self._state_lock:
            self._active = None
        if self._descriptor is not None:
            descriptor_module.remove_own_descriptor(self._descriptor_path, self._token)
        LOGGER.debug("layout broker stopped port=%s", self._port)

    def wait(self, timeout: float | None = None) -> bool:
        return self._stop_event.wait(timeout)

    @property
    def port(self) -> int:
        return self._port

    @property
    def token(self) -> str:
        """The session token.  Callers must never log or persist this value."""

        return self._token

    @property
    def descriptor_path(self) -> str:
        return self._descriptor_path

    @property
    def descriptor(self) -> dict[str, Any] | None:
        return dict(self._descriptor) if self._descriptor is not None else None

    def status(self) -> dict[str, Any]:
        with self._state_lock:
            stats = dict(self._stats)
            in_flight = self._active is not None and not self._active.done()
        return {
            "schemaVersion": "ueak.layoutbroker-run/1.0",
            "ok": True,
            "readOnly": True,
            "address": "127.0.0.1",
            "port": self._port,
            "projectName": self._project_name,
            "projectPathHash": self._project_path_hash,
            "brokerVersion": protocol.BROKER_VERSION,
            "algorithm": dict(protocol.ALGORITHM),
            "descriptorSecurity": self._security.as_dict() if self._security else None,
            "inFlight": in_flight,
            "lastErrorCode": self._last_error_code,
            **stats,
        }

    # -- serving -----------------------------------------------------------

    def _serve_loop(self) -> None:
        while not self._stop_event.is_set():
            listener = self._listener
            if listener is None:
                break
            try:
                connection, address = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                if address[0] != "127.0.0.1":
                    continue
                with connection:
                    self._handle_connection(connection)
            except Exception as exc:  # noqa: BLE001 - one bad client must not stop the broker
                LOGGER.warning("layout broker connection ended abnormally: %s", type(exc).__name__)
        LOGGER.debug("layout broker accept loop exited")

    def _handle_connection(self, connection: socket.socket) -> None:
        received_at = time.monotonic()
        connection.settimeout(min(self._read_timeout, self._total_deadline))
        try:
            line = self._read_line(connection)
        except _RequestTooLarge:
            self._reject(connection, None, protocol.ERROR_REQUEST_TOO_LARGE, "The request exceeds the 1 MiB limit.")
            return
        except protocol.BrokerProtocolError as exc:
            self._reject(connection, None, exc.code, exc.message, retryable=exc.retryable)
            return
        except (socket.timeout, TimeoutError):
            self._record(protocol.ERROR_TIMEOUT)
            self._send_error(
                connection,
                None,
                protocol.ERROR_TIMEOUT,
                "The request line was not received before the deadline.",
                retryable=True,
            )
            return
        except OSError:
            self._record(protocol.ERROR_CANCELLED)
            return

        if line is None:
            self._record(protocol.ERROR_CANCELLED)
            return

        payload: Any
        try:
            payload = protocol.decode_message(line)
        except protocol.BrokerProtocolError as exc:
            self._reject(
                connection,
                None,
                exc.code,
                exc.message,
                retryable=exc.retryable,
            )
            return

        request_id = protocol.echo_request_id(payload)
        try:
            request = protocol.validate_request(
                payload,
                project_name=self._project_name,
                project_path_hash=self._project_path_hash,
                token=self._token,
                expected_session_id=self._expect_session_id,
            )
        except protocol.BrokerProtocolError as exc:
            self._reject(
                connection,
                request_id,
                exc.code,
                protocol.sanitize_message(exc.message, self._token),
                retryable=exc.retryable,
            )
            return

        if not self._claim_request(request.request_id):
            with self._state_lock:
                self._stats["duplicates"] += 1
            self._reject(
                connection,
                request.request_id,
                protocol.ERROR_DUPLICATE_REQUEST,
                "This requestId was already used; replay is refused.",
            )
            return
        if not self._rate_allows(request.session_id):
            with self._state_lock:
                self._stats["rateLimited"] += 1
            self._reject(
                connection,
                request.request_id,
                protocol.ERROR_RATE_LIMITED,
                "The per-session request rate limit was exceeded.",
                retryable=True,
            )
            return
        if not self._job_slot_free():
            with self._state_lock:
                self._stats["busy"] += 1
            self._reject(
                connection,
                request.request_id,
                protocol.ERROR_BUSY,
                "Another layout job is still in flight; retry after it finishes.",
                retryable=True,
            )
            return

        with self._state_lock:
            self._stats["accepted"] += 1
        self._run_and_reply(connection, request, received_at)

    def _run_and_reply(
        self,
        connection: socket.socket,
        request: protocol.BrokerRequest,
        received_at: float,
    ) -> None:
        started = time.monotonic()
        remaining = self._total_deadline - (started - received_at)
        if remaining <= 0:
            with self._state_lock:
                self._stats["timedOut"] += 1
            self._last_error_code = protocol.ERROR_TIMEOUT
            LOGGER.warning("layout broker total deadline requestId=%s", request.request_id)
            self._send_error(
                connection,
                request.request_id,
                protocol.ERROR_TIMEOUT,
                "The total request deadline was exceeded.",
                retryable=True,
            )
            return
        job_wait = min(request.deadline_ms / 1000.0, remaining)
        try:
            job = self._executor.submit(self._invoke_preview, request)
        except RuntimeError:
            self._reject(
                connection,
                request.request_id,
                protocol.ERROR_WORKER_FAILED,
                "The broker job executor is not available.",
            )
            return
        with self._state_lock:
            self._active = job
        job.add_done_callback(self._clear_active)

        try:
            result = job.result(timeout=job_wait)
        except FutureTimeoutError:
            with self._state_lock:
                self._stats["timedOut"] += 1
            self._last_error_code = protocol.ERROR_TIMEOUT
            LOGGER.warning("layout broker timeout requestId=%s", request.request_id)
            self._send_error(
                connection,
                request.request_id,
                protocol.ERROR_TIMEOUT,
                "The layout job exceeded its deadline.",
                retryable=True,
            )
            return
        except Exception as exc:  # noqa: BLE001 - the pure algorithm failed closed
            self._last_error_code = protocol.ERROR_WORKER_FAILED
            LOGGER.warning(
                "layout broker job failed requestId=%s type=%s",
                request.request_id,
                type(exc).__name__,
            )
            self._send_error(
                connection,
                request.request_id,
                protocol.ERROR_WORKER_FAILED,
                f"The layout preview failed: {protocol.sanitize_message(str(exc), self._token)}",
            )
            return

        duration_ms = int(round((time.monotonic() - started) * 1000))
        try:
            accepted = protocol.validate_preview_result(
                result, request.preflight, token=self._token
            )
            protocol.validate_plan_id(
                accepted,
                spacing_x=request.spacing_x,
                spacing_y=request.spacing_y,
                target_width=request.target_width,
                seed=request.seed,
            )
            response = protocol.success_response(request.request_id, accepted, duration_ms)
            encoded = protocol.encode_message(response)
        except protocol.BrokerProtocolError as exc:
            self._last_error_code = exc.code
            LOGGER.warning(
                "layout broker self-check rejected result requestId=%s code=%s",
                request.request_id,
                exc.code,
            )
            self._send_error(
                connection,
                request.request_id,
                protocol.ERROR_WORKER_FAILED,
                "The broker rejected its own preview result and sent no plan.",
            )
            return

        try:
            connection.sendall(encoded)
        except OSError:
            with self._state_lock:
                self._stats["cancelled"] += 1
            self._last_error_code = protocol.ERROR_CANCELLED
            LOGGER.debug("layout broker client cancelled requestId=%s", request.request_id)
            return
        with self._state_lock:
            self._stats["completed"] += 1
        LOGGER.debug(
            "layout broker completed requestId=%s nodes=%s moved=%s durationMs=%s",
            request.request_id,
            result.get("nodeCount"),
            result.get("movedCount"),
            duration_ms,
        )

    def _invoke_preview(self, request: protocol.BrokerRequest) -> dict[str, Any]:
        # Reuses the one authoritative pure implementation; never a fork.
        return self._preview_fn(
            request.preflight,
            request.context_envelope,
            mode=request.mode,
            spacing_x=request.spacing_x,
            spacing_y=request.spacing_y,
            target_width=request.target_width,
            seed=request.seed,
        )

    def _clear_active(self, job: Future[dict[str, Any]]) -> None:
        with self._state_lock:
            if self._active is job:
                self._active = None

    def _job_slot_free(self) -> bool:
        with self._state_lock:
            return self._active is None or self._active.done()

    # -- framing -----------------------------------------------------------

    def _read_line(self, connection: socket.socket) -> bytes | None:
        buffer = bytearray()
        while True:
            chunk = connection.recv(65536)
            if not chunk:
                if not buffer:
                    return None
                raise protocol.BrokerProtocolError(
                    protocol.ERROR_REQUEST_INVALID,
                    "The request line ended before a newline.",
                )
            buffer.extend(chunk)
            newline = buffer.find(b"\n")
            if newline >= 0:
                line = bytes(buffer[:newline])
                if len(line) > protocol.MAX_MESSAGE_BYTES:
                    raise _RequestTooLarge
                return line
            if len(buffer) > protocol.MAX_MESSAGE_BYTES:
                raise _RequestTooLarge

    def _reject(
        self,
        connection: socket.socket,
        request_id: str | None,
        code: str,
        message: str,
        *,
        retryable: bool = False,
    ) -> None:
        self._record(code)
        LOGGER.debug("layout broker rejected requestId=%s code=%s", request_id, code)
        self._send_error(connection, request_id, code, message, retryable=retryable)

    def _send_error(
        self,
        connection: socket.socket,
        request_id: str | None,
        code: str,
        message: str,
        *,
        retryable: bool = False,
    ) -> None:
        try:
            encoded = protocol.encode_message(
                protocol.error_response(
                    request_id,
                    code,
                    protocol.sanitize_message(message, self._token),
                    retryable=retryable,
                )
            )
            connection.sendall(encoded)
        except (OSError, protocol.BrokerProtocolError):
            pass

    def _record(self, code: str) -> None:
        with self._state_lock:
            self._stats["rejected"] += 1
            self._last_error_code = code

    # -- replay/rate limiting ---------------------------------------------

    def _claim_request(self, request_id: str) -> bool:
        with self._state_lock:
            if request_id in self._seen_requests:
                return False
            self._seen_requests[request_id] = None
            while len(self._seen_requests) > _MAX_SEEN_REQUESTS:
                self._seen_requests.popitem(last=False)
            return True

    def _rate_allows(self, session_id: str) -> bool:
        now = time.monotonic()
        with self._state_lock:
            window = self._rate.get(session_id)
            if window is None:
                window = deque()
                self._rate[session_id] = window
                while len(self._rate) > _MAX_RATE_SESSIONS:
                    self._rate.popitem(last=False)
            while window and now - window[0] > protocol.RATE_LIMIT_WINDOW_SECONDS:
                window.popleft()
            if len(window) >= self._rate_limit:
                return False
            window.append(now)
            return True


class _RequestTooLarge(Exception):
    pass


__all__ = ["LayoutBrokerServer"]
