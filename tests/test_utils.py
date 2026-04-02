# tests/test_utils.py
import json
from datetime import datetime as real_datetime

import pytest

import utils
from utils import load_config, seconds_until_target, setup_logger


class TestLoadConfig:
    def test_loads_valid_config(self, tmp_path):
        config_data = {
            "phone": "13800138000",
            "password": "testpass",
            "plan": "Pro",
            "target_time": "10:00:00",
            "max_retries": 10,
            "retry_interval_ms": 500,
        }
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config_data))

        config = load_config(str(config_file))
        assert config.phone == "13800138000"
        assert config.password == "testpass"
        assert config.plan == "Pro"
        assert config.max_retries == 10

    def test_rejects_empty_phone(self, tmp_path):
        config_data = {
            "phone": "",
            "password": "testpass",
            "plan": "Pro",
            "target_time": "10:00:00",
            "max_retries": 10,
            "retry_interval_ms": 500,
        }
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config_data))

        with pytest.raises(ValueError, match="phone"):
            load_config(str(config_file))

    def test_rejects_invalid_plan(self, tmp_path):
        config_data = {
            "phone": "13800138000",
            "password": "testpass",
            "plan": "Ultra",
            "target_time": "10:00:00",
            "max_retries": 10,
            "retry_interval_ms": 500,
        }
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config_data))

        with pytest.raises(ValueError, match="plan"):
            load_config(str(config_file))

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_config("/nonexistent/config.json")

    def test_defaults_applied(self, tmp_path):
        config_data = {
            "phone": "13800138000",
            "password": "testpass",
            "plan": "Lite",
            "target_time": "10:00:00",
        }
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config_data))

        config = load_config(str(config_file))
        assert config.max_retries == 10
        assert config.retry_interval_ms == 500
        assert config.access_token == ""
        assert config.storage_state_path == ""

    def test_rejects_invalid_target_time(self, tmp_path):
        config_data = {
            "phone": "13800138000",
            "password": "testpass",
            "plan": "Pro",
            "target_time": "10:00",
        }
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config_data))

        with pytest.raises(ValueError, match="target_time"):
            load_config(str(config_file))

    def test_rejects_too_short_phone(self, tmp_path):
        config_data = {
            "phone": "12345",
            "password": "testpass",
            "plan": "Pro",
            "target_time": "10:00:00",
        }
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config_data))

        with pytest.raises(ValueError, match="phone"):
            load_config(str(config_file))


class TestSecondsUntilTarget:
    def test_returns_positive_seconds(self):
        secs = seconds_until_target("23:59:59")
        assert secs > 0

    def test_returns_negative_if_past(self):
        class FixedDateTime(real_datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 4, 2, 0, 0, 5)

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(utils, "datetime", FixedDateTime)
        try:
            secs = seconds_until_target("00:00:01")
        finally:
            monkeypatch.undo()

        assert -5 <= secs <= -3

    def test_rolls_forward_to_next_day_when_time_passed(self, monkeypatch):
        class FixedDateTime(real_datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 4, 2, 23, 59, 58)

        monkeypatch.setattr(utils, "datetime", FixedDateTime)

        secs = seconds_until_target("00:00:01")

        assert 2 <= secs <= 4
