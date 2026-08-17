# 基金策略分析 API 服务

基于 FastAPI 构建的基金量化策略分析服务，提供多种技术指标策略（RSI、MACD、布林带、双重确认）的交易信号分析功能，并支持板块主力资金数据查询。

## ✨ 功能特性

### 策略分析
- **RSI 策略**: 基于相对强弱指数，超卖买入、超买卖出
- **MACD 策略**: 趋势跟踪，金叉买入、死叉卖出
- **布林带策略**: 反转策略，下轨买入、回归中轨卖出
- **双重确认策略**: 趋势 + RSI 择时

### 基金数据
- **基金完整信息**: 一次拿全基本信息 + 历史净值 + 费率表
- **实时估值**: 盘中分钟级估算净值（powercloud 聚合）
- **昨日净值**: 最近一个交易日官方净值
- **手续费**: 7 类费率信息（申购/赎回/运作/认购等）

### 板块主力资金
- **板块资金表**: 全量行业/概念板块的主力资金、散户资金、成交额
- **主力行为判定**: 按主力强度自动归类为抢筹 / 建仓 / 洗盘 / 出货
- **按名查询**: 通过板块名查询主力行为（精确 + 模糊兜底）

### 其他功能
- **RESTful API**: 简洁的 API 设计，易于集成
- **数据源**: 使用 AkShare 获取基金净值数据
- **Docker 支持**: 多阶段构建优化，支持容器化部署
- **图表数据**: 提供 RSI 策略历史图表数据用于前端可视化

## 🛠️ 技术栈

- **Web 框架**: FastAPI >=0.115.12
- **数据验证**: Pydantic >=2.11.4
- **数据处理**: Pandas >=2.0.0, NumPy >=2.0.2
- **数据源**: AkShare >=1.17.87
- **异步 HTTP**: HTTPX >=0.27.0
- **包管理**: uv
- **测试框架**: Pytest >=8.0.0

## 🚀 快速开始

### 本地运行

```bash
# 安装依赖
uv sync

# 启动服务
uvicorn src.python_cli_starter.main:app --reload
```

服务启动后访问：
- API 文档: `http://localhost:8000/docs`

### 运行测试

```bash
# 安装测试依赖
uv sync --extra test

# 运行所有测试
uv run pytest tests/ -v

# 运行特定测试文件
uv run pytest tests/test_fund_realtime.py -v

# 按关键字筛选
uv run pytest tests/ -k test_rsi -v

# 仅运行 CSV 参数化用例（见下方「测试数据源」）
uv run pytest tests/test_fund_realtime.py::TestCSVFundRealtime -v
```

### 测试结构

| 测试文件 | 覆盖范围 |
|----------|----------|
| `tests/test_api.py` | API 集成测试（健康检查、策略调用等） |
| `tests/test_charts.py` | RSI 图表数据接口 |
| `tests/test_strategies.py` | 策略单元测试（RSI / MACD / 布林带 / 双重确认） |
| `tests/test_fund_fee.py` | 基金手续费接口（`/funds/{code}/fee`） |
| `tests/test_fund_info.py` | 基金完整信息接口（`/fund/info/{code}`） |
| `tests/test_fund_realtime.py` | 实时估值与昨日净值接口（`/fund/realtime/{code}`、`/fund/nav/{code}`） |
| `tests/test_sector_capital.py` | 板块主力资金接口（`/sector/capital`、`/sector/capital/action/{name}`） |

所有测试均通过 mock 注入伪造数据，**不依赖网络**，可离线稳定运行。

### 测试数据源

`test_fund_realtime.py` 中的 `TestCSVFundRealtime` 测试类以仓库根目录的 [`test_funds.csv`](./test_funds.csv) 作为数据源，参数化校验其中**每一只**基金（含开放式 / QDII / LOF 等多类型）都能通过 powercloud 接口被查到实时估值。

- CSV 采用 `utf-8-sig` 读取（文件含 BOM）
- 每行一只基金，参数化用例以基金代码为用例名，失败时可精确定位
- **增删基金无需改测试代码**：向 `test_funds.csv` 增删行后，参数化用例会自动跟随

## 📡 API 端点

### System
| 端点 | 方法 | 功能 |
|------|------|------|
| `GET /health` | 健康检查 |

### Strategies
| 端点 | 方法 | 功能 |
|------|------|------|
| `GET /strategies` | 获取所有可用策略列表 |
| `GET /strategies/{strategy_name}/{fund_code}` | 执行指定策略分析 |

### Charts
| 端点 | 方法 | 功能 |
|------|------|------|
| `GET /charts/rsi/{fund_code}` | 获取 RSI 策略图表数据 |

### Fund
| 端点 | 方法 | 功能 |
|------|------|------|
| `GET /funds/{fund_code}/fee` | 获取基金手续费信息 |
| `GET /fund/info/{fundCode}` | 获取基金完整信息（基本信息+历史净值+费率） |
| `GET /fund/realtime/{fundCode}` | 获取基金盘中实时估值（分钟级，powercloud 聚合） |
| `GET /fund/nav/{fundCode}` | 获取基金昨日真实净值 |

> **实时估值数据源**：powercloud 聚合接口（已封装东财实时估算 + 历史净值回退 + QDII 处理）。`estimateNav` 取东财原值 `gsz`；`quoteSource`/`message` 标识数据状态；`intraday` 返回盘中分时数据（非交易时段为空）；`holdingsDate`/`holdings` 返回重仓股持仓明细（`pct` 为占净值比例，季报口径，无股票持仓为空数组）。QDII/货币型等无盘中估值的基金自动回退到最近净值。

### SectorCapital（板块主力资金）
| 端点 | 方法 | 功能 |
|------|------|------|
| `GET /sector/capital` | 获取板块主力资金数据表（`?type=industry` 行业 / `concept` 概念） |
| `GET /sector/capital/action/{sector_name}` | 按板块名查询主力行为（精确 + 模糊兜底） |

> **板块资金数据源**：东方财富数据中心 `data.eastmoney.com/dataapi/bkzj/getbkzj`。计算口径：主力暗盘 = 主力资金 − 散户资金；主力强度 = 主力暗盘 / 成交额 × 100；主力行为按强度归类（`>=3` 抢筹 / `[1,3)` 建仓 / `(-1,1)` 洗盘 / `<=-1` 出货）。
>
> **缓存策略**：内存缓存（不落库）。交易日 9:30-16:00 每 10 分钟刷新一次；16:00 后冻结到次日开盘；服务启动即预热；刷新失败保留旧缓存。两个接口均读缓存。

### 策略参数说明

- `strategy_name`: 策略名称（`rsi`, `macd`, `bollinger_bands`, `dual_confirmation`）
- `fund_code`: 基金代码（6位数字）
- `is_holding`: (可选) 对于需要持仓状态的策略，指定当前是否持有该基金（`true`/`false`）

#### 示例

```bash
# RSI 策略
curl http://localhost:8000/strategies/rsi/161725

# MACD 策略（需要持仓状态）
curl http://localhost:8000/strategies/macd/161725?is_holding=false

# 布林带策略
curl http://localhost:8000/strategies/bollinger_bands/161725?is_holding=true

# 双重确认策略
curl http://localhost:8000/strategies/dual_confirmation/161725?is_holding=false

# 板块主力资金（行业）
curl http://localhost:8000/sector/capital?type=industry

# 按板块名查主力行为
curl http://localhost:8000/sector/capital/action/食品饮料
```

## 📊 响应格式

```json
{
  "fund_code": "161725",
  "strategy_name": "rsi",
  "signal": "持有/观望",
  "reason": "RSI (45.23) 处于 30 和 70 之间的中间区域。",
  "latest_date": "2026-01-20",
  "latest_close": 1.2345,
  "metrics": {
    "rsi_period": 14,
    "rsi_value": 45.23,
    "rsi_upper_band": 70.0,
    "rsi_lower_band": 30.0
  }
}
```

## 🐳 Docker 部署

### 构建并启动

```bash
# 构建镜像
docker build -t fund-strategies-api:latest .

# 启动服务
docker compose up -d
```

### 常用命令

```bash
# 查看日志
docker compose logs -f

# 停止服务
docker compose down

# 重新构建并启动
docker compose up -d --build
```

## 🧪 策略扩展

新增自定义策略步骤：

1. 在 `strategies/` 目录创建新模块，例如 `my_strategy.py`
2. 实现策略函数：

```python
def run_strategy(fund_code: str, is_holding: bool = False) -> dict:
    # 获取数据、计算指标、生成信号
    return {
        "signal": "买入" | "卖出" | "持有/观望",
        "reason": "信号原因说明",
        "latest_date": date,
        "latest_close": float,
        "metrics": {"指标名": 值, ...}
    }
```

3. 在 `strategies/__init__.py` 注册策略：

```python
from . import my_strategy
STRATEGY_REGISTRY["my_strategy"] = my_strategy.run_strategy
```

## 📄 License

MIT
