# src/python_cli_starter/schemas.py
from pydantic import BaseModel, ConfigDict
from typing import Dict, Any, Optional, List
from datetime import date, datetime
from enum import Enum


class SignalType(str, Enum):
    BUY = '买入'
    SELL = '卖出'
    HOLD = '持有/观望'


class StrategySignal(BaseModel):
    """策略分析信号响应模型"""
    model_config = ConfigDict(from_attributes=True)

    fund_code: str
    strategy_name: str
    signal: SignalType
    reason: str
    latest_date: date
    latest_close: float
    metrics: Dict[str, Any]


class StrategyListResponse(BaseModel):
    """策略列表响应"""
    strategies: list[str]
    count: int


class HealthResponse(BaseModel):
    """健康检查响应"""
    status: str
    timestamp: str

class ChartSignalPoint(BaseModel):
    """图表信号点坐标"""
    coord: list[str | float]  # [date_str, rsi_value]
    value: str

class ChartSignals(BaseModel):
    """买卖信号集合"""
    buy: list[ChartSignalPoint]
    sell: list[ChartSignalPoint]

class RsiConfig(BaseModel):
    """RSI 配置参数"""
    rsiPeriod: int
    rsiUpper: float
    rsiLower: float

class RsiChartResponse(BaseModel):
    """RSI 图表全量数据响应"""
    dates: list[str]
    netValues: list[float | None]
    rsiValues: list[float | None]
    signals: ChartSignals
    config: RsiConfig


class FundFeeResponse(BaseModel):
    """基金手续费信息响应模型。

    7 类费率信息：
    - 键值对类（dict）：trade_status / purchase_redemption_amount / trade_confirm_days / operation_fees
    - 分档费率类（list）：subscription_fee_rate / purchase_fee_rate / redemption_fee_rate

    说明：不同基金类型（混合/股票/债券/货币/ETF）可获取的字段差异较大，
    缺失的区块会返回空 dict 或空 list，调用方按需取用即可。
    """
    fund_code: str
    trade_status: Dict[str, str]                       # 交易状态（申购/赎回/定投状态等）
    purchase_redemption_amount: Dict[str, str]         # 申购与赎回金额（起点/限额等）
    trade_confirm_days: Dict[str, str]                 # 交易确认日（买入/卖出确认日）
    operation_fees: Dict[str, str]                     # 运作费用（管理/托管/销售服务费率）
    subscription_fee_rate: List[Dict[str, Any]]        # 认购费率（分档）
    purchase_fee_rate: List[Dict[str, Any]]            # 申购费率（前端，分档）
    redemption_fee_rate: List[Dict[str, Any]]          # 赎回费率（分档）


class FundNavPoint(BaseModel):
    """历史净值单点（契约字段）。"""
    date: str
    nav: str


class FundRedemptionFee(BaseModel):
    """赎回费率阶梯单档（契约字段）。"""
    holdingPeriod: str
    rate: str


class FundFees(BaseModel):
    """基金费率信息（契约字段，仅前端展示用）。"""
    purchaseFee: Optional[str] = None                  # 申购费率（首档优惠费率文本）
    redemptionFees: List[FundRedemptionFee] = []       # 赎回费率阶梯（按持有期）
    managementFee: Optional[str] = None                # 管理费
    custodyFee: Optional[str] = None                   # 托管费
    rawText: Optional[str] = None                      # 原始费率说明文本（兜底展示）


class FundInfoResponse(BaseModel):
    """基金完整信息响应模型（对齐 Nuxt 端接口契约）。

    一次返回基本信息 + 历史净值 + 费率表，供 findOrCreateFund 写入
    funds + navHistory + 费率表。
    """
    code: str
    name: str
    fundType: str                                      # "open" | "qdii_lof"
    yesterdayNav: str                                  # 最新一日单位净值（字符串保留精度）
    navDate: str                                       # yesterdayNav 对应日期
    history: List[FundNavPoint]                        # 历史净值，按日期升序
    fees: FundFees                                     # 费率信息


class FundRealtimeEstimation(BaseModel):
    """基金实时估值响应模型（交易时段分钟级刷新）。

    数据来源：powercloud 聚合接口 ``monitor.powercloud.work/api/fund/{code}``
    （已封装东财实时估算 + 历史净值回退 + QDII 处理）。

    字段说明：
    - **estimateNav**: 估算单位净值（4 位小数字符串，来自 ``gsz`` 原值）
    - **estimateGrowthRate**: 估算涨跌幅（数字百分比，如 -1.85 表示 -1.85%）
    - **estimateDate**: 估值日期（yyyy-mm-dd）
    - **publishedNav**: 已确认官方净值（有则填，盘前/QDII 为 None）
    - **publishedGrowthRate**: 已确认官方涨跌幅（%）
    - **yesterdayNav**: 上一交易日单位净值（来自 ``dwjz``）
    - **quoteSource**: 数据来源标识（``realtime`` 盘中实时 / ``history_fallback`` 历史回退）
    - **message**: 状态说明（如「QDII暂无盘中估值，展示最近净值」）
    - **intraday**: 盘中分时数据（``[{time, value, ...}]``，非交易时段为空数组）

    注意：QDII / 货币型等无盘中估值的基金，``success`` 为 False 时会回退到最近净值，
    通过 ``quoteSource`` / ``message`` 标识。
    """
    code: str
    name: str
    estimateNav: Optional[str] = None                  # 估算净值（4 位小数字符串）
    estimateGrowthRate: Optional[float] = None         # 估算涨跌幅（%）
    estimateDate: str = ""                             # 估值日期
    publishedNav: Optional[str] = None                 # 已确认官方净值（无则 None）
    publishedGrowthRate: Optional[float] = None        # 已确认官方涨跌幅（%）
    yesterdayNav: Optional[str] = None                 # 上一交易日单位净值
    yesterdayDate: str = ""                            # 上一交易日日期
    quoteSource: Optional[str] = None                  # 数据来源标识
    message: str = ""                                  # 状态说明
    intraday: List[Dict[str, Any]] = []                # 盘中分时数据（非交易时段为空）


class SectorCapitalItem(BaseModel):
    """板块主力资金单项（契约字段，金额均为「亿元」字符串）。

    - **changePercent**: 涨幅（%，如 3.75 表示 +3.75%）
    - **amount**: 成交额（东财原始 f6，元 → 亿元）
    - **mainCapital**: 主力资金（主力净流入额 f62）
    - **retailCapital**: 散户资金（小单净流入额 f84）
    - **mainHidden**: 主力暗盘 = 主力资金 - 散户资金
    - **mainStrength**: 主力强度 = 主力暗盘 / 成交额 * 100（%，成交额为 0 记 0）
    - **mainAction**: 主力行为（抢筹 / 建仓 / 洗盘 / 出货）
    """
    name: str                                          # 板块名称
    code: str = ""                                     # 板块代码（BKxxxx，附带返回）
    changePercent: float                               # 涨幅（%）
    amount: str                                        # 成交额（亿元字符串）
    mainCapital: str                                   # 主力资金（亿元字符串）
    retailCapital: str                                 # 散户资金（亿元字符串）
    mainHidden: str                                    # 主力暗盘（亿元字符串）
    mainStrength: float                                # 主力强度（%）
    mainAction: str                                    # 主力行为（抢筹/建仓/洗盘/出货）


class SectorCapitalListResponse(BaseModel):
    """板块主力资金表响应（全量板块）。"""
    type: str                                          # 板块类型（industry / concept）
    count: int
    sectors: List[SectorCapitalItem]


class SectorCapitalActionResponse(BaseModel):
    """按板块名查询主力行为响应。

    精确匹配优先，找不到做子串模糊兜底，故 ``matched`` 可能 >1。
    """
    query: str                                         # 原始查询串
    type: str                                          # 板块类型（industry / concept）
    matched: int                                       # 命中条数
    sectors: List[SectorCapitalItem]


class FundYesterdayNav(BaseModel):
    """基金昨日真实净值响应模型（最近一个交易日官方净值）。

    数据来源：akshare fund_open_fund_info_em 单位净值走势，取 tail(1)。
    与 fund_info._fetch_history_nav 一致。
    """
    code: str
    name: str = ""                                     # 基金名称（该数据源不含，留空）
    nav: str                                           # 最新一日单位净值（4 位小数字符串）
    navDate: str                                       # 净值日期（yyyy-mm-dd）
    growthRate: Optional[float] = None                 # 日增长率（%）