# GLM Coding Plan Rush

Auto-purchase script for Zhipu GLM Coding Plan. At 10:00 AM daily the purchase page is overloaded and nearly impossible to load. This script uses **browser login + pure API purchase** to maximize success rate.

## How It Works

```
Start
  |
  +-- Phase 1: Login (before 10:00)
  |     Open Chromium -> auto-fill phone + password -> login
  |     Capture cookies, JWT auth token, org/project headers
  |     Close browser immediately
  |
  +-- Phase 2: Wait
  |     Live countdown -> pre-warm connection at T-3s
  |     Busy-wait last 1.5s for precise trigger
  |
  +-- Phase 3: Rush (fires at exactly 10:00:00)
        POST /api/biz/pay/preview with productId
        Up to 10 retries, 500ms interval
```

## Quick Start

### 1. Requirements

- macOS / Windows
- Python 3.10+
- Network access to bigmodel.cn

### 2. Install

```bash
cd /Users/allen/Documents/Code/allen-\ project/GLM

# 安装 Python 依赖
pip3 install -r requirements.txt
playwright install chromium
```

### 3. Configure

```bash
cp config.example.json config.json
# Edit config.json with your account info
```

**config.json fields:**

| Field | Description | Default |
|-------|-------------|---------|
| `phone` | Phone number (required) | - |
| `password` | Password (required) | - |
| `plan` | Target plan: `Lite` / `Pro` / `Max` | `Pro` |
| `target_time` | Rush time in `HH:MM:SS` format | `10:00:00` |
| `max_retries` | Max retry attempts | `10` |
| `retry_interval_ms` | Retry interval in milliseconds | `500` |
| `product_id` | Override product ID (optional) | Auto-mapped from plan |
| `access_token` | JWT token (optional, auto-captured from login) | - |
| `storage_state_path` | Playwright storage state JSON (optional) | - |

**Product ID mapping (auto-resolved from plan):**

| Plan | Product ID | Price |
|------|-----------|-------|
| Lite | `product-02434c` | ¥49/mo |
| Pro | `product-1df3e1` | ¥149/mo |
| Max | `product-5d3a03` | ¥1,266.3/quarter |

To buy a different tier (e.g., quarterly/yearly), find the product ID from `product.json` and set `product_id` in config.

**Example config:**

```json
{
  "phone": "13800138000",
  "password": "your_password",
  "plan": "Pro",
  "target_time": "10:00:00",
  "max_retries": 10,
  "retry_interval_ms": 500
}
```

> **Note:** `config.json` is excluded from git via `.gitignore`.

### 4. Run

Start the script around **9:55 AM**:

```bash
python3 main.py
```

Expected output:

```
╭──────────────────────────────────────╮
│ [1/4] Config Loaded                  │
│ GLM Coding Plan Rush (Pure API)      │
│ Plan: Pro                             │
│ Phone: 138****8000                    │
│ Target: 10:00:00                      │
│ Product: product-1df3e1               │
╰──────────────────────────────────────╯

[2/4] Logging in...
      Logged in ✓
      API capture: found endpoint POST /api/biz/pay/preview ✓
      Captured site headers: bigmodel-organization, bigmodel-project ✓
      Captured cookies + auth_token ✓
      Browser closed, switched to pure API mode ✓

[3/4] Waiting for rush...
      Countdown 00:04:32
      ...
      Pre-warming connection...
      Fire!

[4/4] Rushing Pro!

      Product ID: product-1df3e1
      Auth Token: ✓
      Site Headers: [bigmodel-organization, bigmodel-project]

      Rush succeeded! Order confirmed!
```

### 5. Prerequisites

- Ensure your Zhipu account has **sufficient balance**. The script uses balance auto-deduction, no QR code scanning needed.

## Tests

```bash
python3 -m pytest tests/ -v
```

| Module | Tests | Coverage |
|--------|-------|----------|
| `test_utils.py` | 8 | Config loading, time calculation, validation |
| `test_capture.py` | 5 | Purchase endpoint detection, keyword matching |
| `test_sniper.py` | 10 | API result classification (success/failed/retry/401) |
| `test_main.py` | 3 | Execute rush flow, product ID resolution |

## Project Structure

```
rush-glm-codingplan/
├── main.py              # Entry point: 3-phase orchestration
├── login.py             # Playwright browser login + session capture
├── sniper.py            # API result detection + purchase logic
├── capture.py           # Network request interception
├── utils.py             # Config, timing, logging helpers
├── product.json         # Available product list from API
├── config.example.json  # Config template
├── requirements.txt     # Python dependencies
├── tests/               # Unit tests (29 total)
│   ├── test_utils.py
│   ├── test_capture.py
│   ├── test_sniper.py
│   └── test_main.py
└── .gitignore
```

## FAQ

**Q: Login failed?**

Screenshots are saved to `debug/`. Check:
1. Phone number and password are correct
2. Account is not locked
3. Open `debug/login_failed.png` for details

**Q: Purchase failed?**

Check `rush.log` for detailed request/response logs. Common causes:
1. Insufficient balance
2. Already subscribed to this plan
3. Server overloaded (increase `max_retries`)

**Q: Can I target a different time?**

Change `target_time` in `config.json` to any `HH:MM:SS` value.

**Q: How to find my product ID?**

1. Open `product.json` (captured from the API during login)
2. Or open browser DevTools -> Network tab -> click purchase -> find the `pay/preview` request -> copy `productId` from the payload
3. Set `product_id` in `config.json`
