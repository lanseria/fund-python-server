# tests/test_fund_realtime.py
"""基金实时估值与昨日净值接口测试。

覆盖：
- GET /fund/realtime/{fundCode}
- GET /fund/nav/{fundCode}

实时估值数据源为 powercloud 聚合接口（``monitor.powercloud.work/api/fund/{code}``），
测试通过 mock ``_fetch_powercloud_estimation`` 注入伪造数据，不依赖网络。
昨日净值仍走 akshare ``fund_open_fund_info_em``，单独 mock。
"""
import csv
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import pandas as pd
from datetime import date
from fastapi.testclient import TestClient

from python_cli_starter.main import app
from python_cli_starter import fund_realtime

client = TestClient(app)


# 仓库根目录的基金代码清单（BOM 编码，须用 utf-8-sig 读取）
_FUNDS_CSV = Path(__file__).parent.parent / "test_funds.csv"


def _load_funds_from_csv():
    """读取 test_funds.csv，返回 ``[(code, name), ...]`` 列表。"""
    funds = []
    with _FUNDS_CSV.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            code = (row.get("代码") or "").strip()
            name = (row.get("名称") or "").strip()
            if code:
                funds.append((code, name))
    return funds


def _make_powercloud_payload(
    code: str,
    name: str,
    *,
    gsz: str = "1.0000",
    gszzl: str = "0.00",
    dwjz: str = "1.0000",
    jzrq: str = "2026-07-24",
    gztime: str = "2026-07-24",
    confirmed_nav: str = "",
    confirmed_change: str = "",
    quote_source: str = "realtime",
    message: str = "",
    success: bool = True,
    intraday=None,
) -> dict:
    """构造 ``_fetch_powercloud_estimation`` 的返回（已解析字典）。

    字段命名对齐 powercloud ``basic`` 的东财原始命名（gsz/gszzl/dwjz 等）。
    """
    return {
        "basic": {
            "code": code,
            "name": name,
            "gsz": gsz,
            "gszzl": gszzl,
            "dwjz": dwjz,
            "jzrq": jzrq,
            "gztime": gztime,
            "confirmed_nav": confirmed_nav,
            "confirmed_change": confirmed_change,
            "confirmed_date": jzrq if confirmed_nav else "",
            "quote_source": quote_source,
            "message": message,
            "success": success,
        },
        "intraday": list(intraday) if intraday else [],
    }


def _make_history_df():
    """构造 fund_open_fund_info_em 历史净值 DataFrame（取 tail(1)）。"""
    return pd.DataFrame(
        {
            "净值日期": [date(2026, 7, 17), date(2026, 7, 24)],
            "单位净值": [0.5320, 0.5582],
            "日增长率": [-1.92, 4.92],
        }
    )


# 参数化：CSV 中每一只基金（涵盖开放式/QDII/LOF 等多类型）
_CSV_FUNDS = _load_funds_from_csv()


class TestRealtimeAPI:
    """GET /fund/realtime/{fundCode} 端点测试（powercloud 数据源）"""

    def test_invalid_code_format_400(self):
        """测试代码格式错误返回 400"""
        for bad_code in ["12345", "1234567", "abc123", "abcdef"]:
            response = client.get(f"/fund/realtime/{bad_code}")
            assert response.status_code == 400, f"{bad_code} 应返回 400"
            assert "格式错误" in response.json()["detail"]

    @patch("python_cli_starter.fund_realtime._fetch_powercloud_estimation")
    def test_realtime_success(self, mock_pc):
        """测试成功获取实时估值，字段匹配契约"""
        mock_pc.return_value = _make_powercloud_payload(
            "161725", "招商中证白酒指数(LOF)A",
            gsz="0.5292", gszzl="-1.91", dwjz="0.5395",
            gztime="2026-07-27", jzrq="2026-07-24",
            quote_source="realtime", success=True,
        )

        response = client.get("/fund/realtime/161725")
        assert response.status_code == 200
        data = response.json()

        assert data["code"] == "161725"
        assert data["name"] == "招商中证白酒指数(LOF)A"
        assert data["estimateNav"] == "0.5292"
        assert data["estimateGrowthRate"] == -1.91
        assert data["estimateDate"] == "2026-07-27"
        assert data["yesterdayNav"] == "0.5395"
        assert data["yesterdayDate"] == "2026-07-24"
        # 状态标识透传
        assert data["quoteSource"] == "realtime"
        assert data["intraday"] == []

    @patch("python_cli_starter.fund_realtime._fetch_powercloud_estimation")
    def test_realtime_published_nav_present(self, mock_pc):
        """测试已确认官方净值场景（publishedNav 非 null）"""
        mock_pc.return_value = _make_powercloud_payload(
            "000001", "华夏成长混合",
            gsz="1.0234", gszzl="0.35", dwjz="1.0198",
            confirmed_nav="1.0198", confirmed_change="0.00",
            quote_source="realtime", success=True,
        )

        response = client.get("/fund/realtime/000001")
        assert response.status_code == 200
        data = response.json()
        assert data["publishedNav"] == "1.0198"
        assert data["estimateNav"] == "1.0234"

    @patch("python_cli_starter.fund_realtime._fetch_powercloud_estimation")
    def test_realtime_placeholder_to_none(self, mock_pc):
        """测试占位符（-）转 None"""
        mock_pc.return_value = _make_powercloud_payload(
            "161725", "招商中证白酒指数(LOF)A",
            gsz="-", gszzl="-", dwjz="0.5395",
            confirmed_nav="-", confirmed_change="-",
        )

        response = client.get("/fund/realtime/161725")
        assert response.status_code == 200
        data = response.json()
        assert data["estimateNav"] is None
        assert data["estimateGrowthRate"] is None
        assert data["publishedNav"] is None
        assert data["publishedGrowthRate"] is None
        assert data["yesterdayNav"] == "0.5395"  # 正常值不受影响

    @patch("python_cli_starter.fund_realtime._fetch_powercloud_estimation")
    def test_realtime_qdii_history_fallback(self, mock_pc):
        """测试 QDII 无盘中估值，回退历史净值（quoteSource/message 标识）"""
        mock_pc.return_value = _make_powercloud_payload(
            "513330", "华夏恒生互联网科技业ETF(QDII)",
            gsz="0.3772", gszzl="-2.28", dwjz="0.386",
            quote_source="history_fallback",
            message="QDII暂无盘中估值，展示最近净值 2026-07-24",
            success=False,
        )

        response = client.get("/fund/realtime/513330")
        assert response.status_code == 200
        data = response.json()
        assert data["quoteSource"] == "history_fallback"
        assert "QDII" in data["message"]
        assert data["estimateNav"] == "0.3772"  # 回退到最近净值

    @patch("python_cli_starter.fund_realtime._fetch_powercloud_estimation")
    def test_realtime_intraday_present(self, mock_pc):
        """测试盘中分时数据透传"""
        intraday = [
            {"time": "09:30", "value": 0.5395},
            {"time": "10:00", "value": 0.5410},
            {"time": "14:55", "value": 0.5292},
        ]
        mock_pc.return_value = _make_powercloud_payload(
            "161725", "招商中证白酒指数(LOF)A",
            gsz="0.5292", gszzl="-1.91", dwjz="0.5395",
            intraday=intraday,
        )

        response = client.get("/fund/realtime/161725")
        assert response.status_code == 200
        data = response.json()
        assert len(data["intraday"]) == 3
        assert data["intraday"][0] == {"time": "09:30", "value": 0.5395}

    @patch("python_cli_starter.fund_realtime._fetch_powercloud_estimation")
    def test_realtime_source_unavailable_404(self, mock_pc):
        """测试数据源返回 None 返回 404"""
        mock_pc.return_value = None

        response = client.get("/fund/realtime/161725")
        assert response.status_code == 404

    @patch("python_cli_starter.fund_realtime._fetch_powercloud_estimation")
    def test_realtime_fund_not_exist_404(self, mock_pc):
        """测试基金不存在（powercloud 返回 name 为空）返回 None → 404"""
        mock_pc.return_value = None

        response = client.get("/fund/realtime/999999")
        assert response.status_code == 404
        assert "999999" in response.json()["detail"]


class TestPowercloudParsing:
    """powercloud 接口解析单元测试"""

    @patch("python_cli_starter.fund_realtime.requests.get")
    def test_powercloud_raw_response_parsed(self, mock_get):
        """测试 powercloud 原始 JSON 被正确解析（端到端，含 requests mock）"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "basic": {
                "code": "161725", "name": "招商中证白酒指数(LOF)A",
                "gsz": "0.5292", "gszzl": "-1.91", "dwjz": "0.5395",
                "jzrq": "2026-07-24", "gztime": "2026-07-27",
                "confirmed_nav": "", "confirmed_change": "",
                "quote_source": "realtime", "message": "", "success": True,
            },
            "intraday": {"data": [{"time": "09:30", "value": 0.5395}], "success": True},
        }
        mock_get.return_value = mock_resp

        result = fund_realtime._fetch_powercloud_estimation("161725")
        assert result is not None
        assert result["basic"]["name"] == "招商中证白酒指数(LOF)A"
        assert result["basic"]["gsz"] == "0.5292"
        assert len(result["intraday"]) == 1

    @patch("python_cli_starter.fund_realtime.requests.get")
    def test_powercloud_empty_name_returns_none(self, mock_get):
        """测试 powercloud 返回 name 为空（基金不存在）返回 None"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "basic": {"code": "999999", "name": "999999", "gsz": "-", "success": False},
            "intraday": {"data": [], "success": False},
        }
        mock_get.return_value = mock_resp

        result = fund_realtime._fetch_powercloud_estimation("999999")
        # name == code（999999）视为不存在
        assert result is None

    @patch("python_cli_starter.fund_realtime.requests.get")
    def test_powercloud_non_200_returns_none(self, mock_get):
        """测试 powercloud 非 200 响应返回 None"""
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_get.return_value = mock_resp

        result = fund_realtime._fetch_powercloud_estimation("161725")
        assert result is None

    @patch("python_cli_starter.fund_realtime.requests.get")
    def test_powercloud_request_exception_returns_none(self, mock_get):
        """测试请求异常返回 None"""
        mock_get.side_effect = Exception("network error")

        result = fund_realtime._fetch_powercloud_estimation("161725")
        assert result is None


class TestCSVFundRealtime:
    """基于 test_funds.csv 的全量基金实时估值测试。

    以仓库根目录 ``test_funds.csv`` 作为权威数据源，参数化校验其中**每一只**
    基金（含开放式 / QDII / LOF 等多种类型）都能通过 powercloud 被查到实时估值，
    且返回字段符合契约。这是「所有基金都能得到估值」的回归保障。
    """

    @pytest.mark.parametrize("fund_code, fund_name", _CSV_FUNDS,
                             ids=[c for c, _ in _CSV_FUNDS])
    @patch("python_cli_starter.fund_realtime._fetch_powercloud_estimation")
    def test_csv_fund_realtime_queryable(self, mock_pc, fund_code, fund_name):
        """CSV 中每只基金都能通过 powercloud 查到实时估值，字段符合契约"""
        mock_pc.return_value = _make_powercloud_payload(
            fund_code, fund_name,
            gsz="1.0000", gszzl="0.00", dwjz="1.0000",
            gztime="2026-07-27", jzrq="2026-07-24",
            quote_source="realtime",
        )

        response = client.get(f"/fund/realtime/{fund_code}")
        assert response.status_code == 200, f"{fund_code} 应返回 200"
        data = response.json()

        assert data["code"] == fund_code.zfill(6)
        assert data["estimateNav"] == "1.0000"
        assert data["estimateDate"] == "2026-07-27"
        assert data["yesterdayNav"] == "1.0000"
        assert data["quoteSource"] == "realtime"


class TestYesterdayNavAPI:
    """GET /fund/nav/{fundCode} 端点测试（akshare 历史净值，未改动）"""

    def test_invalid_code_format_400(self):
        """测试代码格式错误返回 400"""
        for bad_code in ["12345", "1234567", "abc123", "abcdef"]:
            response = client.get(f"/fund/nav/{bad_code}")
            assert response.status_code == 400, f"{bad_code} 应返回 400"
            assert "格式错误" in response.json()["detail"]

    @patch("python_cli_starter.fund_realtime.ak.fund_open_fund_info_em")
    def test_nav_success(self, mock_history):
        """测试成功获取昨日净值，字段匹配契约"""
        mock_history.return_value = _make_history_df()

        response = client.get("/fund/nav/161725")
        assert response.status_code == 200
        data = response.json()

        assert data["code"] == "161725"
        assert data["name"] == ""
        # tail(1) = 2026-07-24 行
        assert data["nav"] == "0.5582"
        assert data["navDate"] == "2026-07-24"
        assert data["growthRate"] == 4.92

    @patch("python_cli_starter.fund_realtime.ak.fund_open_fund_info_em")
    def test_nav_empty_history_404(self, mock_history):
        """测试历史净值为空返回 404"""
        mock_history.return_value = pd.DataFrame()

        response = client.get("/fund/nav/161725")
        assert response.status_code == 404
        assert "161725" in response.json()["detail"]

    @patch("python_cli_starter.fund_realtime.ak.fund_open_fund_info_em")
    def test_nav_exception_404(self, mock_history):
        """测试数据源抛异常返回 404"""
        mock_history.side_effect = Exception("akshare error")

        response = client.get("/fund/nav/161725")
        assert response.status_code == 404

    @patch("python_cli_starter.fund_realtime.ak.fund_open_fund_info_em")
    def test_nav_none_return_404(self, mock_history):
        """测试数据源返回 None 返回 404"""
        mock_history.return_value = None

        response = client.get("/fund/nav/161725")
        assert response.status_code == 404


class TestFundRealtimeUnit:
    """fund_realtime 模块单元测试"""

    def test_to_float_handles_placeholders(self):
        """测试 _to_float 对占位符的处理"""
        assert fund_realtime._to_float("1.23%") == 1.23
        assert fund_realtime._to_float("-1.85%") == -1.85
        assert fund_realtime._to_float("0.5479") == 0.5479
        # powercloud 占位符
        assert fund_realtime._to_float("-") is None
        assert fund_realtime._to_float("---") is None
        assert fund_realtime._to_float("") is None
        assert fund_realtime._to_float(None) is None
        assert fund_realtime._to_float(float("nan")) is None

    def test_fmt_float(self):
        """测试数值格式化"""
        assert fund_realtime._fmt_float(0.5479) == "0.5479"
        assert fund_realtime._fmt_float(1.0) == "1.0000"
        assert fund_realtime._fmt_float(None) is None

    @patch("python_cli_starter.fund_realtime._fetch_powercloud_estimation")
    def test_get_realtime_estimation_returns_dict(self, mock_pc):
        """测试模块直接调用返回字典结构"""
        mock_pc.return_value = _make_powercloud_payload(
            "161725", "招商中证白酒指数(LOF)A",
            gsz="0.5292", gszzl="-1.91", dwjz="0.5395",
        )

        result = fund_realtime.get_realtime_estimation("161725")
        assert result is not None
        assert isinstance(result, dict)
        assert result["code"] == "161725"
        assert result["estimateNav"] == "0.5292"

    @patch("python_cli_starter.fund_realtime._fetch_powercloud_estimation")
    def test_get_realtime_estimation_not_found(self, mock_pc):
        """测试数据源返回 None 时返回 None"""
        mock_pc.return_value = None

        result = fund_realtime.get_realtime_estimation("999999")
        assert result is None

    @patch("python_cli_starter.fund_realtime.ak.fund_open_fund_info_em")
    def test_get_yesterday_nav_returns_dict(self, mock_history):
        """测试模块直接调用返回字典结构"""
        mock_history.return_value = _make_history_df()

        result = fund_realtime.get_yesterday_nav("161725")
        assert result is not None
        assert isinstance(result, dict)
        assert result["nav"] == "0.5582"
        assert result["navDate"] == "2026-07-24"
        assert result["growthRate"] == 4.92
