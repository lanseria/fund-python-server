# src/python_cli_starter/strategies/rsi_strategy.py

import akshare as ak
import pandas as pd
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

# --- 策略常量 ---
RSI_PERIOD = 14
RSI_UPPER = 70.0
RSI_LOWER = 30.0

def get_latest_fund_data(fund_symbol: str):
    """获取基金最近100天的净值数据"""
    logger.info(f"[RSI Strategy] 正在为基金 {fund_symbol} 获取最新净值数据...")
    start_date = datetime.today() - timedelta(days=100)
    
    try:
        # 使用 akshare 获取数据
        fund_nav_df = ak.fund_open_fund_info_em(symbol=fund_symbol, indicator="单位净值走势")
        fund_nav_df['净值日期'] = pd.to_datetime(fund_nav_df['净值日期'])
        fund_nav_df = fund_nav_df.set_index('净值日期')
        fund_nav_df = fund_nav_df[['单位净值']]
        fund_nav_df.columns = ['close']
        fund_nav_df['close'] = pd.to_numeric(fund_nav_df['close'])
        
        # 筛选日期并按日期升序排序
        fund_nav_df = fund_nav_df[fund_nav_df.index >= start_date].sort_index(ascending=True)
        
        if fund_nav_df.empty or len(fund_nav_df) < RSI_PERIOD + 1:
            logger.warning(f"[RSI Strategy] 获取到的数据为空或数据量不足以计算RSI。")
            return None
            
        logger.info(f"[RSI Strategy] 数据获取成功，共 {len(fund_nav_df)} 条记录。")
        return fund_nav_df
        
    except Exception as e:
        logger.error(f"[RSI Strategy] 获取基金 {fund_symbol} 数据时发生错误: {e}")
        return None

def calculate_rsi(data: pd.DataFrame, period: int) -> pd.DataFrame:
    """使用 pandas 手动计算 RSI 指标。"""
    delta = data['close'].diff()
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)
    
    # 使用 Simple Moving Average (SMA) 作为初始值，更符合标准RSI计算
    ema_up = up.ewm(com=period - 1, adjust=False).mean()
    ema_down = down.ewm(com=period - 1, adjust=False).mean()
    
    rs = ema_up / ema_down
    data['rsi'] = 100 - (100 / (1 + rs))
    return data

def run_strategy(fund_code: str) -> dict:
    """
    执行RSI策略并返回决策结果。
    :param fund_code: 基金代码。
    :return: 包含决策信号和数据的字典，如果失败则返回 None。
    """
    df = get_latest_fund_data(fund_code)

    if df is None:
        return {"error": f"无法获取基金 {fund_code} 的数据。"}

    df_with_rsi = calculate_rsi(df, period=RSI_PERIOD)
    
    # 提取最新的数据
    latest_data = df_with_rsi.iloc[-1]
    latest_date = latest_data.name.date()
    latest_close = latest_data['close']
    latest_rsi = latest_data['rsi']

    # --- 核心决策逻辑 ---
    if pd.isna(latest_rsi):
        signal = "持有/观望"
        reason = f"RSI值无效 ({latest_rsi})，数据不足或计算错误，建议观望。"
    elif latest_rsi <= RSI_LOWER:
        signal = "买入"
        reason = f"RSI ({latest_rsi:.2f}) 进入超卖区 (<= {RSI_LOWER})，是潜在的买入时机。"
    elif latest_rsi >= RSI_UPPER:
        signal = "卖出"
        reason = f"RSI ({latest_rsi:.2f}) 进入超买区 (>= {RSI_UPPER})，是潜在的卖出时机。"
    else:
        signal = "持有/观望"
        reason = f"RSI ({latest_rsi:.2f}) 处于 {RSI_LOWER} 和 {RSI_UPPER} 之间的中间区域。"

    return {
        "signal": signal,
        "reason": reason,
        "latest_date": latest_date,
        "latest_close": latest_close,
        "metrics": {
            "rsi_period": RSI_PERIOD,
            "rsi_value": round(latest_rsi, 2) if pd.notna(latest_rsi) else None,
            "rsi_upper_band": RSI_UPPER,
            "rsi_lower_band": RSI_LOWER,
        }
    }