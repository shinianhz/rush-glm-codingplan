# utils.py
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path

VALID_PLANS = ("Lite", "Pro", "Max")

LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(message)s"


@dataclass
class Config:
    phone: str
    password: str
    plan: str
    target_time: str
    max_retries: int = 10
    retry_interval_ms: int = 500

    @property
    def target_time_obj(self) -> time:
        h, m, s = (int(x) for x in self.target_time.split(":"))
        return time(h, m, s)


def load_config(path: str = "config.json") -> Config:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(p) as f:
        data = json.load(f)

    phone = data.get("phone", "")
    if not phone:
        raise ValueError("'phone' is required in config")

    password = data.get("password", "")
    if not password:
        raise ValueError("'password' is required in config")

    plan = data.get("plan", "Pro")
    if plan not in VALID_PLANS:
        raise ValueError(f"'plan' must be one of {VALID_PLANS}, got '{plan}'")

    return Config(
        phone=phone,
        password=password,
        plan=plan,
        target_time=data.get("target_time", "10:00:00"),
        max_retries=data.get("max_retries", 10),
        retry_interval_ms=data.get("retry_interval_ms", 500),
    )


def seconds_until_target(target_str: str) -> float:
    """Seconds from now until the next occurrence of target_time.
    Negative if that time has already passed today."""
    h, m, s = (int(x) for x in target_str.split(":"))
    target = time(h, m, s)
    now = datetime.now()
    target_dt = datetime.combine(now.date(), target)
    diff = (target_dt - now).total_seconds()
    return diff


def setup_logger(log_file: str = "rush.log") -> logging.Logger:
    logger = logging.getLogger("glm_rush")
    logger.setLevel(logging.DEBUG)

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(LOG_FORMAT))
    logger.addHandler(fh)

    return logger


def ensure_debug_dir() -> Path:
    d = Path("debug")
    d.mkdir(exist_ok=True)
    return d
