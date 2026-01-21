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