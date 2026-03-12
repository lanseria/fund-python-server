# src/python_cli_starter/main.py
from fastapi import FastAPI, HTTPException, Query, status, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
import inspect
from typing import Optional
from contextlib import asynccontextmanager
import logging
from datetime import datetime, date, time
import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .strategies import STRATEGY_REGISTRY
from . import schemas
from . import charts
from . import market
from .database import (
    save_eastmoney_sectors,
    save_ths_sectors,
    get_today_eastmoney_sectors,
    get_today_ths_sectors,
)

# 日志配置
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


# --- 节假日配置与交易时间判断逻辑 ---
HOLIDAYS_CONFIG = [
    (date(2026, 1, 1), date(2026, 1, 3)),  # 元旦
    (date(2026, 2, 15), date(2026, 2, 23)),  # 春节
    (date(2026, 4, 4), date(2026, 4, 6)),  # 清明节
    (date(2026, 5, 1), date(2026, 5, 5)),  # 劳动节
    (date(2026, 6, 19), date(2026, 6, 21)),  # 端午节
    (date(2026, 9, 25), date(2026, 9, 27)),  # 中秋节
    (date(2026, 10, 1), date(2026, 10, 7)),  # 国庆节
]


def is_trading_day(dt: datetime = None) -> bool:
    """检查指定日期是否为A股交易日"""
    if dt is None:
        dt = datetime.now()

    # 1. 检查是否为周末 (Python中: 0=周一, ..., 5=周六, 6=周日)
    if dt.weekday() >= 5:
        return False

    # 2. 检查节假日
    current_date = dt.date()
    for start_date, end_date in HOLIDAYS_CONFIG:
        if start_date <= current_date <= end_date:
            return False

    return True


def is_trading_hours(dt: datetime = None) -> bool:
    """检查当前时间是否在A股交易时间内 (9:30-11:30 或 13:00-15:00)"""
    if dt is None:
        dt = datetime.now()

    current_time = dt.time()

    morning_start = time(9, 30)
    morning_end = time(11, 30)
    afternoon_start = time(13, 0)
    afternoon_end = time(15, 0)

    is_morning = morning_start <= current_time <= morning_end
    is_afternoon = afternoon_start <= current_time <= afternoon_end

    return is_morning or is_afternoon


# 初始化定时任务调度器
scheduler = AsyncIOScheduler()


async def fetch_and_save_sectors_task():
    """定时爬取与保存板块数据的后台任务"""
    now = datetime.now()

    if not is_trading_day(now):
        logger.info(f"定时任务跳过: {now.strftime('%Y-%m-%d')} 为非交易日")
        return

    if not is_trading_hours(now):
        logger.info(f"定时任务跳过: {now.strftime('%H:%M')} 为非交易时段")
        return

    logger.info("定时任务: 处于交易时段，开始获取并存储板块数据...")
    try:
        # 1. 东方财富数据
        eastmoney_sectors = await market.fetch_eastmoney_sectors()
        if eastmoney_sectors:
            await save_eastmoney_sectors(eastmoney_sectors)

        # 2. 同花顺数据
        ths_sectors = await market.fetch_ths_sectors()
        if ths_sectors:
            await save_ths_sectors(ths_sectors)
    except Exception as e:
        logger.error(f"定时获取板块数据异常: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("策略分析 API 服务启动")

    scheduler.add_job(fetch_and_save_sectors_task, "cron", hour=11, minute=30)
    scheduler.add_job(fetch_and_save_sectors_task, "cron", hour=14, minute=30)
    scheduler.add_job(fetch_and_save_sectors_task, "cron", hour=16, minute=30)
    scheduler.start()

    # 服务启动时，不等待15分钟，立即执行一次数据爬取
    asyncio.create_task(fetch_and_save_sectors_task())

    yield

    scheduler.shutdown()
    logger.info("策略分析 API 服务关闭")


app = FastAPI(title="基金策略分析 API", lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """捕获 422 异常并打印等效的 curl 请求以便调试"""
    # 1. 构造基础命令
    command = f"curl -X {request.method} '{str(request.url)}'"

    # 2. 遍历并添加请求头 (忽略 host 和 content-length)
    for name, value in request.headers.items():
        if name.lower() not in ("host", "content-length"):
            # 简单转义单引号防止 shell 解析错误
            safe_value = value.replace("'", "'\\''")
            command += f" -H '{name}: {safe_value}'"

    # 3. 尝试读取并添加请求体 (Body)
    try:
        body = await request.body()
        if body:
            body_str = body.decode("utf-8").replace("'", "'\\''")
            command += f" -d '{body_str}'"
    except Exception:
        pass

    # 4. 打印调试信息
    logger.warning("参数验证失败 (422 Unprocessable Entity)！")
    logger.warning(f"可用于本地调试的 curl 命令如下:\n{command}\n")
    logger.warning(f"具体的验证错误原因: {exc.errors()}")

    # 5. 返回默认的 422 JSON 响应结构
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": exc.errors()},
    )


# --- 前端展示页面 HTML ---
DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>板块数据监控中心</title>
    <!-- 引入 Vue 3 和 Tailwind CSS -->
    <script src="https://unpkg.com/vue@3/dist/vue.global.js"></script>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        [v-cloak] { display: none; }
        .text-red { color: #ef4444; }   /* A股习惯：红涨 */
        .text-green { color: #22c55e; } /* A股习惯：绿跌 */
    </style>
</head>
<body class="bg-gray-100 min-h-screen p-4 md:p-8">
    <div id="app" v-cloak class="max-w-7xl mx-auto bg-white rounded-xl shadow-lg overflow-hidden">
        <!-- 页面头部 -->
        <div class="bg-blue-600 p-6 text-white flex flex-col md:flex-row md:justify-between md:items-center gap-4">
            <h1 class="text-2xl font-bold">板块数据监控中心</h1>
            <div class="flex items-center gap-4">
                <div class="text-sm opacity-80">自动拉取最新市场数据</div>
                <button @click="refreshData"
                        :disabled="isFetching"
                        class="bg-white text-blue-600 px-4 py-2 rounded-lg text-sm font-medium hover:bg-blue-50 transition disabled:opacity-50 disabled:cursor-not-allowed">
                    刷新数据
                </button>
            </div>
        </div>

        <!-- 一键获取区域 -->
        <div class="p-4 bg-yellow-50 border-b border-yellow-200">
            <div class="flex flex-col md:flex-row gap-4 items-start md:items-center">
                <div class="flex-1 w-full">
                    <label class="block text-sm font-medium text-gray-700 mb-1">东方财富 Cookie (可选)</label>
                    <input v-model="cookieInput" type="text" placeholder="从浏览器复制东方财富的 Cookie 粘贴到这里..."
                           class="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none transition-all text-sm">
                </div>
                <div class="flex flex-col gap-2">
                    <button @click="fetchAllData"
                            :disabled="isFetching"
                            class="bg-blue-600 text-white px-6 py-2 rounded-lg text-sm font-medium hover:bg-blue-700 transition disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2">
                        <svg v-if="isFetching" class="animate-spin h-4 w-4" viewBox="0 0 24 24">
                            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" fill="none"></circle>
                            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                        </svg>
                        {{ isFetching ? '获取中...' : '一键获取全部' }}
                    </button>
                    <div v-if="fetchStatus.length > 0" class="text-xs text-gray-600">
                        <div v-for="(step, idx) in fetchStatus" :key="idx"
                             :class="step.success ? 'text-green-600' : 'text-red-600'">
                            {{ step.name }}: {{ step.message }}
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- 操作区：Tab切换 与 搜索 -->
        <div class="p-6 border-b border-gray-200 flex flex-col sm:flex-row justify-between items-center space-y-4 sm:space-y-0">
            <div class="flex space-x-1 bg-gray-100 p-1 rounded-lg">
                <button @click="activeTab = 'eastmoney'"
                        :class="activeTab === 'eastmoney' ? 'bg-white shadow text-blue-600' : 'text-gray-600 hover:text-gray-800'"
                        class="px-6 py-2 rounded-md text-sm font-medium transition-all duration-200">
                    东方财富板块
                </button>
                <button @click="activeTab = 'ths'"
                        :class="activeTab === 'ths' ? 'bg-white shadow text-blue-600' : 'text-gray-600 hover:text-gray-800'"
                        class="px-6 py-2 rounded-md text-sm font-medium transition-all duration-200">
                    同花顺板块
                </button>
            </div>

            <div class="relative w-full sm:w-72">
                <input v-model="searchQuery" type="text" placeholder="搜索板块名称..."
                       class="w-full pl-10 pr-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none transition-all">
                <svg class="w-5 h-5 text-gray-400 absolute left-3 top-2.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"></path>
                </svg>
            </div>
        </div>

        <!-- 东方财富数据表格 -->
        <div v-show="activeTab === 'eastmoney'" class="overflow-x-auto">
            <table class="w-full text-left border-collapse">
                <thead>
                    <tr class="bg-gray-50 text-gray-600 text-sm border-b">
                        <th class="p-4 font-semibold whitespace-nowrap">板块名称</th>
                        <th class="p-4 font-semibold whitespace-nowrap">涨跌幅</th>
                        <th class="p-4 font-semibold whitespace-nowrap">总市值</th>
                        <th class="p-4 font-semibold whitespace-nowrap">换手率</th>
                        <th class="p-4 font-semibold whitespace-nowrap">成交额</th>
                        <th class="p-4 font-semibold whitespace-nowrap">日期</th>
                        <th class="p-4 font-semibold whitespace-nowrap">更新时间</th>
                    </tr>
                </thead>
                <tbody class="divide-y divide-gray-100">
                    <tr v-for="item in filteredEastMoney" :key="item.name" class="hover:bg-blue-50 transition-colors">
                        <td class="p-4 text-gray-800 font-medium">{{ item.name }}</td>
                        <td class="p-4 font-bold" :class="getColorClass(item.change_percent)">{{ item.change_percent_desc }}</td>
                        <td class="p-4 text-gray-600">{{ item.market_cap_desc }}</td>
                        <td class="p-4 text-gray-600">{{ item.turnover_rate_desc }}</td>
                        <td class="p-4 text-gray-600">{{ item.amount_desc }}</td>
                        <td class="p-4 text-gray-600">{{ item.date }}</td>
                        <td class="p-4 text-gray-600">{{ item.updated_at }}</td>
                    </tr>
                    <tr v-if="filteredEastMoney.length === 0">
                        <td colspan="7" class="p-8 text-center text-gray-500">未找到匹配的板块数据</td>
                    </tr>
                </tbody>
            </table>
        </div>

        <!-- 同花顺数据表格 -->
        <div v-show="activeTab === 'ths'" class="overflow-x-auto">
            <table class="w-full text-left border-collapse">
                <thead>
                    <tr class="bg-gray-50 text-gray-600 text-sm border-b">
                        <th class="p-4 font-semibold whitespace-nowrap">板块名称</th>
                        <th class="p-4 font-semibold whitespace-nowrap">涨跌幅</th>
                        <th class="p-4 font-semibold whitespace-nowrap">净流入(亿)</th>
                        <th class="p-4 font-semibold whitespace-nowrap">上涨家数</th>
                        <th class="p-4 font-semibold whitespace-nowrap">下跌家数</th>
                        <th class="p-4 font-semibold whitespace-nowrap">成交额占比</th>
                        <th class="p-4 font-semibold whitespace-nowrap">日期</th>
                        <th class="p-4 font-semibold whitespace-nowrap">更新时间</th>
                    </tr>
                </thead>
                <tbody class="divide-y divide-gray-100">
                    <tr v-for="item in filteredThs" :key="item.name" class="hover:bg-blue-50 transition-colors">
                        <td class="p-4 text-gray-800 font-medium">{{ item.name }}</td>
                        <td class="p-4 font-bold" :class="getColorClass(item.change_percent)">{{ item.change_percent }}%</td>
                        <td class="p-4 font-bold" :class="getColorClass(item.net_inflow)">{{ item.net_inflow }}</td>
                        <td class="p-4 text-red">{{ item.up_count }}</td>
                        <td class="p-4 text-green">{{ item.down_count }}</td>
                        <td class="p-4 text-gray-600">{{ item.turnover_ratio }}%</td>
                        <td class="p-4 text-gray-600">{{ item.date }}</td>
                        <td class="p-4 text-gray-600">{{ item.updated_at }}</td>
                    </tr>
                    <tr v-if="filteredThs.length === 0">
                        <td colspan="8" class="p-8 text-center text-gray-500">未找到匹配的板块数据</td>
                    </tr>
                </tbody>
            </table>
        </div>
    </div>

    <script>
        const { createApp, ref, computed, onMounted } = Vue

        createApp({
            setup() {
                const activeTab = ref('eastmoney')
                const searchQuery = ref('')
                const eastMoneyData = ref([])
                const thsData = ref([])
                const cookieInput = ref('')
                const isFetching = ref(false)
                const fetchStatus = ref([])

                const fetchEastMoney = async () => {
                    try {
                        const res = await fetch('/market/df_sectors')
                        const data = await res.json()
                        eastMoneyData.value = data.sectors ||[]
                    } catch (e) {
                        console.error('获取东方财富数据失败:', e)
                    }
                }

                const fetchThs = async () => {
                    try {
                        const res = await fetch('/market/ths_sectors')
                        const data = await res.json()
                        thsData.value = data.sectors ||[]
                    } catch (e) {
                        console.error('获取同花顺数据失败:', e)
                    }
                }

                const refreshData = async () => {
                    await Promise.all([fetchEastMoney(), fetchThs()])
                }

                const fetchAllData = async () => {
                    isFetching.value = true
                    fetchStatus.value = []

                    try {
                        const res = await fetch('/market/fetch/batch', {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/json'
                            },
                            body: JSON.stringify({
                                cookie: cookieInput.value || null
                            })
                        })

                        const data = await res.json()
                        fetchStatus.value = data.steps || []

                        // 执行完成后刷新页面数据
                        if (data.success || data.steps.some(s => s.success)) {
                            await refreshData()
                        }
                    } catch (e) {
                        console.error('批量获取失败:', e)
                        fetchStatus.value = [{
                            name: '请求失败',
                            success: false,
                            message: e.message || '未知错误',
                            count: 0
                        }]
                    } finally {
                        isFetching.value = false
                    }
                }

                // 统一的红绿判断逻辑
                const getColorClass = (val) => {
                    if (val > 0) return 'text-red'
                    if (val < 0) return 'text-green'
                    return 'text-gray-500'
                }

                onMounted(() => {
                    fetchEastMoney()
                    fetchThs()
                })

                const filteredEastMoney = computed(() => {
                    if (!searchQuery.value) return eastMoneyData.value
                    const query = searchQuery.value.toLowerCase()
                    return eastMoneyData.value.filter(item => item.name.toLowerCase().includes(query))
                })

                const filteredThs = computed(() => {
                    if (!searchQuery.value) return thsData.value
                    const query = searchQuery.value.toLowerCase()
                    return thsData.value.filter(item => item.name.toLowerCase().includes(query))
                })

                return {
                    activeTab,
                    searchQuery,
                    filteredEastMoney,
                    filteredThs,
                    getColorClass,
                    cookieInput,
                    isFetching,
                    fetchStatus,
                    fetchAllData,
                    refreshData
                }
            }
        }).mount('#app')
    </script>
</body>
</html>
"""


@app.get(
    "/", response_class=HTMLResponse, summary="板块监控前端面板", tags=["Dashboard"]
)
async def dashboard():
    """返回内置的数据监控静态 HTML 页面"""
    return DASHBOARD_HTML


@app.get(
    "/strategies",
    response_model=schemas.StrategyListResponse,
    summary="获取所有可用策略列表",
    tags=["Strategies"],
)
def list_strategies():
    """返回所有已注册的策略名称。"""
    return schemas.StrategyListResponse(
        strategies=list(STRATEGY_REGISTRY.keys()), count=len(STRATEGY_REGISTRY)
    )


@app.get(
    "/strategies/{strategy_name}/{fund_code}",
    response_model=schemas.StrategySignal,
    summary="执行指定策略分析",
    tags=["Strategies"],
)
def get_strategy_signal(
    strategy_name: str,
    fund_code: str,
    is_holding: Optional[bool] = Query(
        None, description="【可选】对于需要持仓状态的策略，指定当前是否持有该基金。"
    ),
):
    """
    根据指定的策略名称和基金代码，运行分析并返回交易信号。

    - **strategy_name**: 策略名称，支持：`rsi`, `macd`, `bollinger_bands`, `dual_confirmation`
    - **fund_code**: 要分析的基金代码（6位数字）
    - **is_holding**: (可选) 对于 `macd`、`bollinger_bands`、`dual_confirmation` 策略需要提供此参数 (`true`/`false`)
    """
    logger.info(
        f"策略分析请求: strategy='{strategy_name}', code='{fund_code}', is_holding={is_holding}"
    )

    strategy_function = STRATEGY_REGISTRY.get(strategy_name)
    if not strategy_function:
        logger.warning(f"未找到策略: '{strategy_name}'")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"策略 '{strategy_name}' 不存在。可用策略: {list(STRATEGY_REGISTRY.keys())}",
        )

    try:
        sig = inspect.signature(strategy_function)
        params = {}

        if "fund_code" in sig.parameters:
            params["fund_code"] = fund_code

        if "is_holding" in sig.parameters:
            if is_holding is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"策略 '{strategy_name}' 需要 'is_holding' 查询参数 (true/false)。",
                )
            params["is_holding"] = is_holding

        result_dict = strategy_function(**params)

        if result_dict.get("error"):
            logger.error(f"策略 '{strategy_name}' 执行失败: {result_dict['error']}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=result_dict["error"],
            )

        return schemas.StrategySignal(
            fund_code=fund_code, strategy_name=strategy_name, **result_dict
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"执行策略 '{strategy_name}' 时发生意外错误")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"执行策略时发生内部错误: {str(e)}",
        )


@app.get(
    "/health",
    response_model=schemas.HealthResponse,
    summary="健康检查",
    tags=["System"],
)
def health_check():
    """服务健康检查端点。"""
    return schemas.HealthResponse(status="ok", timestamp=datetime.now().isoformat())


@app.get(
    "/charts/rsi/{fund_code}",
    response_model=schemas.RsiChartResponse,
    summary="获取 RSI 策略图表数据",
    tags=["Charts"],
)
def get_rsi_chart(fund_code: str):
    """
    获取指定基金的 RSI 策略全量历史数据，用于前端 ECharts 绘图。
    包含：
    - 历史净值
    - RSI 指标值
    - 基于策略生成的买卖信号点
    """
    chart_data = charts.get_rsi_chart_data(fund_code)

    if not chart_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"无法获取基金 {fund_code} 的图表数据。",
        )

    return chart_data


@app.get(
    "/market/df_sectors",
    response_model=schemas.SectorListResponse,
    summary="获取行业板块数据(东方财富)",
    tags=["Market"],
)
async def get_df_sector_list():
    """
    从数据库获取当日（最新可用）东方财富的行业板块数据。
    """
    sectors = await get_today_eastmoney_sectors()

    return schemas.SectorListResponse(
        count=len(sectors),
        sectors=[schemas.SectorInfo.model_validate(s) for s in sectors],
    )


@app.get(
    "/market/ths_sectors",
    response_model=schemas.ThsSectorListResponse,
    summary="获取同花顺行业板块数据",
    tags=["Market"],
)
async def get_ths_sector_list():
    """
    从数据库获取当日（最新可用）同花顺行业板块数据。
    """
    sectors = await get_today_ths_sectors()

    return schemas.ThsSectorListResponse(
        count=len(sectors),
        sectors=[schemas.ThsSectorInfo.model_validate(s) for s in sectors],
    )


@app.get(
    "/market/sector_names", summary="获取两家数据源的板块名称列表", tags=["Market"]
)
async def get_sector_names():
    """
    返回当日数据库中最新的东方财富和同花顺的所有板块名称。
    格式示范：
    {
        "东方财富": ["板块1", "板块2"],
        "同花顺": ["板块1"]
    }
    """
    em_sectors = await get_today_eastmoney_sectors()
    ths_sectors = await get_today_ths_sectors()

    return {
        "东方财富": [s.name for s in em_sectors],
        "同花顺": [s.name for s in ths_sectors],
    }


@app.post(
    "/market/fetch/eastmoney",
    response_model=schemas.EastMoneyFetchResponse,
    summary="手动触发获取东方财富板块数据",
    tags=["Market"],
)
async def trigger_fetch_eastmoney(request: schemas.EastMoneyFetchRequest):
    """
    手动触发爬取东方财富板块数据并保存到数据库。
    可以传入 cookie 来绕过 Playwright，提升速度并防止被反爬拦截。
    fs_type: 2=行业板块(默认), 3=概念板块
    """
    logger.info(
        f"手动触发获取东方财富数据: cookie_provided={bool(request.cookie)}, fs_type={request.fs_type}"
    )
    try:
        sectors = await market.fetch_eastmoney_sectors(
            cookie=request.cookie, fs_type=request.fs_type
        )
        if sectors:
            await save_eastmoney_sectors(sectors)
            return schemas.EastMoneyFetchResponse(
                success=True, message="获取并保存成功", count=len(sectors)
            )
        else:
            return schemas.EastMoneyFetchResponse(
                success=False,
                message="获取成功但没有数据，可能被封禁或非交易时间",
                count=0,
            )
    except market.EastMoneyAPIException as e:
        if e.status_code == 422:
            logger.warning("东方财富真实接口返回 422，已拦截并返回 curl 供调试。")
            # 不引发 HTTPException，而是直接返回 Schema 从而触发 200 状态码
            return schemas.EastMoneyFetchResponse(
                success=False,
                message="【提醒】东方财富接口验证失败(422)！请复制下方的 curl 命令在终端进行调试查找原因。",
                count=0,
                curl_command=e.curl_cmd,
            )
        else:
            raise HTTPException(
                status_code=500, detail=f"第三方接口异常: HTTP {e.status_code}"
            )
    except Exception as e:
        logger.error(f"手动触发获取东方财富数据异常: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"抓取异常: {str(e)}",
        )


@app.post(
    "/market/upload/eastmoney",
    response_model=schemas.EastMoneyUploadResponse,
    summary="手动上传东方财富JSONP数据",
    tags=["Market"],
)
async def upload_eastmoney_data(request: Request):
    """
    接收纯文本格式的东方财富 JSONP 或 JSON 字符串，
    无需 JSON 包裹，直接把原始文本放在请求体(Body)中发送即可。
    解析其中的板块数据并插入/更新到数据库。支持分批次上传。
    """
    # 直接读取原始的 HTTP Body 请求体并解码为字符串
    raw_bytes = await request.body()
    raw_data = raw_bytes.decode("utf-8")

    logger.info(f"收到手动上传的东方财富纯文本数据，数据长度: {len(raw_data)}")
    try:
        sectors = market.parse_eastmoney_jsonp(raw_data)
        if sectors:
            await save_eastmoney_sectors(sectors)
            return schemas.EastMoneyUploadResponse(
                success=True,
                message=f"成功解析并保存 {len(sectors)} 条数据",
                count=len(sectors),
            )
        else:
            return schemas.EastMoneyUploadResponse(
                success=False, message="未能从提供的数据中解析出有效内容", count=0
            )
    except Exception as e:
        logger.error(f"手动上传东方财富数据异常: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"数据处理异常: {str(e)}",
        )


@app.post(
    "/market/fetch/batch",
    response_model=schemas.BatchFetchResponse,
    summary="一键获取所有板块数据(东方财富行业+概念+同花顺)",
    tags=["Market"],
)
async def trigger_fetch_batch(request: schemas.BatchFetchRequest):
    """
    一键获取所有板块数据，按顺序执行：
    1. 东方财富行业板块 (fs_type=2)
    2. 东方财富概念板块 (fs_type=3)
    3. 同花顺板块
    全部完成后返回每一步的执行结果。
    """
    logger.info(f"开始批量获取板块数据: cookie_provided={bool(request.cookie)}")

    steps = []
    all_success = True

    # 步骤 1: 东方财富行业板块 (fs_type=2)
    try:
        logger.info("步骤 1/3: 获取东方财富行业板块...")
        sectors = await market.fetch_eastmoney_sectors(
            cookie=request.cookie, fs_type=2
        )
        count = len(sectors) if sectors else 0
        if sectors:
            await save_eastmoney_sectors(sectors)
        steps.append(schemas.BatchFetchStepResult(
            name="东方财富行业板块",
            success=count > 0,
            message=f"获取并保存 {count} 条数据" if count > 0 else "未获取到数据",
            count=count
        ))
        if count == 0:
            all_success = False
    except Exception as e:
        logger.error(f"步骤 1 异常: {e}")
        steps.append(schemas.BatchFetchStepResult(
            name="东方财富行业板块",
            success=False,
            message=f"异常: {str(e)}",
            count=0
        ))
        all_success = False

    # 步骤 2: 东方财富概念板块 (fs_type=3)
    try:
        logger.info("步骤 2/3: 获取东方财富概念板块...")
        sectors = await market.fetch_eastmoney_sectors(
            cookie=request.cookie, fs_type=3
        )
        count = len(sectors) if sectors else 0
        if sectors:
            await save_eastmoney_sectors(sectors)
        steps.append(schemas.BatchFetchStepResult(
            name="东方财富概念板块",
            success=count > 0,
            message=f"获取并保存 {count} 条数据" if count > 0 else "未获取到数据",
            count=count
        ))
        if count == 0:
            all_success = False
    except Exception as e:
        logger.error(f"步骤 2 异常: {e}")
        steps.append(schemas.BatchFetchStepResult(
            name="东方财富概念板块",
            success=False,
            message=f"异常: {str(e)}",
            count=0
        ))
        all_success = False

    # 步骤 3: 同花顺板块
    try:
        logger.info("步骤 3/3: 获取同花顺板块...")
        sectors = await market.fetch_ths_sectors()
        count = len(sectors) if sectors else 0
        if sectors:
            await save_ths_sectors(sectors)
        steps.append(schemas.BatchFetchStepResult(
            name="同花顺板块",
            success=count > 0,
            message=f"获取并保存 {count} 条数据" if count > 0 else "未获取到数据",
            count=count
        ))
        if count == 0:
            all_success = False
    except Exception as e:
        logger.error(f"步骤 3 异常: {e}")
        steps.append(schemas.BatchFetchStepResult(
            name="同花顺板块",
            success=False,
            message=f"异常: {str(e)}",
            count=0
        ))
        all_success = False

    return schemas.BatchFetchResponse(
        success=all_success,
        message="批量获取完成" if all_success else "部分任务失败",
        steps=steps
    )
