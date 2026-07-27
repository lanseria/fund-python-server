# src/python_cli_starter/fund_realtime.py

"""基金实时估值与昨日净值模块。

提供两个核心能力：
    1. ``get_realtime_estimation`` — 交易时段内分钟级刷新的基金实时估算净值
    2. ``get_yesterday_nav``        — 最近一个交易日的官方单位净值（昨日真实净值）

数据源：
    - 实时估值：``monitor.powercloud.work/api/fund/{code}`` 聚合接口
    - 昨日净值：akshare ``fund_open_fund_info_em``（单位净值走势，取 tail(1)，与
      本仓库 ``fund_info._fetch_history_nav`` 一致）

实现要点：
    - 实时估值改用 powercloud 聚合接口：它已封装好「东财实时估算 + 历史净值回退 +
      QDII 处理」，字段语义清晰（``gsz`` 估算净值原值、``confirmed_nav`` 官方净值、
      ``success``/``quote_source``/``message`` 状态标识）。
    - 历史上先后用过：东财 ``fundgz`` JSONP（已废弃）、akshare 东财估值表
      ``fund_value_estimation_em``（底层接口失效致全量基金无法估值）、新浪
      ``hq.sinajs.cn``（估算净值需反算）。现统一收敛到 powercloud。
    - powercloud 对非 6 位代码（如 5 位）可能误匹配，故代码格式校验（6 位数字）
      由调用方（API 路由层）保证，先于数据源调用。
    - powercloud 额外返回 ``intraday``（盘中分时）、``history``、``holdings``，
      本模块仅透传 ``intraday`` 分时数据（非交易时段为空数组）。
"""

import logging
from typing import Any, Dict, List, Optional

import akshare as ak
import pandas as pd
import requests

logger = logging.getLogger(__name__)

# powercloud 聚合接口
_POWERCLOUD_URL_TPL = "https://monitor.powercloud.work/api/fund/{code}"
_POWERCLOUD_TIMEOUT = 10.0
_POWERCLOUD_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

# 东财/akshare 常用占位符，命中即视为无数据
_PLACEHOLDERS = {"", "-", "---", "—", "--", "nan", "NaN"}


def _to_float(value: Any) -> Optional[float]:
    """把字符串/数值稳健转为 float。失败/空值/占位符返回 None。"""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        s = str(value).strip()
        if s in _PLACEHOLDERS:
            return None
        # 去掉百分号
        if s.endswith("%"):
            s = s[:-1]
        return float(s)
    except (ValueError, TypeError):
        return None


def _fetch_powercloud_estimation(fund_code: str) -> Optional[Dict[str, Any]]:
    """
    请求 powercloud 聚合接口并解析 ``basic`` + ``intraday``。

    powercloud ``basic`` 关键字段（东财原始命名）：
        - ``gsz``      盘中估算净值（实时估算时为当前估值；非交易时段/QDII 回退到最近净值）
        - ``gszzl``    估算涨跌幅（%）
        - ``gztime``   估值时间
        - ``dwjz``     单位净值（即上一确认日净值）
        - ``jzrq``     净值日期
        - ``confirmed_nav``    已确认官方净值
        - ``confirmed_change`` 已确认官方涨跌幅
        - ``confirmed_date``   官方净值日期
        - ``name``     基金名称
        - ``success``  是否有盘中实时估算（非交易时段/QDII 为 False）
        - ``quote_source`` 数据来源标识（``realtime`` / ``history_fallback`` 等）
        - ``message``  状态说明

    :return: 含 ``basic`` / ``intraday`` 的字典；请求失败或无数据返回 None。
    """
    fund_code = str(fund_code)
    url = _POWERCLOUD_URL_TPL.format(code=fund_code)
    try:
        resp = requests.get(
            url, headers=_POWERCLOUD_HEADERS, timeout=_POWERCLOUD_TIMEOUT
        )
    except Exception as e:
        logger.warning(f"[FundRealtime] powercloud 请求异常 code={fund_code}: {e}")
        return None

    if resp.status_code != 200:
        logger.warning(
            f"[FundRealtime] powercloud 响应非 200 code={fund_code} "
            f"status={resp.status_code}"
        )
        return None

    try:
        payload = resp.json()
    except ValueError as e:
        logger.warning(f"[FundRealtime] powercloud 响应非 JSON code={fund_code}: {e}")
        return None

    basic = payload.get("basic") or {}
    # 基金代码不存在的兜底：powercloud 会返回 name==code 且估算净值为占位符
    name = str(basic.get("name", "")).strip()
    gsz = str(basic.get("gsz", "")).strip()
    if (
        not basic
        or name in _PLACEHOLDERS
        or (name == fund_code and gsz in _PLACEHOLDERS)
    ):
        logger.info(f"[FundRealtime] powercloud 无基金 {fund_code} 数据")
        return None

    intraday = payload.get("intraday") or {}
    return {
        "basic": basic,
        "intraday": list(intraday.get("data") or []),
    }


def _build_from_powercloud(fund_code: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """把 powercloud 解析结果映射为对外契约字典。"""
    basic = data.get("basic") or {}

    estimate_nav = _to_float(basic.get("gsz"))
    estimate_growth = _to_float(basic.get("gszzl"))
    yesterday_nav = _to_float(basic.get("dwjz"))
    published_nav = _to_float(basic.get("confirmed_nav"))
    published_growth = _to_float(basic.get("confirmed_change"))

    return {
        "code": fund_code,
        "name": str(basic.get("name", "")).strip(),
        "estimateNav": _fmt_float(estimate_nav),
        "estimateGrowthRate": estimate_growth,
        "estimateDate": str(basic.get("gztime", "")).strip(),
        "publishedNav": _fmt_float(published_nav),
        "publishedGrowthRate": published_growth,
        "yesterdayNav": _fmt_float(yesterday_nav),
        "yesterdayDate": str(basic.get("jzrq", "")).strip(),
        # 新增：状态标识（透明透传，便于前端区分实时估算 vs 历史回退）
        "quoteSource": str(basic.get("quote_source", "")).strip() or None,
        "message": str(basic.get("message", "")).strip(),
        # 新增：盘中分时数据（非交易时段/QDII 为空数组）
        "intraday": list(data.get("intraday") or []),
    }


def get_realtime_estimation(fund_code: str) -> Optional[Dict[str, Any]]:
    """
    获取单只基金的实时估算净值（交易时段内分钟级刷新）。

    数据源：powercloud 聚合接口（已封装东财实时估算 + 历史净值回退 + QDII 处理）。

    :param fund_code: 基金代码（调用方需保证为合法 6 位代码）
    :return: 契约结构字典；数据源不可用或基金不存在返回 None。
             ``quoteSource``/``message`` 标识数据状态；``intraday`` 为盘中分时
             （非交易时段为空数组）。
    """
    fund_code = str(fund_code)
    data = _fetch_powercloud_estimation(fund_code)
    if data is None:
        logger.warning(f"[FundRealtime] 无法获取基金 {fund_code} 的实时估值")
        return None

    logger.info(
        f"[FundRealtime] 基金 {fund_code} 实时估值获取成功 "
        f"quote_source={data['basic'].get('quote_source')}"
    )
    return _build_from_powercloud(fund_code, data)


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


def _fmt_float(value: Optional[float]) -> Optional[str]:
    """数值格式化为 4 位小数字符串；None 保持 None。"""
    if value is None:
        return None
    return f"{value:.4f}"
