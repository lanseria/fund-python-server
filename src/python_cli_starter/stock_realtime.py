# src/python_cli_starter/stock_realtime.py

"""股票批量实时行情模块。

为「基金自算估值」提供底层行情能力：根据基金季报重仓股代码，批量拉取
A 股实时最新价与当日涨跌幅，供 Nuxt 端按持仓占比加权估算基金净值。

数据源：
    腾讯行情 ``qt.gtimg.cn/q=sh600519,sz000858,hk00700,...``（GBK 编码文本，
    ``~`` 分隔；字段位置与 Nuxt 端 dataFetcher 对指数/LOF 的解析一致：
    parts[1] 名称 / parts[2] 代码 / parts[3] 最新价 / parts[30] 时间
    / parts[32] 涨跌幅%）。A 股时间为 YYYYMMDDHHmmss 紧凑数字，
    港股为 ``2026/09/21 14:19:48`` 斜杠格式，均由 ``_format_quote_time``
    去非数字后统一解析。不用东财 push2：其对部分网络环境（IPv6/TLS
    指纹）会直接断连，腾讯源更稳。

实现要点：
    - 市场前缀规则：5 位数字 → 港股 ``hk{code}``（powercloud 重仓口径的
      港股代码为 5 位，如 00700/01810，腾讯行情以 hk 前缀区分，港股字段
      位置与 A 股一致）；6 开头 → ``sh{code}``，0/3 开头 → ``sz{code}``；
      其余（北交所 4/8 开头、美股等）不支持，归入 ``missing``
      返回，由调用方跳过。
    - 进程内 TTL 缓存（60 秒），按单只股票粒度缓存：请求集与缓存集有交集时
      只拉取过期/缺失部分，批量请求间天然去重。同步路由 + threading.Lock，
      按请求懒刷新，无需后台循环。
    - Windows 下 requests 会拾取系统代理（注册表），代理对行情域名异常时
      降级为不走代理直连重试一次。
"""

import logging
import threading
import time as _time
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

_QT_URL = "https://qt.gtimg.cn/q="
_QT_TIMEOUT = 10.0
# 单次批量请求的最大代码数
_FETCH_CHUNK_SIZE = 60
# 进程内缓存 TTL（秒）
_CACHE_TTL = 60.0
# 单次请求的股票代码数量上限（路由层校验）
_MAX_CODES = 200

# 腾讯行情返回的占位/无效标记（注意 "0.00" 对涨跌幅是合法值，不能放这里）
_PLACEHOLDERS = {"", "-", "---", "—", "--"}

# ---------------------------------------------------------------------------
# 进程内 TTL 缓存：{code: {"item": dict, "updated_at": epoch_seconds}}
# ---------------------------------------------------------------------------
_CACHE: Dict[str, Dict[str, Any]] = {}
_CACHE_LOCK = threading.Lock()


def qt_symbol(code: str) -> Optional[str]:
    """把股票代码解析为腾讯行情符号（sh/sz/hk 前缀）。

    5 位数字 → 港股 ``hk{code}``（powercloud 重仓口径的港股代码为 5 位，
    如 00700/01810；长度判断须先于 0/3/6 前缀判断，否则 "00700" 会被误判
    为深市）；6 开头 → 沪市 ``sh{code}``；0/3 开头 → 深市 ``sz{code}``；
    其余（北交所 4/8 开头、美股等）返回 None（不支持）。
    """
    if len(code) == 5 and code.isdigit():
        return f"hk{code}"
    if code.startswith("6"):
        return f"sh{code}"
    if code.startswith("0") or code.startswith("3"):
        return f"sz{code}"
    return None


def _to_float(value: Optional[str]) -> Optional[float]:
    """稳健转 float；占位符返回 None（"0.00" 是合法数值，不在此列）。"""
    if value is None:
        return None
    s = value.strip()
    if s in _PLACEHOLDERS:
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _format_quote_time(raw: Optional[str]) -> Tuple[str, str]:
    """把行情时间（YYYYMMDDHHmmss 紧凑数字）转为 (yyyy-mm-dd, HH:mm:ss)。"""
    if not raw:
        return ("", "")
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) < 14:
        return ("", "")
    return (
        f"{digits[0:4]}-{digits[4:6]}-{digits[6:8]}",
        f"{digits[8:10]}:{digits[10:12]}:{digits[12:14]}",
    )


def _request_text(symbols: List[str]) -> Optional[str]:
    """GET 腾讯行情并返回 GBK 解码文本；代理异常时降级直连重试一次。"""
    url = _QT_URL + ",".join(symbols)
    try:
        resp = requests.get(url, timeout=_QT_TIMEOUT)
        return resp.content.decode("gbk", errors="replace")
    except requests.exceptions.ProxyError as e:
        logger.warning(f"[StockRealtime] 代理连接失败，改为直连重试: {e}")
        direct = requests.Session()
        direct.trust_env = False
        resp = direct.get(url, timeout=_QT_TIMEOUT)
        return resp.content.decode("gbk", errors="replace")


def _parse_line(line: str) -> Optional[Dict[str, Any]]:
    """解析单行 ``v_sh600519="1~贵州茅台~600519~..."`` 为响应条目。

    A 股（``v_sh``/``v_sz``）与港股（``v_hk``）行格式一致：关键字段位置
    相同，仅港股时间为 ``2026/09/21 14:19:48`` 斜杠格式（时间解析兼容）。
    """
    # 跳过 v_pv_none_match 等无效行
    if not line.startswith(("v_sh", "v_sz", "v_hk")):
        return None
    parts = line.split("~")
    if len(parts) < 33:
        return None
    code = parts[2].strip()
    if not code:
        return None
    quote_date, quote_time = _format_quote_time(parts[30])
    price = _to_float(parts[3])
    return {
        "code": code,
        # 东财/腾讯名称内部带空格对齐（如 "五 粮 液"），统一去掉
        "name": parts[1].strip().replace(" ", ""),
        # 停牌等场景腾讯返回 0.00 价，视为无有效价格
        "price": price if price else None,
        "changePct": _to_float(parts[32]),
        "date": quote_date,
        "time": quote_time,
    }


def _fetch_chunks(chunks: List[List[str]]) -> Dict[str, Dict[str, Any]]:
    """批量拉取多组腾讯行情符号，返回 {code: item}（失败代码不在结果中）。"""
    items: Dict[str, Dict[str, Any]] = {}
    for chunk in chunks:
        try:
            text = _request_text(chunk)
        except Exception as e:
            logger.warning(f"[StockRealtime] 腾讯行情请求异常 symbols={chunk}: {e}")
            continue
        if not text:
            continue
        for line in text.split(";"):
            line = line.strip()
            if not line:
                continue
            item = _parse_line(line)
            if item is not None:
                items[item["code"]] = item
    return items


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


def get_stocks_realtime(codes: List[str]) -> Dict[str, Any]:
    """批量获取股票实时行情。

    :param codes: 6 位股票代码列表（调用方已去重；不支持的代码会进 missing）
    :return: ``{"stocks": [StockRealtimeItem...], "missing": [code...]}``；
             ``stocks`` 含成功获取的行情（停牌等无行情时 price/changePct 为
             None），``missing`` 为不支持的市场或拉取失败的代码。
    """
    supported: List[str] = []
    missing: List[str] = []
    seen = set()
    for code in codes:
        if code in seen:
            continue
        seen.add(code)
        if qt_symbol(code) is None:
            missing.append(code)
        else:
            supported.append(code)

    fresh, stale = _get_fresh_items(supported)
    if stale:
        chunks = [
            [qt_symbol(c) for c in stale[i : i + _FETCH_CHUNK_SIZE]]
            for i in range(0, len(stale), _FETCH_CHUNK_SIZE)
        ]
        fetched = _fetch_chunks(chunks)
        _store_items(fetched)
        fresh.update(fetched)

    fetched_codes = set(fresh.keys())
    still_missing = [c for c in supported if c not in fetched_codes]
    stocks = [fresh[c] for c in supported if c in fetched_codes]
    return {
        "stocks": stocks,
        "missing": missing + still_missing,
    }
