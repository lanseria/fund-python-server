# tests/test_sector_capital.py
"""板块主力资金数据接口测试。

覆盖：
- GET /sector/capital          —— 全量板块主力资金表
- GET /sector/capital/action/{sector_name} —— 按板块名查主力行为

数据源为东方财富 push2 clist 接口，测试通过 mock
``sector_capital._fetch_sector_capital`` 注入伪造原始字典，不依赖网络。
"""
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from python_cli_starter.main import app
from python_cli_starter import sector_capital

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


# ---------------------------------------------------------------------------
# 单元测试：行为分类 + 计算逻辑
# ---------------------------------------------------------------------------
class TestClassifyBehavior:
    """主力行为判定边界（>=3 抢筹 / [1,3) 建仓 / [-1,1) 洗盘 / <=-1 出货）。"""

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
# API 测试：GET /sector/capital
# ---------------------------------------------------------------------------
class TestSectorCapitalListAPI:
    """GET /sector/capital。"""

    def test_invalid_type_returns_400(self):
        r = client.get("/sector/capital?type=xxx")
        assert r.status_code == 400
        assert "无效的板块类型" in r.json()["detail"]

    @patch("python_cli_starter.sector_capital._fetch_sector_capital", new_callable=AsyncMock)
    def test_list_success(self, mock_fetch):
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

    @patch("python_cli_starter.sector_capital._fetch_sector_capital", new_callable=AsyncMock)
    def test_default_type_is_industry(self, mock_fetch):
        mock_fetch.return_value = []
        r = client.get("/sector/capital")
        assert r.status_code == 200
        assert r.json()["type"] == "industry"

    @patch("python_cli_starter.sector_capital._fetch_sector_capital", new_callable=AsyncMock)
    def test_concept_type(self, mock_fetch):
        mock_fetch.return_value = [_make_raw("光伏概念")]
        r = client.get("/sector/capital?type=concept")
        assert r.status_code == 200
        assert r.json()["type"] == "concept"

    @patch("python_cli_starter.sector_capital._fetch_sector_capital", new_callable=AsyncMock)
    def test_source_unavailable_502(self, mock_fetch):
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

    @patch("python_cli_starter.sector_capital._fetch_sector_capital", new_callable=AsyncMock)
    def test_exact_match(self, mock_fetch):
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

    @patch("python_cli_starter.sector_capital._fetch_sector_capital", new_callable=AsyncMock)
    def test_fuzzy_match(self, mock_fetch):
        """精确未命中时走子串模糊。"""
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

    @patch("python_cli_starter.sector_capital._fetch_sector_capital", new_callable=AsyncMock)
    def test_no_match_returns_404(self, mock_fetch):
        mock_fetch.return_value = [_make_raw("食品饮料")]
        r = client.get("/sector/capital/action/不存在的板块XYZ")
        assert r.status_code == 404

    @patch("python_cli_starter.sector_capital._fetch_sector_capital", new_callable=AsyncMock)
    def test_source_unavailable_502(self, mock_fetch):
        mock_fetch.return_value = None
        r = client.get("/sector/capital/action/食品")
        assert r.status_code == 502
