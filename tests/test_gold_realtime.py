# tests/test_gold_realtime.py
"""上海黄金交易所贵金属实时行情模块测试（新浪 gds_ 接口）。

全部用 mock 替代新浪行情网络请求，只验证行解析、涨跌幅计算与批量聚合逻辑。
"""
from unittest.mock import patch

import pytest

from python_cli_starter import gold_realtime


# 用 2026-09-22 实测抓到的沪金99 行构造样例（字段位置与真实接口一致）
AU9999_LINE = (
    'var hq_str_gds_AU9999='
    '"931.80,0,931.56,932.00,951.50,930.00,15:29:59,938.09,941.50,'
    '687566,330.00,423.00,2026-09-22,沪金99";'
)
AUTD_LINE = (
    'var hq_str_gds_AUTD='
    '"931.30,0,931.30,931.99,942.50,931.00,15:29:59,938.11,941.60,'
    '37436,5.00,5.00,2026-09-22,黄金延期";'
)


@pytest.fixture(autouse=True)
def _clear_cache():
    """每个用例清空进程内行情缓存，避免用例间串扰。"""
    gold_realtime._CACHE.clear()
    yield
    gold_realtime._CACHE.clear()


class TestParseLine:
    """新浪贵金属单行解析"""

    def test_parse_au9999_line(self):
        item = gold_realtime._parse_line(AU9999_LINE)
        assert item is not None
        assert item["code"] == "AU9999"
        assert item["name"] == "沪金99"
        assert item["price"] == 931.80
        assert item["prevClose"] == 938.09
        # 涨跌幅 = (931.80 - 938.09) / 938.09 × 100 ≈ -0.6703
        assert item["changePct"] == pytest.approx(-0.6705, abs=1e-4)
        assert item["date"] == "2026-09-22"
        assert item["time"] == "15:29:59"

    def test_parse_autd_line(self):
        item = gold_realtime._parse_line(AUTD_LINE)
        assert item is not None
        assert item["code"] == "AUTD"
        assert item["name"] == "黄金延期"
        assert item["price"] == 931.30

    def test_parse_invalid_lines(self):
        # 非 gds_ 行、未知合约、字段不足的行
        assert gold_realtime._parse_line('v_sh600519="1~贵州茅台~600519~";') is None
        assert gold_realtime._parse_line('var hq_str_gds_XXXX="1~2~3";') is None
        assert gold_realtime._parse_line('var hq_str_gds_AU9999="931.80,0";') is None

    def test_missing_prev_close_change_pct_none(self):
        line = (
            'var hq_str_gds_AU9999="931.80,0,931.56,0,0,0,15:29:59,0,0,'
            '0,0,0,2026-09-22,沪金99";'
        )
        item = gold_realtime._parse_line(line)
        assert item is not None
        assert item["price"] == 931.80
        assert item["prevClose"] is None
        assert item["changePct"] is None

    def test_unopened_price_zero_change_pct_none(self):
        # 未开盘：最新价为 0，price 与 changePct 均为 None
        line = (
            'var hq_str_gds_AU9999="0,0,0,0,0,0,,0,0,0,0,0,2026-09-22,沪金99";'
        )
        item = gold_realtime._parse_line(line)
        assert item is not None
        assert item["price"] is None
        assert item["changePct"] is None


class TestGetGoldRealtime:
    """批量聚合（mock 网络层）"""

    def test_batch_and_missing(self):
        text = AU9999_LINE + "\n" + AUTD_LINE
        with patch.object(gold_realtime, "_request_text", return_value=text):
            result = gold_realtime.get_gold_realtime(["AU9999", "AUTD", "XXX"])

        assert set(result["missing"]) == {"XXX"}
        assert {q["code"] for q in result["quotes"]} == {"AU9999", "AUTD"}

    def test_request_failure_goes_missing(self):
        def fake_request(symbols):
            raise ConnectionError("boom")

        with patch.object(gold_realtime, "_request_text", side_effect=fake_request):
            result = gold_realtime.get_gold_realtime(["AU9999"])
        assert result["quotes"] == []
        assert result["missing"] == ["AU9999"]

    def test_cache_hit_avoids_refetch(self):
        calls = []

        def fake_request(symbols):
            calls.append(list(symbols))
            return AU9999_LINE

        with patch.object(gold_realtime, "_request_text", side_effect=fake_request):
            first = gold_realtime.get_gold_realtime(["AU9999"])
            second = gold_realtime.get_gold_realtime(["AU9999"])

        assert len(first["quotes"]) == 1
        assert second["quotes"][0]["price"] == first["quotes"][0]["price"]
        assert len(calls) == 1  # 第二次命中缓存，不再请求

    def test_lowercase_code_normalized(self):
        with patch.object(gold_realtime, "_request_text", return_value=AU9999_LINE):
            result = gold_realtime.get_gold_realtime(["au9999"])
        assert result["missing"] == []
        assert result["quotes"][0]["code"] == "AU9999"


class TestGoldRealtimeAPI:
    """/gold/realtime 路由层校验"""

    def test_gold_quote_ok(self, app):
        from fastapi.testclient import TestClient

        with TestClient(app) as client, patch.object(
            gold_realtime, "_request_text", return_value=AU9999_LINE
        ):
            resp = client.get("/gold/realtime", params={"codes": "AU9999"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["missing"] == []
        quote = data["quotes"][0]
        assert quote["code"] == "AU9999"
        assert quote["price"] == 931.8
        assert quote["prevClose"] == 938.09
        assert quote["changePct"] == pytest.approx(-0.6705, abs=1e-4)

    def test_invalid_code_rejected(self, app):
        from fastapi.testclient import TestClient

        with TestClient(app) as client:
            resp = client.get("/gold/realtime", params={"codes": "AU9999,XAU"})

        assert resp.status_code == 400
        assert "XAU" in resp.json()["detail"]
