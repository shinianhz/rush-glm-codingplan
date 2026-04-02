# utils.py
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path

VALID_PLANS = ("Lite", "Pro", "Max")
VALID_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}

LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(message)s"

PURCHASE_URL = "https://bigmodel.cn/api/biz/pay/preview"

# plan → default productId (monthly tier). Override with config.product_id.
PLAN_PRODUCT_MAP = {
    "Lite": "product-02434c",   # ¥49/mo
    "Pro":  "product-1df3e1",   # ¥149/mo
    "Max":  "product-5d3a03",   # Max quarterly ¥1266.3
}


@dataclass
class Config:
    phone: str
    password: str
    plan: str
    target_time: str
    max_retries: int = 10
    retry_interval_ms: int = 500
    purchase_url: str = ""
    purchase_method: str = "POST"
    purchase_body: dict | None = None
    product_id: str = ""
    # Optional: paste JWT from DevTools → Network → any bigmodel API → Request Headers → Authorization (without "Bearer " is ok)
    access_token: str = ""
    # Optional: path to Playwright storage_state JSON (browser.new_context(storage_state=...)) to skip re-login
    storage_state_path: str = ""

    @property
    def target_time_obj(self) -> time:
        return parse_target_time(self.target_time)

    @property
    def resolved_product_id(self) -> str:
        """Return explicit product_id if set, otherwise map from plan."""
        if self.product_id:
            return self.product_id
        return PLAN_PRODUCT_MAP.get(self.plan, "")


def parse_target_time(target_str: str) -> time:
    try:
        h, m, s = (int(x) for x in target_str.split(":"))
        return time(h, m, s)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "'target_time' must use HH:MM:SS format, e.g. '10:00:00'"
        ) from exc


def mask_phone(phone: str) -> str:
    if len(phone) < 7:
        return phone
    return phone[:3] + "****" + phone[-4:]


def load_config(path: str = "config.json") -> Config:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(p) as f:
        data = json.load(f)

    phone = data.get("phone", "")
    if not phone:
        raise ValueError("'phone' is required in config")
    if len(phone) < 7:
        raise ValueError("'phone' must be at least 7 characters long")

    password = data.get("password", "")
    if not password:
        raise ValueError("'password' is required in config")

    plan = data.get("plan", "Pro")
    if plan not in VALID_PLANS:
        raise ValueError(f"'plan' must be one of {VALID_PLANS}, got '{plan}'")

    target_time = data.get("target_time", "10:00:00")
    parse_target_time(target_time)

    purchase_method = str(data.get("purchase_method", "POST")).upper()
    if purchase_method not in VALID_METHODS:
        raise ValueError(
            f"'purchase_method' must be one of {sorted(VALID_METHODS)}, got '{purchase_method}'"
        )

    return Config(
        phone=phone,
        password=password,
        plan=plan,
        target_time=target_time,
        max_retries=data.get("max_retries", 10),
        retry_interval_ms=data.get("retry_interval_ms", 500),
        purchase_url=data.get("purchase_url", ""),
        purchase_method=purchase_method,
        purchase_body=data.get("purchase_body"),
        product_id=(data.get("product_id") or "").strip(),
        access_token=(data.get("access_token") or "").strip(),
        storage_state_path=(data.get("storage_state_path") or "").strip(),
    )


def seconds_until_target(target_str: str) -> float:
    """Seconds until the next target time, unless we are only moments late today."""
    target = parse_target_time(target_str)
    now = datetime.now()
    target_dt = datetime.combine(now.date(), target)

    diff = (target_dt - now).total_seconds()
    if diff < 0 and abs(diff) > 60:
        target_dt += timedelta(days=1)
        diff = (target_dt - now).total_seconds()

    return diff


def setup_logger(log_file: str = "rush.log") -> logging.Logger:
    logger = logging.getLogger("glm_rush")
    logger.setLevel(logging.DEBUG)

    for handler in list(logger.handlers):
        if isinstance(handler, logging.FileHandler) and getattr(handler, "baseFilename", None):
            if Path(handler.baseFilename) == Path(log_file).resolve():
                return logger

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(LOG_FORMAT))
    logger.addHandler(fh)

    return logger


def ensure_debug_dir() -> Path:
    d = Path("debug")
    d.mkdir(exist_ok=True)
    return d


def strip_bearer_prefix(token: str) -> str:
    t = token.strip()
    if t.lower().startswith("bearer "):
        return t[7:].strip()
    return t


def httpx_cookies_from_playwright(rows: list[dict]) -> "httpx.Cookies":
    import httpx

    jar = httpx.Cookies()
    for c in rows:
        name = c.get("name")
        value = c.get("value")
        if not name or value is None:
            continue
        domain = (c.get("domain") or "").lstrip(".")
        path = c.get("path") or "/"
        jar.set(name, value, domain=domain, path=path)
    return jar
