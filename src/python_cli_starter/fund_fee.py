# src/python_cli_starter/fund_fee.py

"""基金手续费信息获取模块。

数据来源：天天基金网基金档案-购买信息
    https://fundf10.eastmoney.com/jjfl_{fund_code}.html

不依赖 akshare 的 fund_fee_em，因其对多级表头（含 "|" 分隔符）解析存在 bug，
故直接请求页面 HTML 并按区块标题稳健解析，兼容不同基金类型
（混合/股票/债券/货币/ETF 等）表格数量差异较大的情况。
"""

import logging
from typing import Any, Dict, List, Optional

import pandas as pd
import requests
from bs4 import BeautifulSoup
from io import StringIO

logger = logging.getLogger(__name__)

# 请求头：模拟真实浏览器，避免被基础反爬拦截
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://fundf10.eastmoney.com/",
}

# 区块标题 -> 统一 key 的映射（取标题前缀即可，例如「申购费率（前端）」也归到申购费率区块）
SECTION_PURCHASE_FRONT = "申购费率（前端）"
SECTION_PURCHASE_BACK = "申购费率（后端）"


def _fetch_html(fund_code: str) -> Optional[str]:
    """请求基金费率页面 HTML。失败返回 None。"""
    url = f"https://fundf10.eastmoney.com/jjfl_{fund_code}.html"
    logger.info(f"[FundFee] 正在获取基金 {fund_code} 的手续费页面: {url}")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15.0)
        if resp.status_code != 200:
            logger.warning(
                f"[FundFee] 基金 {fund_code} 页面请求失败, HTTP {resp.status_code}"
            )
            return None
        # 页面级"无此基金"判定：依据标题中是否缺少基金名称
        # 注意：单只基金某些费率区块为空时页面里会出现「暂无数据!」单元格，
        # 这是区块级缺失（如 ETF 无申购与赎回金额），不应误判为整页无效。
        if "没有找到" in resp.text or "无此基金" in resp.text:
            logger.warning(f"[FundFee] 基金 {fund_code} 页面提示无此基金")
            return None
        return resp.text
    except Exception as e:
        logger.error(f"[FundFee] 获取基金 {fund_code} 页面异常: {e}")
        return None


def _kv_pairs_from_table(df: pd.DataFrame) -> Dict[str, str]:
    """
    解析「键值对」型表格：奇数列为 key、偶数列为 value。
    原始结构形如：
        [申购状态, 开放申购, 赎回状态, 开放赎回, 定投状态, 支持]
    转换为：{"申购状态": "开放申购", "赎回状态": "开放赎回", ...}
    """
    result: Dict[str, str] = {}
    if df is None or df.empty:
        return result

    values = df.iloc[0].tolist()
    # 按 (key, value) 两两分组
    for i in range(0, len(values) - 1, 2):
        key = values[i]
        val = values[i + 1]
        if pd.isna(key):
            continue
        key_str = str(key).strip()
        val_str = "" if pd.isna(val) else str(val).strip()
        if key_str:
            result[key_str] = val_str

    # 多行的情况（如「申购与赎回金额」会拆成多个表，逐行合并处理）
    for row_idx in range(1, len(df)):
        values = df.iloc[row_idx].tolist()
        for i in range(0, len(values) - 1, 2):
            key = values[i]
            val = values[i + 1]
            if pd.isna(key):
                continue
            key_str = str(key).strip()
            val_str = "" if pd.isna(val) else str(val).strip()
            if key_str and key_str not in result:
                result[key_str] = val_str

    return result


def _split_original_and_discount(rate_text: str) -> Dict[str, str]:
    """
    解析「原费率 | 优惠费率」格式，修复 akshare 无法处理多级表头的 bug。
    例如 "1.50% | 0.15%" -> {"原费率": "1.50%", "天天基金优惠费率": "0.15%"}
    """
    if not rate_text:
        return {"原费率": "", "天天基金优惠费率": ""}

    parts = [p.strip() for p in str(rate_text).split("|")]
    if len(parts) >= 2:
        return {
            "原费率": parts[0],
            "天天基金优惠费率": parts[1],
        }
    # 没有优惠费率，仅有原费率（例如 "每笔1000元"）
    return {
        "原费率": parts[0] if parts else "",
        "天天基金优惠费率": parts[0] if parts else "",
    }


def _parse_fee_rate_table(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """解析「适用金额/期限 -> 费率」分档表格。"""
    result: List[Dict[str, Any]] = []
    if df is None or df.empty:
        return result

    columns = list(df.columns)
    # 第一列固定为「适用金额」或「适用期限」
    range_col = columns[0]

    for _, row in df.iterrows():
        applicable = row[range_col]
        if pd.isna(applicable):
            continue

        item: Dict[str, Any] = {"适用区间": str(applicable).strip()}

        rate_col = columns[1] if len(columns) > 1 else None
        if rate_col is None:
            continue

        rate_text = row[rate_col]
        rate_str = "" if pd.isna(rate_text) else str(rate_text)

        # 处理「原费率 | 优惠费率」组合列
        if "|" in rate_str or "原费率" in str(rate_col):
            item.update(_split_original_and_discount(rate_str))
        else:
            # 普通单费率列（赎回费率/认购费率某些场景）
            item["费率"] = rate_str.strip()

        result.append(item)

    return result


def _find_section_tables(soup: BeautifulSoup) -> Dict[str, List[pd.DataFrame]]:
    """
    按区块标题稳健识别费率表格，避免不同基金类型表格索引漂移。
    返回 {区块标题: [表格DataFrame列表]}。
    """
    sections: Dict[str, List[pd.DataFrame]] = {}

    # 页面里每个费率区块通常由一个含 <h4> 的容器包裹若干 <table>
    for container in soup.select(".boxitem, .jjfl, .cont"):
        title_tag = container.find(["h4", "h3"])
        if not title_tag:
            continue
        title = title_tag.get_text(strip=True)
        # 去掉标题里附带的说明文字（取首个非空段落即可）
        title = title.split("◆")[0].split("[")[0].strip()

        tables = container.find_all("table")
        if not tables:
            continue

        dfs: List[pd.DataFrame] = []
        for tbl in tables:
            try:
                df_list = pd.read_html(StringIO(str(tbl)))
                for df in df_list:
                    if df is not None and not df.empty:
                        dfs.append(df)
            except ValueError:
                # read_html 在无表格结构时会抛 ValueError，忽略
                continue
            except Exception as e:
                logger.debug(f"[FundFee] 解析表格异常: {e}")
                continue

        if dfs and title:
            sections[title] = sections.get(title, []) + dfs

    return sections


def _merge_kv_sections(dfs: List[pd.DataFrame]) -> Dict[str, str]:
    """合并多个键值对型表格为一个字典。"""
    merged: Dict[str, str] = {}
    for df in dfs:
        merged.update(_kv_pairs_from_table(df))
    return merged


def get_fund_fee(fund_code: str) -> Optional[Dict[str, Any]]:
    """
    获取指定基金的全部手续费信息。

    :param fund_code: 基金代码（如 "000001"）
    :return: 包含 7 类费率信息的字典；若获取失败返回 None。结构：
        {
            "trade_status": {...},                # 交易状态
            "purchase_redemption_amount": {...},  # 申购与赎回金额
            "trade_confirm_days": {...},          # 交易确认日
            "operation_fees": {...},              # 运作费用（管理/托管费等）
            "subscription_fee_rate": [...],       # 认购费率（分档）
            "purchase_fee_rate": [...],           # 申购费率（前端，分档）
            "redemption_fee_rate": [...]          # 赎回费率（分档）
        }
    """
    html = _fetch_html(fund_code)
    if not html:
        return None

    try:
        soup = BeautifulSoup(html, "html.parser")
        sections = _find_section_tables(soup)
    except Exception as e:
        logger.error(f"[FundFee] 基金 {fund_code} 页面解析异常: {e}")
        return None

    if not sections:
        logger.warning(f"[FundFee] 基金 {fund_code} 未解析到任何费率区块")
        return None

    # 区块标题匹配辅助函数（兼容标题带括号、说明文字等情况）
    def match_section(keyword: str) -> List[pd.DataFrame]:
        for title, dfs in sections.items():
            if title.startswith(keyword):
                return dfs
        return []

    result: Dict[str, Any] = {
        "trade_status": {},
        "purchase_redemption_amount": {},
        "trade_confirm_days": {},
        "operation_fees": {},
        "subscription_fee_rate": [],
        "purchase_fee_rate": [],
        "redemption_fee_rate": [],
    }

    # 1. 交易状态
    result["trade_status"] = _merge_kv_sections(match_section("交易状态"))

    # 2. 申购与赎回金额（由两个表格拼接）
    result["purchase_redemption_amount"] = _merge_kv_sections(
        match_section("申购与赎回金额")
    )

    # 3. 交易确认日
    result["trade_confirm_days"] = _merge_kv_sections(match_section("交易确认日"))

    # 4. 运作费用
    result["operation_fees"] = _merge_kv_sections(match_section("运作费用"))

    # 5. 认购费率（分档）
    sub_dfs = match_section("认购费率")
    if sub_dfs:
        result["subscription_fee_rate"] = _parse_fee_rate_table(sub_dfs[0])

    # 6. 申购费率（前端，分档）
    pur_dfs = match_section("申购费率")
    if pur_dfs:
        result["purchase_fee_rate"] = _parse_fee_rate_table(pur_dfs[0])

    # 7. 赎回费率（分档）
    red_dfs = match_section("赎回费率")
    if red_dfs:
        result["redemption_fee_rate"] = _parse_fee_rate_table(red_dfs[0])

    logger.info(
        f"[FundFee] 基金 {fund_code} 手续费解析完成: "
        f"交易状态={len(result['trade_status'])}项, "
        f"申购费率档数={len(result['purchase_fee_rate'])}, "
        f"赎回费率档数={len(result['redemption_fee_rate'])}"
    )

    return result
