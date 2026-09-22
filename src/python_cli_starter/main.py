# src/python_cli_starter/main.py
from fastapi import FastAPI, HTTPException, Query, status, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
import inspect
import re
from typing import Optional
import logging
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime

from .strategies import STRATEGY_REGISTRY
from . import schemas
from . import charts
from . import fund_fee
from . import fund_info
from . import fund_realtime
from . import sector_capital
from . import stock_realtime
from . import gold_realtime

# 日志配置
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


# --- 板块主力资金缓存：后台定时刷新 ---
_REFRESH_CHECK_INTERVAL = 60  # 每 60 秒检查一次是否需要刷新


async def _sector_capital_refresh_loop():
    """后台循环：每 60 秒检查板块资金缓存是否需要刷新。

    仅在刷新窗口（交易日 9:30-16:00）且距上次刷新满 10 分钟时触发；
    刷新失败保留旧缓存（仅记日志）。非交易时段/16:00 后不刷新（冻结）。
    """
    fs_types = list(sector_capital._FS_TYPE_MAP.keys())
    while True:
        try:
            now = datetime.now()
            for fs_type in fs_types:
                async with sector_capital._get_lock():
                    entry = sector_capital._cache_get(fs_type)
                    if sector_capital._should_refresh(
                        entry.get("updated_at") if entry else None, now
                    ):
                        await sector_capital._refresh_cache(fs_type, now)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"[SectorCapital] 后台刷新循环异常: {e}")
        await asyncio.sleep(_REFRESH_CHECK_INTERVAL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动预热缓存 + 后台刷新循环；关闭时取消。"""
    logger.info("策略分析 API 服务启动")
    try:
        await sector_capital.warmup_cache()
    except Exception as e:
        logger.error(f"[SectorCapital] 启动预热异常（不阻塞启动）: {e}")

    refresh_task = asyncio.create_task(_sector_capital_refresh_loop())
    yield
    refresh_task.cancel()
    try:
        await refresh_task
    except (asyncio.CancelledError, Exception):
        pass
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
    "/funds/{fund_code}/fee",
    response_model=schemas.FundFeeResponse,
    summary="获取基金手续费信息",
    tags=["Funds"],
)
def get_fund_fee_info(fund_code: str):
    """
    根据基金代码获取其手续费（费率）信息，供第三方调用。

    数据来源：天天基金网-基金档案-购买信息。

    返回内容包含 7 类费率信息：
    - **trade_status**: 交易状态（申购/赎回/定投状态等）
    - **purchase_redemption_amount**: 申购与赎回金额（起点/限额等）
    - **trade_confirm_days**: 交易确认日（买入/卖出确认日，如 T+1）
    - **operation_fees**: 运作费用（管理费率/托管费率/销售服务费率）
    - **subscription_fee_rate**: 认购费率（分档）
    - **purchase_fee_rate**: 申购费率（前端，分档，含原费率与天天基金优惠费率）
    - **redemption_fee_rate**: 赎回费率（按持有期限分档）

    注意：不同基金类型（混合/股票/债券/货币/ETF）可获取的字段差异较大，
    缺失的区块会返回空 dict 或空 list。
    """
    logger.info(f"基金手续费查询请求: code='{fund_code}'")

    try:
        fee_info = fund_fee.get_fund_fee(fund_code)

        if fee_info is None:
            logger.warning(f"未获取到基金 {fund_code} 的手续费信息")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"无法获取基金 {fund_code} 的手续费信息，请确认基金代码是否正确。",
            )

        return schemas.FundFeeResponse(fund_code=fund_code, **fee_info)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"获取基金 {fund_code} 手续费信息时发生意外错误")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取手续费信息时发生内部错误: {str(e)}",
        )


# 基金代码格式：6 位数字
FUND_CODE_PATTERN = re.compile(r"^\d{6}$")


@app.get(
    "/fund/info/{fundCode}",
    response_model=schemas.FundInfoResponse,
    summary="获取单只基金的完整信息",
    tags=["Fund"],
)
def get_fund_info_api(fundCode: str):
    """
    获取单只基金的完整信息（基本信息 + 历史净值 + 费率表）。

    供 Nuxt 端 `findOrCreateFund` 调用，一次拿全所有数据，写入
    `funds` + `navHistory` + 费率表。

    - **fundCode**: 6 位基金代码（path 参数）
    - **fundType**: `"open"` 或 `"qdii_lof"`，由本服务判断
    - **history**: 全量历史净值，按日期升序（最早→最新）
    - **fees**: 费率信息（仅前端展示用）

    错误响应：
    - `400`: 基金代码格式错误（非 6 位数字）
    - `404`: 基金代码不存在
    """
    logger.info(f"基金完整信息查询请求: code='{fundCode}'")

    # 1. 代码格式校验（6 位数字）
    if not FUND_CODE_PATTERN.match(fundCode):
        logger.warning(f"基金代码格式错误: '{fundCode}'")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"基金代码格式错误：'{fundCode}' 不是有效的 6 位数字代码。",
        )

    # 2. 获取完整信息
    try:
        info = fund_info.get_fund_info(fundCode)

        if info is None:
            logger.warning(f"未获取到基金 {fundCode} 的信息")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"无法获取基金 {fundCode} 的初始信息。",
            )

        return schemas.FundInfoResponse(**info)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"获取基金 {fundCode} 完整信息时发生意外错误")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取基金信息时发生内部错误: {str(e)}",
        )


@app.get(
    "/fund/realtime/{fundCode}",
    response_model=schemas.FundRealtimeEstimation,
    summary="获取基金实时估值（分钟级）",
    tags=["Fund"],
)
def get_fund_realtime_api(fundCode: str):
    """
    获取单只基金的盘中实时估算净值（交易时段内分钟级刷新）。

    数据来源：powercloud 聚合接口（已封装东财实时估算 + 历史净值回退 + QDII 处理）。

    - **estimateNav**: 估算单位净值（原值，非反算）
    - **estimateGrowthRate**: 估算涨跌幅（%）
    - **yesterdayNav**: 上一交易日单位净值
    - **quoteSource**: 数据来源标识（`realtime` / `history_fallback`）
    - **message**: 状态说明（如「QDII暂无盘中估值」）
    - **intraday**: 盘中分时数据（非交易时段为空数组）
    - **holdingsDate**: 重仓股持仓报告期（季报日期，如 `2026-06-30`）
    - **holdings**: 重仓股持仓明细（`{code, name, pct, price, change_pct, ...}`，
      `pct` 为占净值比例，如 `"17.28%"`；纯债/货币等无股票持仓为空数组）

    说明：
    - QDII / 货币型等无盘中估值的基金，会回退到最近净值，通过 `quoteSource`/`message` 标识。
    - `publishedNav` 在官方净值未确认时为 null。
    - `holdings` 为季报披露口径（占净值比例），存在季报滞后；行情字段为最新盘中值。

    错误响应：
    - `400`: 基金代码格式错误（非 6 位数字）
    - `404`: 基金不存在或数据源不可用
    """
    logger.info(f"基金实时估值查询请求: code='{fundCode}'")

    if not FUND_CODE_PATTERN.match(fundCode):
        logger.warning(f"基金代码格式错误: '{fundCode}'")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"基金代码格式错误：'{fundCode}' 不是有效的 6 位数字代码。",
        )

    try:
        result = fund_realtime.get_realtime_estimation(fundCode)

        if result is None:
            logger.warning(f"未获取到基金 {fundCode} 的实时估值")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"无法获取基金 {fundCode} 的盘中实时估值，"
                    f"该基金可能不在东方财富盘中估值列表（如 QDII/货币型/小众基金），"
                    f"或数据源暂时不可用。"
                ),
            )

        return schemas.FundRealtimeEstimation(**result)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"获取基金 {fundCode} 实时估值时发生意外错误")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取实时估值时发生内部错误: {str(e)}",
        )


@app.get(
    "/fund/nav/{fundCode}",
    response_model=schemas.FundYesterdayNav,
    summary="获取基金昨日真实净值",
    tags=["Fund"],
)
def get_fund_yesterday_nav_api(fundCode: str):
    """
    获取单只基金最近一个交易日的官方单位净值（昨日真实净值）。

    数据来源：akshare fund_open_fund_info_em 单位净值走势，取 tail(1)，
    与 `/fund/info/{fundCode}` 内部一致。

    - **nav**: 最新一日单位净值（4 位小数字符串）
    - **navDate**: 净值日期
    - **growthRate**: 日增长率（%）

    说明：本数据源不返回基金名称，name 字段为空字符串，需从
    `/fund/info/{fundCode}` 获取。

    错误响应：
    - `400`: 基金代码格式错误（非 6 位数字）
    - `404`: 基金代码不存在或无净值数据
    """
    logger.info(f"基金昨日净值查询请求: code='{fundCode}'")

    if not FUND_CODE_PATTERN.match(fundCode):
        logger.warning(f"基金代码格式错误: '{fundCode}'")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"基金代码格式错误：'{fundCode}' 不是有效的 6 位数字代码。",
        )

    try:
        result = fund_realtime.get_yesterday_nav(fundCode)

        if result is None:
            logger.warning(f"未获取到基金 {fundCode} 的昨日净值")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"无法获取基金 {fundCode} 的昨日净值，"
                    f"请确认基金代码是否正确。"
                ),
            )

        return schemas.FundYesterdayNav(**result)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"获取基金 {fundCode} 昨日净值时发生意外错误")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取昨日净值时发生内部错误: {str(e)}",
        )


@app.get(
    "/sector/capital",
    response_model=schemas.SectorCapitalListResponse,
    summary="获取板块主力资金数据表",
    tags=["SectorCapital"],
)
async def get_sector_capital(
    type: str = Query(
        "industry",
        description="板块类型：`industry`(行业板块,默认) / `concept`(概念板块)",
    ),
):
    """
    获取全量板块（行业/概念）的主力资金数据表，返回一张完整的「板块主力资金」表。

    数据来源：东方财富数据中心板块资金流向接口（实时查询，不落库）。

    返回字段（金额均为「亿元」字符串）：
    - **changePercent**: 涨幅（%）
    - **amount**: 成交额
    - **mainCapital**: 主力资金（主力净流入）
    - **retailCapital**: 散户资金（小单净流入）
    - **mainHidden**: 主力暗盘 = 主力资金 - 散户资金
    - **mainStrength**: 主力强度 = 主力暗盘 / 成交额 * 100（%）
    - **mainAction**: 主力行为（抢筹 / 建仓 / 洗盘 / 出货）

    错误响应：
    - `400`: 板块类型 type 非法（仅支持 industry / concept）
    - `5xx`: 数据源故障
    """
    logger.info(f"板块主力资金查询请求: type='{type}'")

    try:
        fs_type = sector_capital._normalize_fs_type(type)
    except ValueError as e:
        logger.warning(f"板块类型非法: type='{type}'")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    try:
        sectors = await sector_capital.get_sector_capital_flow(fs_type)
        if sectors is None:
            logger.warning(f"未获取到板块主力资金数据 type='{type}'")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="东方财富数据源暂时不可用，请稍后重试。",
            )

        return schemas.SectorCapitalListResponse(
            type=fs_type,
            count=len(sectors),
            sectors=[schemas.SectorCapitalItem(**s) for s in sectors],
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"获取板块主力资金数据时发生意外错误")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取板块主力资金数据时发生内部错误: {str(e)}",
        )


@app.get(
    "/sector/capital/action/{sector_name}",
    response_model=schemas.SectorCapitalActionResponse,
    summary="按板块名查询主力行为",
    tags=["SectorCapital"],
)
async def get_sector_capital_action(
    sector_name: str,
    type: str = Query(
        "industry",
        description="板块类型：`industry`(行业板块,默认) / `concept`(概念板块)",
    ),
):
    """
    通过板块名查询其主力行为。

    匹配规则：**精确匹配优先**；找不到则做子串模糊兜底（包含关系，双向），
    故 ``matched`` 可能大于 1（返回所有命中项）。

    错误响应：
    - `400`: 板块类型 type 非法，或板块名为空
    - `404`: 板块名无任何精确/模糊匹配
    - `5xx`: 数据源故障
    """
    logger.info(f"板块主力行为查询请求: name='{sector_name}', type='{type}'")

    if not sector_name or not sector_name.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="板块名不能为空。",
        )

    try:
        fs_type = sector_capital._normalize_fs_type(type)
    except ValueError as e:
        logger.warning(f"板块类型非法: type='{type}'")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    try:
        matched = await sector_capital.find_sector_action(sector_name, fs_type)
        if matched is None:
            logger.warning(f"板块 '{sector_name}' 无匹配 type='{type}'")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"未找到板块 '{sector_name}'（type={fs_type}）。"
                    f"可调用 /sector/capital 查看全部板块名。"
                ),
            )

        return schemas.SectorCapitalActionResponse(
            query=sector_name,
            type=fs_type,
            matched=len(matched),
            sectors=[schemas.SectorCapitalItem(**s) for s in matched],
        )
    except sector_capital.SectorCapitalUnavailable:
        logger.warning(f"数据源不可用: 板块 '{sector_name}' type='{type}'")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="东方财富数据源暂时不可用，请稍后重试。",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"查询板块 '{sector_name}' 主力行为时发生意外错误")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"查询主力行为时发生内部错误: {str(e)}",
        )


@app.get(
    "/stocks/realtime",
    response_model=schemas.StockRealtimeResponse,
    summary="批量获取股票实时行情",
    tags=["Stock"],
)
def get_stocks_realtime_api(
    codes: str = Query(
        ...,
        description="逗号分隔的股票代码（A股 6 位/港股 5 位），如 `600519,000858,00700`（上限 200 只）",
    ),
):
    """
    批量获取多只 A 股/港股股票的实时最新价与当日涨跌幅（供基金自算估值加权）。

    数据来源：腾讯行情批量接口，进程内 60 秒 TTL 缓存
    （按单只股票粒度，批量请求间自动去重）。港股代码为 5 位数字
    （powercloud 重仓口径，如 00700），行情时间仍为北京时间。

    - **stocks**: 成功获取的行情 `{code, name, price, changePct, date, time}`
      （停牌股 price/changePct 为 null，行情时间为北京时间）
    - **missing**: 不支持的市场（北交所/美股）、非法代码或拉取失败的代码，
      调用方按缺失权重剔除

    错误响应：
    - `400`: codes 参数缺失/为空、含非 5-6 位数字代码，或超过 200 只
    """
    logger.info(f"股票批量行情查询请求: codes='{codes[:200]}'")

    code_list = [c.strip() for c in str(codes).split(",") if c.strip()]
    if not code_list:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="codes 参数不能为空，格式如 codes=600519,000858。",
        )

    invalid = [c for c in code_list if not re.match(r"^\d{5,6}$", c)]
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"股票代码格式错误（A股 6 位/港股 5 位数字）：{invalid[:5]}",
        )

    if len(code_list) > stock_realtime._MAX_CODES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"单次最多查询 {stock_realtime._MAX_CODES} 只股票，当前 {len(code_list)} 只。",
        )

    try:
        result = stock_realtime.get_stocks_realtime(code_list)
        return schemas.StockRealtimeResponse(count=len(result["stocks"]), **result)
    except Exception as e:
        logger.exception("批量获取股票行情时发生意外错误")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取股票行情时发生内部错误: {str(e)}",
        )


@app.get(
    "/gold/realtime",
    response_model=schemas.GoldRealtimeResponse,
    summary="批量获取上海黄金交易所贵金属实时行情",
    tags=["Gold"],
)
def get_gold_realtime_api(
    codes: str = Query(
        ...,
        description="逗号分隔的贵金属代码，如 `AU9999`（支持 AU9999 沪金99 / AUTD 黄金延期，上限 10 个）",
    ),
):
    """
    批量获取上海黄金交易所贵金属实时最新价与当日涨跌幅（供黄金类基金自算估值）。

    数据来源：新浪财经贵金属行情（gds_ 接口，需 Referer），进程内 60 秒 TTL
    缓存。涨跌幅按 (最新价 - 昨收) / 昨收 计算；SGE 夜市（20:00-02:30）归属
    次一交易日，涨跌幅天然包含隔夜跳空，与黄金基金净值口径一致。

    - **quotes**: 成功获取的行情 `{code, name, price, prevClose, changePct,
      date, time}`（未开盘等场景 price/changePct 为 null）
    - **missing**: 不支持的代码或拉取失败的代码，调用方对黄金基金整体跳过

    错误响应：
    - `400`: codes 参数缺失/为空、含不支持的贵金属代码，或超过 10 个
    """
    logger.info(f"贵金属行情查询请求: codes='{codes[:100]}'")

    code_list = [c.strip() for c in str(codes).split(",") if c.strip()]
    if not code_list:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="codes 参数不能为空，格式如 codes=AU9999。",
        )

    invalid = [c for c in code_list if c.upper() not in gold_realtime._GDS_CODES]
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"不支持的贵金属代码（支持 {'/'.join(gold_realtime._GDS_CODES)}）：{invalid[:5]}",
        )

    if len(code_list) > gold_realtime._MAX_CODES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"单次最多查询 {gold_realtime._MAX_CODES} 个合约，当前 {len(code_list)} 个。",
        )

    try:
        result = gold_realtime.get_gold_realtime(code_list)
        return schemas.GoldRealtimeResponse(count=len(result["quotes"]), **result)
    except Exception as e:
        logger.exception("批量获取贵金属行情时发生意外错误")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取贵金属行情时发生内部错误: {str(e)}",
        )
