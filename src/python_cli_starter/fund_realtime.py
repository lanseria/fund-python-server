# src/python_cli_starter/fund_realtime.py

"""基金实时估值与昨日净值模块。

提供两个核心能力：
    1. ``get_realtime_estimation`` — 交易时段内分钟级刷新的基金实时估算净值
    2. ``get_yesterday_nav``        — 最近一个交易日的官方单位净值（昨日真实净值）

数据源：
    - 实时估值：akshare ``fund_value_estimation_em``（东方财富盘中估值，全市场聚合表）
    - 昨日净值：akshare ``fund_open_fund_info_em``（单位净值走势，取 tail(1)，与
      本仓库 ``fund_info._fetch_history_nav`` 一致）

实现要点：
    - 旧的实时估值 JSONP 接口 ``fundgz.1234567.com.cn/js/{code}.js`` 已废弃失效，
      改用东财官方盘中估值表。
    - ``fund_value_estimation_em(symbol='全部')`` 存在 20000 行截断 bug，会漏掉部分
      基金（典型如主流 LOF）。因此合并多个 symbol 类型去重后缓存。
    - 估值表列名嵌有动态当天日期（如 ``2026-07-21-估算数据-估算值``），无法硬编码，
      统一按列位置（iloc）取值。
    - 估值表全市场拉取约 0.3~1.7s，且估值本身仅分钟级更新，故采用进程内 60s 缓存。
"""

import logging
import time
from typing import Any, Dict, List, Optional

import akshare as ak
import pandas as pd

logger = logging.getLogger(__name__)

# 合并多个 symbol 类型拉取估值表，规避 fund_value_estimation_em('全部') 的 20000 行截断 bug
# （'全部' 会漏掉大量基金，包括主流 LOF 如 161725；'LOF' / '场内交易基金' 可补全）
_ESTIMATION_SYMBOLS: List[str] = ["全部", "LOF", "场内交易基金"]

# 进程内缓存（估值分钟级刷新，缓存 60s 延迟完全可接受）
_CACHE_TTL = 60.0  # 秒
_cache: Dict[str, Any] = {"df": None, "ts": 0.0}


def _to_float(value: Any) -> Optional[float]:
    """把估值表里的百分比/数值字符串稳健转为 float。失败/空值返回 None。"""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        s = str(value).strip()
        # 跳过东财的占位符
        if s in ("", "---", "—", "--", "nan", "NaN"):
            return None
        # 去掉百分号
        if s.endswith("%"):
            s = s[:-1]
        return float(s)
    except (ValueError, TypeError):
        return None


def _load_merged_table() -> Optional[pd.DataFrame]:
    """
    拉取多个 symbol 的估值表并按基金代码去重合并。

    单个 symbol 异常时降级跳过；全部失败返回 None。
    """
    frames: List[pd.DataFrame] = []
    for symbol in _ESTIMATION_SYMBOLS:
        try:
            df = ak.fund_value_estimation_em(symbol=symbol)
        except Exception as e:
            logger.warning(f"[FundRealtime] 估值表拉取失败 symbol={symbol}: {e}")
            continue
        if df is None or df.empty or df.shape[1] < 9:
            continue
        frames.append(df)

    if not frames:
        return None

    merged = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
    if merged.empty:
        return None

    # 列 [1] = 基金代码，按其去重保留首行
    merged = merged.drop_duplicates(subset=[merged.columns[1]], keep="first")
    logger.info(
        f"[FundRealtime] 估值表合并完成: {len(merged)} 只基金 "
        f"(来源 symbol: {[s for s in _ESTIMATION_SYMBOLS]})"
    )
    return merged.reset_index(drop=True)


def _get_cached_table() -> Optional[pd.DataFrame]:
    """
    获取（必要时刷新）进程内缓存的估值表。

    缓存 TTL 为 ``_CACHE_TTL`` 秒。过期或为空时重建。
    """
    now = time.monotonic()
    if _cache["df"] is not None and (now - _cache["ts"]) < _CACHE_TTL:
        return _cache["df"]

    df = _load_merged_table()
    if df is None:
        # 拉取失败：若仍有旧缓存可继续用，否则返回 None（由上层转 404/降级）
        if _cache["df"] is not None:
            logger.warning("[FundRealtime] 估值表刷新失败，回退使用旧缓存")
            return _cache["df"]
        return None

    _cache["df"] = df
    _cache["ts"] = now
    return df


def clear_cache() -> None:
    """清空进程内估值表缓存（测试用）。"""
    _cache["df"] = None
    _cache["ts"] = 0.0


def get_realtime_estimation(fund_code: str) -> Optional[Dict[str, Any]]:
    """
    获取单只基金的实时估算净值（交易时段内分钟级刷新）。

    :param fund_code: 基金代码（调用方需保证为合法 6 位代码）
    :return: 契约结构字典；若基金不在东财盘中估值列表返回 None。
             不同基金类型（QDII/货币型等）部分字段可能为 None。
    """
    fund_code = str(fund_code)
    df = _get_cached_table()
    if df is None or df.empty:
        logger.warning(f"[FundRealtime] 估值表不可用，无法查询基金 {fund_code}")
        return None

    # 列位置（列名含动态当天日期，必须按位置取）：
    #   [0]序号 [1]基金代码 [2]基金名称
    #   [3]估算值 [4]估算增长率 [5]公布单位净值 [6]公布日增长率 [7]估算偏差
    #   [8]上一交易日单位净值
    code_col = df.columns[1]
    mask = df[code_col].astype(str).str.zfill(6) == fund_code.zfill(6)
    matched = df[mask]
    if matched.empty:
        logger.info(f"[FundRealtime] 基金 {fund_code} 不在盘中估值列表")
        return None

    row = matched.iloc[0]
    name = "" if pd.isna(row.iloc[2]) else str(row.iloc[2]).strip()
    estimate_nav_raw = row.iloc[3]
    estimate_growth_raw = row.iloc[4]
    published_nav_raw = row.iloc[5]
    published_growth_raw = row.iloc[6]
    yesterday_nav_raw = row.iloc[8] if df.shape[1] > 8 else None

    estimate_nav = _to_float(estimate_nav_raw)
    estimate_growth = _to_float(estimate_growth_raw)
    yesterday_nav = _to_float(yesterday_nav_raw)
    published_nav = _to_float(published_nav_raw)

    # 估值时间：取当天日期（akshare 该接口未返回分钟级时间戳，只能到日）
    estimate_date = _extract_date_from_column(str(df.columns[3]))

    return {
        "code": fund_code,
        "name": name,
        "estimateNav": _fmt_float(estimate_nav),
        "estimateGrowthRate": estimate_growth,  # 已是数字百分比，如 -1.85
        "estimateDate": estimate_date,
        "publishedNav": _fmt_float(published_nav),  # 盘前为 None
        "publishedGrowthRate": _to_float(published_growth_raw),
        "yesterdayNav": _fmt_float(yesterday_nav),
        "yesterdayDate": _extract_date_from_column(str(df.columns[8]))
        if df.shape[1] > 8
        else "",
    }


def get_yesterday_nav(fund_code: str) -> Optional[Dict[str, Any]]:
    """
    获取单只基金最近一个交易日的官方单位净值（昨日真实净值）。

    通过 ``fund_open_fund_info_em`` 单位净值走势取最后一条，与
    本仓库 ``fund_info._fetch_history_nav`` 一致。

    :param fund_code: 基金代码（调用方需保证为合法 6 位代码）
    :return: 契约结构字典；获取失败返回 None。
    """
    fund_code = str(fund_code)
    logger.info(f"[FundRealtime] 正在获取基金 {fund_code} 昨日净值...")
    try:
        df = ak.fund_open_fund_info_em(symbol=fund_code, indicator="单位净值走势")
    except Exception as e:
        logger.error(f"[FundRealtime] 基金 {fund_code} 昨日净值获取异常: {e}")
        return None

    if df is None or df.empty:
        logger.warning(f"[FundRealtime] 基金 {fund_code} 历史净值为空")
        return None

    last = df.iloc[-1]
    nav_date = last.get("净值日期")
    nav = last.get("单位净值")
    growth = last.get("日增长率")

    if pd.isna(nav_date) or pd.isna(nav):
        logger.warning(f"[FundRealtime] 基金 {fund_code} 最新净值数据异常")
        return None

    date_str = (
        nav_date.strftime("%Y-%m-%d") if hasattr(nav_date, "strftime") else str(nav_date)
    )
    nav_float = float(nav)
    growth_float = _to_float(growth)

    # 顺带尝试取基金名称（akshare 该接口无名称，留空由调用方按需补充）
    return {
        "code": fund_code,
        "name": "",
        "nav": f"{nav_float:.4f}",
        "navDate": date_str,
        "growthRate": growth_float,
    }


def _extract_date_from_column(col_name: str) -> str:
    """
    从估值表动态日期列名（如 ``2026-07-21-估算数据-估算值``）中提取日期部分。
    """
    # 取首段非空（按 "-" 切，前 3 段即 yyyy-mm-dd）
    parts = col_name.split("-")
    if len(parts) >= 3 and parts[0].isdigit() and len(parts[0]) == 4:
        return "-".join(parts[:3])
    return ""


def _fmt_float(value: Optional[float]) -> Optional[str]:
    """数值格式化为 4 位小数字符串；None 保持 None。"""
    if value is None:
        return None
    return f"{value:.4f}"
