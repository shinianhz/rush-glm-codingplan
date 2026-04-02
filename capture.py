# capture.py
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

PURCHASE_KEYWORDS = ("subscribe", "order", "purchase", "buy", "plan", "pay", "payment", "product", "billing")
CAPTURE_FILE = "api_capture.json"


@dataclass
class PurchaseEndpoint:
    url: str
    method: str
    headers: dict[str, str] | None = None
    body: dict[str, Any] | None = None


def detect_purchase_endpoint(
    requests: list[dict[str, Any]],
) -> PurchaseEndpoint | None:
    """From a list of intercepted requests, find the one most likely to be
    the purchase/subscribe endpoint."""
    candidates: list[dict[str, Any]] = []
    for req in requests:
        url = req.get("url", "").lower()
        method = req.get("method", "GET").upper()
        if method != "POST":
            continue
        if any(kw in url for kw in PURCHASE_KEYWORDS):
            candidates.append(req)

    if not candidates:
        return None

    best = candidates[0]
    return PurchaseEndpoint(
        url=best["url"],
        method=best.get("method", "POST"),
        headers=best.get("headers"),
        body=best.get("body"),
    )


def save_captured_endpoint(endpoint: PurchaseEndpoint, path: str = CAPTURE_FILE) -> None:
    data = asdict(endpoint)
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False))


def load_captured_endpoint(path: str = CAPTURE_FILE) -> PurchaseEndpoint | None:
    p = Path(path)
    if not p.exists():
        return None
    data = json.loads(p.read_text())
    return PurchaseEndpoint(**data)
