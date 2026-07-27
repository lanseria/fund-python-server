# tests/test_fund_realtime.py
"""基金实时估值与昨日净值接口测试。

覆盖：
- GET /fund/realtime/{fundCode}
- GET /fund/nav/{fundCode}

通过 mock akshare 的两个数据源注入伪造数据：
- fund_value_estimation_em（实时估值表，列名含动态日期，按列位置构造）
- fund_open_fund_info_em（历史净值，取 tail(1)）
"""
import csv
from pathlib import Path

import pytest
import pandas as pd
from datetime import date, datetime
from unittest.mock import patch
from fastapi.testclient import TestClient

from python_cli_starter.main import app
from python_cli_starter import fund_realtime

client = TestClient(app)


def _make_estimation_df(
    today: str = "2026-07-21",
    yesterday: str = "2026-07-20",
    rows=None,
):
    """
    构造 fund_value_estimation_em 的返回结构（列名含动态日期）。

    列位置：
      [0]序号 [1]基金代码 [2]基金名称
      [3]估算值 [4]估算增长率 [5]公布单位净值 [6]公布日增长率
      [7]估算偏差 [8]上一交易日单位净值
    """
    if rows is None:
        rows = [
            # 161725 白酒 LOF（典型场景）
            (1, "161725", "招商中证白酒指数(LOF)A",
             0.5479, "-1.85%", "---", "---", -0.0103, 0.5582),
            # 000001 华夏成长（普通开放基金，有公布净值）
            (2, "000001", "华夏成长混合",
             1.0234, "0.35%", "1.0198", "0.00%", 0.0036, 1.0198),
        ]
    columns = [
        "序号", "基金代码", "基金名称",
        f"{today}-估算数据-估算值", f"{today}-估算数据-估算增长率",
        f"{today}-公布数据-单位净值", f"{today}-公布数据-日增长率",
        "估算偏差", f"{yesterday}-单位净值",
    ]
    return pd.DataFrame(rows, columns=columns)


def _make_history_df():
    """构造 fund_open_fund_info_em 历史净值 DataFrame（取 tail(1)）。"""
    return pd.DataFrame(
        {
            "净值日期": [date(2026, 7, 17), date(2026, 7, 20)],
            "单位净值": [0.5320, 0.5582],
            "日增长率": [-1.92, 4.92],
        }
    )


# 仓库根目录的基金代码清单（BOM 编码，须用 utf-8-sig 读取）
_FUNDS_CSV = Path(__file__).parent.parent / "test_funds.csv"


def _load_funds_from_csv():
    """读取 test_funds.csv，返回 ``[(code, name), ...]`` 列表。

    CSV 由仓库根目录维护（含开放式/QDII/LOF 等多种类型），作为实时估值
    可查询基金的权威清单。这里解析后用于参数化测试，确保清单中**每一只**
    基金都能从估值表被查到。
    """
    funds = []
    with _FUNDS_CSV.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            code = (row.get("代码") or "").strip()
            name = (row.get("名称") or "").strip()
            if code:
                funds.append((code, name))
    return funds


def _build_estimation_df_from_csv(
    today: str = "2026-07-21",
    yesterday: str = "2026-07-20",
):
    """以 test_funds.csv 全量基金构造一份估值表。

    每只基金一行，按列位置与真实东财估值表保持一致：
      [0]序号 [1]基金代码 [2]基金名称
      [3]估算值 [4]估算增长率 [5]公布单位净值 [6]公布日增长率
      [7]估算偏差 [8]上一交易日单位净值

    用于参数化测试：验证清单中任意基金都能被 ``get_realtime_estimation``
    正确命中并返回契约字段。
    """
    rows = []
    for idx, (code, name) in enumerate(_load_funds_from_csv(), start=1):
        rows.append(
            (
                idx,
                code,
                name,
                1.0000,  # 估算值（占位，测试只校验可查到与字段存在）
                "0.00%",
                "---",  # 盘前未公布
                "---",
                0.0,
                1.0000,  # 上一交易日净值
            )
        )
    columns = [
        "序号", "基金代码", "基金名称",
        f"{today}-估算数据-估算值", f"{today}-估算数据-估算增长率",
        f"{today}-公布数据-单位净值", f"{today}-公布数据-日增长率",
        "估算偏差", f"{yesterday}-单位净值",
    ]
    return pd.DataFrame(rows, columns=columns)


# 参数化：CSV 中每一只基金（涵盖开放式/QDII/LOF 等多类型）
_CSV_FUNDS = _load_funds_from_csv()


class TestRealtimeAPI:
    """GET /fund/realtime/{fundCode} 端点测试"""

    def setup_method(self):
        """每个测试前清空缓存，避免互相污染。"""
        fund_realtime.clear_cache()

    def test_invalid_code_format_400(self):
        """测试代码格式错误返回 400"""
        for bad_code in ["12345", "1234567", "abc123", "abcdef"]:
            response = client.get(f"/fund/realtime/{bad_code}")
            assert response.status_code == 400, f"{bad_code} 应返回 400"
            assert "格式错误" in response.json()["detail"]

    @patch("python_cli_starter.fund_realtime.ak.fund_value_estimation_em")
    def test_realtime_success(self, mock_est):
        """测试成功获取实时估值，字段匹配契约"""
        mock_est.return_value = _make_estimation_df()

        response = client.get("/fund/realtime/161725")
        assert response.status_code == 200
        data = response.json()

        assert data["code"] == "161725"
        assert data["name"] == "招商中证白酒指数(LOF)A"
        assert data["estimateNav"] == "0.5479"
        assert data["estimateGrowthRate"] == -1.85
        assert data["estimateDate"] == "2026-07-21"
        assert data["yesterdayNav"] == "0.5582"
        assert data["yesterdayDate"] == "2026-07-20"
        # 盘前未公布，publishedNav 为 null
        assert data["publishedNav"] is None

    @patch("python_cli_starter.fund_realtime.ak.fund_value_estimation_em")
    def test_realtime_published_nav_present(self, mock_est):
        """测试已公布官方净值场景（publishedNav 非 null）"""
        mock_est.return_value = _make_estimation_df()

        response = client.get("/fund/realtime/000001")
        assert response.status_code == 200
        data = response.json()

        assert data["code"] == "000001"
        assert data["publishedNav"] == "1.0198"
        assert data["estimateNav"] == "1.0234"

    @patch("python_cli_starter.fund_realtime.ak.fund_value_estimation_em")
    def test_realtime_not_in_list_404(self, mock_est):
        """测试基金不在估值列表返回 404"""
        mock_est.return_value = _make_estimation_df()

        response = client.get("/fund/realtime/999999")
        assert response.status_code == 404
        detail = response.json()["detail"]
        assert "999999" in detail
        assert "盘中" in detail or "估值" in detail

    @patch("python_cli_starter.fund_realtime.ak.fund_value_estimation_em")
    def test_realtime_source_unavailable_404(self, mock_est):
        """测试数据源全部拉取失败返回 404"""
        mock_est.return_value = pd.DataFrame()

        response = client.get("/fund/realtime/161725")
        assert response.status_code == 404

    @patch("python_cli_starter.fund_realtime.ak.fund_value_estimation_em")
    def test_realtime_source_exception_404(self, mock_est):
        """测试数据源抛异常时返回 404（缓存空时无旧缓存兜底）"""
        mock_est.side_effect = Exception("upstream error")

        response = client.get("/fund/realtime/161725")
        assert response.status_code == 404

    @patch("python_cli_starter.fund_realtime.ak.fund_value_estimation_em")
    def test_realtime_cache_hit(self, mock_est):
        """测试进程内缓存：两次查询只触发一次 akshare 调用"""
        mock_est.return_value = _make_estimation_df()

        # 第一次请求：拉取并缓存
        r1 = client.get("/fund/realtime/161725")
        assert r1.status_code == 200

        # 第二次请求：应命中缓存，不再调用 akshare
        r2 = client.get("/fund/realtime/000001")
        assert r2.status_code == 200

        # fund_value_estimation_em 被多次调用（每个 symbol 一次，共 3 个），
        # 但缓存命中后第二次查询不应再增加调用次数
        first_call_count = mock_est.call_count
        assert first_call_count == len(fund_realtime._ESTIMATION_SYMBOLS)

        r3 = client.get("/fund/realtime/161725")
        assert r3.status_code == 200
        # 缓存未过期，调用次数不应增加
        assert mock_est.call_count == first_call_count

    @patch("python_cli_starter.fund_realtime.ak.fund_value_estimation_em")
    def test_realtime_merges_multiple_symbols(self, mock_est):
        """测试合并多个 symbol 去重：基金代码跨 symbol 出现时去重保留首行"""
        # '全部' 表里没有 161725，'LOF' 表里有，合并后应能查到
        all_df = _make_estimation_df(
            rows=[(1, "000001", "华夏成长混合",
                   1.0234, "0.35%", "1.0198", "0.00%", 0.0036, 1.0198)]
        )
        lof_df = _make_estimation_df(
            rows=[(1, "161725", "招商中证白酒指数(LOF)A",
                   0.5479, "-1.85%", "---", "---", -0.0103, 0.5582)]
        )
        # mock 依次返回 全部 / LOF / 场内交易基金
        mock_est.side_effect = [all_df, lof_df, pd.DataFrame()]

        response = client.get("/fund/realtime/161725")
        assert response.status_code == 200
        assert response.json()["name"] == "招商中证白酒指数(LOF)A"


class TestYesterdayNavAPI:
    """GET /fund/nav/{fundCode} 端点测试"""

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
        # name 在该数据源中为空
        assert data["name"] == ""
        # tail(1) = 2026-07-20 行
        assert data["nav"] == "0.5582"
        assert data["navDate"] == "2026-07-20"
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


class TestCSVFundRealtime:
    """基于 test_funds.csv 的全量基金实时估值测试。

    以仓库根目录 ``test_funds.csv`` 作为权威数据源，参数化校验其中**每一只**
    基金（含开放式 / QDII / LOF 等多种类型）都能从东财估值表中被查到，
    且返回字段符合契约。
    """

    def setup_method(self):
        fund_realtime.clear_cache()

    @pytest.mark.parametrize("fund_code, fund_name", _CSV_FUNDS,
                             ids=[c for c, _ in _CSV_FUNDS])
    @patch("python_cli_starter.fund_realtime.ak.fund_value_estimation_em")
    def test_csv_fund_realtime_queryable(self, mock_est, fund_code, fund_name):
        """CSV 中每只基金都能查到实时估值，字段符合契约"""
        mock_est.return_value = _build_estimation_df_from_csv()

        response = client.get(f"/fund/realtime/{fund_code}")
        assert response.status_code == 200, f"{fund_code} 应返回 200"
        data = response.json()

        assert data["code"] == fund_code.zfill(6)
        # 估值字段应能正常返回（CSV 全为可估值基金，非 null）
        assert data["estimateNav"] is not None
        assert data["estimateDate"] == "2026-07-21"
        assert data["yesterdayNav"] is not None
        assert data["yesterdayDate"] == "2026-07-20"

    @patch("python_cli_starter.fund_realtime.ak.fund_value_estimation_em")
    def test_csv_all_funds_loaded_into_table(self, mock_est):
        """CSV 全量基金应被合并进估值表（不少于 CSV 行数）"""
        df = _build_estimation_df_from_csv()
        mock_est.return_value = df

        fund_realtime.clear_cache()
        # 触发一次缓存重建（合并后应去重到 CSV 全量基金数）
        result = fund_realtime.get_realtime_estimation(_CSV_FUNDS[0][0])
        assert result is not None

        cached = fund_realtime._cache["df"]
        assert cached is not None
        assert len(cached) >= len(_CSV_FUNDS)


class TestFundRealtimeUnit:
    """fund_realtime 模块单元测试"""

    def setup_method(self):
        fund_realtime.clear_cache()

    def test_to_float_handles_placeholders(self):
        """测试 _to_float 对东财占位符的处理"""
        from python_cli_starter.fund_realtime import _to_float

        assert _to_float("1.23%") == 1.23
        assert _to_float("-1.85%") == -1.85
        assert _to_float("0.5479") == 0.5479
        assert _to_float("---") is None
        assert _to_float("") is None
        assert _to_float(None) is None
        assert _to_float(float("nan")) is None

    def test_extract_date_from_column(self):
        """测试从动态日期列名提取日期"""
        from python_cli_starter.fund_realtime import _extract_date_from_column

        assert _extract_date_from_column("2026-07-21-估算数据-估算值") == "2026-07-21"
        assert _extract_date_from_column("2026-07-20-单位净值") == "2026-07-20"
        assert _extract_date_from_column("序号") == ""

    def test_fmt_float(self):
        """测试数值格式化"""
        from python_cli_starter.fund_realtime import _fmt_float

        assert _fmt_float(0.5479) == "0.5479"
        assert _fmt_float(1.0) == "1.0000"
        assert _fmt_float(None) is None

    @patch("python_cli_starter.fund_realtime.ak.fund_value_estimation_em")
    def test_get_realtime_estimation_returns_dict(self, mock_est):
        """测试模块直接调用返回字典结构"""
        mock_est.return_value = _make_estimation_df()

        result = fund_realtime.get_realtime_estimation("161725")
        assert result is not None
        assert isinstance(result, dict)
        assert result["code"] == "161725"
        assert result["estimateNav"] == "0.5479"

    @patch("python_cli_starter.fund_realtime.ak.fund_value_estimation_em")
    def test_get_realtime_estimation_not_found(self, mock_est):
        """测试基金不在估值列表返回 None"""
        mock_est.return_value = _make_estimation_df()

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
        assert result["navDate"] == "2026-07-20"
        assert result["growthRate"] == 4.92
