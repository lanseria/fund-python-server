# src/python_cli_starter/market.py

import httpx
import json
import logging
import re
import asyncio
import math
from typing import List, Dict, Any, Optional, Tuple
from .schemas import SectorInfo

logger = logging.getLogger(__name__)

# 常量定义
PAGE_SIZE = 100  # 接口限制最大每页数量
BASE_URL = "https://push2.eastmoney.com/api/qt/clist/get"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Referer": "https://quote.eastmoney.com/"
}

async def _fetch_page_raw(client: httpx.AsyncClient, page: int) -> Tuple[List[Dict], int]:
    """
    获取单页原始数据
    :return: (当前页的数据列表, 数据总数)
    """
    params = {
        "np": "1",
        "fltt": "1",
        "invt": "2",
        "cb": "jQuery_callback",
        "fs": "m:90+t:2+f:!50",
        "fields": "f14,f20,f8,f3",  # f14:名称, f20:市值, f8:换手率, f3:涨跌幅
        "fid": "f3",
        "pn": str(page),
        "pz": str(PAGE_SIZE),
        "po": "1",
        "dect": "1",
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        "wbp2u": "|0|0|0|web",
        "_": "1772456919762"
    }

    try:
        response = await client.get(BASE_URL, params=params, headers=HEADERS, timeout=10.0)
        response.raise_for_status()
        text = response.text

        # JSONP 解析
        match = re.search(r'jQuery_callback\((.*)\)', text, re.DOTALL)
        if not match:
            # 尝试直接解析 JSON
            try:
                data = response.json()
            except json.JSONDecodeError:
                logger.error(f"[Page {page}] 无法解析响应数据")
                return [], 0
        else:
            json_str = match.group(1)
            try:
                data = json.loads(json_str)
            except json.JSONDecodeError:
                logger.error(f"[Page {page}] JSONP 解析失败")
                return [], 0

        if "data" in data and data["data"]:
            total = data["data"].get("total", 0)
            diff_list = data["data"].get("diff", [])
            return diff_list, total
        else:
            return [], 0

    except Exception as e:
        logger.error(f"[Page {page}] 请求失败: {e}")
        return [], 0


def _process_item(item: Dict) -> SectorInfo:
    """
    将原始字典转换为 SectorInfo 对象
    """
    # 1. 提取原始数据
    name = str(item.get("f14", "未知板块"))
    
    # 辅助函数：安全转换数字
    def clean_float(val):
        if val is None or val == "-":
            return 0.0
        if not isinstance(val, (int, float)):
            try:
                return float(val)
            except (ValueError, TypeError):
                return 0.0
        return float(val)

    raw_cap = item.get("f20", 0)
    raw_turnover = item.get("f8", 0)
    raw_change = item.get("f3", 0)

    market_cap_val = clean_float(raw_cap)
    turnover_val = clean_float(raw_turnover)
    change_val = clean_float(raw_change)

    # 2. 格式化逻辑
    # 市值：接口单位是元，转换为亿
    market_cap_desc = f"{market_cap_val / 100000000:.2f} 亿"

    # 换手率：接口返回 100 表示 1%，所以 739 -> 7.39%
    turnover_desc = f"{turnover_val / 100:.2f}%"

    # 涨跌幅：接口直接返回百分比数值，1.23 -> 1.23%
    change_desc = f"{change_val / 100:.2f}%"

    return SectorInfo(
        name=name,
        market_cap=market_cap_val,
        market_cap_desc=market_cap_desc,
        turnover_rate=turnover_val,
        turnover_rate_desc=turnover_desc,
        change_percent=change_val,
        change_percent_desc=change_desc
    )


async def fetch_eastmoney_sectors() -> Optional[List[SectorInfo]]:
    """
    分页获取所有东方财富板块数据并合并
    """
    async with httpx.AsyncClient() as client:
        # 1. 获取第一页数据和总数
        logger.info("正在获取板块数据第一页...")
        first_page_items, total_count = await _fetch_page_raw(client, 1)
        
        if not first_page_items and total_count == 0:
            logger.warning("未能获取到板块数据")
            return []

        all_raw_items = list(first_page_items)
        
        # 2. 计算剩余页数
        total_pages = math.ceil(total_count / PAGE_SIZE)
        logger.info(f"获取成功，共 {total_count} 条数据，需请求 {total_pages} 页")

        if total_pages > 1:
            # 3. 并发获取剩余页面
            tasks = []
            for page in range(2, total_pages + 1):
                tasks.append(_fetch_page_raw(client, page))
            
            # 等待所有请求完成
            results = await asyncio.gather(*tasks)
            
            # 合并结果
            for items, _ in results:
                all_raw_items.extend(items)

        # 4. 统一处理数据格式
        logger.info(f"所有页面获取完成，开始处理 {len(all_raw_items)} 条记录")
        processed_sectors = []
        for item in all_raw_items:
            try:
                processed_sectors.append(_process_item(item))
            except Exception as e:
                logger.error(f"处理单条数据出错: {e}, item: {item}")
                continue
                
        return processed_sectors