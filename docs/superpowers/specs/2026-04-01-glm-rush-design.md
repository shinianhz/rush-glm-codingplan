# GLM Coding Plan Rush Purchase Script - Design Doc

**Date**: 2026-04-01
**Status**: Approved

## Background

GLM Coding Plan (https://bigmodel.cn/glm-coding) is a subscription product offering three tiers: Lite (¥132.3/qtr), Pro (¥402.3/qtr), Max (¥1266.3/qtr). Daily sales open at 10:00 AM but the site is consistently overloaded at that time, making manual purchase nearly impossible.

## Requirements

| Item | Value |
|------|-------|
| Target page | `https://bigmodel.cn/glm-coding` |
| Target plan | Configurable (Lite/Pro/Max) via `config.json` |
| Login | Phone number + password (automated) |
| Payment | Account balance auto-deduction (no scan needed) |
| Run mode | Manual start before 10:00, precise execution at 10:00:00 |
| Platform | macOS, Python 3.10+ |

## Architecture

Hybrid approach: Playwright browser automation + direct API calls.

### 3-Phase Execution

**Phase 1: Pre-login** (on script start)
- Playwright headed mode opens login page
- Auto-fill phone + password, submit login
- Capture cookies, auth token
- Intercept network requests to identify purchase API endpoint
- Save API structure to `api_capture.json`

**Phase 2: Precise Wait** (after login until 10:00)
- Heartbeat every 5s to keep session alive
- Display countdown timer
- Pre-warm connections at 9:59:58 (DNS + TCP + TLS)
- Pre-build API requests at 9:59:59

**Phase 3: Rush Purchase** (at 10:00:00)
- **Channel 1 (API direct)**: POST to purchase endpoint with captured auth
  - Retry up to `max_retries` times, interval `retry_interval_ms`
  - On 200 + success/order_id: done
  - On 429/503: retry
  - On "already subscribed"/"insufficient balance": stop
- **Channel 2 (Browser fallback)**: if all API attempts fail
  - Refresh page, click plan button, click confirm
  - Retry up to `max_retries` times
  - On success toast: done

### Configuration

```json
{
  "phone": "",
  "password": "",
  "plan": "Pro",
  "target_time": "10:00:00",
  "max_retries": 10,
  "retry_interval_ms": 500
}
```

### Project Structure

```
glm-rush/
├── config.json          # User config (gitignored)
├── config.example.json  # Template without secrets
├── main.py              # Entry: parse config -> login -> wait -> rush
├── login.py             # Playwright login + session capture
├── sniper.py            # Core: API channel + browser channel + retry
├── capture.py           # API interception, endpoint detection, caching
├── utils.py             # Countdown, logging, timing utils
├── requirements.txt     # playwright, httpx, rich
└── .gitignore           # config.json, debug/, api_capture.json
```

### Dependencies

- `playwright` - Browser automation (Chromium)
- `httpx` - Async HTTP client for API direct calls
- `rich` - Terminal UI (countdown, colored output)

### Error Handling

| Error | Action |
|-------|--------|
| Login failure (wrong password/locked) | Exit immediately with message |
| Page load timeout | Screenshot to `debug/`, retry |
| Session expired | Auto re-login |
| Network disconnected | Exponential backoff, max 5 retries |
| API capture fails (no endpoint found) | Fall back to browser-only mode |
| Unknown error | Screenshot + log, stop to prevent misuse |

### API Capture Strategy

1. After login, enable `page.route()` to intercept all requests
2. Navigate to `/glm-coding` page
3. Filter requests to `bigmodel.cn` API endpoints
4. Auto-identify purchase-related endpoints by URL keywords: `subscribe`, `order`, `purchase`, `plan`
5. Save URL, headers, body format to `api_capture.json`
6. If no purchase API found, operate in browser-only mode

### Timing Precision

- 9:59:58 - Connection pre-warm (DNS resolve, TCP connect, TLS handshake)
- 9:59:59 - Pre-build all request objects in memory
- 10:00:00 - Fire all requests immediately

### Result Detection

- API: HTTP 200 + body contains `success` or `order_id` -> success
- API: body contains "already subscribed" / "insufficient balance" -> known failure, stop
- Browser: page shows success toast or "已订阅" -> success
- All retries exhausted -> report failure

### Logging

- Each attempt logged with timestamp, status code, response summary to `rush.log`
- Terminal shows real-time: countdown -> per-attempt result -> final status
