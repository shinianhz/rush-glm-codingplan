# GLM Coding Plan Rush

自动抢购智谱 GLM Coding Plan 的脚本。每天 10:00 开售时因访问量过大页面难以打开，此脚本通过 **API 直连 + 浏览器自动化** 双通道提高抢购成功率。

## 工作原理

```
启动脚本
  │
  ├─ 阶段 1: 登录
  │   打开浏览器 → 自动填入手机号+密码 → 登录
  │   抓取 cookies、auth token、购买 API 端点
  │
  ├─ 阶段 2: 等待
  │   每 5 秒心跳保持会话 → 实时倒计时
  │   目标前 2 秒预热网络连接
  │   目标前 1.5 秒忙等待确保精确触发
  │
  └─ 阶段 3: 抢购 (10:00:00 精确触发)
      通道 1: 直接调用购买 API（最快）
      通道 2: 浏览器自动化点击（兜底）
      每通道最多重试 10 次，间隔 500ms
```

## 快速开始

### 1. 环境要求

- macOS
- Python 3.10+
- 网络（需能访问 bigmodel.cn）

### 2. 安装

```bash
cd /Users/allen/Documents/Code/allen-\ project/GLM

# 安装 Python 依赖
pip3 install -r requirements.txt

# 安装 Chromium 浏览器（Playwright 用）
playwright install chromium
```

### 3. 配置

```bash
# 复制配置模板
cp config.example.json config.json

# 编辑配置，填入你的账号信息
vim config.json
```

`config.json` 配置说明：

| 字段 | 说明 | 可选值 | 默认值 |
|------|------|--------|--------|
| `phone` | 手机号（必填） | - | - |
| `password` | 密码（必填） | - | - |
| `plan` | 目标档位 | `Lite` / `Pro` / `Max` | `Pro` |
| `target_time` | 抢购时间 | `HH:MM:SS` 格式 | `10:00:00` |
| `max_retries` | 每通道最大重试次数 | 任意正整数 | `10` |
| `retry_interval_ms` | 重试间隔（毫秒） | 任意正整数 | `500` |

配置示例：

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

> **注意**: `config.json` 已被 `.gitignore` 排除，不会被提交到版本控制。

### 4. 运行

在每天 **9:55** 左右启动脚本：

```bash
cd /Users/allen/Documents/Code/allen-\ project/GLM
python3 main.py
```

运行后你会看到：

```
╭─────────────────────────────────╮
│ [1/4] 配置已加载                │
│ GLM Coding Plan Rush            │
│ 档位: Pro                       │
│ 手机: 138****8000               │
│ 目标时间: 10:00:00              │
│ 最大重试: 10                    │
╰─────────────────────────────────╯

[2/4] 登录中...
      已填入手机号 → 点击登录 → 登录成功 ✓
      导航到 Coding Plan 页面...
      API 抓包: 发现购买端点 POST /api/.../order ✓
      抓取 cookies + auth_token ✓

[3/4] 等待开售...
      倒计时 00:04:32
      ...
      预热连接...
      开火!

[4/4] 开始抢购 Pro!

通道 1: API 直连
      [API] 尝试 1/10 → 503
      [API] 尝试 2/10 → success
      API 通道成功! 购买成功! 订单号: ORD-xxxxx

浏览器将在 30 秒后关闭，你可以手动检查...
```

### 5. 前提条件

确保你的智谱账户**余额充足**，脚本使用余额自动扣款，不需要扫码支付。

## 测试

项目包含 23 个单元测试，覆盖配置加载、API 端点检测、抢购结果判定等核心逻辑。

### 运行全部测试

```bash
cd /Users/allen/Documents/Code/allen-\ project/GLM
python3 -m pytest tests/ -v
```

预期输出：

```
tests/test_capture.py  ......  (5 tests)
tests/test_sniper.py   .......  (11 tests)
tests/test_utils.py    .......  (7 tests)

23 passed in 0.03s
```

### 运行单个模块测试

```bash
# 配置加载测试
python3 -m pytest tests/test_utils.py -v

# API 端点检测测试
python3 -m pytest tests/test_capture.py -v

# 抢购结果判定测试
python3 -m pytest tests/test_sniper.py -v
```

### 测试覆盖范围

| 模块 | 测试数 | 覆盖内容 |
|------|--------|----------|
| `utils.py` | 7 | 配置加载验证（手机号为空、档位非法、文件不存在、默认值）、时间计算 |
| `capture.py` | 5 | 购买端点识别（subscribe/order/purchase 关键词匹配、POST 优先、无匹配返回 None） |
| `sniper.py` | 11 | API 结果分类（成功/已订阅/余额不足/503/429/500）、页面文本分类（购买成功/已订阅/售罄） |

### 端到端测试（需真实账号）

```bash
# 设置 target_time 为 1-2 分钟后，观察完整流程
python3 main.py
```

## 项目结构

```
rush-glm-codingplan/
├── main.py              # 入口：3 阶段编排（配置→登录→等待→抢购）
├── login.py             # Playwright 浏览器登录 + 会话抓取
├── sniper.py            # 双通道抢购逻辑（API 直连 + 浏览器点击）
├── capture.py           # 网络请求拦截，识别购买 API 端点
├── utils.py             # 配置加载、计时、日志
├── config.json          # 你的配置（已 gitignore）
├── config.example.json  # 配置模板
├── requirements.txt     # Python 依赖
├── tests/               # 单元测试（23 个）
│   ├── test_utils.py
│   ├── test_capture.py
│   └── test_sniper.py
└── .gitignore
```

## 常见问题

**Q: 登录失败怎么办？**

脚本会在 `debug/` 目录保存截图。检查：
1. 手机号和密码是否正确
2. 账号是否被锁定
3. 打开 `debug/login_failed.png` 查看页面状态

**Q: 两条通道都失败了？**

检查 `rush.log` 日志文件，每次尝试的状态码和响应都有记录。可能原因：
1. 余额不足
2. 已经购买过该档位
3. 服务器确实扛不住（增加 `max_retries` 和 `retry_interval_ms`）

**Q: 可以改抢其他时间吗？**

修改 `config.json` 中的 `target_time`，格式 `HH:MM:SS`。

**Q: 浏览器窗口可以隐藏吗？**

当前使用有头模式（`headless=False`）方便观察和调试。如需无头模式，编辑 `login.py` 中 `browser = await pw.chromium.launch(headless=False)` 改为 `headless=True`。
