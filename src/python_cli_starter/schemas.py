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

class SectorInfo(BaseModel):
    """板块简要信息"""
    model_config = ConfigDict(from_attributes=True)

    name: str            # f14 板块名称

    market_cap: float    # f20 总市值 (原始值)
    market_cap_desc: str # 格式化后的市值 (例如: 99186.73 亿)

    turnover_rate: float      # f8 换手率 (原始值)
    turnover_rate_desc: str   # 格式化后的换手率 (例如: 0.16%)

    change_percent: float     # f3 涨跌幅 (原始值)
    change_percent_desc: str  # 格式化后的涨跌幅 (例如: 1.25%)

    amount: float             # f6 成交额 (原始值)
    amount_desc: str          # 格式化后的成交额 (例如: 123.45 亿)

    date: date                # 记录日期
    updated_at: datetime      # 更新时间

class SectorListResponse(BaseModel):
    """板块列表响应"""
    count: int
    sectors: list[SectorInfo]

class ThsSectorInfo(BaseModel):
    """同花顺板块信息"""
    model_config = ConfigDict(from_attributes=True)

    name: str             # 板块名称
    change_percent: float # 涨跌幅 (%)
    net_inflow: float     # 净流入 (亿元)
    up_count: int         # 上涨家数
    down_count: int       # 下跌家数
    turnover_ratio: float # 成交额占比 (%)

    date: date            # 记录日期
    updated_at: datetime  # 更新时间

class ThsSectorListResponse(BaseModel):
    """同花顺板块列表响应"""
    count: int
    sectors: list[ThsSectorInfo]

class EastMoneyFetchRequest(BaseModel):
    """触发获取东方财富板块数据的请求参数"""
    cookie: Optional[str] = None
    fs_type: int = 2  # 默认为 2 (行业板块)，可传 3 (概念板块) 等

class EastMoneyFetchResponse(BaseModel):
    """触发获取响应"""
    success: bool
    message: str
    count: int
    curl_command: Optional[str] = None

class EastMoneyUploadResponse(BaseModel):
    """上传数据的响应"""
    success: bool
    message: str
    count: int

class FetchWithThsRequest(BaseModel):
    """获取东方财富板块 + 同花顺的请求参数"""
    cookie: Optional[str] = None
    fs_type: int = 2  # 2=行业板块, 3=概念板块

class FetchWithThsStepResult(BaseModel):
    """单步执行结果"""
    name: str
    success: bool
    message: str
    count: int

class FetchWithThsResponse(BaseModel):
    """获取响应"""
    success: bool
    message: str
    steps: list[FetchWithThsStepResult]


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