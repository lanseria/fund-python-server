# tests/test_stock_realtime.py
"""股票批量实时行情模块测试（A股 sh/sz + 港股 hk）。

全部用 mock 替代腾讯行情网络请求，只验证代码解析、行解析与批量聚合逻辑。
"""
from unittest.mock import patch

import pytest

from python_cli_starter import stock_realtime


def _make_qt_line(prefix: str, name: str, code: str, price: str,
                  quote_time: str, change_pct: str) -> str:
    """构造腾讯行情单行文本，字段位置与真实接口一致。

    parts[1] 名称 / parts[2] 代码 / parts[3] 最新价 /
    parts[30] 时间 / parts[32] 涨跌幅%（其余位置填占位 0）。
    """
    parts = ["0"] * 33
    parts[0] = f'{prefix}="100'
    parts[1] = name
    parts[2] = code
    parts[3] = price
    parts[30] = quote_time
    parts[32] = change_pct
    # 34 个字段：末尾多留一位承接收尾引号，涨跌幅字段不被污染
    parts.append("0")
    return "~".join(parts)


# A 股行：时间 YYYYMMDDHHmmss 紧凑数字
A_SHARE_LINE = _make_qt_line(
    "v_sh600519", "贵州茅台", "600519", "1257.12",
    "20260921141948", "-0.78",
)
# 港股行：时间 2026/09/21 14:19:48 斜杠格式
HK_LINE = _make_qt_line(
    "v_hk00700", "腾讯控股", "00700", "428.000",
    "2026/09/21 14:19:48", "2.15",
)


@pytest.fixture(autouse=True)
def _clear_cache():
    """每个用例清空进程内行情缓存，避免用例间串扰。"""
    stock_realtime._CACHE.clear()
    yield
    stock_realtime._CACHE.clear()


class TestQtSymbol:
    """股票代码 → 腾讯行情符号映射"""

    def test_a_share_prefix(self):
        assert stock_realtime.qt_symbol("600519") == "sh600519"
        assert stock_realtime.qt_symbol("000858") == "sz000858"
        assert stock_realtime.qt_symbol("300750") == "sz300750"

    def test_hk_five_digit(self):
        assert stock_realtime.qt_symbol("00700") == "hk00700"
        assert stock_realtime.qt_symbol("01810") == "hk01810"
        # 0/3/6 开头的 5 位代码也是港股，长度判断须先于前缀判断
        assert stock_realtime.qt_symbol("07000") == "hk07000"
        assert stock_realtime.qt_symbol("30088") == "hk30088"
        assert stock_realtime.qt_symbol("68836") == "hk68836"

    def test_unsupported(self):
        # 北交所 4/8 开头（6 位）、美股字母代码、非法格式
        assert stock_realtime.qt_symbol("832566") is None
        assert stock_realtime.qt_symbol("430047") is None
        assert stock_realtime.qt_symbol("AAPL") is None
        assert stock_realtime.qt_symbol("") is None


class TestParseLine:
    """腾讯行情单行解析"""

    def test_parse_a_share_line(self):
        item = stock_realtime._parse_line(A_SHARE_LINE + '";')
        assert item is not None
        assert item["code"] == "600519"
        assert item["name"] == "贵州茅台"
        assert item["price"] == 1257.12
        assert item["changePct"] == -0.78
        assert item["date"] == "2026-09-21"
        assert item["time"] == "14:19:48"

    def test_parse_hk_line(self):
        item = stock_realtime._parse_line(HK_LINE + '";')
        assert item is not None
        assert item["code"] == "00700"
        assert item["name"] == "腾讯控股"
        assert item["price"] == 428.0
        assert item["changePct"] == 2.15
        # 港股斜杠时间归一化为 yyyy-mm-dd / HH:mm:ss
        assert item["date"] == "2026-09-21"
        assert item["time"] == "14:19:48"

    def test_parse_invalid_lines(self):
        # v_pv_none_match 等无效行、字段不足的行
        assert stock_realtime._parse_line('v_pv_none_match="1') is None
        assert stock_realtime._parse_line("v_sh600519=~贵州茅台~600519~") is None

    def test_suspended_price_zero_treated_as_none(self):
        line = _make_qt_line(
            "v_sz000858", "五 粮 液", "000858", "0.00",
            "20260921141948", "0.00",
        )
        item = stock_realtime._parse_line(line)
        assert item is not None
        assert item["price"] is None
        # 名称内部空格对齐去掉
        assert item["name"] == "五粮液"


class TestGetStocksRealtime:
    """批量聚合（mock 网络层）"""

    def test_mixed_a_share_and_hk(self):
        def fake_request(symbols):
            lines = []
            for sym in symbols:
                if sym == "sh600519":
                    lines.append(A_SHARE_LINE)
                elif sym == "hk00700":
                    lines.append(HK_LINE)
            return ";".join(lines) + ";"

        with patch.object(stock_realtime, "_request_text", side_effect=fake_request):
            result = stock_realtime.get_stocks_realtime(["600519", "00700"])

        assert result["missing"] == []
        codes = {s["code"] for s in result["stocks"]}
        assert codes == {"600519", "00700"}
        by_code = {s["code"]: s for s in result["stocks"]}
        assert by_code["00700"]["price"] == 428.0
        assert by_code["600519"]["price"] == 1257.12

    def test_unsupported_codes_go_missing(self):
        with patch.object(stock_realtime, "_request_text", return_value=""):
            result = stock_realtime.get_stocks_realtime(["832566", "AAPL"])
        assert result["stocks"] == []
        assert set(result["missing"]) == {"832566", "AAPL"}

    def test_fetch_failure_goes_missing(self):
        # 请求抛异常的代码不在结果中，归入 missing
        def fake_request(symbols):
            raise ConnectionError("boom")

        with patch.object(stock_realtime, "_request_text", side_effect=fake_request):
            result = stock_realtime.get_stocks_realtime(["600519"])
        assert result["stocks"] == []
        assert result["missing"] == ["600519"]

    def test_cache_hit_avoids_refetch(self):
        calls = []

        def fake_request(symbols):
            calls.append(list(symbols))
            return A_SHARE_LINE + ";"

        with patch.object(stock_realtime, "_request_text", side_effect=fake_request):
            first = stock_realtime.get_stocks_realtime(["600519"])
            second = stock_realtime.get_stocks_realtime(["600519"])

        assert len(first["stocks"]) == 1
        assert second["stocks"][0]["price"] == first["stocks"][0]["price"]
        assert len(calls) == 1  # 第二次命中缓存，不再请求


class TestStocksRealtimeAPI:
    """/stocks/realtime 路由层校验"""

    def test_hk_code_accepted(self, app):
        from fastapi.testclient import TestClient

        line = A_SHARE_LINE + ";" + HK_LINE + ";"

        with TestClient(app) as client, patch.object(
            stock_realtime, "_request_text", return_value=line
        ):
            resp = client.get("/stocks/realtime", params={"codes": "600519,00700"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2
        assert data["missing"] == []
        assert {s["code"] for s in data["stocks"]} == {"600519", "00700"}

    def test_invalid_code_rejected(self, app):
        from fastapi.testclient import TestClient

        with TestClient(app) as client:
            resp = client.get("/stocks/realtime", params={"codes": "600519,AAPL"})

        assert resp.status_code == 400
        assert "AAPL" in resp.json()["detail"]
