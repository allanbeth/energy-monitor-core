import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from flask import Flask

from energy_monitor_core.app import _parse_log_entries, _read_recent_log_lines, _register_routes


HEADER = "2026-11-08 02:40:15,773 INFO [energy_monitor_core.module.ina] "
NEXT = "2026-11-08 02:40:16,553 WARNING [energy_monitor_core.pollers.victron] "


class LogRecordTests(unittest.TestCase):
    def test_single_and_multiline_json_records(self):
        text = (
            HEADER + 'Polled module snapshot: {\n  "module": "ina",\n'
            '  "sensor_types": ["solar", "wind"],\n  "nested": {"value": 2}\n}\n'
            + NEXT + "Connection failed"
        )
        entries = _parse_log_entries(text)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["timestamp"], "2026-11-08 02:40:15,773")
        self.assertEqual(entries[0]["module"], "energy_monitor_core.module.ina")
        self.assertEqual(entries[0]["message"], "Polled module snapshot:")
        self.assertEqual(entries[0]["data"]["nested"], {"value": 2})
        self.assertEqual(entries[1]["level"], "WARNING")
        self.assertEqual(entries[1]["message"], "Connection failed")
        self.assertNotIn("data", entries[1])

    def test_inline_json_arrays_and_empty_objects(self):
        entries = _parse_log_entries(
            HEADER + 'Payload: {"x": "<script>"}\n'
            + HEADER + "Values: [1, 2]\n"
            + HEADER + "Empty: {}"
        )
        self.assertEqual([entry["data"] for entry in entries], [{"x": "<script>"}, [1, 2], {}])

    def test_non_json_and_tracebacks_are_preserved(self):
        text = (
            HEADER + "Broken payload: {\n  not json\n}\n"
            + NEXT + "Request failed\nTraceback (most recent call last):\n  failure"
        )
        entries = _parse_log_entries(text)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["message"], "Broken payload: {\n  not json\n}")
        self.assertNotIn("data", entries[0])
        self.assertIn("Traceback", entries[1]["message"])
        self.assertNotIn("data", entries[1])

    def test_reader_includes_record_cut_by_line_limit(self):
        text = HEADER + 'Payload: {\n  "x": 1,\n  "y": 2\n}\n' + NEXT + "Last"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "app.log"
            path.write_text(text, encoding="utf-8")
            result = _read_recent_log_lines(path, 3)
            self.assertEqual(result, text)
            self.assertEqual(len(_parse_log_entries(result)), 2)
            self.assertEqual(_read_recent_log_lines(path, 1), NEXT + "Last")

    def test_api_returns_complete_records_for_core_and_module(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "logs" / "modules").mkdir(parents=True)
            log = HEADER + 'Snapshot: {\n  "values": [1, 2]\n}\n' + NEXT + "Done"
            (root / "logs" / "app.log").write_text(log, encoding="utf-8")
            (root / "logs" / "modules" / "ina.log").write_text(log, encoding="utf-8")
            app = Flask(__name__)
            app.config.update(SECRET_KEY="test", APP_ROOT=str(root), SERVER_INSTANCE_ID="test")
            config = Mock()
            config.get_config.return_value = {"auth": {"enabled": False}}
            _register_routes(app, config, Mock(), Mock(), None)
            client = app.test_client()
            for url, module in (("/api/logs/recent", None), ("/api/logs/recent?module=ina", "ina")):
                response = client.get(url)
                self.assertEqual(response.status_code, 200)
                payload = response.get_json()
                self.assertEqual(payload["module"], module)
                self.assertEqual(payload["lines"], log)
                self.assertEqual(len(payload["entries"]), 2)
                self.assertEqual(payload["entries"][0]["data"], {"values": [1, 2]})


if __name__ == "__main__":
    unittest.main()
