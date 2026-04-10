# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Auto-purchase tool for Zhipu GLM Coding Plan subscriptions. The site's purchase page is overloaded at 10:00 AM daily, so this script uses a 3-phase approach: browser login to capture auth, countdown wait, then concurrent API rush at the target time.

## Commands

```bash
# Install dependencies
pip3 install -r requirements.txt
playwright install chromium

# Run the tool
python3 main.py

# Export Playwright storage state (optional, for reusing login sessions)
python3 export_playwright_state.py

# Run all tests
python3 -m pytest tests/ -v

# Run a single test file
python3 -m pytest tests/test_sniper.py -v
```

## Architecture

**3-phase flow orchestrated in `main.py`:**

1. **Login** (`login.py`) — Launches headful Chromium via Playwright, auto-fills phone/password, captures JWT auth token (from cookies, localStorage, or response interception), org/project headers (`bigmodel-organization`, `bigmodel-project`), and cookies. Browser closes immediately after capture. Returns a `LoginSession` dataclass.

2. **Wait** (`main.py:wait_for_target`) — Adaptive countdown with pre-warm HTTP/2 connection at T-3s and busy-wait in the final 1.5s for precise timing.

3. **Rush** (`main.py:execute_rush` → `_do_purchase`) — Fires concurrent POST requests to `/api/biz/pay/preview` with BALANCE preview fields. Uses N parallel async workers (configurable via `concurrency`) for `rush_duration_s` seconds. Results are classified in `main.py:_single_request`; `sniper.detect_api_result` remains for legacy helpers.

**Supporting modules:**

- `sniper.py` — API response classifier (`detect_api_result`) mapping HTTP status + body to `RushStatus` enum (SUCCESS/FAILED/RETRY/ALREADY_DONE). Also contains `rush_via_api` and `rush_direct` (older retry-loop approaches, kept as fallback).
- `capture.py` — Intercepts browser network requests to detect purchase endpoints by URL keyword matching. Saves/loads from `api_capture.json`.
- `utils.py` — `Config` dataclass, config loading/validation, time calculations, logging setup, cookie conversion. Contains `PLAN_PRODUCT_MAP` (plan name → product ID).
- `export_playwright_state.py` — Standalone utility to export Playwright storage state for session reuse.

**Config:** `config.json` (gitignored, template at `config.example.json`). Key fields: `phone`, `password`, `plan` (Lite/Pro/Max), `target_time` (HH:MM:SS), `concurrency`, `rush_duration_s`, `product_id` (overrides plan mapping), `access_token`, `storage_state_path`.

## Key Patterns

- All logging goes through `logging.getLogger("glm_rush")` with file output to `rush.log`
- Console output uses `rich` library (panels, colored text)
- API responses are in Chinese; error message checks use Chinese strings (e.g., `"已售罄"`, `"系统繁忙"`, `"已订阅"`)
- Auth token is resolved with priority: response interception > localStorage/sessionStorage > cookie > config `access_token`
- Product ID is resolved via `config.resolved_product_id`: explicit `product_id` in config takes precedence, then `PLAN_PRODUCT_MAP` in `utils.py`
