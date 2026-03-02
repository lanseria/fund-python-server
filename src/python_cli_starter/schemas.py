# src/python_cli_starter/schemas.py
from pydantic import BaseModel, ConfigDict
from typing import Dict, Any
from datetime import date
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
    name: str            # f14 板块名称
    
    market_cap: float    # f20 总市值 (原始值)
    market_cap_desc: str # 格式化后的市值 (例如: 99186.73 亿)
    
    turnover_rate: float      # f8 换手率 (原始值)
    turnover_rate_desc: str   # 格式化后的换手率 (例如: 0.16%)
    
    change_percent: float     # f3 涨跌幅 (原始值)
    change_percent_desc: str  # 格式化后的涨跌幅 (例如: 1.25%)

class SectorListResponse(BaseModel):
    """板块列表响应"""
    count: int
    sectors: list[SectorInfo]

class ThsSectorInfo(BaseModel):
    """同花顺板块信息"""
    name: str             # 板块名称
    change_percent: float # 涨跌幅 (%)
    net_inflow: float     # 净流入 (亿元)
    up_count: int         # 上涨家数
    down_count: int       # 下跌家数
    turnover_ratio: float # 成交额占比 (%)

class ThsSectorListResponse(BaseModel):
    """同花顺板块列表响应"""
    count: int
    sectors: list[ThsSectorInfo]