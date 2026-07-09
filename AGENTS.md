# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概览

基金策略分析 API 服务，基于 FastAPI 构建。提供多种量化技术指标策略（RSI、MACD、布林带、双重确认）的基金交易信号分析。

## 常用命令

```bash
# 依赖管理
uv sync
uv sync --extra test          # 安装测试依赖

# 启动服务
uvicorn src.python_cli_starter.main:app --reload

# 运行测试
uv run pytest tests/ -v
uv run pytest tests/ -k test_rsi -v    # 运行特定测试

# Docker 部署
docker compose up -d                    # 启动服务
docker compose logs -f                     # 查看日志
docker compose down                      # 停止服务
```

## 代码架构

```
src/python_cli_starter/
├── main.py                 # FastAPI 入口，路由定义
├── schemas.py              # Pydantic 验证/响应模型
└── strategies/            # 量化策略模块
    ├── __init__.py                # 策略注册表
    ├── rsi_strategy.py            # RSI 策略
    ├── macd_strategy.py           # MACD 趋势策略
    ├── bollinger_bands_strategy.py # 布林带策略
    └── dual_confirmation_strategy.py # 双重确认策略

tests/
├── conftest.py          # pytest 配置
├── test_api.py          # API 集成测试
└── test_strategies.py    # 策略单元测试
```

## API 端点

| 端点 | 方法 | 功能 |
|------|------|------|
| `GET /health` | 健康检查 |
| `GET /strategies` | 获取所有可用策略列表 |
| `GET /strategies/{strategy_name}/{fund_code}` | 执行指定策略分析 |

## 策略说明

### RSI 策略 (`rsi`)
- **参数**: `fund_code`
- **信号**: RSI ≤ 30 买入，RSI ≥ 70 卖出，否则观望

### MACD 策略 (`macd`)
- **参数**: `fund_code`, `is_holding` (必填)
- **信号**: 金叉买入，死叉卖出

### 布林带策略 (`bollinger_bands`)
- **参数**: `fund_code`, `is_holding` (必填)
- **信号**: 跌破下轨买入，回归中轨卖出

### 双重确认策略 (`dual_confirmation`)
- **参数**: `fund_code`, `is_holding` (必填)
- **信号**: 趋势向上 + RSI 超卖时买入，趋势向下时卖出

## 策略扩展

新增策略步骤：

1. 在 `strategies/` 目录创建策略模块
2. 实现 `run_strategy(fund_code: str, is_holding: bool = False) -> dict`
3. 返回格式：
   ```python
   {
       "signal": "买入" | "卖出" | "持有/观望",
       "reason": "信号原因说明",
       "latest_date": date,
       "latest_close": float,
       "metrics": {"指标名": 值, ...}
   }
   ```
4. 在 `strategies/__init__.py` 注册：
   ```python
   from . import your_strategy
   STRATEGY_REGISTRY["your_strategy"] = your_strategy.run_strategy
   ```

## Docker 部署

```bash
# 构建镜像
docker build -t fund-strategies-api:latest .

# 启动服务
docker compose up -d

# 查看日志
docker compose logs -f

# 停止服务
docker compose down
```

服务启动后可访问：`http://localhost:8000/docs`

## 技术栈

- FastAPI >=0.115.12
- Pydantic >=2.11.4
- Pandas >=2.0.0
- AkShare >=1.17.87
- Pytest >=8.0.0 (测试)
