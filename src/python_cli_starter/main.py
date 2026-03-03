# src/python_cli_starter/main.py
from fastapi import FastAPI, HTTPException, Query, status
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
    init_db, 
    save_eastmoney_sectors, 
    save_ths_sectors,
    get_today_eastmoney_sectors,
    get_today_ths_sectors
)

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# --- 节假日配置与交易时间判断逻辑 ---
HOLIDAYS_CONFIG =[
    (date(2026, 1, 1), date(2026, 1, 3)),   # 元旦
    (date(2026, 2, 15), date(2026, 2, 23)), # 春节
    (date(2026, 4, 4), date(2026, 4, 6)),   # 清明节
    (date(2026, 5, 1), date(2026, 5, 5)),   # 劳动节
    (date(2026, 6, 19), date(2026, 6, 21)), # 端午节
    (date(2026, 9, 25), date(2026, 9, 27)), # 中秋节
    (date(2026, 10, 1), date(2026, 10, 7)), # 国庆节
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
    logger.info('策略分析 API 服务启动')
    
    # 初始化创建表结构
    await init_db()
    
    # 设定定时任务：每 15 分钟执行一次
    scheduler.add_job(fetch_and_save_sectors_task, 'interval', minutes=15)
    scheduler.start()
    
    # 服务启动时，不等待15分钟，立即执行一次数据爬取
    asyncio.create_task(fetch_and_save_sectors_task())
    
    yield
    
    scheduler.shutdown()
    logger.info('策略分析 API 服务关闭')


app = FastAPI(title='基金策略分析 API', lifespan=lifespan)


@app.get(
    '/strategies',
    response_model=schemas.StrategyListResponse,
    summary='获取所有可用策略列表',
    tags=['Strategies']
)
def list_strategies():
    """返回所有已注册的策略名称。"""
    return schemas.StrategyListResponse(
        strategies=list(STRATEGY_REGISTRY.keys()),
        count=len(STRATEGY_REGISTRY)
    )


@app.get(
    '/strategies/{strategy_name}/{fund_code}',
    response_model=schemas.StrategySignal,
    summary='执行指定策略分析',
    tags=['Strategies']
)
def get_strategy_signal(
    strategy_name: str,
    fund_code: str,
    is_holding: Optional[bool] = Query(None, description='【可选】对于需要持仓状态的策略，指定当前是否持有该基金。')
):
    """
    根据指定的策略名称和基金代码，运行分析并返回交易信号。

    - **strategy_name**: 策略名称，支持：`rsi`, `macd`, `bollinger_bands`, `dual_confirmation`
    - **fund_code**: 要分析的基金代码（6位数字）
    - **is_holding**: (可选) 对于 `macd`、`bollinger_bands`、`dual_confirmation` 策略需要提供此参数 (`true`/`false`)
    """
    logger.info(f"策略分析请求: strategy='{strategy_name}', code='{fund_code}', is_holding={is_holding}")

    strategy_function = STRATEGY_REGISTRY.get(strategy_name)
    if not strategy_function:
        logger.warning(f"未找到策略: '{strategy_name}'")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"策略 '{strategy_name}' 不存在。可用策略: {list(STRATEGY_REGISTRY.keys())}"
        )

    try:
        sig = inspect.signature(strategy_function)
        params = {}

        if 'fund_code' in sig.parameters:
            params['fund_code'] = fund_code

        if 'is_holding' in sig.parameters:
            if is_holding is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"策略 '{strategy_name}' 需要 'is_holding' 查询参数 (true/false)。"
                )
            params['is_holding'] = is_holding

        result_dict = strategy_function(**params)

        if result_dict.get('error'):
            logger.error(f"策略 '{strategy_name}' 执行失败: {result_dict['error']}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=result_dict['error']
            )

        return schemas.StrategySignal(
            fund_code=fund_code,
            strategy_name=strategy_name,
            **result_dict
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"执行策略 '{strategy_name}' 时发生意外错误")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"执行策略时发生内部错误: {str(e)}"
        )


@app.get(
    '/health',
    response_model=schemas.HealthResponse,
    summary='健康检查',
    tags=['System']
)
def health_check():
    """服务健康检查端点。"""
    return schemas.HealthResponse(
        status='ok',
        timestamp=datetime.now().isoformat()
    )

@app.get(
    '/charts/rsi/{fund_code}',
    response_model=schemas.RsiChartResponse,
    summary='获取 RSI 策略图表数据',
    tags=['Charts']
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
            detail=f"无法获取基金 {fund_code} 的图表数据。"
        )
        
    return chart_data

@app.get(
    '/market/df_sectors',
    response_model=schemas.SectorListResponse,
    summary='获取行业板块数据(东方财富)',
    tags=['Market']
)
async def get_df_sector_list():
    """
    从数据库获取当日（最新可用）东方财富的行业板块数据。
    """
    sectors = await get_today_eastmoney_sectors()
    
    return schemas.SectorListResponse(
        count=len(sectors),
        sectors=[
            schemas.SectorInfo(
                name=s.name,
                market_cap=s.market_cap,
                market_cap_desc=s.market_cap_desc,
                turnover_rate=s.turnover_rate,
                turnover_rate_desc=s.turnover_rate_desc,
                change_percent=s.change_percent,
                change_percent_desc=s.change_percent_desc
            ) for s in sectors
        ]
    )

@app.get(
    '/market/ths_sectors',
    response_model=schemas.ThsSectorListResponse,
    summary='获取同花顺行业板块数据',
    tags=['Market']
)
async def get_ths_sector_list():
    """
    从数据库获取当日（最新可用）同花顺行业板块数据。
    """
    sectors = await get_today_ths_sectors()
    
    return schemas.ThsSectorListResponse(
        count=len(sectors),
        sectors=[
            schemas.ThsSectorInfo(
                name=s.name,
                change_percent=s.change_percent,
                net_inflow=s.net_inflow,
                up_count=s.up_count,
                down_count=s.down_count,
                turnover_ratio=s.turnover_ratio
            ) for s in sectors
        ]
    )

@app.get(
    '/market/sector_names',
    summary='获取两家数据源的板块名称列表',
    tags=['Market']
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
        "同花顺":[s.name for s in ths_sectors]
    }