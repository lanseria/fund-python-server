# src/python_cli_starter/gold_realtime.py

"""上海黄金交易所（SGE）贵金属实时行情模块。

为「基金自算估值」的黄金分支提供底层行情能力：黄金 ETF 联接等场外基金
（如前海开源黄金ETF联接C 021740）季报无股票重仓、几乎满仓跟踪国内金价，
按 SGE Au99.99 现货涨跌幅套用昨净即可近似基金净值涨跌幅，供 Nuxt 端
替代重仓股加权方案。

数据源：
    新浪财经贵金属行情 ``hq.sinajs.cn/list=gds_AU9999,...``（GBK 编码文本，
    必须携带 ``Referer: https://finance.sina.com.cn/`` 否则被拒）。不选腾讯
    行情（qt.gtimg.cn 不覆盖上海黄金交易所），也不选东财 push2（部分网络
    环境对 IPv6/TLS 指纹直接断连，与 stock_realtime 的选型结论一致）。

gds_ 行字段（以 AU9999 为例，``931.80,0,931.56,932.00,951.50,930.00,
15:29:59,938.09,941.50,687566,330.00,423.00,2026-09-22,沪金99``）：
    parts[0] 最新价 / [2] 均价 / [3] 今开 / [4] 最高 / [5] 最低 / [6] 时间
    / [7] 昨收 / [8] 昨结算 / [9] 成交量 / [12] 日期 / [13] 合约名。
    接口不直接给涨跌幅，按 ``(最新价 - 昨收) / 昨收`` 计算。SGE 夜市
    （20:00-02:30）归属次一交易日，昨收为上一交易日收盘价，因此涨跌幅
    天然包含隔夜跳空，与基金净值口径一致。

实现要点：
    - 交易时段：日市 9:00-15:30 与 A 股重叠，Nuxt 端现有 cron 窗口
      （9-16 点）无需调整；夜市暂不消费。
    - 进程内 TTL 缓存（60 秒），按单合约粒度缓存，与 stock_realtime
      同款「懒刷新」模式：同步路由 + threading.Lock，无需后台循环。
    - Windows 下 requests 会拾取系统代理（注册表），代理对行情域名异常时
      降级为不走代理直连重试一次。
"""

import logging
import threading
import time as _time
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

_SINA_GDS_URL = "https://hq.sinajs.cn/list="
# 新浪行情接口强校验 Referer，缺省会返回空内容/拒绝
_SINA_REFERER = "https://finance.sina.com.cn/"
_SINA_TIMEOUT = 10.0
# 进程内缓存 TTL（秒）
_CACHE_TTL = 60.0
# 单次请求的合约数量上限（路由层校验）
_MAX_CODES = 10

# 支持的贵金属代码 → 新浪 gds_ 符号。AU9999（沪金99，Au99.99 现货）是
# 黄金 ETF 及其联接基金的主流跟踪标的；AUTD（黄金延期）作备用对照。
_GDS_CODES: Dict[str, str] = {
    "AU9999": "gds_AU9999",
    "AUTD": "gds_AUTD",
}

# ---------------------------------------------------------------------------
# 进程内 TTL 缓存：{code: {"item": dict, "updated_at": epoch_seconds}}
# ---------------------------------------------------------------------------
_CACHE: Dict[str, Dict[str, Any]] = {}
_CACHE_LOCK = threading.Lock()


def _to_float(value: Optional[str]) -> Optional[float]:
    """稳健转 float；空串/占位符返回 None。"""
    if value is None:
        return None
    s = value.strip()
    if s in {"", "-", "---"}:
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _parse_line(line: str) -> Optional[Dict[str, Any]]:
    """解析单行 ``var hq_str_gds_AU9999="..."`` 为响应条目。

    涨跌幅按昨收（parts[7]）计算，保留 4 位小数；昨收缺失/为 0（如新合约
    首日）时 changePct 为 None，由调用方跳过。
    """
    # 行形如 var hq_str_gds_AU9999="...";，gds_ 与 =" 之间即规范代码
    if not line.startswith("var hq_str_gds_"):
        return None
    head, _, quoted = line.partition('="')
    symbol = head[len("var hq_str_gds_") :]
    if symbol not in _GDS_CODES:
        return None
    code = symbol
    parts = quoted.rstrip('";').split(",")
    if len(parts) < 14:
        return None
    # 未开盘/新合约首日价格为 0，视为无有效行情（与 stock_realtime 口径一致）
    price = _to_float(parts[0]) or None
    prev_close = _to_float(parts[7]) or None
    change_pct: Optional[float] = None
    if price is not None and prev_close:
        change_pct = round((price - prev_close) / prev_close * 100, 4)
    return {
        "code": code,
        # 合约名（如 "沪金99"/"黄金延期"）
        "name": parts[13].strip(),
        "price": price,
        "prevClose": prev_close,
        "changePct": change_pct,
        "date": parts[12].strip(),
        "time": parts[6].strip(),
    }


def _request_text(symbols: List[str]) -> Optional[str]:
    """GET 新浪贵金属行情并返回 GBK 解码文本；代理异常时降级直连重试一次。"""
    url = _SINA_GDS_URL + ",".join(symbols)
    headers = {"Referer": _SINA_REFERER}
    try:
        resp = requests.get(url, timeout=_SINA_TIMEOUT, headers=headers)
        return resp.content.decode("gbk", errors="replace")
    except requests.exceptions.ProxyError as e:
        logger.warning(f"[GoldRealtime] 代理连接失败，改为直连重试: {e}")
        direct = requests.Session()
        direct.trust_env = False
        resp = direct.get(url, timeout=_SINA_TIMEOUT, headers=headers)
        return resp.content.decode("gbk", errors="replace")


def _get_fresh_items(codes: List[str]) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    """取缓存中仍新鲜的条目，返回 ({code: item}, 需要拉取的代码列表)。"""
    now = _time.time()
    fresh: Dict[str, Dict[str, Any]] = {}
    stale: List[str] = []
    with _CACHE_LOCK:
        for code in codes:
            entry = _CACHE.get(code)
            if entry and now - entry["updated_at"] < _CACHE_TTL:
                fresh[code] = entry["item"]
            else:
                stale.append(code)
    return fresh, stale


def _store_items(items: Dict[str, Dict[str, Any]]) -> None:
    now = _time.time()
    with _CACHE_LOCK:
        for code, item in items.items():
            _CACHE[code] = {"item": item, "updated_at": now}


def get_gold_realtime(codes: List[str]) -> Dict[str, Any]:
    """批量获取上海黄金交易所贵金属实时行情。

    :param codes: 贵金属代码列表（如 ``["AU9999"]``，调用方已去重；
                  不支持的代码会进 missing）
    :return: ``{"quotes": [GoldRealtimeItem...], "missing": [code...]}``；
             ``quotes`` 含成功获取的行情（未开盘等场景 price/changePct 可为
             None），``missing`` 为不支持的代码或拉取失败的代码。
    """
    supported: List[str] = []
    missing: List[str] = []
    seen = set()
    for code in codes:
        normalized = code.strip().upper()
        if normalized in seen:
            continue
        seen.add(normalized)
        if normalized in _GDS_CODES:
            supported.append(normalized)
        else:
            missing.append(code)

    fresh, stale = _get_fresh_items(supported)
    if stale:
        symbols = [_GDS_CODES[c] for c in stale]
        try:
            text = _request_text(symbols)
        except Exception as e:
            logger.warning(f"[GoldRealtime] 新浪贵金属行情请求异常 symbols={symbols}: {e}")
            text = None
        items: Dict[str, Dict[str, Any]] = {}
        if text:
            for line in text.split(";"):
                line = line.strip()
                if not line:
                    continue
                item = _parse_line(line)
                if item is not None:
                    items[item["code"]] = item
        _store_items(items)
        fresh.update(items)

    fetched_codes = set(fresh.keys())
    still_missing = [c for c in supported if c not in fetched_codes]
    quotes = [fresh[c] for c in supported if c in fetched_codes]
    return {
        "quotes": quotes,
        "missing": missing + still_missing,
    }
