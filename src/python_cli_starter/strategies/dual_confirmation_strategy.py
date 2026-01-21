# src/python_cli_starter/strategies/dual_confirmation_strategy.py

import akshare as ak
import pandas as pd
from datetime import datetime, timedelta
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

# --- 策略常量 ---
TREND_MA_PERIOD = 120
RSI_PERIOD = 14
RSI_LOWER = 30.0

def get_latest_fund_data(fund_symbol: str) -> pd.DataFrame:
    """获取基金最近200天的净值数据"""
    logger.info(f"[Dual Confirm Strategy] 正在为基金 {fund_symbol} 获取最新净值数据...")
    start_date = datetime.today() - timedelta(days=200)
    
    try:
        fund_nav_df = ak.fund_open_fund_info_em(symbol=fund_symbol, indicator="单位净值走势")
        fund_nav_df['净值日期'] = pd.to_datetime(fund_nav_df['净值日期'])
        fund_nav_df = fund_nav_df.set_index('净值日期')
        fund_nav_df = fund_nav_df[['单位净值']]
        fund_nav_df.columns = ['close']
        fund_nav_df['close'] = pd.to_numeric(fund_nav_df['close'])
        
        fund_nav_df = fund_nav_df[fund_nav_df.index >= start_date].sort_index(ascending=True)
        
        if fund_nav_df.empty or len(fund_nav_df) < TREND_MA_PERIOD + 1:
            logger.warning(f"[Dual Confirm Strategy] 获取到的数据为空或数据量不足。")
            return None
            
        logger.info(f"[Dual Confirm Strategy] 数据获取成功，共 {len(fund_nav_df)} 条记录。")
        return fund_nav_df
        
    except Exception as e:
        logger.error(f"[Dual Confirm Strategy] 获取基金 {fund_symbol} 数据时发生错误: {e}")
        return None

def calculate_indicators(data: pd.DataFrame, trend_period: int, rsi_period: int) -> pd.DataFrame:
    """计算趋势均线和RSI。"""
    data['trend_ma'] = data['close'].rolling(window=trend_period).mean()
    delta = data['close'].diff()
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)
    ema_up = up.ewm(com=rsi_period - 1, adjust=False).mean()
    ema_down = down.ewm(com=rsi_period - 1, adjust=False).mean()
    rs = ema_up / ema_down
    data['rsi'] = 100 - (100 / (1 + rs))
    return data

def run_strategy(fund_code: str, is_holding: bool) -> Dict[str, Any]:
    """执行“双重确认”策略并返回决策结果。"""
    df = get_latest_fund_data(fund_code)
    if df is None:
        return {"error": f"无法获取基金 {fund_code} 的数据。"}

    df_with_indicators = calculate_indicators(df, trend_period=TREND_MA_PERIOD, rsi_period=RSI_PERIOD)
    
    latest_data = df_with_indicators.iloc[-1]
    latest_date = latest_data.name.date()
    latest_close = latest_data['close']
    trend_ma = latest_data['trend_ma']
    latest_rsi = latest_data['rsi']

    if pd.isna(trend_ma) or pd.isna(latest_rsi):
        signal = "持有/观望"
        reason = "指标值无效，数据不足或计算错误，建议观望。"
    else:
        is_in_uptrend = latest_close > trend_ma
        
        if not is_holding:
            if not is_in_uptrend:
                signal = "持有/观望"
                reason = f"价格({latest_close:.4f})低于长期均线({trend_ma:.4f})，处于熊市，不考虑买入。"
            else:
                if latest_rsi <= RSI_LOWER:
                    signal = "买入"
                    reason = f"确认牛市，且RSI({latest_rsi:.2f})进入回调区(<= {RSI_LOWER})，是绝佳的买入时机。"
                else:
                    signal = "持有/观望"
                    reason = f"处于牛市，但RSI({latest_rsi:.2f})未进入回调区，等待更好的买点。"
        else: # is_holding is True
            if not is_in_uptrend:
                signal = "卖出"
                reason = f"价格({latest_close:.4f})已跌破长期均线({trend_ma:.4f})，趋势反转，应立即卖出。"
            else:
                signal = "持有/观望" # 继续持有
                reason = f"价格仍在长期均线之上，牛市趋势未变，继续持有以捕捉更大涨幅。"

    return {
        "signal": signal,
        "reason": reason,
        "latest_date": latest_date,
        "latest_close": latest_close,
        "metrics": {
            "trend_ma_period": TREND_MA_PERIOD,
            "trend_ma_value": round(trend_ma, 4) if pd.notna(trend_ma) else None,
            "rsi_period": RSI_PERIOD,
            "rsi_value": round(latest_rsi, 2) if pd.notna(latest_rsi) else None,
            "rsi_lower_band": RSI_LOWER,
        }
    }