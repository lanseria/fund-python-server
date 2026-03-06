# 基金策略分析 API 服务

基于 FastAPI 构建的基金量化策略分析服务，提供多种技术指标策略（RSI、MACD、布林带、双重确认）的交易信号分析功能，同时支持市场板块数据实时监控。

## ✨ 功能特性

### 策略分析
- **RSI 策略**: 基于相对强弱指数，超卖买入、超买卖出
- **MACD 策略**: 趋势跟踪，金叉买入、死叉卖出
- **布林带策略**: 反转策略，下轨买入、回归中轨卖出
- **双重确认策略**: 趋势 + RSI 择时

### 市场数据监控
- **东方财富板块**: 实时获取行业板块涨跌幅、总市值、换手率、成交额
- **同花顺板块**: 获取板块涨跌幅、资金净流入、涨跌家数、成交额占比
- **定时任务**: 交易日交易时段每 15 分钟自动更新数据
- **前端面板**: 内置 Vue 3 + Tailwind CSS 实时监控页面

### 其他功能
- **RESTful API**: 简洁的 API 设计，易于集成
- **数据源**: 使用 AkShare 获取基金净值数据
- **数据库**: PostgreSQL 存储板块历史数据
- **Docker 支持**: 多阶段构建优化，支持容器化部署
- **图表数据**: 提供 RSI 策略历史图表数据用于前端可视化

## 🛠️ 技术栈

- **Web 框架**: FastAPI >=0.115.12
- **数据验证**: Pydantic >=2.11.4
- **数据处理**: Pandas >=2.0.0, NumPy >=2.0.2
- **数据源**: AkShare >=1.17.87
- **异步**: HTTPX >=0.27.0
- **浏览器自动化**: Playwright >=1.41.0
- **数据库**: SQLAlchemy >=2.0.0, asyncpg >=0.29.0, Alembic >=1.18.4
- **定时任务**: APScheduler >=3.10.4
- **包管理**: uv
- **测试框架**: Pytest >=8.0.0

## 🚀 快速开始

### 本地运行

```bash
# 安装依赖
uv sync

# 数据库迁移
uv run alembic upgrade head

# 启动服务
uvicorn src.python_cli_starter.main:app --reload
```

服务启动后访问：
- 监控面板: `http://localhost:8000/`
- API 文档: `http://localhost:8000/docs`

### 运行测试

```bash
# 安装测试依赖
uv sync --extra test

# 运行所有测试
uv run pytest tests/ -v

# 运行特定测试
uv run pytest tests/ -k test_rsi -v
```

## 📡 API 端点

### Dashboard
| 端点 | 方法 | 功能 |
|------|------|------|
| `GET /` | 板块监控前端面板 |

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

### Market
| 端点 | 方法 | 功能 |
|------|------|------|
| `GET /market/df_sectors` | 获取东方财富行业板块数据 |
| `GET /market/ths_sectors` | 获取同花顺行业板块数据 |
| `GET /market/sector_names` | 获取两家数据源的板块名称列表 |
| `POST /market/fetch/eastmoney` | 手动触发获取东方财富板块数据 |
| `POST /market/upload/eastmoney` | 手动上传东方财富JSONP数据 |

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

## 🗄️ 数据库配置

项目使用 PostgreSQL 数据库，通过环境变量配置连接：

```bash
# .env 文件示例
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/fund_db
```

### Alembic 迁移命令

```bash
# 创建迁移
uv run alembic revision --autogenerate -m "描述信息"

# 执行迁移
uv run alembic upgrade head

# 回滚迁移
uv run alembic downgrade -1
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
