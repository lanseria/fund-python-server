# src/python_cli_starter/main.py (修改后)
from fastapi import FastAPI, HTTPException, Query, status, Query
import inspect
from typing import Optional
from contextlib import asynccontextmanager
import logging
from datetime import datetime

# 1. 导入新的日志配置和我们自己的模块
from .logger_config import setup_logging
from . import models, schemas, data_fetcher, services
from .strategies import STRATEGY_REGISTRY
from . import charts

# 2. 在应用启动前，最先配置日志
setup_logging()
logger = logging.getLogger(__name__)

# 在应用启动时创建数据库表
models.create_db_and_tables()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 在应用启动时执行的代码
    logger.info("FastAPI 应用启动...")
    # 移除启动后台调度器的代码
    
    yield # 这是应用运行的时间点
    
    # 在应用关闭时执行的代码
    logger.info("FastAPI 应用关闭...")
    # 移除停止后台调度器的代码

# 将FastAPI实例命名为 api_app，以示区分
api_app = FastAPI(title="基金投资助手 API", lifespan=lifespan)

# --- 3. 添加新的工具类路由 ---
@api_app.get(
    "/funds/{fund_code}/realtime", 
    response_model=schemas.FundRealtimeInfo, 
    summary="获取基金实时估值信息"
)
def get_fund_realtime(fund_code: str):
    """
    获取指定基金的实时估值数据。
    
    返回字段说明:
    - **yesterday_nav**: 昨日单位净值 (dwjz)
    - **estimate_nav**: 今日实时估值 (gsz)
    - **percentage_change**: 估算涨跌幅 (gszzl)
    - **update_time**: 估值更新时间 (gztime)
    """
    # 复用 data_fetcher 中高效的 HTTP 请求逻辑
    data = data_fetcher.fetch_fund_realtime_estimate(fund_code)
    
    if not data:
        raise HTTPException(status_code=404, detail=f"未找到基金 {fund_code} 的实时数据或代码无效。")
    
    try:
        # 数据类型转换与安全处理
        yesterday_nav = float(data.get('dwjz', 0))
        estimate_nav = float(data['gsz']) if data.get('gsz') else None
        percentage_change = float(data['gszzl']) if data.get('gszzl') else None
        
        update_time = None
        if data.get('gztime'):
            # 接口返回格式通常为 "2024-05-20 14:35"
            # 在 Python 3.11+ 中，fromisoformat 处理这种格式更加稳健
            try:
                update_time = datetime.strptime(data['gztime'], "%Y-%m-%d %H:%M")
            except ValueError:
                # 容错处理，防止时间格式微调导致崩溃
                logger.warning(f"时间格式解析失败: {data['gztime']}")
                pass

        return schemas.FundRealtimeInfo(
            fund_code=data.get('fundcode', fund_code),
            name=data.get('name', '未知'),
            yesterday_nav=yesterday_nav,
            estimate_nav=estimate_nav,
            percentage_change=percentage_change,
            update_time=update_time
        )
    except Exception as e:
        logger.error(f"解析基金 {fund_code} 实时数据时发生错误: {e}")
        raise HTTPException(status_code=500, detail=f"数据解析错误: {str(e)}")
@api_app.get(
    "/funds/{fund_code}/portfolio", 
    response_model=schemas.FundPortfolioResponse, 
    summary="获取基金股票持仓明细"
)
def get_fund_portfolio_endpoint(
    fund_code: str,
    year: str = Query(..., description="查询年份，例如 '2024'"),
):
    """
    获取指定基金在特定年份的股票投资组合（前十大重仓股等）。
    
    **数据来源**: 天天基金网 (通过 AkShare)
    
    **返回字段**:
    - **percentage**: 占净值比例 (%)
    - **share_holding**: 持股数 (万股)
    - **market_value**: 持仓市值 (万元)
    """
    try:
        holdings_data = services.get_fund_portfolio(fund_code, year)
        
        # 即使列表为空也返回成功的响应，只是 holdings 为空列表
        return schemas.FundPortfolioResponse(
            fund_code=fund_code,
            year=year,
            holdings=holdings_data
        )
    except ValueError as ve:
        # 服务层抛出的已知错误
        raise HTTPException(status_code=404, detail=str(ve))
    except Exception as e:
        logger.exception(f"API Error: get_fund_portfolio failed for {fund_code}")
        raise HTTPException(status_code=500, detail=f"服务器内部错误: {str(e)}")

@api_app.get(
    "/strategies/{strategy_name}/{fund_code}", 
    response_model=schemas.StrategySignal, 
    summary="获取基金策略信号"
)
def get_strategy_signal(
    strategy_name: str, 
    fund_code: str,
    is_holding: Optional[bool] = Query(None, description="【可选】对于需要持仓状态的策略，指定当前是否持有该基金。") # <-- 添加 is_holding 参数
):
    """
    根据指定的策略名称和基金代码，运行分析并返回交易信号。

    - **strategy_name**: 策略的简称 (例如: `rsi`, `bollinger_bands`)。
    - **fund_code**: 要分析的基金代码。
    - **is_holding**: (可选) 对于像布林带这样的策略，需要提供此参数 (`true`/`false`)。
    """
    logger.info(f"收到策略分析请求: strategy='{strategy_name}', code='{fund_code}', is_holding={is_holding}")

    strategy_function = STRATEGY_REGISTRY.get(strategy_name)
    if not strategy_function:
        logger.warning(f"请求了未知的策略: '{strategy_name}'")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"策略 '{strategy_name}' 不存在。可用策略: {list(STRATEGY_REGISTRY.keys())}"
        )

    try:
        # --- 智能参数传递 ---
        # 检查策略函数需要哪些参数
        sig = inspect.signature(strategy_function)
        params = {}
        
        # 必须提供 fund_code
        if 'fund_code' in sig.parameters:
            params['fund_code'] = fund_code
        
        # 如果策略需要 is_holding，则传递它
        if 'is_holding' in sig.parameters:
            if is_holding is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"策略 '{strategy_name}' 需要 'is_holding' (true/false) 查询参数。"
                )
            params['is_holding'] = is_holding

        # 执行策略函数，并传入构造好的参数
        result_dict = strategy_function(**params)
        
        # (后续错误处理和响应封装保持不变)
        if result_dict.get("error"):
            error_message = result_dict["error"]
            logger.error(f"策略 '{strategy_name}' 执行失败: {error_message}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=error_message
            )

        response_data = schemas.StrategySignal(
            fund_code=fund_code,
            strategy_name=strategy_name,
            **result_dict
        )
        return response_data

    except HTTPException as http_exc:
        # 重新抛出已知的HTTP异常
        raise http_exc
    except Exception as e:
        logger.exception(f"执行策略 '{strategy_name}' 时发生意外错误。")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"执行策略时发生内部错误: {str(e)}"
        )
    

@api_app.get(
    "/charts/rsi/{fund_code}",
    summary="获取RSI策略图表数据 (ECharts, 全部历史)",
    tags=["Charts"] # 使用 tags 对 API 进行分组
)
def get_rsi_chart_endpoint(fund_code: str):
    """
    获取指定基金的全部历史净值和RSI指标数据，
    返回格式适配 ECharts，用于绘制策略回测图。
    """
    logger.info(f"收到RSI图表数据请求 (全部历史): code='{fund_code}'")
    
    # 调用更新后的函数，不再传递 start_date
    chart_data = charts.get_rsi_chart_data(fund_code)
    
    if chart_data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"无法为基金 {fund_code} 生成图表数据，请检查代码或确认该基金有历史数据。"
        )
        
    return chart_data