# src/python_cli_starter/market.py

import httpx
import json
import logging
import re
import asyncio
import math
from bs4 import BeautifulSoup
from typing import List, Dict, Any, Optional, Tuple
from .schemas import SectorInfo, ThsSectorInfo

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
    
# --- 同花顺数据处理逻辑 ---

async def _fetch_ths_page(client: httpx.AsyncClient, page: int) -> List[Dict[str, Any]]:
    """
    获取并解析同花顺单页 HTML 数据。
    注意：返回的是包含原始成交额的字典列表，用于后续计算占比。
    """
    url = f"https://q.10jqka.com.cn/thshy/index/field/199112/order/desc/page/{page}/ajax/1/"
    
    headers = {
        'accept': 'text/html, */*; q=0.01',
        'accept-language': 'zh-CN,zh;q=0.9',
        'dnt': '1',
        'hexin-v': 'Azbr76JdQyRbizdAeVXNj61Uh2c9V3TrzJKuwKAeIL-1TdiZCOfKoZwr_g5z',
        'priority': 'u=1, i',
        'referer': 'https://q.10jqka.com.cn/thshy/',
        'sec-ch-ua': '"Not:A-Brand";v="99", "Google Chrome";v="145", "Chromium";v="145"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
        'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'cors',
        'sec-fetch-site': 'same-origin',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36',
        'x-requested-with': 'XMLHttpRequest'
    }

    try:
        response = await client.get(url, headers=headers, timeout=10.0)
        
        if 'charset=gbk' in response.headers.get('content-type', '').lower() or response.encoding == 'ISO-8859-1':
            response.encoding = 'gbk'
            
        html_content = response.text
        
        if "Nginx forbidden" in html_content or response.status_code == 403:
            logger.error(f"[THS Page {page}] 请求被拦截 (403/Forbidden)")
            return []

        soup = BeautifulSoup(html_content, "html.parser")
        table_rows = soup.select("tbody tr")
        
        raw_results = []
        for row in table_rows:
            cols = row.find_all("td")
            if len(cols) < 8:
                continue
            
            if "暂无成份股数据" in row.get_text():
                continue

            try:
                # 辅助清洗函数
                def clean_num(text):
                    try:
                        return float(text.strip().replace('%', ''))
                    except ValueError:
                        return 0.0

                def clean_int(text):
                    try:
                        return int(text.strip())
                    except ValueError:
                        return 0

                name = cols[1].get_text(strip=True)
                change_percent = clean_num(cols[2].get_text(strip=True))
                # cols[3] 是成交量(万手)，我们不再需要，或者不需要存入结果
                # cols[4] 是成交额(亿元)，我们需要它来计算占比
                raw_amount = clean_num(cols[4].get_text(strip=True))
                
                net_inflow = clean_num(cols[5].get_text(strip=True))
                up_count = clean_int(cols[6].get_text(strip=True))
                down_count = clean_int(cols[7].get_text(strip=True))
                
                # 暂存为字典，包含 raw_amount 以便后续聚合
                raw_results.append({
                    "name": name,
                    "change_percent": change_percent,
                    "raw_amount": raw_amount, # 暂存，不直接放入 Schema
                    "net_inflow": net_inflow,
                    "up_count": up_count,
                    "down_count": down_count
                })
            except (IndexError, ValueError) as e:
                continue
            
        return raw_results

    except Exception as e:
        logger.error(f"[THS Page {page}] 解析失败: {e}")
        return []

async def fetch_ths_sectors() -> List[ThsSectorInfo]:
    """
    并发获取同花顺板块数据，并计算成交额占比。
    """
    async with httpx.AsyncClient() as client:
        # 并发请求2页数据
        tasks = [
            _fetch_ths_page(client, 1),
            _fetch_ths_page(client, 2)
        ]
        
        results = await asyncio.gather(*tasks)
        
        all_raw_data = []
        for page_data in results:
            all_raw_data.extend(page_data)
        
        if not all_raw_data:
            return []

        # 1. 计算所有板块的总成交额
        total_market_amount = sum(item["raw_amount"] for item in all_raw_data)
        
        # 防止除以零
        if total_market_amount == 0:
            total_market_amount = 1.0 

        final_sectors = []
        for item in all_raw_data:
            # 2. 计算占比: (板块成交额 / 总成交额) * 100
            ratio = (item["raw_amount"] / total_market_amount) * 100
            
            sector_info = ThsSectorInfo(
                name=item["name"],
                change_percent=item["change_percent"],
                net_inflow=item["net_inflow"],
                up_count=item["up_count"],
                down_count=item["down_count"],
                turnover_ratio=round(ratio, 2) # 保留两位小数
            )
            final_sectors.append(sector_info)
            
        logger.info(f"同花顺数据处理完成，共 {len(final_sectors)} 条，总成交额 {total_market_amount:.2f} 亿")
        return final_sectors