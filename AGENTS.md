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
├── fund_fee.py             # 基金手续费信息获取（不依赖 akshare）
├── fund_info.py            # 基金完整信息聚合（基本信息+历史净值+费率）
├── fund_realtime.py        # 基金实时估值与昨日净值（含 60s 进程内缓存）
└── strategies/            # 量化策略模块
    ├── __init__.py                # 策略注册表
    ├── rsi_strategy.py            # RSI 策略
    ├── macd_strategy.py           # MACD 趋势策略
    ├── bollinger_bands_strategy.py # 布林带策略
    └── dual_confirmation_strategy.py # 双重确认策略

tests/
├── conftest.py          # pytest 配置
├── test_api.py          # API 集成测试
├── test_strategies.py    # 策略单元测试
├── test_fund_fee.py     # 基金手续费接口测试
├── test_fund_info.py    # 基金完整信息接口测试
└── test_fund_realtime.py # 基金实时估值与昨日净值接口测试
```

## API 端点

| 端点 | 方法 | 功能 |
|------|------|------|
| `GET /health` | 健康检查 |
| `GET /strategies` | 获取所有可用策略列表 |
| `GET /strategies/{strategy_name}/{fund_code}` | 执行指定策略分析 |
| `GET /funds/{fund_code}/fee` | 获取基金手续费信息 |
| `GET /fund/info/{fundCode}` | 获取单只基金完整信息（基本信息+历史净值+费率） |
| `GET /fund/realtime/{fundCode}` | 获取基金盘中实时估值（分钟级，60s 缓存） |
| `GET /fund/nav/{fundCode}` | 获取基金昨日真实净值 |

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

## 基金手续费接口 (`/funds/{fund_code}/fee`)

通过基金代码获取其手续费（费率）信息，供第三方调用。

- **数据来源**：天天基金网-基金档案-购买信息（`https://fundf10.eastmoney.com/jjfl_{fund_code}.html`）
- **实现说明**：直接请求页面 HTML 并按区块标题解析，**不使用 akshare 的 `fund_fee_em`**，因其对「申购费率/认购费率」的多级表头（含 `|` 分隔符）解析存在 bug
- **核心模块**：`fund_fee.get_fund_fee(fund_code)`，返回 7 类费率信息：

| 字段 | 类型 | 说明 |
|------|------|------|
| `trade_status` | dict | 交易状态（申购/赎回/定投状态等） |
| `purchase_redemption_amount` | dict | 申购与赎回金额（起点/限额等） |
| `trade_confirm_days` | dict | 交易确认日（买入/卖出确认日，如 T+1） |
| `operation_fees` | dict | 运作费用（管理费率/托管费率/销售服务费率） |
| `subscription_fee_rate` | list | 认购费率（按金额分档） |
| `purchase_fee_rate` | list | 申购费率（前端，含原费率与天天基金优惠费率） |
| `redemption_fee_rate` | list | 赎回费率（按持有期限分档） |

> 注意：不同基金类型（混合/股票/债券/货币/ETF）可获取字段差异较大，缺失区块返回空 dict 或空 list。

## 基金完整信息接口 (`/fund/info/{fundCode}`)

获取单只基金的完整信息（基本信息 + 历史净值 + 费率表），供 Nuxt 端 `findOrCreateFund` 调用，一次拿全数据。

- **三源聚合**（均在 0.1~0.3s 级别）：
  1. 基本信息（名称/类型/费率摘要）：天天基金 `jbgk_{code}.html`
  2. 历史净值：`akshare.fund_open_fund_info_em`（单位净值走势，全量升序）
  3. 费率详情：复用 `fund_fee` 模块
- **核心模块**：`fund_info.get_fund_info(fund_code)`
- **错误响应**：
  - `400`：代码格式错误（非 6 位数字）
  - `404`：基金代码不存在（含天天基金占位页面，基金简称为 `---`）
  - `5xx`：服务故障
- **容错降级**：基本信息是核心（失败即 404）；历史净值/费率失败时降级为空列表，不中断整体响应

### fundType 判断规则
- 基金类型或简称含 `QDII`，或简称含 `LOF` → `"qdii_lof"`
- 否则 → `"open"`

### 响应字段（对齐前端契约）

| 字段 | 类型 | 说明 |
|------|------|------|
| `code` | str | 基金代码 |
| `name` | str | 基金简称 |
| `fundType` | str | `"open"` \| `"qdii_lof"` |
| `yesterdayNav` | str | 最新一日单位净值（字符串保留精度） |
| `navDate` | str | yesterdayNav 对应日期 |
| `history` | list | 历史净值，按日期升序 `[{date, nav}]` |
| `fees.purchaseFee` | str\|null | 申购费率（首档优惠费率） |
| `fees.redemptionFees` | list | 赎回费阶梯 `[{holdingPeriod, rate}]` |
| `fees.managementFee` | str\|null | 管理费（如 `"0.60%/年"`） |
| `fees.custodyFee` | str\|null | 托管费 |
| `fees.rawText` | str\|null | 原始费率说明文本（兜底展示） |

## 基金实时估值接口 (`/fund/realtime/{fundCode}`)

获取单只基金的盘中实时估算净值（交易时段内分钟级刷新）。

- **数据来源**：东方财富盘中估值表（`akshare.fund_value_estimation_em`）
- **核心模块**：`fund_realtime.get_realtime_estimation(fund_code)`
- **缓存策略**：进程内缓存 60 秒（估值分钟级刷新，60s 延迟可接受）；缓存过期或为空时重建
- **实现要点**：
  - 旧的实时估值 JSONP 接口 `fundgz.1234567.com.cn/js/{code}.js` 已废弃失效，改用东财官方盘中估值表
  - `fund_value_estimation_em(symbol='全部')` 存在 20000 行截断 bug，会漏掉部分基金（典型如主流 LOF）；因此合并 `['全部', 'LOF', '场内交易基金']` 多个 symbol 去重
  - 估值表列名嵌有动态当天日期（如 `2026-07-21-估算数据-估算值`），无法硬编码，按列位置（iloc）取值
- **错误响应**：
  - `400`：代码格式错误（非 6 位数字）
  - `404`：基金不在东财盘中估值列表（QDII/货币型/部分小众基金），或数据源不可用
  - `5xx`：服务故障

### 响应字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `code` | str | 基金代码 |
| `name` | str | 基金名称 |
| `estimateNav` | str\|null | 估算单位净值（4 位小数字符串） |
| `estimateGrowthRate` | float\|null | 估算涨跌幅（%，如 `-1.85` 表示 -1.85%） |
| `estimateDate` | str | 估值日期（接口仅到日级，无分钟级时间戳） |
| `publishedNav` | str\|null | 当日官方净值（盘前为 null，收盘后公布） |
| `publishedGrowthRate` | float\|null | 当日官方涨跌幅（%） |
| `yesterdayNav` | str\|null | 上一交易日官方净值（来自同表「上一交易日单位净值」列） |
| `yesterdayDate` | str | 上一交易日日期 |

> 注意：QDII（T+2 净值）/货币型/部分小众基金不在东财盘中估值列表，会返回 404。如需此类基金的估值，需基于季报重仓股 + 实时股价自行估算（本接口暂不实现）。

## 基金昨日净值接口 (`/fund/nav/{fundCode}`)

获取单只基金最近一个交易日的官方单位净值（昨日真实净值）。

- **数据来源**：`akshare.fund_open_fund_info_em`（单位净值走势），取 `tail(1)`，与 `fund_info._fetch_history_nav` 一致
- **核心模块**：`fund_realtime.get_yesterday_nav(fund_code)`
- **错误响应**：
  - `400`：代码格式错误（非 6 位数字）
  - `404`：基金代码不存在或无净值数据
  - `5xx`：服务故障

### 响应字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `code` | str | 基金代码 |
| `name` | str | 基金名称（该数据源不含，留空，需从 `/fund/info/{fundCode}` 获取） |
| `nav` | str | 最新一日单位净值（4 位小数字符串） |
| `navDate` | str | 净值日期（yyyy-mm-dd） |
| `growthRate` | float\|null | 日增长率（%） |

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
