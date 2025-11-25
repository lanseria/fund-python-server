# schemas.py
from pydantic import BaseModel, ConfigDict
from typing import Optional, Dict, Any, List
from datetime import datetime, date
from enum import Enum

class HoldingCreate(BaseModel):
    code: str
    name: str
    holding_amount: float

class Holding(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str
    shares: float # Numeric 在 Pydantic 中通常映射为 float
    yesterday_nav: float
    holding_amount: float
    today_estimate_nav: Optional[float] = None
    today_estimate_amount: Optional[float] = None
    percentage_change: Optional[float] = None
    today_estimate_update_time: Optional[datetime] = None

class HoldingUpdate(BaseModel):
    holding_amount: float


class SignalType(str, Enum):
    BUY = "买入"
    SELL = "卖出"
    HOLD = "持有/观望"


class FundRealtimeInfo(BaseModel):
    """基金实时估值信息响应模型"""
    fund_code: str
    name: str
    yesterday_nav: float
    estimate_nav: Optional[float] = None
    percentage_change: Optional[float] = None
    update_time: Optional[datetime] = None

class StockHolding(BaseModel):
    """单只股票持仓明细"""
    serial_number: int
    stock_code: str
    stock_name: str
    percentage: float
    share_holding: float
    market_value: float
    quarter: str

class FundPortfolioResponse(BaseModel):
    """基金持仓组合响应"""
    fund_code: str
    year: str
    holdings: List[StockHolding]

class StrategySignal(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    fund_code: str
    strategy_name: str
    signal: SignalType
    reason: str
    latest_date: date
    latest_close: float
    metrics: Dict[str, Any]

class IndexTechnicalInfo(BaseModel):
    """单个指数的技术面信息"""
    name: str
    current: float
    change_pct: float
    ma_status: str  # 例如: "above_MA5, below_MA20"
    macd_status: str # 例如: "golden_cross", "death_cross", "widening"
    technical_trend: str # 例如: "bullish", "bearish", "rebound"

class MarketOverviewResponse(BaseModel):
    """市场概览响应"""
    timestamp: datetime
    indices: Dict[str, IndexTechnicalInfo]