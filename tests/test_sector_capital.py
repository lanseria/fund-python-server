# tests/test_sector_capital.py
"""板块主力资金数据接口测试。

覆盖：
- GET /sector/capital          —— 全量板块主力资金表
- GET /sector/capital/action/{sector_name} —— 按板块名查主力行为
- 缓存策略：_should_refresh 判定、缓存命中不重复拉取、冻结窗口、刷新失败保留旧缓存

数据源为东方财富 dataapi 接口，测试通过 mock
``sector_capital._fetch_sector_capital`` 注入伪造原始字典，不依赖网络。
"""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from python_cli_starter.main import app
from python_cli_starter import sector_capital

# 注意：TestClient(app) 不带 with 不会触发 lifespan/warmup，故模块级实例化安全
client = TestClient(app)


def _make_raw(
    name: str,
    *,
    code: str = "BK0000",
    f3: float = 100.0,      # 涨幅 dataapi 原始值（= 百分比 × 100，如 100 表示 1.00%）
    f6: float = 1e9,        # 成交额（元）= 10 亿
    f62: float = 2e8,       # 主力资金 = 2 亿
    f84: float = -5e7,      # 散户资金 = -0.5 亿
    f66: float = 0.0,
    f72: float = 0.0,
    f78: float = 0.0,
    f184: float = 0.0,
) -> dict:
    """构造单条东财原始字典。"""
    return {
        "f12": code,
        "f14": name,
        "f2": 1000.0,
        "f3": f3,
        "f6": f6,
        "f62": f62,
        "f66": f66,
        "f72": f72,
        "f78": f78,
        "f84": f84,
        "f184": f184,
    }


@pytest.fixture(autouse=True)
def _clear_cache():
    """每个测试前后清空模块级缓存，避免测试间相互污染。"""
    sector_capital._CACHE.clear()
    yield
    sector_capital._CACHE.clear()


# ---------------------------------------------------------------------------
# 单元测试：行为分类 + 计算逻辑
# ---------------------------------------------------------------------------
class TestClassifyBehavior:
    """主力行为判定边界（>=3 抢筹 / [1,3) 建仓 / (-1,1) 洗盘 / <=-1 出货）。"""

    @pytest.mark.parametrize(
        "strength,expected",
        [
            (5.0, "抢筹"),
            (3.0, "抢筹"),     # 边界：3 归抢筹
            (2.99, "建仓"),
            (1.0, "建仓"),     # 边界：1 归建仓
            (0.99, "洗盘"),
            (0.0, "洗盘"),
            (-0.99, "洗盘"),
            (-1.0, "出货"),    # 边界：-1 归出货
            (-1.01, "出货"),
            (-5.0, "出货"),
        ],
    )
    def test_boundary(self, strength, expected):
        assert sector_capital._classify_behavior(strength) == expected


class TestBuildItem:
    """原始字典 → 契约字段映射 + 计算。"""

    def test_calc_main_hidden_and_strength(self):
        """主力暗盘 = 主力 - 散户；主力强度 = 暗盘 / 成交额 * 100。"""
        # 主力 2 亿，散户 -0.5 亿 → 暗盘 2.5 亿；成交额 10 亿 → 强度 25%
        raw = _make_raw("测试板块", f6=1e9, f62=2e8, f84=-5e7)
        item = sector_capital._build_item(raw)

        assert item["name"] == "测试板块"
        assert item["code"] == "BK0000"
        assert item["mainCapital"] == "2.00 亿"
        assert item["retailCapital"] == "-0.50 亿"
        assert item["mainHidden"] == "2.50 亿"
        assert item["mainStrength"] == 25.0
        assert item["mainAction"] == "抢筹"

    def test_negative_strength_to_ship(self):
        """负向强度：主力 -1 亿，散户 1 亿，成交额 5 亿。"""
        # 暗盘 = -1 - 1 = -2 亿；强度 = -2/5*100 = -40 → 出货
        raw = _make_raw("出货板块", f6=5e8, f62=-1e8, f84=1e8)
        item = sector_capital._build_item(raw)
        assert item["mainHidden"] == "-2.00 亿"
        assert item["mainStrength"] == -40.0
        assert item["mainAction"] == "出货"

    def test_zero_amount_strength_is_zero(self):
        """成交额为 0 时强度记 0（不抛异常）。"""
        raw = _make_raw("零成交", f6=0.0, f62=1e8, f84=-1e8)
        item = sector_capital._build_item(raw)
        assert item["mainStrength"] == 0.0
        assert item["mainAction"] == "洗盘"

    def test_amount_desc_format(self):
        """成交额按亿元格式化。"""
        raw = _make_raw("X", f6=4.4e10)  # 440 亿
        item = sector_capital._build_item(raw)
        assert item["amount"] == "440.00 亿"

    def test_change_percent_divide_by_100(self):
        """dataapi f3 为乘 100 后的原始值（375 → 3.75%）。"""
        raw = _make_raw("X", f3=375)
        item = sector_capital._build_item(raw)
        assert item["changePercent"] == 3.75

        raw2 = _make_raw("Y", f3=-263)  # -2.63%
        item2 = sector_capital._build_item(raw2)
        assert item2["changePercent"] == -2.63


class TestNormalizeFsType:
    """板块类型规整 + 别名兼容。"""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("industry", "industry"),
            ("concept", "concept"),
            ("INDUSTRY", "industry"),
            ("行业", "industry"),
            ("概念板块", "concept"),
            ("2", "industry"),
            ("3", "concept"),
            (None, "industry"),
            ("", "industry"),  # 空串默认行业
        ],
    )
    def test_valid(self, raw, expected):
        assert sector_capital._normalize_fs_type(raw) == expected

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            sector_capital._normalize_fs_type("xxx")


# ---------------------------------------------------------------------------
# 单元测试：交易日 / 刷新窗口 / 刷新决策
# ---------------------------------------------------------------------------
# 固定一个交易日（周三）与非交易日（周六）用于时间相关测试
_TRADING_DAY = datetime(2026, 7, 29)  # 周三，非节假日
_SATURDAY = datetime(2026, 8, 1)      # 周六


class TestTradingTime:
    """交易日 / 刷新窗口判定。"""

    def test_weekend_not_trading_day(self):
        assert sector_capital._is_trading_day(_SATURDAY) is False

    def test_weekday_trading_day(self):
        assert sector_capital._is_trading_day(_TRADING_DAY) is True

    def test_holiday_not_trading_day(self):
        # 2026-10-01 国庆节
        assert sector_capital._is_trading_day(datetime(2026, 10, 2)) is False

    def test_in_refresh_window_trading_hours(self):
        # 交易日 10:30 处于刷新窗口
        dt = _TRADING_DAY.replace(hour=10, minute=30)
        assert sector_capital._in_refresh_window(dt) is True

    def test_outside_refresh_window_before_open(self):
        # 交易日 9:00 早于开盘
        dt = _TRADING_DAY.replace(hour=9, minute=0)
        assert sector_capital._in_refresh_window(dt) is False

    def test_outside_refresh_window_after_16(self):
        # 交易日 16:30 晚于 16:00（冻结）
        dt = _TRADING_DAY.replace(hour=16, minute=30)
        assert sector_capital._in_refresh_window(dt) is False

    def test_refresh_window_boundary_16(self):
        # 交易日 16:00 仍在窗口内（边界含）
        dt = _TRADING_DAY.replace(hour=16, minute=0)
        assert sector_capital._in_refresh_window(dt) is True

    def test_weekend_not_in_window_even_in_hours(self):
        # 周六 10:30 虽在时段内但非交易日
        dt = _SATURDAY.replace(hour=10, minute=30)
        assert sector_capital._in_refresh_window(dt) is False


class TestShouldRefresh:
    """刷新决策各分支。"""

    def test_empty_cache_should_refresh(self):
        """缓存为空 → 始终刷新（即便非交易时段，冷启动场景）。"""
        # 周六（非交易日）+ 缓存空 → 仍 True（预热）
        assert sector_capital._should_refresh(None, _SATURDAY.replace(hour=20)) is True

    def test_frozen_window_no_refresh(self):
        """有缓存 + 非刷新窗口（16:00 后）→ 不刷新（冻结）。"""
        updated = _TRADING_DAY.replace(hour=15, minute=50)
        now = _TRADING_DAY.replace(hour=16, minute=30)  # 16:30 已冻结
        assert sector_capital._should_refresh(updated, now) is False

    def test_frozen_next_day_morning_no_refresh(self):
        """次日开盘前（9:00）仍冻结，用昨日缓存。"""
        updated = _TRADING_DAY.replace(hour=15, minute=50)
        now = (_TRADING_DAY + timedelta(days=1)).replace(hour=9, minute=0)
        assert sector_capital._should_refresh(updated, now) is False

    def test_within_interval_no_refresh(self):
        """刷新窗口内但未满 10 分钟 → 不刷新。"""
        updated = _TRADING_DAY.replace(hour=10, minute=0)
        now = _TRADING_DAY.replace(hour=10, minute=5)  # 仅过 5 分钟
        assert sector_capital._should_refresh(updated, now) is False

    def test_interval_reached_should_refresh(self):
        """刷新窗口内满 10 分钟 → 刷新。"""
        updated = _TRADING_DAY.replace(hour=10, minute=0)
        now = _TRADING_DAY.replace(hour=10, minute=10)  # 恰好 10 分钟
        assert sector_capital._should_refresh(updated, now) is True

    def test_weekend_with_cache_no_refresh(self):
        """周末有缓存 → 不刷新（冻结到周一）。"""
        updated = _TRADING_DAY.replace(hour=15, minute=0)  # 周五缓存
        now = _SATURDAY.replace(hour=10, minute=30)        # 周六
        assert sector_capital._should_refresh(updated, now) is False


# ---------------------------------------------------------------------------
# 单元测试：缓存读写行为
# ---------------------------------------------------------------------------
class TestCacheBehavior:
    """缓存命中 / 不重复拉取 / 刷新失败保留旧缓存。"""

    @pytest.mark.asyncio
    @patch("python_cli_starter.sector_capital._fetch_sector_capital", new_callable=AsyncMock)
    async def test_cache_hit_no_duplicate_fetch(self, mock_fetch):
        """连续两次读缓存：冷启动拉一次，第二次命中缓存不再拉。"""
        mock_fetch.return_value = [_make_raw("板块A")]
        # 用 patch datetime.now 让两次都在「冷启动」判定下首次拉取，
        # 但第二次因 updated_at 已设且未满 10 分钟（同窗口）→ 命中缓存
        with patch("python_cli_starter.sector_capital.datetime") as mock_dt:
            # 模拟交易日 10:00（刷新窗口内）
            t = _TRADING_DAY.replace(hour=10, minute=0)
            mock_dt.now.return_value = t
            r1 = await sector_capital.get_sector_capital_flow("industry")
            assert r1 is not None and r1[0]["name"] == "板块A"
            assert mock_fetch.call_count == 1

            # 10:05 再次读，未满 10 分钟 → 命中缓存，不拉取
            mock_dt.now.return_value = _TRADING_DAY.replace(hour=10, minute=5)
            r2 = await sector_capital.get_sector_capital_flow("industry")
            assert mock_fetch.call_count == 1  # 仍是 1 次
            assert r2 == r1

    @pytest.mark.asyncio
    @patch("python_cli_starter.sector_capital._fetch_sector_capital", new_callable=AsyncMock)
    async def test_refresh_failure_keeps_old_cache(self, mock_fetch):
        """刷新窗口满 10 分钟再次拉取失败 → 保留旧缓存，返回旧数据。"""
        mock_fetch.return_value = [_make_raw("旧板块")]
        with patch("python_cli_starter.sector_capital.datetime") as mock_dt:
            mock_dt.now.return_value = _TRADING_DAY.replace(hour=10, minute=0)
            await sector_capital.get_sector_capital_flow("industry")

            # 10:15 再次读（满 10 分钟），但数据源这次失败
            mock_fetch.return_value = None
            mock_dt.now.return_value = _TRADING_DAY.replace(hour=10, minute=15)
            r = await sector_capital.get_sector_capital_flow("industry")
            assert r is not None
            assert r[0]["name"] == "旧板块"  # 仍是旧缓存数据

    @pytest.mark.asyncio
    @patch("python_cli_starter.sector_capital._fetch_sector_capital", new_callable=AsyncMock)
    async def test_frozen_window_keeps_old_cache(self, mock_fetch):
        """16:00 后冻结：即便很久没更新也不拉取，返回冻结缓存。"""
        mock_fetch.return_value = [_make_raw("冻结板块")]
        with patch("python_cli_starter.sector_capital.datetime") as mock_dt:
            # 15:50 拉一次
            mock_dt.now.return_value = _TRADING_DAY.replace(hour=15, minute=50)
            await sector_capital.get_sector_capital_flow("industry")
            assert mock_fetch.call_count == 1

            # 次日 9:00（开盘前，冻结窗口）再读 → 不拉取
            mock_dt.now.return_value = (_TRADING_DAY + timedelta(days=1)).replace(hour=9, minute=0)
            r = await sector_capital.get_sector_capital_flow("industry")
            assert mock_fetch.call_count == 1  # 未增加
            assert r[0]["name"] == "冻结板块"

    @pytest.mark.asyncio
    @patch("python_cli_starter.sector_capital._fetch_sector_capital", new_callable=AsyncMock)
    async def test_empty_cache_and_fetch_fail_returns_none(self, mock_fetch):
        """冷启动拉取失败且无旧缓存 → 返回 None（接口映射 502）。"""
        mock_fetch.return_value = None
        r = await sector_capital.get_sector_capital_flow("industry")
        assert r is None

    @pytest.mark.asyncio
    @patch("python_cli_starter.sector_capital._fetch_sector_capital", new_callable=AsyncMock)
    async def test_warmup_both_fs_types(self, mock_fetch):
        """warmup_cache 对 industry / concept 各拉一次。"""
        mock_fetch.return_value = [_make_raw("X")]
        await sector_capital.warmup_cache()
        assert mock_fetch.call_count == 2
        assert "industry" in sector_capital._CACHE
        assert "concept" in sector_capital._CACHE


# ---------------------------------------------------------------------------
# API 测试：GET /sector/capital
# ---------------------------------------------------------------------------
class TestSectorCapitalListAPI:
    """GET /sector/capital。"""

    def test_invalid_type_returns_400(self):
        r = client.get("/sector/capital?type=xxx")
        assert r.status_code == 400
        assert "无效的板块类型" in r.json()["detail"]

    @patch("python_cli_starter.sector_capital.datetime")
    @patch("python_cli_starter.sector_capital._fetch_sector_capital", new_callable=AsyncMock)
    def test_list_success(self, mock_fetch, mock_dt):
        mock_dt.now.return_value = _TRADING_DAY.replace(hour=10, minute=0)
        mock_fetch.return_value = [
            _make_raw("食品饮料", code="BK0438", f3=375, f6=4.4e10, f62=1.78e9, f84=-6.9e7),
            _make_raw("被动元件", code="BK1339", f3=211, f6=3.12e10, f62=2.59e9, f84=-1.23e9),
        ]
        r = client.get("/sector/capital?type=industry")
        assert r.status_code == 200
        body = r.json()
        assert body["type"] == "industry"
        assert body["count"] == 2
        first = body["sectors"][0]
        assert first["name"] == "食品饮料"
        assert first["mainAction"] in ("抢筹", "建仓", "洗盘", "出货")
        # 校验字段齐全
        for key in ("name", "code", "changePercent", "amount", "mainCapital",
                    "retailCapital", "mainHidden", "mainStrength", "mainAction"):
            assert key in first

    @patch("python_cli_starter.sector_capital.datetime")
    @patch("python_cli_starter.sector_capital._fetch_sector_capital", new_callable=AsyncMock)
    def test_default_type_is_industry(self, mock_fetch, mock_dt):
        mock_dt.now.return_value = _TRADING_DAY.replace(hour=10, minute=0)
        mock_fetch.return_value = []
        r = client.get("/sector/capital")
        assert r.status_code == 200
        assert r.json()["type"] == "industry"

    @patch("python_cli_starter.sector_capital.datetime")
    @patch("python_cli_starter.sector_capital._fetch_sector_capital", new_callable=AsyncMock)
    def test_concept_type(self, mock_fetch, mock_dt):
        mock_dt.now.return_value = _TRADING_DAY.replace(hour=10, minute=0)
        mock_fetch.return_value = [_make_raw("光伏概念")]
        r = client.get("/sector/capital?type=concept")
        assert r.status_code == 200
        assert r.json()["type"] == "concept"

    @patch("python_cli_starter.sector_capital.datetime")
    @patch("python_cli_starter.sector_capital._fetch_sector_capital", new_callable=AsyncMock)
    def test_source_unavailable_502(self, mock_fetch, mock_dt):
        # 冷启动 + 数据源失败 → None → 502
        mock_dt.now.return_value = _TRADING_DAY.replace(hour=10, minute=0)
        mock_fetch.return_value = None
        r = client.get("/sector/capital")
        assert r.status_code == 502


# ---------------------------------------------------------------------------
# API 测试：GET /sector/capital/action/{sector_name}
# ---------------------------------------------------------------------------
class TestSectorCapitalActionAPI:
    """GET /sector/capital/action/{sector_name}。"""

    def test_empty_name_returns_400(self):
        r = client.get("/sector/capital/action/ ")
        assert r.status_code == 400
        assert "不能为空" in r.json()["detail"]

    def test_invalid_type_returns_400(self):
        r = client.get("/sector/capital/action/食品?type=bad")
        assert r.status_code == 400

    @patch("python_cli_starter.sector_capital.datetime")
    @patch("python_cli_starter.sector_capital._fetch_sector_capital", new_callable=AsyncMock)
    def test_exact_match(self, mock_fetch, mock_dt):
        mock_dt.now.return_value = _TRADING_DAY.replace(hour=10, minute=0)
        mock_fetch.return_value = [
            _make_raw("食品饮料", code="BK0438"),
            _make_raw("电池", code="BK1015"),
        ]
        r = client.get("/sector/capital/action/食品饮料")
        assert r.status_code == 200
        body = r.json()
        assert body["query"] == "食品饮料"
        assert body["matched"] == 1
        assert body["sectors"][0]["code"] == "BK0438"

    @patch("python_cli_starter.sector_capital.datetime")
    @patch("python_cli_starter.sector_capital._fetch_sector_capital", new_callable=AsyncMock)
    def test_fuzzy_match(self, mock_fetch, mock_dt):
        """精确未命中时走子串模糊。"""
        mock_dt.now.return_value = _TRADING_DAY.replace(hour=10, minute=0)
        mock_fetch.return_value = [
            _make_raw("食品饮料"),
            _make_raw("白酒"),
            _make_raw("饮料乳品"),
        ]
        r = client.get("/sector/capital/action/饮料")
        assert r.status_code == 200
        body = r.json()
        assert body["matched"] == 2  # 食品饮料 + 饮料乳品
        names = [s["name"] for s in body["sectors"]]
        assert "食品饮料" in names
        assert "饮料乳品" in names

    @patch("python_cli_starter.sector_capital.datetime")
    @patch("python_cli_starter.sector_capital._fetch_sector_capital", new_callable=AsyncMock)
    def test_no_match_returns_404(self, mock_fetch, mock_dt):
        mock_dt.now.return_value = _TRADING_DAY.replace(hour=10, minute=0)
        mock_fetch.return_value = [_make_raw("食品饮料")]
        r = client.get("/sector/capital/action/不存在的板块XYZ")
        assert r.status_code == 404

    @patch("python_cli_starter.sector_capital.datetime")
    @patch("python_cli_starter.sector_capital._fetch_sector_capital", new_callable=AsyncMock)
    def test_source_unavailable_502(self, mock_fetch, mock_dt):
        mock_dt.now.return_value = _TRADING_DAY.replace(hour=10, minute=0)
        mock_fetch.return_value = None
        r = client.get("/sector/capital/action/食品")
        assert r.status_code == 502
