# src/python_cli_starter/sector_capital.py

"""板块主力资金数据模块。

提供两个核心能力：
    1. ``get_sector_capital_flow`` — 拉取全量板块（行业/概念）的主力资金表
    2. ``find_sector_action``      — 按板块名查询主力行为（精确优先，模糊兜底）

数据源：东方财富数据中心板块资金流向接口
    ``https://data.eastmoney.com/dataapi/bkzj/getbkzj``

    历史上用过的 ``push2.eastmoney.com/api/qt/clist/get``（实时推送接口）在部分网络
    环境下不可达（连接被重置），改用同源的 ``data.eastmoney.com/dataapi`` 接口，
    两者字段命名完全一致（同为东财 f 系列），但后者一次返回全量、无需分页，且走
    ``data.eastmoney.com`` 域名（更稳定可达）。

字段映射（原始单位均为「元」）：
    - ``f14`` 板块名 / ``f3`` 涨幅（需 /100，如 217 → 2.17%）/ ``f6`` 成交额
    - ``f62`` 主力资金（主力净流入额）
    - ``f66`` 超大单净流入 / ``f72`` 大单净流入 / ``f78`` 中单净流入
    - ``f84`` 散户资金（小单净流入）
    - ``f184`` 主力净比（东财自己算的「主力净流入/成交额」，仅参考，不作主依据）

计算口径（业务自定）：
    - 主力暗盘 = 主力资金 - 散户资金
    - 主力强度 = 主力暗盘 / 成交额 * 100（成交额为 0 记 0）
    - 主力行为（按主力强度判定）：
        ``>=3`` 抢筹 / ``[1,3)`` 建仓 / ``(-1,1)`` 洗盘 / ``<=-1`` 出货

缓存策略（内存缓存，按 fs_type 分别缓存）：
    - 刷新时段：交易日 9:30-16:00，每 10 分钟刷新一次（含 15:00-16:00 收盘统计期）
    - 冻结：16:00 后不再刷新，缓存冻结有效到次日 9:30 开盘后被新数据覆盖；
      周末/节假日全天用最近一份缓存
    - 冷启动：``warmup_cache()`` 服务启动时立即预热（无论是否交易时段）
    - 刷新失败：交易时段某个窗口拉数据源失败时保留旧缓存（即便已过期），接口始终有数据
    - 两层入口（``get_sector_capital_flow`` / ``find_sector_action``）均读缓存，不直连数据源
"""

import asyncio
import logging
from datetime import datetime, time
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

# 东财数据中心板块资金流向接口（一次返回全量，无需分页）
_BASE_URL = "https://data.eastmoney.com/dataapi/bkzj/getbkzj"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://data.eastmoney.com/bkzj/hy.html",
}
# 请求字段：板块代码+名称+最新价+涨幅+成交额+各类资金净流入（逗号分隔传入 key 参数）
_FIELDS = "f12,f14,f2,f3,f6,f62,f66,f72,f78,f84,f184"
_TIMEOUT = 10.0

# 板块类型 → 东财 code 参数（dataapi 用 code，对应 push2 的 fs）
_FS_TYPE_MAP = {
    "industry": "m:90+t:2",  # 行业板块（约 496 条）
    "concept": "m:90+t:3",  # 概念板块（约 504 条）
}

# 元 → 亿元 的换算
_YI = 1e8

# 刷新间隔（秒）：交易时段内每 10 分钟刷新一次
_REFRESH_INTERVAL = 10 * 60
# 刷新时段：交易日 9:30-16:00（含 15:00-16:00 收盘统计期）
_REFRESH_START = time(9, 30)
_REFRESH_END = time(16, 0)

# A 股节假日（2026 年），用于判定是否为交易日
_HOLIDAYS_CONFIG = [
    (datetime(2026, 1, 1).date(), datetime(2026, 1, 3).date()),    # 元旦
    (datetime(2026, 2, 15).date(), datetime(2026, 2, 23).date()),  # 春节
    (datetime(2026, 4, 4).date(), datetime(2026, 4, 6).date()),    # 清明节
    (datetime(2026, 5, 1).date(), datetime(2026, 5, 5).date()),    # 劳动节
    (datetime(2026, 6, 19).date(), datetime(2026, 6, 21).date()),  # 端午节
    (datetime(2026, 9, 25).date(), datetime(2026, 9, 27).date()),  # 中秋节
    (datetime(2026, 10, 1).date(), datetime(2026, 10, 7).date()),  # 国庆节
]


def _is_trading_day(dt: datetime) -> bool:
    """检查指定日期是否为 A 股交易日（排除周末与节假日）。"""
    if dt.weekday() >= 5:  # 5=周六, 6=周日
        return False
    d = dt.date()
    for start, end in _HOLIDAYS_CONFIG:
        if start <= d <= end:
            return False
    return True


def _in_refresh_window(dt: datetime) -> bool:
    """是否处于刷新窗口：交易日且 9:30 ≤ 当前时间 ≤ 16:00。"""
    if not _is_trading_day(dt):
        return False
    return _REFRESH_START <= dt.time() <= _REFRESH_END


# ---------------------------------------------------------------------------
# 内存缓存
# ---------------------------------------------------------------------------
# 结构：{fs_type: {"data": [...], "updated_at": datetime}}
_CACHE: Dict[str, Dict[str, Any]] = {}
# 单把锁串行化刷新（防并发请求重复拉数据源）；锁在事件循环启动后惰性创建
_CACHE_LOCK: Optional[asyncio.Lock] = None


def _get_lock() -> asyncio.Lock:
    """惰性创建 asyncio.Lock（须在事件循环内调用，避免无循环时报错）。"""
    global _CACHE_LOCK
    if _CACHE_LOCK is None:
        _CACHE_LOCK = asyncio.Lock()
    return _CACHE_LOCK


def _cache_get(fs_type: str) -> Optional[Dict[str, Any]]:
    """读取某 fs_type 的缓存项（可能为空）。"""
    return _CACHE.get(fs_type)


def _should_refresh(updated_at: Optional[datetime], now: datetime) -> bool:
    """判断是否需要刷新缓存。

    规则：
    1. 缓存为空（updated_at is None）→ True（预热 / 冷启动）
    2. 不在刷新窗口（非交易日 / 非 9:30-16:00）→ False（冻结，用旧缓存）
    3. 在刷新窗口且 now - updated_at ≥ 10 分钟 → True
    4. 在刷新窗口但未满 10 分钟 → False
    """
    if updated_at is None:
        return True
    if not _in_refresh_window(now):
        return False
    return (now - updated_at).total_seconds() >= _REFRESH_INTERVAL


def _normalize_fs_type(fs_type: Any) -> str:
    """把外部传入的板块类型规整为 ``industry`` / ``concept``。

    兼容大小写、中文别名（行业/概念）。空值默认行业。非法值抛 ``ValueError``。
    """
    if fs_type is None:
        return "industry"
    s = str(fs_type).strip().lower()
    # 空串默认行业
    if not s:
        return "industry"
    alias = {
        "industry": "industry",
        "concept": "concept",
        "行业": "industry",
        "行业板块": "industry",
        "概念": "concept",
        "概念板块": "concept",
        "2": "industry",
        "3": "concept",
    }
    if s not in alias:
        raise ValueError(
            f"无效的板块类型：'{fs_type}'，支持 industry(行业) / concept(概念)。"
        )
    return alias[s]


async def _fetch_sector_capital(fs_type: str) -> Optional[List[Dict[str, Any]]]:
    """一次性拉取全量板块原始字典列表（行业/概念）。

    dataapi 接口一次返回全部条目，无需分页。返回结果按东财默认顺序（主力净流入降序）。

    :param fs_type: ``industry`` / ``concept``
    :return: 原始 item 列表；数据源不可用返回 None。
    """
    code = _FS_TYPE_MAP.get(fs_type)
    if code is None:
        return None

    params = {
        "key": _FIELDS,  # 指定返回字段（逗号分隔）
        "code": code,    # 板块分类：行业 m:90+t:2 / 概念 m:90+t:3
    }
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                _BASE_URL, params=params, headers=_HEADERS, timeout=_TIMEOUT
            )
    except Exception as e:
        logger.error(f"[SectorCapital] dataapi 请求异常 fs_type={fs_type}: {e}")
        return None

    if resp.status_code != 200:
        logger.warning(
            f"[SectorCapital] dataapi 响应非 200 fs_type={fs_type}: {resp.status_code}"
        )
        return None

    try:
        payload = resp.json()
    except ValueError as e:
        logger.warning(f"[SectorCapital] dataapi 响应非 JSON fs_type={fs_type}: {e}")
        return None

    data = payload.get("data") or {}
    diff = data.get("diff") or []
    if not diff:
        logger.warning(f"[SectorCapital] dataapi 未返回数据 fs_type={fs_type}")
        return None

    logger.info(
        f"[SectorCapital] fs_type={fs_type} 拉取完成，共 {len(diff)} 条"
    )
    return diff


def _to_float(val: Any) -> float:
    """稳健转 float，None/占位符/异常记 0.0。"""
    if val is None or val == "-":
        return 0.0
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def _fmt_yi(yuan: float) -> str:
    """元 → 亿元字符串（保留 2 位小数，带正负号）。"""
    return f"{yuan / _YI:.2f} 亿"


def _classify_behavior(strength: float) -> str:
    """按主力强度判定主力行为。

    边界（极端档优先）：``>=3`` 抢筹、``<=-1`` 出货、``[1,3)`` 建仓、``(-1,1)`` 洗盘。
    即 ``3`` 归抢筹、``-1`` 归出货。
    """
    if strength >= 3:
        return "抢筹"
    if strength <= -1:
        return "出货"
    if strength >= 1:
        return "建仓"
    return "洗盘"


def _build_item(raw: Dict[str, Any]) -> Dict[str, Any]:
    """把单条原始字典映射为对外契约字段（金额转「亿元」字符串）。

    注意：dataapi 接口的 ``f3``（涨幅）为已乘 100 的原始值（如 217 表示 2.17%），
    故此处统一除以 100；金额类字段（f6/f62/f84 等）单位为元，转「亿元」展示。
    """
    main_capital = _to_float(raw.get("f62"))  # 主力资金（主力净流入）
    retail_capital = _to_float(raw.get("f84"))  # 散户资金（小单净流入）
    amount = _to_float(raw.get("f6"))  # 成交额

    main_hidden = main_capital - retail_capital  # 主力暗盘
    main_strength = (main_hidden / amount * 100) if amount else 0.0  # 主力强度（%）
    main_strength = round(main_strength, 2)
    action = _classify_behavior(main_strength)

    # dataapi f3 为乘 100 后的原始值（217 → 2.17%），统一除 100
    change_percent = round(_to_float(raw.get("f3")) / 100.0, 2)

    return {
        "name": str(raw.get("f14", "")).strip(),
        "code": str(raw.get("f12", "")).strip(),  # 板块代码 BKxxxx，附带返回
        "changePercent": change_percent,
        "amount": _fmt_yi(amount),
        "mainCapital": _fmt_yi(main_capital),
        "retailCapital": _fmt_yi(retail_capital),
        "mainHidden": _fmt_yi(main_hidden),
        "mainStrength": main_strength,
        "mainAction": action,
    }


async def _refresh_cache(fs_type: str, now: datetime) -> bool:
    """拉取数据源并刷新指定 fs_type 的缓存。

    :return: 刷新成功 True；数据源不可用 False（保留旧缓存不动）。
    """
    raw_items = await _fetch_sector_capital(fs_type)
    if raw_items is None:
        logger.warning(f"[SectorCapital] 刷新失败，保留旧缓存 fs_type={fs_type}")
        return False
    data = [_build_item(it) for it in raw_items if str(it.get("f14", "")).strip()]
    _CACHE[fs_type] = {"data": data, "updated_at": now}
    logger.info(
        f"[SectorCapital] 缓存已刷新 fs_type={fs_type} 条数={len(data)} "
        f"时间={now.strftime('%Y-%m-%d %H:%M:%S')}"
    )
    return True


async def _get_or_refresh(fs_type: str) -> Optional[List[Dict[str, Any]]]:
    """读缓存入口：按需刷新后返回缓存数据。

    - 取锁串行化，防并发请求重复拉数据源
    - ``_should_refresh`` 判定需要刷新则拉数据源（失败保留旧缓存）
    - 缓存为空且拉取失败 → 返回 None（调用方据此返回 502）
    """
    fs_type = _normalize_fs_type(fs_type)
    async with _get_lock():
        now = datetime.now()
        entry = _cache_get(fs_type)
        if _should_refresh(entry.get("updated_at") if entry else None, now):
            await _refresh_cache(fs_type, now)
            entry = _cache_get(fs_type)
        return entry["data"] if entry else None


async def warmup_cache() -> None:
    """预热缓存：服务启动时对 industry / concept 各拉一次（无论是否交易时段）。

    失败不抛异常（仅记日志），接口会在首个请求时再尝试。
    """
    for fs_type in _FS_TYPE_MAP:
        async with _get_lock():
            ok = await _refresh_cache(fs_type, datetime.now())
            if not ok:
                logger.warning(f"[SectorCapital] 预热失败 fs_type={fs_type}（首请求时重试）")


async def get_sector_capital_flow(fs_type: str = "industry") -> Optional[List[Dict[str, Any]]]:
    """拉取全量板块的主力资金表（行业/概念）—— 读缓存。

    首次请求或缓存过期（交易时段满 10 分钟）时触发刷新；非交易时段/16:00 后
    返回冻结的最近一份缓存。详见模块文档「缓存策略」。

    :param fs_type: ``industry`` / ``concept``，默认行业。
    :return: 板块资金项列表；缓存为空且数据源不可用返回 None。
    """
    return await _get_or_refresh(fs_type)


class SectorCapitalUnavailable(Exception):
    """数据源不可用异常（用于与「无匹配」区分，前者映射 502）。"""


async def find_sector_action(
    name: str, fs_type: str = "industry"
) -> Optional[List[Dict[str, Any]]]:
    """按板块名查询主力行为。

    匹配规则：精确匹配优先；找不到则做子串模糊匹配（包含关系，双向）。
    可能返回多条命中（模糊匹配时）。

    :param name: 板块名（不能为空）
    :param fs_type: ``industry`` / ``concept``
    :return: 命中的板块资金项列表；无匹配返回 None。
    :raises SectorCapitalUnavailable: 数据源不可用（调用方据此返回 502）。
    """
    name = (name or "").strip()
    if not name:
        return None

    all_sectors = await get_sector_capital_flow(fs_type)
    if all_sectors is None:
        raise SectorCapitalUnavailable("东方财富数据源暂时不可用")

    # 1. 精确匹配
    exact = [s for s in all_sectors if s["name"] == name]
    if exact:
        logger.info(f"[SectorCapital] 板块 '{name}' 精确命中 {len(exact)} 条")
        return exact

    # 2. 模糊兜底：名称包含查询串，或查询串包含名称
    fuzzy = [
        s for s in all_sectors if name in s["name"] or s["name"] in name
    ]
    logger.info(
        f"[SectorCapital] 板块 '{name}' 模糊命中 {len(fuzzy)} 条"
    )
    return fuzzy or None
