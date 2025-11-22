# Fund-Server 策略分析 API 文档

## 1. 概述

策略分析API是 `fund-server` 项目的核心功能之一，旨在为指定的金融标的（基金）提供基于不同量化模型的交易信号。该API采用动态和可扩展的设计，允许开发者轻松添加新的交易策略，而无需修改核心API路由逻辑。

**核心特性:**

*   **统一入口**: 所有策略均通过一个统一的API端点进行调用。
*   **动态加载**: API能够根据请求的策略名称动态加载并执行相应的策略逻辑。
*   **状态支持**: 支持需要用户当前持仓状态（如布林带策略）和无状态（如RSI策略）的混合模型。
*   **标准化响应**: 所有策略均返回统一、结构化的JSON数据，包含明确的交易信号、决策理由和关键指标。
*   **易于扩展**: 添加新策略只需创建策略文件并在注册表中注册即可。

## 2. 核心端点

### 获取策略信号

此端点是策略分析功能的唯一入口。

`GET /strategies/{strategy_name}/{fund_code}`

#### 2.1. 参数详解

| 参数         | 位置 | 类型    | 是否必须 | 描述                                                                                                   |
| :----------- | :--- | :------ | :------- | :----------------------------------------------------------------------------------------------------- |
| `strategy_name` | 路径 | string  | **是**   | 策略的简称。例如: `rsi`, `bollinger_bands`。                                                           |
| `fund_code`     | 路径 | string  | **是**   | 要分析的基金代码，例如 `001749`。                                                                        |
| `is_holding`    | 查询 | boolean | **可选** | 对于需要持仓状态的策略 (如 `bollinger_bands`)，此参数为**必填**。`true` 表示当前持有, `false` 表示当前未持有。 |

#### 2.2. 通用响应格式

所有成功的请求（HTTP `200 OK`）都会返回一个结构化的JSON对象。

**JSON 结构示例:**
```json
{
  "fund_code": "string",
  "strategy_name": "string",
  "signal": "string (Enum)",
  "reason": "string",
  "latest_date": "date (YYYY-MM-DD)",
  "latest_close": "float",
  "metrics": {
    "key1": "value1",
    "key2": "value2"
  }
}
```

**响应字段详解:**

| 字段            | 类型                               | 描述                                                               |
| :-------------- | :--------------------------------- | :----------------------------------------------------------------- |
| `fund_code`     | string                             | 请求分析的基金代码。                                               |
| `strategy_name` | string                             | 请求执行的策略名称。                                               |
| `signal`        | string (Enum: "买入", "卖出", "持有/观望") | 策略生成的最终交易信号。                                           |
| `reason`        | string                             | 产生该交易信号的具体原因和文字解释。                               |
| `latest_date`   | string (Date)                      | 策略分析所依据的最新数据的日期。                                   |
| `latest_close`  | float                              | 策略分析所依据的最新净值或收盘价。                                 |
| `metrics`       | object                             | 一个包含该策略核心计算指标的字典，内容随不同策略而变化。 |

## 3. 可用策略列表

### 3.1. 相对强弱指数 (RSI) 策略

*   **策略名称**: `rsi`
*   **简介**: 一个经典的动量震荡指标，用于判断市场的超买或超卖状态。策略逻辑为“超卖时买入，超买时卖出”。
*   **所需参数**: 无需 `is_holding` 参数。
*   **`metrics` 指标详解**:
    *   `rsi_period` (integer): RSI计算周期，默认为14。
    *   `rsi_value` (float): 最新的RSI计算值。
    *   `rsi_upper_band` (float): 超买阈值，默认为70.0。
    *   `rsi_lower_band` (float): 超卖阈值，默认为30.0。

### 3.2. 布林带 (Bollinger Bands) 反转策略

*   **策略名称**: `bollinger_bands`
*   **简介**: 一个基于价格波动性的反转策略。当价格触及或跌破下轨时买入，当价格从低位回归至中轨时卖出。
*   **所需参数**: **必须**提供 `is_holding` 查询参数。
*   **`metrics` 指标详解**:
    *   `bband_period` (integer): 布林带计算周期，默认为50。
    *   `bband_dev_factor` (float): 标准差倍数，默认为2.0。
    *   `bband_upper` (float): 最新的布林带上轨值。
    *   `bband_mid` (float): 最新的布林带中轨值（移动平均线）。
    *   `bband_lower` (float): 最新的布林带下轨值。

## 4. 使用示例 (cURL)

#### 示例 1: 获取 `001749` 基金的RSI策略信号
```bash
curl -X GET "http://127.0.0.1:8888/strategies/rsi/001749"
```

#### 示例 2: 获取 `007301` 基金的布林带策略信号（假设当前未持有）
```bash
curl -X GET "http://127.0.0.1:8888/strategies/bollinger_bands/007301?is_holding=false"
```

#### 示例 3: 获取 `007301` 基金的布林带策略信号（假设当前已持有）
```bash
curl -X GET "http://127.0.0.1:8888/strategies/bollinger_bands/007301?is_holding=true"
```

## 5. 错误处理

API会通过标准的HTTP状态码来反馈错误。

| 状态码 | 含义             | 可能原因                                                                   |
| :----- | :--------------- | :------------------------------------------------------------------------- |
| `400 Bad Request`  | 客户端请求错误 | 1. 调用需要 `is_holding` 的策略时，未提供该查询参数。                      |
| `404 Not Found`    | 未找到资源     | 1. 请求的 `strategy_name` 不存在于策略注册表中。                           |
| `500 Internal Server Error` | 服务器内部错误 | 1. 依赖的第三方数据源（如 `akshare`）获取数据失败。<br>2. 策略计算过程中出现意外的程序错误。 |

#### 错误响应示例 (`400 Bad Request`)
```json
{
  "detail": "策略 'bollinger_bands' 需要 'is_holding' (true/false) 查询参数。"
}
```

### 3.4. 双重确认 (Dual Confirmation) 策略

*   **策略名称**: `dual_confirmation`
*   **简介**: 结合了趋势判断和择时买点的复合策略。只有当价格处于长期均线上方（牛市）时，才考虑RSI指标给出的超卖（回调）买入信号。当价格跌破长期均线时，无条件卖出。
*   **所需参数**: **必须**提供 `is_holding` 查询参数。
*   **`metrics` 指标详解**:
    *   `trend_ma_period` (integer): 长期趋势均线周期，默认为120。
    *   `trend_ma_value` (float): 最新的长期均线值。
    *   `rsi_period` (integer): RSI计算周期，默认为14。
    *   `rsi_value` (float): 最新的RSI值。
    *   `rsi_lower_band` (float): RSI超卖阈值，默认为30.0。

... (更新使用示例)

#### 示例 4: 获取 `003096` 基金的双均线交叉策略信号（假设当前未持有）
```bash
curl -X GET "http://127.0.0.1:8888/strategies/ma_cross/003096?is_holding=false"

### 3.5. MACD 趋势跟踪策略

*   **策略名称**: `macd`
*   **简介**: 经典的趋势跟踪指标，通过分析短期与长期指数移动平均线之间的差离值（DIF）和其信号线（DEA）的交叉情况来判断买卖时机。“金叉”为买入信号，“死叉”为卖出信号。
*   **所需参数**: **必须**提供 `is_holding` 查询参数。
*   **`metrics` 指标详解**:
    *   `macd_short_period` (integer): DIF计算中的快线周期，默认为12。
    *   `macd_long_period` (integer): DIF计算中的慢线周期，默认为26。
    *   `macd_signal_period` (integer): DEA计算的周期，默认为9。
    *   `dif_value` (float): 最新的DIF线（MACD线）的值。
    *   `dea_value` (float): 最新的DEA线（信号线）的值。
    *   `macd_hist_value` (float): 最新的MACD柱状图的值 (DIF - DEA)。

... (更新使用示例)

#### 示例 6: 获取 `001632` 基金的MACD策略信号（假设当前未持有）
```bash
curl -X GET "http://127.0.0.1:8888/strategies/macd/001632?is_holding=false"

... (原有内容)

## 📊 图表数据 API

此系列 API 旨在为前端提供适配主流图表库（如 ECharts）的结构化数据，方便进行策略回测的可视化展示。

### 获取 RSI 策略图表数据 (全部历史)

**GET** `/charts/rsi/{fund_code}`

获取用于绘制基金**全部历史**净值曲线、RSI 指标曲线以及买卖信号点的全套数据。

-   **路径参数**:
    -   `fund_code` (string, **required**): 基金代码，例如 `004253`。
-   **查询参数**:
    -   (无)

-   **示例请求**:
    ```bash
    curl -X GET "http://127.0.0.1:8888/charts/rsi/004253"
    ```

-   **成功响应示例 (200 OK)**:
    (响应示例格式保持不变)
    ```json
    {
      "dates": [
        "2020-01-02",
        "2020-01-03",
        "...",
        "2024-05-20"
      ],
      "netValues": [ ... ],
      "rsiValues": [ ... ],
      "signals": { ... },
      "config": { ... }
    }
    ```

-   **ECharts 前端使用建议**:
    (此部分保持不变)

---