# src/python_cli_starter/strategies/bollinger_bands_strategy.py

import akshare as ak
import pandas as pd
from datetime import datetime, timedelta
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

# --- 策略常量 ---
BBANDS_PERIOD = 50
BBANDS_DEV_FACTOR = 2.0

def get_latest_fund_data(fund_symbol: str) -> pd.DataFrame:
    """获取基金最近200天的净值数据"""
    logger.info(f"[BBands Strategy] 正在为基金 {fund_symbol} 获取最新净值数据...")
    # 缓冲期增加到200天以确保50周期计算的稳定性
    start_date = datetime.today() - timedelta(days=200)
    
    try:
        fund_nav_df = ak.fund_open_fund_info_em(symbol=fund_symbol, indicator="单位净值走势")
        fund_nav_df['净值日期'] = pd.to_datetime(fund_nav_df['净值日期'])
        fund_nav_df = fund_nav_df.set_index('净值日期')
        fund_nav_df = fund_nav_df[['单位净值']]
        fund_nav_df.columns = ['close']
        fund_nav_df['close'] = pd.to_numeric(fund_nav_df['close'])
        
        fund_nav_df = fund_nav_df[fund_nav_df.index >= start_date].sort_index(ascending=True)
        
        if fund_nav_df.empty or len(fund_nav_df) < BBANDS_PERIOD + 1:
            logger.warning(f"[BBands Strategy] 获取到的数据为空或数据量不足以计算布林带。")
            return None
            
        logger.info(f"[BBands Strategy] 数据获取成功，共 {len(fund_nav_df)} 条记录。")
        return fund_nav_df
        
    except Exception as e:
        logger.error(f"[BBands Strategy] 获取基金 {fund_symbol} 数据时发生错误: {e}")
        return None

def calculate_bollinger_bands(data: pd.DataFrame, period: int, dev_factor: float) -> pd.DataFrame:
    """使用 pandas 手动计算布林带指标。"""
    data['bband_mid'] = data['close'].rolling(window=period).mean()
    rolling_std = data['close'].rolling(window=period).std()
    data['bband_upper'] = data['bband_mid'] + (rolling_std * dev_factor)
    data['bband_lower'] = data['bband_mid'] - (rolling_std * dev_factor)
    return data

def run_strategy(fund_code: str, is_holding: bool) -> Dict[str, Any]:
    """
    执行布林带策略并返回决策结果。
    :param fund_code: 基金代码。
    :param is_holding: 用户当前是否持有该基金。
    :return: 包含决策信号和数据的字典。
    """
    df = get_latest_fund_data(fund_code)

    if df is None:
        return {"error": f"无法获取基金 {fund_code} 的数据。"}

    df_with_bbands = calculate_bollinger_bands(df, period=BBANDS_PERIOD, dev_factor=BBANDS_DEV_FACTOR)
    
    latest_data = df_with_bbands.iloc[-1]
    latest_date = latest_data.name.date()
    latest_close = latest_data['close']
    bband_mid = latest_data['bband_mid']
    bband_upper = latest_data['bband_upper']
    bband_lower = latest_data['bband_lower']
    
    if pd.isna(bband_lower) or pd.isna(bband_mid):
        signal = "持有/观望"
        reason = "布林带指标值无效，数据不足或计算错误，建议观望。"
    # 如果当前空仓，只判断买入条件
    elif not is_holding:
        if latest_close <= bband_lower:
            signal = "买入"
            reason = f"价格({latest_close:.4f})已触及或跌破布林带下轨({bband_lower:.4f})，是潜在的买入时机。"
        else:
            signal = "持有/观望" # "继续观望" 映射为 "持有/观望"
            reason = f"价格({latest_close:.4f})高于布林带下轨({bband_lower:.4f})，未到买入时机。"
    # 如果当前持仓，只判断卖出条件
    else: # is_holding is True
        if latest_close >= bband_mid:
            signal = "卖出"
            reason = f"价格({latest_close:.4f})已回归到布林带中轨({bband_mid:.4f})，是潜在的卖出时机。"
        else:
            signal = "买入" # "继续持有" 在此策略下等同于"买入"区的持有状态
            reason = f"价格({latest_close:.4f})未回归到布林带中轨({bband_mid:.4f})，未到卖出时机，继续持有。"
    
    # 统一信号输出
    if "买入" in signal:
        final_signal = "买入"
    elif "卖出" in signal:
        final_signal = "卖出"
    else:
        final_signal = "持有/观望"


    return {
        "signal": final_signal,
        "reason": reason,
        "latest_date": latest_date,
        "latest_close": latest_close,
        "metrics": {
            "bband_period": BBANDS_PERIOD,
            "bband_dev_factor": BBANDS_DEV_FACTOR,
            "bband_upper": round(bband_upper, 4) if pd.notna(bband_upper) else None,
            "bband_mid": round(bband_mid, 4) if pd.notna(bband_mid) else None,
            "bband_lower": round(bband_lower, 4) if pd.notna(bband_lower) else None,
        }
    }