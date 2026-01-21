# src/python_cli_starter/strategies/macd_strategy.py

import akshare as ak
import pandas as pd
from datetime import datetime, timedelta
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

# --- 策略常量 ---
MACD_SHORT_PERIOD = 12
MACD_LONG_PERIOD = 26
MACD_SIGNAL_PERIOD = 9

def get_latest_fund_data(fund_symbol: str) -> pd.DataFrame:
    """获取基金最近150天的净值数据"""
    logger.info(f"[MACD Strategy] 正在为基金 {fund_symbol} 获取最新净值数据...")
    # 缓冲期设为150天，足够计算26周期的EMA
    start_date = datetime.today() - timedelta(days=150)
    
    try:
        fund_nav_df = ak.fund_open_fund_info_em(symbol=fund_symbol, indicator="单位净值走势")
        fund_nav_df['净值日期'] = pd.to_datetime(fund_nav_df['净值日期'])
        fund_nav_df = fund_nav_df.set_index('净值日期')
        fund_nav_df = fund_nav_df[['单位净值']]
        fund_nav_df.columns = ['close']
        fund_nav_df['close'] = pd.to_numeric(fund_nav_df['close'])
        
        fund_nav_df = fund_nav_df[fund_nav_df.index >= start_date].sort_index(ascending=True)
        
        if fund_nav_df.empty or len(fund_nav_df) < MACD_LONG_PERIOD + 2:
            logger.warning(f"[MACD Strategy] 获取到的数据为空或数据量不足以判断交叉。")
            return None
            
        logger.info(f"[MACD Strategy] 数据获取成功，共 {len(fund_nav_df)} 条记录。")
        return fund_nav_df
        
    except Exception as e:
        logger.error(f"[MACD Strategy] 获取基金 {fund_symbol} 数据时发生错误: {e}")
        return None

def calculate_macd(data: pd.DataFrame, short_period: int, long_period: int, signal_period: int) -> pd.DataFrame:
    """使用 pandas 手动计算MACD指标。"""
    ema_short = data['close'].ewm(span=short_period, adjust=False).mean()
    ema_long = data['close'].ewm(span=long_period, adjust=False).mean()
    data['macd'] = ema_short - ema_long
    data['macd_signal'] = data['macd'].ewm(span=signal_period, adjust=False).mean()
    data['macd_hist'] = data['macd'] - data['macd_signal']
    return data

def run_strategy(fund_code: str, is_holding: bool) -> Dict[str, Any]:
    """执行MACD策略并返回决策结果。"""
    df = get_latest_fund_data(fund_code)
    if df is None:
        return {"error": f"无法获取基金 {fund_code} 的数据。"}

    df_with_macd = calculate_macd(df, 
                                  short_period=MACD_SHORT_PERIOD, 
                                  long_period=MACD_LONG_PERIOD, 
                                  signal_period=MACD_SIGNAL_PERIOD)
    
    latest_data = df_with_macd.iloc[-1]
    previous_data = df_with_macd.iloc[-2]

    latest_date = latest_data.name.date()
    latest_close = latest_data['close']
    current_macd = latest_data['macd']
    current_signal = latest_data['macd_signal']
    prev_macd = previous_data['macd']
    prev_signal = previous_data['macd_signal']

    if pd.isna(current_macd) or pd.isna(current_signal) or pd.isna(prev_macd) or pd.isna(prev_signal):
        signal = "持有/观望"
        reason = "MACD指标值无效，数据不足或计算错误，建议观望。"
    else:
        is_golden_cross = (prev_macd < prev_signal) and (current_macd >= current_signal)
        is_death_cross = (prev_macd > prev_signal) and (current_macd <= current_signal)

        if not is_holding:
            if is_golden_cross:
                signal = "买入"
                reason = f"MACD出现金叉 (DIF:{current_macd:.4f} 上穿 DEA:{current_signal:.4f})，是潜在的买入时机。"
            else:
                signal = "持有/观望"
                reason = f"未形成金叉，当前DIF({current_macd:.4f}), DEA({current_signal:.4f})。"
        else: # is_holding is True
            if is_death_cross:
                signal = "卖出"
                reason = f"MACD出现死叉 (DIF:{current_macd:.4f} 下穿 DEA:{current_signal:.4f})，是潜在的卖出时机。"
            else:
                signal = "持有/观望" # 继续持有
                reason = f"未形成死叉，继续持有。当前DIF({current_macd:.4f}), DEA({current_signal:.4f})。"

    return {
        "signal": signal,
        "reason": reason,
        "latest_date": latest_date,
        "latest_close": latest_close,
        "metrics": {
            "macd_short_period": MACD_SHORT_PERIOD,
            "macd_long_period": MACD_LONG_PERIOD,
            "macd_signal_period": MACD_SIGNAL_PERIOD,
            "dif_value": round(current_macd, 4) if pd.notna(current_macd) else None,
            "dea_value": round(current_signal, 4) if pd.notna(current_signal) else None,
            "macd_hist_value": round(latest_data['macd_hist'], 4) if pd.notna(latest_data['macd_hist']) else None,
        }
    }