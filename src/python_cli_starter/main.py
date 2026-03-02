# src/python_cli_starter/main.py
from fastapi import FastAPI, HTTPException, Query, status
import inspect
from typing import Optional
from contextlib import asynccontextmanager
import logging
from datetime import datetime

from .strategies import STRATEGY_REGISTRY
from . import schemas
from . import charts
from . import market

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info('策略分析 API 服务启动')
    yield
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
    '/market/sectors',
    response_model=schemas.SectorListResponse,
    summary='获取行业板块数据',
    tags=['Market']
)
async def get_sector_list():
    """
    获取所有行业板块的实时数据 (转发自东方财富)。
    
    返回字段说明:
    - **name**: 板块名称
    - **market_cap**: 总市值 (原始数值)
    - **market_cap_desc**: 总市值 (格式化，单位：亿)
    - **turnover_rate**: 换手率 (原始数值)
    - **turnover_rate_desc**: 换手率 (格式化，百分比)
    """
    sectors = await market.fetch_eastmoney_sectors()
    
    if sectors is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="无法从上游数据源(东方财富)获取数据"
        )
        
    return schemas.SectorListResponse(
        count=len(sectors),
        sectors=sectors
    )

@app.get(
    '/market/ths_sectors',
    response_model=schemas.ThsSectorListResponse,
    summary='获取同花顺行业板块数据',
    tags=['Market']
)
async def get_ths_sector_list():
    """
    获取同花顺行业板块数据 (解析 HTML)。
    
    包含字段:
    - **name**: 板块名称
    - **change_percent**: 涨跌幅 (%)
    - **net_inflow**: 净流入 (亿元)
    - **up_count**: 上涨家数
    - **down_count**: 下跌家数
    - **turnover_ratio**: 成交额占比 (%)
    """
    sectors = await market.fetch_ths_sectors()
    
    return schemas.ThsSectorListResponse(
        count=len(sectors),
        sectors=sectors
    )