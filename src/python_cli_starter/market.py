# src/python_cli_starter/market.py

import httpx
import json
import logging
import re
import asyncio
import math
from bs4 import BeautifulSoup
from typing import List, Dict, Any, Optional, Tuple
from playwright.async_api import async_playwright, Page
from .schemas import SectorInfo, ThsSectorInfo

logger = logging.getLogger(__name__)

# 常量定义
PAGE_SIZE = 100  # 接口限制最大每页数量
BASE_URL = "https://push2.eastmoney.com/api/qt/clist/get" 
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://quote.eastmoney.com/"
}

async def _fetch_page_raw(context, page: int, ut: str) -> Tuple[List[Dict], int]:
    """
    获取单页原始数据 (使用 Playwright 的 APIRequestContext，携带动态 ut)
    """
    import time
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
        "ut": ut,  # <--- 使用拦截到的动态 ut
        "wbp2u": "|0|0|0|web",
        "_": str(int(time.time() * 1000))  # 动态生成当前时间戳
    }

    try:
        # 调用上下文底层的真实网络栈发请求，因为和刚才的访问同属一个 context，Cookie 会自动附带过去！
        response = await context.request.get(BASE_URL, params=params, headers=HEADERS, timeout=10000)
        text = await response.text()

        # JSONP 解析
        match = re.search(r'jQuery_callback\((.*)\)', text, re.DOTALL)
        if not match:
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                logger.error(f"[EastMoney Page {page}] 无法解析响应数据")
                return[], 0
        else:
            json_str = match.group(1)
            try:
                data = json.loads(json_str)
            except json.JSONDecodeError:
                logger.error(f"[EastMoney Page {page}] JSONP 解析失败")
                return[], 0

        if "data" in data and data["data"]:
            total = data["data"].get("total", 0)
            diff_list = data["data"].get("diff",[])
            return diff_list, total
        else:
            return [], 0

    except Exception as e:
        logger.error(f"[EastMoney Page {page}] 请求失败: {e}")
        return[], 0


def _process_item(item: Dict) -> SectorInfo:
    """将原始字典转换为 SectorInfo 对象"""
    name = str(item.get("f14", "未知板块"))
    
    def clean_float(val):
        if val is None or val == "-": return 0.0
        if not isinstance(val, (int, float)):
            try: return float(val)
            except (ValueError, TypeError): return 0.0
        return float(val)

    raw_cap = item.get("f20", 0)
    raw_turnover = item.get("f8", 0)
    raw_change = item.get("f3", 0)

    market_cap_val = clean_float(raw_cap)
    turnover_val = clean_float(raw_turnover)
    change_val = clean_float(raw_change)

    return SectorInfo(
        name=name,
        market_cap=market_cap_val,
        market_cap_desc=f"{market_cap_val / 100000000:.2f} 亿",
        turnover_rate=turnover_val,
        turnover_rate_desc=f"{turnover_val / 100:.2f}%",
        change_percent=change_val,
        change_percent_desc=f"{change_val / 100:.2f}%"
    )


async def fetch_eastmoney_sectors() -> Optional[List[SectorInfo]]:
    """
    使用 Playwright 解决联动校验：先用无头浏览器访问网页获取自动生成的 cookie 并截获 ut，再请求 API
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"]
        )
        # 初始化一个上下文，所有由这个 context 产生的 page 和 request 共享同一个 cookie jar
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

        # --- 第一步：开启真实页面，拦截动态 ut 并获取 Cookie ---
        init_page = await context.new_page()
        captured_ut = "fa5fd1943c7b386f172d6893dbfba10b"  # 默认备用值
        
        async def handle_request(request):
            nonlocal captured_ut
            # 监听所有包含目标接口的请求
            if "push2.eastmoney.com/api/qt/clist/get" in request.url:
                from urllib.parse import urlparse, parse_qs
                parsed = urlparse(request.url)
                qs = parse_qs(parsed.query)
                if 'ut' in qs and qs['ut']:
                    captured_ut = qs['ut'][0]

        init_page.on("request", handle_request)

        logger.info("正在访问东方财富主页，通过 JS 渲染初始化 Cookie 并截获动态 ut...")
        try:
            # 访问页面，让网页自动帮我们进行环境验证，并触发数据请求，从而被监听截获到 ut
            await init_page.goto("https://quote.eastmoney.com/center/gridlist.html#hs_a_board", wait_until="networkidle", timeout=12000)
        except Exception as e:
            logger.warning(f"访问东方财富主页耗时较长或异常(通常能成功注入Cookie无需担心): {e}")

        logger.info(f"成功截获动态生成的 ut 参数: {captured_ut}")
        await init_page.close() # 关闭引导页，此时 context 已包含了正确的 Cookie

        # --- 第二步：带着拿到的 context(带Cookie)和 ut 循环去请求真正的数据 ---
        logger.info("正在获取东方财富板块数据第一页...")
        first_page_items, total_count = await _fetch_page_raw(context, 1, captured_ut)
        
        if not first_page_items and total_count == 0:
            logger.warning("未能获取到东方财富板块数据")
            await browser.close()
            return[]

        all_raw_items = list(first_page_items)
        total_pages = math.ceil(total_count / PAGE_SIZE)
        logger.info(f"东方财富数据获取成功，共 {total_count} 条数据，需请求 {total_pages} 页")

        if total_pages > 1:
            # 并发获取后续页面，上下文底层的 TCP 连接池会自动调度复用
            tasks =[]
            for page in range(2, total_pages + 1):
                tasks.append(_fetch_page_raw(context, page, captured_ut))
            
            results = await asyncio.gather(*tasks)
            
            for items, _ in results:
                all_raw_items.extend(items)

        await browser.close()

        # 处理数据
        logger.info(f"东方财富所有页面获取完成，开始处理 {len(all_raw_items)} 条记录")
        processed_sectors = []
        for item in all_raw_items:
            try:
                processed_sectors.append(_process_item(item))
            except Exception as e:
                logger.error(f"处理单条数据出错: {e}, item: {item}")
                continue
                
        return processed_sectors
    
# --- 同花顺数据处理逻辑 ---

async def _fetch_ths_page(page: Page, page_num: int) -> List[Dict[str, Any]]:
    """
    获取并解析同花顺单页 HTML 数据（使用 Playwright 模拟浏览器）。
    注意：返回的是包含原始成交额的字典列表，用于后续计算占比。
    """
    url = f"https://q.10jqka.com.cn/thshy/index/field/199112/order/desc/page/{page_num}/ajax/1/"
    
    try:
        # 访问页面，带有 referer 有助于绕过部分基础检测
        response = await page.goto(url, referer="https://q.10jqka.com.cn/thshy/")
        # 等待页面 DOM 加载完成
        await page.wait_for_load_state("domcontentloaded")
        
        html_content = await page.content()
        
        if "Nginx forbidden" in html_content or (response and response.status == 403):
            logger.error(f"[THS Page {page_num}] 请求被拦截 (403/Forbidden)")
            return[]

        soup = BeautifulSoup(html_content, "html.parser")
        table_rows = soup.select("tbody tr")
        
        # 兼容处理：如果没有 tbody 标签，直接选 tr
        if not table_rows:
            table_rows = soup.select("tr")
            
        raw_results =[]
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
                    "raw_amount": raw_amount,
                    "net_inflow": net_inflow,
                    "up_count": up_count,
                    "down_count": down_count
                })
            except (IndexError, ValueError) as e:
                continue
            
        return raw_results

    except Exception as e:
        logger.error(f"[THS Page {page_num}] 解析失败: {e}")
        return []

async def fetch_ths_sectors() -> List[ThsSectorInfo]:
    """
    使用 Playwright 模拟真实浏览器并发获取同花顺板块数据，并计算成交额占比。
    该方法能够有效让网站执行自身 JS 并生成合格的 hexin-v/v 的 cookie 信息。
    """
    async with async_playwright() as p:
        # 启动无头浏览器，添加参数尽力绕过简单的机器人检测
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"]
        )
        
        # 创建上下文，设置常见的 User-Agent 与窗口大小
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080}
        )
        
        # 第一步：关键点！先访问主页，让网页自动执行 JS 生成正确的 cookie (含 v/hexin-v)
        main_page = await context.new_page()
        try:
            logger.info("正在访问同花顺主页以获取认证信息(自动计算 hexin-v)...")
            await main_page.goto("https://q.10jqka.com.cn/thshy/", wait_until="networkidle", timeout=15000)
        except Exception as e:
            logger.warning(f"访问同花顺主页遇到异常（不一定会影响后续爬取）: {e}")
        
        # 第二步：使用已经带有有效 Cookie 的上下文请求数据页 (因为同上下文的 cookie 共享)
        page1 = await context.new_page()
        page2 = await context.new_page()
        
        tasks =[
            _fetch_ths_page(page1, 1),
            _fetch_ths_page(page2, 2)
        ]
        
        results = await asyncio.gather(*tasks)
        
        await browser.close()
        
        all_raw_data =[]
        for page_data in results:
            all_raw_data.extend(page_data)
        
        if not all_raw_data:
            return[]

        # 1. 计算所有板块的总成交额
        total_market_amount = sum(item["raw_amount"] for item in all_raw_data)
        
        # 防止除以零
        if total_market_amount == 0:
            total_market_amount = 1.0 

        final_sectors =[]
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