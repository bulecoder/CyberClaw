import json
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from cyberclaw.core.logger import JSONLEventLogger


class TestJSONLEventLogger(unittest.TestCase):
    def test_lazy_start_and_idempotent_close(self):
        with TemporaryDirectory() as temp_dir:
            log_dir = Path(temp_dir) / "logs"
            logger = JSONLEventLogger(log_dir, register_atexit=False)

            self.assertFalse(log_dir.exists())
            self.assertIsNone(logger.worker_thread)
            self.assertTrue(logger.log_event("session", "llm_input", count=1))
            self.assertTrue(logger.close())
            self.assertTrue(logger.close())

            self.assertTrue(log_dir.exists())
            self.assertEqual(logger.get_stats()["written"], 1)

    def test_redacts_secrets_and_replaces_content_with_metadata(self):
        with TemporaryDirectory() as temp_dir:
            logger = JSONLEventLogger(temp_dir, register_atexit=False)
            logger.log_event(
                "session",
                "tool_call",
                args={
                    "filepath": "hello.txt",
                    "content": "private document body",
                    "api_key": "sk-testsecret123",
                    "headers": {"Authorization": "Bearer secret-token"},
                    "command": "curl -H 'Authorization: Bearer another-secret'",
                },
            )
            logger.close()

            event = json.loads((Path(temp_dir) / "session.jsonl").read_text("utf-8"))
            serialized = json.dumps(event, ensure_ascii=False)
            self.assertEqual(event["args"]["filepath"], "hello.txt")
            self.assertEqual(event["args"]["content"], "[CONTENT:21 chars]")
            self.assertEqual(event["args"]["api_key"], "[REDACTED]")
            self.assertNotIn("testsecret123", serialized)
            self.assertNotIn("secret-token", serialized)
            self.assertNotIn("another-secret", serialized)

    def test_full_queue_drops_event_without_blocking(self):
        with TemporaryDirectory() as temp_dir:
            logger = JSONLEventLogger(
                temp_dir,
                queue_size=1,
                register_atexit=False,
            )
            writer_entered = threading.Event()
            release_writer = threading.Event()

            def blocked_write(_event):
                writer_entered.set()
                release_writer.wait(timeout=2)

            with patch.object(logger, "_write_event", side_effect=blocked_write):
                self.assertTrue(logger.log_event("session", "first"))
                self.assertTrue(writer_entered.wait(timeout=1))
                self.assertTrue(logger.log_event("session", "second"))
                self.assertFalse(logger.log_event("session", "third"))
                release_writer.set()
                self.assertTrue(logger.close())

            self.assertEqual(logger.get_stats()["dropped"], 1)

    def test_write_failure_is_counted_and_worker_continues(self):
        with TemporaryDirectory() as temp_dir:
            logger = JSONLEventLogger(temp_dir, register_atexit=False)
            with patch.object(logger, "_write_event", side_effect=OSError("disk full")):
                self.assertTrue(logger.log_event("session", "event"))
                self.assertTrue(logger.close())

            stats = logger.get_stats()
            self.assertEqual(stats["write_failures"], 1)
            self.assertEqual(stats["written"], 0)

    def test_log_after_close_is_rejected(self):
        with TemporaryDirectory() as temp_dir:
            logger = JSONLEventLogger(temp_dir, register_atexit=False)
            self.assertTrue(logger.close())
            self.assertFalse(logger.log_event("session", "late_event"))
            self.assertEqual(logger.get_stats()["dropped"], 1)


if __name__ == "__main__":
    unittest.main()
