# src/python_cli_starter/fund_info.py

"""基金完整信息聚合模块。

为 Nuxt 端 `findOrCreateFund` 提供一次拿全数据的接口，聚合三个数据源：
    1. 基本信息（名称/类型/费率摘要）：天天基金 jbgk 页面
       https://fundf10.eastmoney.com/jbgk_{fund_code}.html
    2. 历史净值：akshare fund_open_fund_info_em（单位净值走势）
    3. 费率详情：复用本仓库 fund_fee.get_fund_fee 模块

返回结构严格对齐前端接口契约（详见接口文档 GET /fund/info/{fundCode}）。
"""

import logging
from datetime import date
from typing import Any, Dict, List, Optional

import akshare as ak
import pandas as pd
import requests
from bs4 import BeautifulSoup

from . import fund_fee

logger = logging.getLogger(__name__)

# 请求头：模拟真实浏览器
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://fundf10.eastmoney.com/",
}


def _fetch_basic_info(fund_code: str) -> Optional[Dict[str, str]]:
    """
    抓取并解析天天基金 jbgk 基本概况页面。

    表格结构：每行 2 对 (th-td th-td)，th 为字段名、td 为字段值。
    本函数仅提取后续需要的字段：基金简称、基金类型、管理费率、托管费率。

    :return: 解析失败返回 None；成功返回字典，缺失字段值为空串。
    """
    url = f"https://fundf10.eastmoney.com/jbgk_{fund_code}.html"
    logger.info(f"[FundInfo] 正在获取基金 {fund_code} 基本信息: {url}")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15.0)
    except Exception as e:
        logger.error(f"[FundInfo] 基金 {fund_code} 基本信息请求异常: {e}")
        return None

    if resp.status_code != 200:
        logger.warning(
            f"[FundInfo] 基金 {fund_code} 基本信息请求失败, HTTP {resp.status_code}"
        )
        return None

    # 页面级"无此基金"判定（与 fund_fee 一致：单元格级「暂无数据」不算）
    if "没有找到" in resp.text or "无此基金" in resp.text:
        logger.warning(f"[FundInfo] 基金 {fund_code} 页面提示无此基金")
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    info: Dict[str, str] = {}

    for tbl in soup.select("table"):
        if "基金简称" not in tbl.get_text() or "基金类型" not in tbl.get_text():
            continue
        # 按行解析 (th, td) 配对
        for row in tbl.select("tr"):
            cells = row.find_all(["th", "td"])
            # cells 形如 [th, td, th, td, ...]
            for i in range(0, len(cells) - 1, 2):
                key_el = cells[i]
                val_el = cells[i + 1]
                if key_el.name != "th":
                    continue
                key = key_el.get_text(strip=True)
                val = val_el.get_text(strip=True)
                if key and key not in info:
                    info[key] = val
        break  # 仅取第一张含「基金简称」的表

    name = info.get("基金简称", "").strip()
    # 天天基金对不存在的代码会返回占位页面，基金简称为空或 "---"，均视为无效
    if not name or name in ("---", "—"):
        logger.warning(f"[FundInfo] 基金 {fund_code} 未能解析出有效基金简称 (name={name!r})")
        return None

    return {
        "name": name,
        "fund_type": info.get("基金类型", "").strip(),
        "management_fee": info.get("管理费率", "").strip(),
        "custody_fee": info.get("托管费率", "").strip(),
    }


def _fetch_history_nav(fund_code: str) -> Optional[List[Dict[str, str]]]:
    """
    通过 akshare 获取基金全量历史净值（单位净值走势），按日期升序。

    :return: [{"date": "2026-07-08", "nav": "1.2345"}, ...]；失败返回 None。
    """
    logger.info(f"[FundInfo] 正在获取基金 {fund_code} 历史净值...")
    try:
        df = ak.fund_open_fund_info_em(symbol=fund_code, indicator="单位净值走势")
    except Exception as e:
        logger.error(f"[FundInfo] 基金 {fund_code} 历史净值获取异常: {e}")
        return None

    if df is None or df.empty:
        logger.warning(f"[FundInfo] 基金 {fund_code} 历史净值为空")
        return None

    history: List[Dict[str, str]] = []
    for _, row in df.iterrows():
        nav_date = row.get("净值日期")
        nav = row.get("单位净值")
        if pd.isna(nav_date) or pd.isna(nav):
            continue
        # 净值日期可能是 date 或 datetime
        date_str = nav_date.strftime("%Y-%m-%d") if hasattr(nav_date, "strftime") else str(nav_date)
        # 单位净值用 repr 保留精度，避免 float→str 丢精度
        nav_str = f"{float(nav):.4f}"
        history.append({"date": date_str, "nav": nav_str})

    if not history:
        return None

    # fund_open_fund_info_em 默认升序返回，这里显式按日期排序确保升序
    history.sort(key=lambda x: x["date"])
    logger.info(f"[FundInfo] 基金 {fund_code} 历史净值 {len(history)} 条")
    return history


def _format_fee_to_percent(text: str) -> str:
    """
    将 jbgk 页面的费率文本统一为契约格式。
    例如 "0.60%（每年）" -> "0.60%/年"；"---" -> ""。
    """
    if not text or text in ("---", "—"):
        return ""
    # （每年） / (每年) -> /年
    result = text.replace("（每年）", "/年").replace("(每年)", "/年")
    return result.strip()


def _determine_fund_type(name: str, fund_type: str) -> str:
    """
    判断基金类型归属。

    规则：基金类型或简称含 QDII，或简称含 LOF -> "qdii_lof"；否则 "open"。
    """
    blob = f"{name} {fund_type}"
    if "QDII" in blob or "qdii" in blob or "LOF" in blob:
        return "qdii_lof"
    return "open"


def _build_fees(
    fee_data: Optional[Dict[str, Any]],
    management_fee: str,
    custody_fee: str,
) -> Dict[str, Any]:
    """
    从 fund_fee 模块输出 + 基本信息费率摘要，构建契约中的 fees 对象。

    - purchaseFee: 取申购费率首档的「天天基金优惠费率」
    - redemptionFees: 赎回费率阶梯 [{holdingPeriod, rate}]
    - managementFee / custodyFee: 优先用基本信息摘要（已含"每年"语义）
    - rawText: 全部费率信息序列化为可读文本，兜底展示
    """
    purchase_fee: Optional[str] = None
    redemption_fees: List[Dict[str, str]] = []

    if fee_data:
        # 申购费率：取首档费率
        # 优先级：天天基金优惠费率 > 原费率 > 费率（单档无优惠场景）
        purchase_list = fee_data.get("purchase_fee_rate") or []
        if purchase_list:
            first = purchase_list[0]
            purchase_fee = (
                first.get("天天基金优惠费率")
                or first.get("原费率")
                or first.get("费率")
            )
            if purchase_fee is not None:
                purchase_fee = str(purchase_fee).strip()
                if not purchase_fee:
                    purchase_fee = None

        # 赎回费率：映射为阶梯
        redemption_list = fee_data.get("redemption_fee_rate") or []
        for item in redemption_list:
            holding = item.get("适用区间", "")
            rate = item.get("费率", item.get("原费率", ""))
            redemption_fees.append(
                {"holdingPeriod": str(holding), "rate": str(rate)}
            )

    # 管理费/托管费：优先用基本信息摘要（"0.60%（每年）"），否则回退 fund_fee 的 operation_fees
    mgmt = _format_fee_to_percent(management_fee)
    cust = _format_fee_to_percent(custody_fee)
    if not mgmt and fee_data:
        mgmt = _format_fee_to_percent(fee_data.get("operation_fees", {}).get("管理费率", ""))
    if not cust and fee_data:
        cust = _format_fee_to_percent(fee_data.get("operation_fees", {}).get("托管费率", ""))

    raw_text = _build_raw_text(fee_data, management_fee, custody_fee)

    return {
        "purchaseFee": purchase_fee,
        "redemptionFees": redemption_fees,
        "managementFee": mgmt or None,
        "custodyFee": cust or None,
        "rawText": raw_text,
    }


def _build_raw_text(
    fee_data: Optional[Dict[str, Any]],
    management_fee: str,
    custody_fee: str,
) -> str:
    """将全部费率信息序列化为可读文本，作为兜底展示。"""
    lines: List[str] = []

    # 运作费用
    op_lines: List[str] = []
    if management_fee:
        op_lines.append(f"管理费率 {_format_fee_to_percent(management_fee)}")
    if custody_fee:
        op_lines.append(f"托管费率 {_format_fee_to_percent(custody_fee)}")
    if fee_data:
        op = fee_data.get("operation_fees", {})
        if op:
            # 补全基本信息里没有的字段（如销售服务费率）
            for k, v in op.items():
                label = k
                if k == "管理费率" and management_fee:
                    continue  # 已由基本信息提供
                if k == "托管费率" and custody_fee:
                    continue
                op_lines.append(f"{label} {v}")
    if op_lines:
        lines.append("运作费用: " + "; ".join(op_lines))

    if not fee_data:
        return "; ".join(lines)

    # 认购费率
    sub = fee_data.get("subscription_fee_rate") or []
    if sub:
        parts = [f"{i.get('适用区间', '')} {i.get('费率', i.get('原费率', ''))}".strip() for i in sub]
        lines.append("认购费率: " + "; ".join(parts))

    # 申购费率
    pur = fee_data.get("purchase_fee_rate") or []
    if pur:
        parts = []
        for i in pur:
            seg = f"{i.get('适用区间', '')} 原费率{i.get('原费率', '')}"
            disc = i.get("天天基金优惠费率")
            if disc:
                seg += f" 优惠{disc}"
            parts.append(seg.strip())
        lines.append("申购费率: " + "; ".join(parts))

    # 赎回费率
    red = fee_data.get("redemption_fee_rate") or []
    if red:
        parts = [f"{i.get('适用区间', '')} {i.get('费率', i.get('原费率', ''))}".strip() for i in red]
        lines.append("赎回费率: " + "; ".join(parts))

    return "; ".join(lines)


def get_fund_info(fund_code: str) -> Optional[Dict[str, Any]]:
    """
    获取单只基金的完整信息（基本信息 + 历史净值 + 费率表）。

    :param fund_code: 基金代码（调用方需保证为合法 6 位代码）
    :return: 契约结构字典；基本信息获取失败返回 None（由上层转 404）。
             历史/费率失败时降级返回空列表，不中断整体响应。
    """
    fund_code = str(fund_code)

    # 1. 基本信息（核心，失败即整体失败）
    basic = _fetch_basic_info(fund_code)
    if not basic:
        return None

    # 2. 历史净值（失败降级为空列表）
    history = _fetch_history_nav(fund_code)
    if history:
        yesterday_nav = history[-1]["nav"]
        nav_date = history[-1]["date"]
    else:
        yesterday_nav = ""
        nav_date = ""

    # 3. 费率详情（失败降级：仅用基本信息中的费率摘要）
    try:
        fee_data = fund_fee.get_fund_fee(fund_code)
    except Exception as e:
        logger.error(f"[FundInfo] 基金 {fund_code} 费率详情获取异常，降级处理: {e}")
        fee_data = None

    fees = _build_fees(fee_data, basic["management_fee"], basic["custody_fee"])
    fund_type = _determine_fund_type(basic["name"], basic["fund_type"])

    return {
        "code": fund_code,
        "name": basic["name"],
        "fundType": fund_type,
        "yesterdayNav": yesterday_nav,
        "navDate": nav_date,
        "history": history if history else [],
        "fees": fees,
    }
