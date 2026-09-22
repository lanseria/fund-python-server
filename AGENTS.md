## 项目概览

基金策略分析 API 服务，基于 FastAPI 构建。提供多种量化技术指标策略（RSI、MACD、布林带、双重确认）的基金交易信号分析，并支持板块主力资金数据查询。

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
docker compose logs -f                  # 查看日志
docker compose down                     # 停止服务
```

## 代码架构

```
src/python_cli_starter/
├── main.py                 # FastAPI 入口，路由定义
├── schemas.py              # Pydantic 验证/响应模型
├── fund_fee.py             # 基金手续费信息获取（不依赖 akshare）
├── fund_info.py            # 基金完整信息聚合（基本信息+历史净值+费率）
├── fund_realtime.py        # 基金实时估值（powercloud 聚合）与昨日净值
├── sector_capital.py       # 板块主力资金数据（东财数据中心）
├── stock_realtime.py       # 股票批量实时行情（腾讯，A股 sh/sz + 港股 hk）
├── gold_realtime.py        # 上海黄金交易所贵金属实时行情（新浪 gds_，Au9999）
└── strategies/             # 量化策略模块
    ├── __init__.py                # 策略注册表
    ├── rsi_strategy.py            # RSI 策略
    ├── macd_strategy.py           # MACD 趋势策略
    ├── bollinger_bands_strategy.py # 布林带策略
    └── dual_confirmation_strategy.py # 双重确认策略

tests/
├── conftest.py          # pytest 配置
├── test_api.py          # API 集成测试
├── test_charts.py       # RSI 图表数据接口测试
├── test_strategies.py   # 策略单元测试
├── test_fund_fee.py     # 基金手续费接口测试
├── test_fund_info.py    # 基金完整信息接口测试
├── test_fund_realtime.py # 基金实时估值与昨日净值接口测试
├── test_stock_realtime.py # 股票批量实时行情接口测试
├── test_gold_realtime.py # 贵金属实时行情接口测试
└── test_sector_capital.py # 板块主力资金接口测试
```

## API 端点

| 端点 | 方法 | 功能 |
|------|------|------|
| `GET /health` | 健康检查 |
| `GET /strategies` | 获取所有可用策略列表 |
| `GET /strategies/{strategy_name}/{fund_code}` | 执行指定策略分析 |
| `GET /charts/rsi/{fund_code}` | 获取 RSI 策略图表数据 |
| `GET /funds/{fund_code}/fee` | 获取基金手续费信息 |
| `GET /fund/info/{fundCode}` | 获取单只基金完整信息（基本信息+历史净值+费率） |
| `GET /fund/realtime/{fundCode}` | 获取基金盘中实时估值（分钟级，powercloud 聚合） |
| `GET /stocks/realtime` | 批量获取 A股/港股股票实时行情（腾讯） |
| `GET /gold/realtime` | 获取上海黄金交易所贵金属实时行情（新浪 gds_，Au9999/AUTD） |
| `GET /fund/nav/{fundCode}` | 获取基金昨日真实净值 |
| `GET /sector/capital` | 获取板块主力资金数据表（行业/概念，实时） |
| `GET /sector/capital/action/{sector_name}` | 按板块名查询主力行为（精确+模糊兜底） |

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

- **数据来源**：powercloud 聚合接口 `monitor.powercloud.work/api/fund/{code}`（`fund_realtime._fetch_powercloud_estimation`）
  - 已封装好「东财实时估算 + 历史净值回退 + QDII 处理」，单次请求返回 `basic` + `intraday` + `history` + `holdings`
  - 响应 ~0.35s，稳定
- **核心模块**：`fund_realtime.get_realtime_estimation(fund_code)`
- **实现要点**：
  - powercloud `basic` 字段为东财原始命名，映射关系：`gsz`→estimateNav（估算净值**原值**）、`gszzl`→estimateGrowthRate、`dwjz`→yesterdayNav、`gztime`→estimateDate、`jzrq`→yesterdayDate、`confirmed_nav`→publishedNav、`confirmed_change`→publishedGrowthRate
  - `success` / `quote_source` / `message` 标识数据状态：`realtime`（盘中实时估算）vs `history_fallback`（非交易时段/QDII 回退到最近净值）
  - QDII（T+2 净值）/ 货币型 等无盘中估值的基金，powercloud 自动回退到最近净值并标注（不再返回 404）
  - 占位符（`-` / `---` / 空）统一转为 `null`
  - powercloud 额外返回的 `holdings`（重仓股持仓）已透传为顶层 `holdingsDate` + `holdings`；内层明细列表由原始的 `holdings` 重命名为 `stocks` 参与解析；`history` 不透传（历史净值由 `/fund/info` 提供）
  - powercloud 对非 6 位代码（如 5 位）可能误匹配，故代码格式校验（6 位数字）由 API 路由层保证，先于数据源调用
  - 历史演进：东财 `fundgz` JSONP（已废弃）→ akshare 东财估值表（底层接口失效）→ 新浪单只（估算净值需反算）→ powercloud 聚合（当前）
- **错误响应**：
  - `400`：代码格式错误（非 6 位数字）
  - `404`：基金不存在（powercloud 返回 name==code 且 gsz 为占位符），或数据源不可用
  - `5xx`：服务故障

### 响应字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `code` | str | 基金代码 |
| `name` | str | 基金名称 |
| `estimateNav` | str\|null | 估算单位净值（4 位小数字符串，来自 `gsz` 原值） |
| `estimateGrowthRate` | float\|null | 估算涨跌幅（%，如 `-1.85` 表示 -1.85%） |
| `estimateDate` | str | 估值日期（yyyy-mm-dd） |
| `publishedNav` | str\|null | 已确认官方净值（有则填，盘前/QDII 为 null） |
| `publishedGrowthRate` | float\|null | 已确认官方涨跌幅（%） |
| `yesterdayNav` | str\|null | 上一交易日单位净值（来自 `dwjz`） |
| `yesterdayDate` | str | 上一交易日日期 |
| `quoteSource` | str\|null | 数据来源标识（`realtime` / `history_fallback`） |
| `message` | str | 状态说明（如「QDII暂无盘中估值，展示最近净值」） |
| `intraday` | list | 盘中分时数据 `[{time, value, ...}]`，非交易时段为空数组 |
| `holdingsDate` | str | 重仓股持仓报告期（季报日期，如 `"2026-06-30"`，无持仓为空串） |
| `holdings` | list | 重仓股持仓明细 `[{code, name, pct, price, change_pct, ...}]`；`pct` 为占净值比例字符串（如 `"17.28%"`，季报口径有滞后），`price`/`change_pct` 为最新盘中行情；纯债/货币等无股票持仓为空数组 |

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

## 板块主力资金接口 (`/sector/capital`)

获取板块（行业/概念）的主力资金流向数据，并据此判定主力行为。

- **数据来源**：东方财富数据中心板块资金流向接口 `data.eastmoney.com/dataapi/bkzj/getbkzj`（实时查询，不落库）
  - 走 `data.eastmoney.com` 域名（稳定可达），一次返回全量、无需分页；历史上用过的 `push2.eastmoney.com/api/qt/clist/get`（实时推送接口）在部分网络环境不可达，已弃用
  - `code=m:90+t:2` 行业板块（约 496 条），`code=m:90+t:3` 概念板块（约 504 条）；`key` 参数逗号分隔指定返回字段
  - 字段映射（原始单位均为「元」）：`f14` 板块名 / `f3` 涨幅（**乘 100 后的原始值**，如 217 = 2.17%，需 /100）/ `f6` 成交额 / `f62` 主力资金（主力净流入）/ `f66` 超大单 / `f72` 大单 / `f78` 中单 / `f84` 散户资金（小单净流入）
- **核心模块**：`sector_capital.py`
  - `get_sector_capital_flow(fs_type)` — 全量板块主力资金表（读缓存）
  - `find_sector_action(name, fs_type)` — 精确匹配优先，子串模糊兜底，可能返回多条
- **缓存策略**（内存缓存，按 fs_type 分别缓存，不落库）：
  - 刷新时段：交易日 9:30-16:00，每 **10 分钟**刷新一次（含 15:00-16:00 收盘统计期）
  - 冻结：16:00 后不再刷新，缓存冻结有效到次日 9:30 开盘后被新数据覆盖；周末/节假日全天用最近一份缓存
  - 冷启动：服务启动时 `warmup_cache()` 立即预热 industry + concept（无论是否交易时段）
  - 刷新失败：交易时段某个窗口拉数据源失败时**保留旧缓存**（即便已过期），接口始终有数据
  - 后台循环：lifespan 起一个每 60 秒检查的 task，按 `_should_refresh` 决策刷新
  - 两个接口均读缓存，不直连数据源；模块级 `_CACHE` + `asyncio.Lock` 串行化刷新

### 计算口径（业务自定）
- **主力暗盘** = 主力资金 − 散户资金
- **主力强度** = 主力暗盘 / 成交额 × 100（成交额为 0 记 0，保留 2 位小数）
- **主力行为**（按主力强度判定，边界值 `3`/`-1` 归极端档）：

  | 主力强度 | 主力行为 |
  |---------|---------|
  | `>= 3` | 抢筹 |
  | `[1, 3)` | 建仓 |
  | `(-1, 1)` | 洗盘 |
  | `<= -1` | 出货 |

- **错误响应**：
  - `400`：板块类型 `type` 非 `industry|concept`（兼容 `行业`/`概念`/`2`/`3` 别名）；板块名为空
  - `404`：板块名无任何精确/模糊匹配
  - `502`：东财数据源不可用
  - `5xx`：服务故障

### 响应字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | str | 板块名称 |
| `code` | str | 板块代码（BKxxxx） |
| `changePercent` | float | 涨幅（%） |
| `amount` | str | 成交额（亿元字符串，如 `"440.19 亿"`） |
| `mainCapital` | str | 主力资金（亿元字符串） |
| `retailCapital` | str | 散户资金（亿元字符串） |
| `mainHidden` | str | 主力暗盘（亿元字符串） |
| `mainStrength` | float | 主力强度（%） |
| `mainAction` | str | 主力行为（抢筹/建仓/洗盘/出货） |

> 两个接口的差异：`/sector/capital` 返回 `{type, count, sectors}` 全量表；`/sector/capital/action/{sector_name}` 返回 `{query, type, matched, sectors}` 命中子集。

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
- HTTPX >=0.27.0
- Pytest >=8.0.0 (测试)
