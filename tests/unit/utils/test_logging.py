"""Tests for the structured JSON logging utility (tw_scanner.utils.logging)."""

import json
import logging
from io import StringIO

from tw_scanner.utils.logging import JsonFormatter, configure_logging, log_stage


class TestJsonFormatter:
    """JsonFormatter must emit compact, valid JSON with the required fields."""

    def _make_record(self, message: str, **extra: object) -> logging.LogRecord:
        record = logging.LogRecord(
            name="tw_scanner.test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg=message,
            args=(),
            exc_info=None,
        )
        if extra:
            record.event = extra
        return record

    def test_output_is_valid_json(self) -> None:
        fmt = JsonFormatter()
        record = self._make_record("hello")
        output = fmt.format(record)
        parsed = json.loads(output)  # must not raise
        assert isinstance(parsed, dict)

    def test_required_fields_present(self) -> None:
        fmt = JsonFormatter()
        record = self._make_record("test message")
        parsed = json.loads(fmt.format(record))
        assert "level" in parsed
        assert "logger" in parsed
        assert "message" in parsed

    def test_event_dict_merged_into_output(self) -> None:
        fmt = JsonFormatter()
        record = self._make_record("stage done")
        record.event = {"stage": "test.stage", "rows_in": 10, "rows_out": 8}  # type: ignore[attr-defined]
        parsed = json.loads(fmt.format(record))
        assert parsed["stage"] == "test.stage"
        assert parsed["rows_in"] == 10
        assert parsed["rows_out"] == 8

    def test_no_event_dict_does_not_error(self) -> None:
        fmt = JsonFormatter()
        record = self._make_record("plain message")
        output = fmt.format(record)
        parsed = json.loads(output)
        assert parsed["message"] == "plain message"


class TestLogStage:
    """log_stage() must emit the standard telemetry payload."""

    def _capture_log(self, logger_name: str = "tw_scanner") -> tuple[logging.Logger, StringIO]:
        buf = StringIO()
        handler = logging.StreamHandler(buf)
        handler.setFormatter(JsonFormatter())
        logger = logging.getLogger(logger_name)
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        return logger, buf

    def test_log_stage_emits_all_required_fields(self) -> None:
        _logger, buf = self._capture_log("tw_scanner.test_log_stage")
        log_stage(
            "test.pipeline",
            rows_in=100,
            rows_out=80,
            duration_ms=42,
            warnings=["w1"],
            logger_name="tw_scanner.test_log_stage",
        )
        raw = buf.getvalue().strip()
        assert raw, "log_stage 應寫入至少一行"
        payload = json.loads(raw.splitlines()[-1])
        assert payload["stage"] == "test.pipeline"
        assert payload["rows_in"] == 100
        assert payload["rows_out"] == 80
        assert payload["duration_ms"] == 42
        assert payload["warnings"] == ["w1"]

    def test_log_stage_empty_warnings_is_list(self) -> None:
        _logger, buf = self._capture_log("tw_scanner.test_log_stage_empty")
        log_stage(
            "test.noop",
            rows_in=0,
            rows_out=0,
            duration_ms=0,
            warnings=None,
            logger_name="tw_scanner.test_log_stage_empty",
        )
        raw = buf.getvalue().strip()
        payload = json.loads(raw.splitlines()[-1])
        assert payload["warnings"] == []

    def test_log_stage_none_rows_allowed(self) -> None:
        _logger, buf = self._capture_log("tw_scanner.test_log_stage_none")
        log_stage(
            "test.none_rows",
            rows_in=None,
            rows_out=None,
            duration_ms=None,
            warnings=[],
            logger_name="tw_scanner.test_log_stage_none",
        )
        raw = buf.getvalue().strip()
        payload = json.loads(raw.splitlines()[-1])
        assert payload["rows_in"] is None
        assert payload["rows_out"] is None


class TestConfigureLogging:
    """configure_logging() must be idempotent (calling twice doesn't add handlers)."""

    def test_idempotent(self) -> None:
        configure_logging()
        handler_count_before = len(logging.getLogger().handlers)
        configure_logging()
        assert len(logging.getLogger().handlers) == handler_count_before
