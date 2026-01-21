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
