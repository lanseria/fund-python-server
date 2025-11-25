# src/python_cli_starter/market_analysis.py
import akshare as ak
import pandas as pd
import logging
from typing import Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

# 指数代码映射 (Akshare symbol)
INDEX_MAPPING = {
    "shanghai_composite": {"symbol": "sh000001", "name": "上证指数"},
    "csi300": {"symbol": "sz399300", "name": "沪深300"},
    "chinext": {"symbol": "sz399006", "name": "创业板指"},
}

def calculate_technical_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """计算 MA 和 MACD 指标"""
    # 1. 计算均线
    df['MA5'] = df['close'].rolling(window=5).mean()
    df['MA20'] = df['close'].rolling(window=20).mean()
    
    # 2. 计算 MACD (12, 26, 9)
    # EMA
    exp12 = df['close'].ewm(span=12, adjust=False).mean()
    exp26 = df['close'].ewm(span=26, adjust=False).mean()
    df['DIF'] = exp12 - exp26
    df['DEA'] = df['DIF'].ewm(span=9, adjust=False).mean()
    df['MACD'] = 2 * (df['DIF'] - df['DEA'])
    
    return df

def analyze_single_index(key: str, config: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """分析单个指数"""
    symbol = config['symbol']
    name = config['name']
    
    try:
        # 获取历史数据 (akshare 接口: stock_zh_index_daily_em)
        # 注意: 东方财富接口通常返回的是未复权数据，对于指数来说通常没问题
        df = ak.stock_zh_index_daily_em(symbol=symbol)
        
        if df is None or df.empty:
            logger.warning(f"无法获取指数数据: {symbol}")
            return None
            
        # 数据清洗
        df['date'] = pd.to_datetime(df['date'])
        df = df.set_index('date').sort_index()
        # 确保列名统一 (akshare 返回列名: date, open, close, high, low, volume, amount)
        # 我们主要需要 close
        df['close'] = pd.to_numeric(df['close'])
        
        # 截取最近足够计算指标的数据 (例如最近100天)
        df_recent = df.tail(100).copy()
        
        if len(df_recent) < 30:
            return None

        # 计算指标
        df_calc = calculate_technical_indicators(df_recent)
        
        # 获取最后一行数据 (最新)
        latest = df_calc.iloc[-1]
        prev = df_calc.iloc[-2]
        
        # --- 1. 基础数据 ---
        current_price = float(latest['close'])
        prev_close = float(prev['close'])
        # 计算涨跌幅
        change_pct = (current_price - prev_close) / prev_close
        
        # --- 2. 均线状态 ---
        ma_status_list = []
        if current_price > latest['MA5']:
            ma_status_list.append("above_MA5")
        else:
            ma_status_list.append("below_MA5")
            
        if current_price > latest['MA20']:
            ma_status_list.append("above_MA20")
        else:
            ma_status_list.append("below_MA20")
            
        ma_status = ", ".join(ma_status_list)
        
        # --- 3. MACD 状态 ---
        dif, dea = latest['DIF'], latest['DEA']
        prev_dif, prev_dea = prev['DIF'], prev['DEA']
        
        macd_status = "neutral"
        if dif > dea and prev_dif <= prev_dea:
            macd_status = "golden_cross" # 金叉
        elif dif < dea and prev_dif >= prev_dea:
            macd_status = "death_cross" # 死叉
        elif dif > dea:
            macd_status = "bullish_zone" # 多头区域
        else:
            macd_status = "bearish_zone" # 空头区域
            
        # --- 4. 技术形态简评 ---
        technical_trend = "sideways"
        if current_price > latest['MA20'] and latest['MA5'] > latest['MA20']:
            technical_trend = "bullish" # 多头排列
        elif current_price < latest['MA20'] and latest['MA5'] < latest['MA20']:
            technical_trend = "bearish" # 空头排列
        elif current_price > latest['MA20'] and latest['MA5'] < latest['MA20']:
            technical_trend = "rebound" # 反弹 (价格在长期均线上，但短期均线还在下)
        elif current_price < latest['MA20'] and latest['MA5'] > latest['MA20']:
            technical_trend = "pullback" # 回调 (价格跌破长期均线，但短期均线还在上)

        return {
            "name": name,
            "current": round(current_price, 2),
            "change_pct": round(change_pct, 4), # 0.005
            "ma_status": ma_status,
            "macd_status": macd_status,
            "technical_trend": technical_trend
        }

    except Exception as e:
        logger.error(f"分析指数 {name} ({symbol}) 时出错: {e}")
        return None

def get_market_overview_data() -> Dict[str, Any]:
    """获取所有配置指数的分析结果"""
    results = {}
    for key, config in INDEX_MAPPING.items():
        data = analyze_single_index(key, config)
        if data:
            results[key] = data
        else:
            # 如果获取失败，返回一个空占位或错误信息，这里选择为了前端健壮性返回默认值
            results[key] = {
                "name": config["name"],
                "current": 0.0,
                "change_pct": 0.0,
                "ma_status": "N/A",
                "macd_status": "N/A",
                "technical_trend": "unknown"
            }
    return results