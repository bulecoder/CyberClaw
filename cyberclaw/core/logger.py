from __future__ import annotations

import atexit
import hashlib
import json
import queue
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_STOP = object()
_DEFAULT_QUEUE_SIZE = 1_000
_MAX_STRING_CHARS = 256
_MAX_CONTAINER_ITEMS = 50
_MAX_EVENT_BYTES = 16_384
_MAX_SANITIZE_DEPTH = 6
_REDACTED = "[REDACTED]"
_SENSITIVE_KEYS = {
    "apikey",
    "authorization",
    "cookie",
    "password",
    "refreshtoken",
    "secret",
    "token",
}
_CONTENT_KEYS = {"content", "messages", "prompt", "systemprompt"}
_SECRET_PATTERNS = (
    re.compile(r"(?i)(bearer\s+)[a-z0-9._~+/=-]+"),
    re.compile(r"(?i)\bsk-[a-z0-9_-]{8,}\b"),
    re.compile(r"(?i)((?:openai|anthropic)?_?api_?key\s*[:=]\s*)\S+"),
)


def _is_sensitive_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", key.casefold())
    return normalized in _SENSITIVE_KEYS or normalized.endswith("apikey")


def _is_content_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", key.casefold())
    return normalized in _CONTENT_KEYS or normalized.endswith("content")


def _sanitize_string(value: str) -> str:
    sanitized = value
    for pattern in _SECRET_PATTERNS:
        if pattern.groups:
            sanitized = pattern.sub(r"\1[REDACTED]", sanitized)
        else:
            sanitized = pattern.sub(_REDACTED, sanitized)
    if len(sanitized) > _MAX_STRING_CHARS:
        return sanitized[:_MAX_STRING_CHARS] + "...[TRUNCATED]"
    return sanitized


def _sanitize_value(value: Any, depth: int = 0) -> Any:
    if depth >= _MAX_SANITIZE_DEPTH:
        return "[MAX_DEPTH]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _sanitize_string(value)
    if isinstance(value, bytes):
        return f"[BYTES:{len(value)}]"
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        items = list(value.items())[:_MAX_CONTAINER_ITEMS]
        for raw_key, item in items:
            key = _sanitize_string(str(raw_key))
            if _is_sensitive_key(key):
                sanitized[key] = _REDACTED
            elif _is_content_key(key):
                sanitized[key] = f"[CONTENT:{len(str(item))} chars]"
            else:
                sanitized[key] = _sanitize_value(item, depth + 1)
        if len(value) > _MAX_CONTAINER_ITEMS:
            sanitized["_truncated_items"] = len(value) - _MAX_CONTAINER_ITEMS
        return sanitized
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
        sanitized_items = [
            _sanitize_value(item, depth + 1)
            for item in items[:_MAX_CONTAINER_ITEMS]
        ]
        if len(items) > _MAX_CONTAINER_ITEMS:
            sanitized_items.append(
                f"[TRUNCATED_ITEMS:{len(items) - _MAX_CONTAINER_ITEMS}]"
            )
        return sanitized_items
    return _sanitize_string(str(value))


class JSONLEventLogger:
    """A bounded, lazily started JSONL metadata logger."""

    def __init__(
        self,
        log_dir: str | Path = "logs",
        queue_size: int = _DEFAULT_QUEUE_SIZE,
        register_atexit: bool = True,
    ):
        if queue_size <= 0:
            raise ValueError("queue_size 必须大于 0")

        self.log_dir = Path(log_dir)
        self.log_queue: queue.Queue[object] = queue.Queue(maxsize=queue_size)
        self.worker_thread: threading.Thread | None = None
        self._state_lock = threading.Lock()
        self._started = False
        self._closed = False
        self._closed_cleanly = True
        self._written = 0
        self._dropped = 0
        self._write_failures = 0
        if register_atexit:
            atexit.register(self.close)

    def start(self) -> bool:
        with self._state_lock:
            if self._closed:
                return False
            if self._started:
                return True
            try:
                self.log_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                self._write_failures += 1
                return False

            self.worker_thread = threading.Thread(
                target=self._write_loop,
                name="cyberclaw-jsonl-logger",
                daemon=True,
            )
            self.worker_thread.start()
            self._started = True
            return True

    @staticmethod
    def _safe_thread_filename(thread_id: str) -> str:
        safe_id = "".join(
            character
            for character in thread_id
            if character.isalnum() or character in "-_"
        ) or "default"
        if len(safe_id) <= 64:
            return safe_id
        suffix = hashlib.sha256(thread_id.encode("utf-8")).hexdigest()[:8]
        return f"{safe_id[:55]}-{suffix}"

    def _write_event(self, log_item: dict[str, Any]) -> None:
        filename = self._safe_thread_filename(log_item["thread_id"])
        file_path = self.log_dir / f"{filename}.jsonl"
        with file_path.open("a", encoding="utf-8") as log_file:
            log_file.write(json.dumps(log_item, ensure_ascii=False) + "\n")

    def _write_loop(self) -> None:
        while True:
            log_item = self.log_queue.get()
            try:
                if log_item is _STOP:
                    return
                try:
                    self._write_event(log_item)  # type: ignore[arg-type]
                    with self._state_lock:
                        self._written += 1
                except Exception as exc:
                    with self._state_lock:
                        self._write_failures += 1
                    print(f"[Logger Error] JSONL 写入失败: {exc}")
            finally:
                self.log_queue.task_done()

    def _build_event(
        self,
        thread_id: str,
        event: str,
        fields: dict[str, Any],
    ) -> dict[str, Any]:
        log_item = _sanitize_value(fields)
        log_item.update({
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "thread_id": _sanitize_string(str(thread_id)),
            "event": _sanitize_string(str(event)),
        })
        encoded = json.dumps(log_item, ensure_ascii=False).encode("utf-8")
        if len(encoded) <= _MAX_EVENT_BYTES:
            return log_item
        return {
            "ts": log_item["ts"],
            "thread_id": log_item["thread_id"],
            "event": log_item["event"],
            "payload_truncated": True,
            "original_bytes": len(encoded),
        }

    def log_event(self, thread_id: str, event: str, **kwargs: Any) -> bool:
        if not self.start():
            with self._state_lock:
                self._dropped += 1
            return False

        log_item = self._build_event(thread_id, event, kwargs)
        with self._state_lock:
            if self._closed:
                self._dropped += 1
                return False
            try:
                self.log_queue.put_nowait(log_item)
                return True
            except queue.Full:
                self._dropped += 1
                return False

    def get_stats(self) -> dict[str, int | bool]:
        with self._state_lock:
            return {
                "started": self._started,
                "closed": self._closed,
                "written": self._written,
                "dropped": self._dropped,
                "write_failures": self._write_failures,
            }

    def close(self, timeout: float = 5.0) -> bool:
        with self._state_lock:
            if self._closed:
                return self._closed_cleanly
            self._closed = True
            if not self._started or self.worker_thread is None:
                return True
            worker_thread = self.worker_thread

        try:
            self.log_queue.put(_STOP, timeout=max(0.0, timeout))
        except queue.Full:
            with self._state_lock:
                self._closed_cleanly = False
            return False

        worker_thread.join(timeout=max(0.0, timeout))
        closed_cleanly = not worker_thread.is_alive()
        with self._state_lock:
            self._closed_cleanly = closed_cleanly
        return closed_cleanly

    shutdown = close


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
audit_logger = JSONLEventLogger(_PROJECT_ROOT / "logs")
