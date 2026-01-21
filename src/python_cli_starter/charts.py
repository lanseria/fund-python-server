# src/python_cli_starter/charts.py

import akshare as ak
import pandas as pd
from datetime import datetime, timedelta
import logging
import numpy as np
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

# --- RSI 策略默认参数 ---
RSI_PERIOD = 14
RSI_UPPER = 70.0
RSI_LOWER = 30.0

def get_historical_fund_data(fund_symbol: str) -> Optional[pd.DataFrame]:
    """获取指定基金的全部历史净值数据。"""
    logger.info(f"[Charts] 正在为基金 {fund_symbol} 获取全部历史净值数据...")
    try:
        fund_nav_df = ak.fund_open_fund_info_em(symbol=fund_symbol, indicator="单位净值走势")
        fund_nav_df['净值日期'] = pd.to_datetime(fund_nav_df['净值日期'])
        fund_nav_df = fund_nav_df.set_index('净值日期')
        fund_nav_df = fund_nav_df[['单位净值']]
        fund_nav_df.columns = ['close']
        fund_nav_df['close'] = pd.to_numeric(fund_nav_df['close'])
        
        # 按日期升序排序
        fund_nav_df = fund_nav_df.sort_index(ascending=True)

        if fund_nav_df.empty:
            logger.warning(f"获取基金 {fund_symbol} 数据为空。")
            return None
            
        logger.info(f"数据获取成功！共获取 {len(fund_nav_df)} 条记录。")
        return fund_nav_df
        
    except Exception as e:
        logger.error(f"获取基金 {fund_symbol} 数据时发生错误: {e}")
        return None

def calculate_rsi(data: pd.DataFrame, period: int) -> pd.DataFrame:
    """计算 RSI 指标。"""
    delta = data['close'].diff()
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)
    ema_up = up.ewm(com=period - 1, adjust=False).mean()
    ema_down = down.ewm(com=period - 1, adjust=False).mean()
    rs = ema_up / ema_down
    data['rsi'] = 100 - (100 / (1 + rs))
    return data

def generate_rsi_signals(data: pd.DataFrame) -> pd.DataFrame:
    """根据RSI指标生成买卖信号。"""
    signals = []
    position = 0
    data['prev_rsi'] = data['rsi'].shift(1)
    
    for i in range(RSI_PERIOD, len(data)):
        current_rsi = data['rsi'].iloc[i]
        prev_rsi = data['prev_rsi'].iloc[i]
        current_date = data.index[i]

        if pd.isna(current_rsi) or pd.isna(prev_rsi):
            continue

        if position == 0 and current_rsi <= RSI_LOWER and prev_rsi > RSI_LOWER:
            signals.append({'date': current_date, 'type': 'buy', 'rsi': current_rsi})
            position = 1
        elif position == 1 and current_rsi >= RSI_UPPER and prev_rsi < RSI_UPPER:
            signals.append({'date': current_date, 'type': 'sell', 'rsi': current_rsi})
            position = 0
    return pd.DataFrame(signals)

def get_rsi_chart_data(fund_code: str) -> Optional[Dict[str, Any]]:
    """
    为RSI策略生成 ECharts 所需的图表数据 (全部历史)。
    """
    df_full = get_historical_fund_data(fund_code)
    if df_full is None or df_full.empty:
        return None

    df_with_rsi = calculate_rsi(df_full, period=RSI_PERIOD)
    
    signals_df = generate_rsi_signals(df_with_rsi)

    # 在序列化之前，将所有 NaN, Inf, -Inf 替换为 None (JSON中的null)
    df_with_rsi.replace([np.inf, -np.inf], np.nan, inplace=True)
    
    # 准备 ECharts 数据
    dates = df_with_rsi.index.strftime('%Y-%m-%d').tolist()
    
    # 将 Series 转换为列表，并在转换过程中处理 NaN
    net_values = [None if pd.isna(v) else round(v, 4) for v in df_with_rsi['close']]
    rsi_values = [None if pd.isna(v) else round(v, 2) for v in df_with_rsi['rsi']]

    # 准备买卖信号点数据
    buy_signals = []
    sell_signals = []
    if not signals_df.empty:
        for _, row in signals_df.iterrows():
            if pd.notna(row['rsi']):
                signal_point = {
                    'coord': [row['date'].strftime('%Y-%m-%d'), round(row['rsi'], 2)],
                    'value': '买入' if row['type'] == 'buy' else '卖出'
                }
                if row['type'] == 'buy':
                    buy_signals.append(signal_point)
                else:
                    sell_signals.append(signal_point)

    return {
        "dates": dates,
        "netValues": net_values,
        "rsiValues": rsi_values,
        "signals": {
            "buy": buy_signals,
            "sell": sell_signals
        },
        "config": {
            "rsiPeriod": RSI_PERIOD,
            "rsiUpper": RSI_UPPER,
            "rsiLower": RSI_LOWER
        }
    }