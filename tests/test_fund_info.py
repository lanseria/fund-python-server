# tests/test_fund_info.py
"""基金完整信息接口测试（GET /fund/info/{fundCode}）。

通过 mock 三个数据源注入伪造数据，覆盖：
- 正常响应（字段全匹配契约）
- fundType 判断（open / qdii_lof / LOF）
- 代码格式错误 → 400
- 基金不存在 → 404
- 历史/费率降级容错
"""
import pytest
import pandas as pd
from datetime import date
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from python_cli_starter.main import app

client = TestClient(app)


# 仿真实 jbgk 页面的基本信息表格 HTML（th-td 配对结构）
def _make_basic_html(
    name="中欧瑾泉灵活配置混合C",
    fund_type="混合型-灵活",
    management_fee="0.60%（每年）",
    custody_fee="0.15%（每年）",
):
    return f"""
    <html><body>
    <table>
      <tr><th>基金全称</th><td>{name}型证券投资基金</td><th>基金简称</th><td>{name}</td></tr>
      <tr><th>基金代码</th><td>001111（前端）基金类型{fund_type}</td><th>基金类型</th><td>{fund_type}</td></tr>
      <tr><th>管理费率</th><td>{management_fee}</td><th>托管费率</th><td>{custody_fee}</td></tr>
      <tr><th>最高申购费率</th><td>0.00%（前端）</td><th>最高赎回费率</th><td>1.50%（前端）</td></tr>
    </table>
    </body></html>
    """


def _mock_response(text: str, status_code: int = 200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    return resp


def _mock_history_df():
    """构造两天的历史净值 DataFrame（模拟 akshare 返回结构）。"""
    return pd.DataFrame(
        {
            "净值日期": [date(2026, 7, 7), date(2026, 7, 8)],
            "单位净值": [1.4450, 1.4464],
            "日增长率": [-0.1, 0.35],
        }
    )


def _mock_fee_data():
    """构造 fund_fee 模块返回的费率字典。"""
    return {
        "trade_status": {"申购状态": "开放申购"},
        "purchase_redemption_amount": {},
        "trade_confirm_days": {},
        "operation_fees": {
            "管理费率": "0.60%（每年）",
            "托管费率": "0.15%（每年）",
            "销售服务费率": "0.01%（每年）",
        },
        "subscription_fee_rate": [],
        "purchase_fee_rate": [
            {
                "适用区间": "小于100万元",
                "原费率": "1.50%",
                "天天基金优惠费率": "0.15%",
            }
        ],
        "redemption_fee_rate": [
            {"适用区间": "小于7天", "费率": "1.50%"},
            {"适用区间": "大于等于7天", "费率": "0.50%"},
        ],
    }


def _patch_all(basic_html, history_df=None, fee_data=None):
    """一次性 patch 三个数据源（默认都成功）。"""
    return [
        patch(
            "python_cli_starter.fund_info.requests.get",
            return_value=_mock_response(basic_html),
        ),
        patch(
            "python_cli_starter.fund_info.ak.fund_open_fund_info_em",
            return_value=history_df if history_df is not None else _mock_history_df(),
        ),
        patch(
            "python_cli_starter.fund_info.fund_fee.get_fund_fee",
            return_value=fee_data if fee_data is not None else _mock_fee_data(),
        ),
    ]


class TestFundInfoAPI:
    """基金完整信息 API 端点测试"""

    def test_invalid_code_format_400(self):
        """测试代码格式错误返回 400"""
        # 非 6 位、含字母
        for bad_code in ["12345", "1234567", "abc123", "abcdef"]:
            response = client.get(f"/fund/info/{bad_code}")
            assert response.status_code == 400, f"{bad_code} 应返回 400"
            assert "格式错误" in response.json()["detail"]

    @patch("python_cli_starter.fund_info.requests.get")
    def test_fund_not_found_404(self, mock_get):
        """测试基金代码不存在返回 404（页面提示无此基金）"""
        mock_get.return_value = _mock_response("<html>没有找到该基金 无此基金</html>")

        response = client.get("/fund/info/999999")
        assert response.status_code == 404
        assert "无法获取基金 999999" in response.json()["detail"]

    @patch("python_cli_starter.fund_info.requests.get")
    def test_fund_not_found_placeholder_404(self, mock_get):
        """测试占位页面（基金简称为 ---）返回 404"""
        # 天天基金对不存在的代码会返回占位页面，基金简称为 "---"
        html = """
        <html><body><table>
          <tr><th>基金简称</th><td>---</td><th>基金类型</th><td>---</td></tr>
        </table></body></html>
        """
        mock_get.return_value = _mock_response(html)

        response = client.get("/fund/info/888888")
        assert response.status_code == 404
        assert "无法获取基金 888888" in response.json()["detail"]

    @patch("python_cli_starter.fund_info.requests.get")
    @patch("python_cli_starter.fund_info.ak.fund_open_fund_info_em")
    @patch("python_cli_starter.fund_info.fund_fee.get_fund_fee")
    def test_success_full_response(
        self, mock_fee, mock_history, mock_get
    ):
        """测试成功响应，字段全匹配契约"""
        mock_get.return_value = _mock_response(_make_basic_html())
        mock_history.return_value = _mock_history_df()
        mock_fee.return_value = _mock_fee_data()

        response = client.get("/fund/info/001111")
        assert response.status_code == 200
        data = response.json()

        # 顶层契约字段
        assert data["code"] == "001111"
        assert data["name"] == "中欧瑾泉灵活配置混合C"
        assert data["fundType"] == "open"
        assert data["yesterdayNav"] == "1.4464"
        assert data["navDate"] == "2026-07-08"

        # history 升序，首尾对应
        assert len(data["history"]) == 2
        assert data["history"][0] == {"date": "2026-07-07", "nav": "1.4450"}
        assert data["history"][-1] == {"date": "2026-07-08", "nav": "1.4464"}

        # fees 契约字段
        fees = data["fees"]
        assert fees["purchaseFee"] == "0.15%"  # 首档优惠费率
        assert len(fees["redemptionFees"]) == 2
        assert fees["redemptionFees"][0] == {"holdingPeriod": "小于7天", "rate": "1.50%"}
        assert fees["managementFee"] == "0.60%/年"
        assert fees["custodyFee"] == "0.15%/年"
        assert fees["rawText"]  # 非空

    @patch("python_cli_starter.fund_info.requests.get")
    @patch("python_cli_starter.fund_info.ak.fund_open_fund_info_em")
    @patch("python_cli_starter.fund_info.fund_fee.get_fund_fee")
    def test_fund_type_qdii(self, mock_fee, mock_history, mock_get):
        """测试 QDII 类型判断为 qdii_lof"""
        mock_get.return_value = _mock_response(
            _make_basic_html(name="大成纳斯达克100ETF联接(QDII)A", fund_type="指数型-海外股票")
        )
        mock_history.return_value = _mock_history_df()
        mock_fee.return_value = _mock_fee_data()

        response = client.get("/fund/info/000834")
        assert response.status_code == 200
        assert response.json()["fundType"] == "qdii_lof"

    @patch("python_cli_starter.fund_info.requests.get")
    @patch("python_cli_starter.fund_info.ak.fund_open_fund_info_em")
    @patch("python_cli_starter.fund_info.fund_fee.get_fund_fee")
    def test_fund_type_lof(self, mock_fee, mock_history, mock_get):
        """测试简称含 LOF 判断为 qdii_lof"""
        mock_get.return_value = _mock_response(
            _make_basic_html(name="融通四季添利债券(LOF)C", fund_type="债券型-混合一级")
        )
        mock_history.return_value = _mock_history_df()
        mock_fee.return_value = _mock_fee_data()

        response = client.get("/fund/info/000673")
        assert response.status_code == 200
        assert response.json()["fundType"] == "qdii_lof"

    @patch("python_cli_starter.fund_info.requests.get")
    @patch("python_cli_starter.fund_info.ak.fund_open_fund_info_em")
    @patch("python_cli_starter.fund_info.fund_fee.get_fund_fee")
    def test_history_degraded_when_empty(self, mock_fee, mock_history, mock_get):
        """测试历史净值获取失败时降级（空列表、空 yesterdayNav）"""
        mock_get.return_value = _mock_response(_make_basic_html())
        mock_history.return_value = pd.DataFrame()  # 空历史
        mock_fee.return_value = _mock_fee_data()

        response = client.get("/fund/info/001111")
        assert response.status_code == 200
        data = response.json()
        assert data["history"] == []
        assert data["yesterdayNav"] == ""
        assert data["navDate"] == ""

    @patch("python_cli_starter.fund_info.requests.get")
    @patch("python_cli_starter.fund_info.ak.fund_open_fund_info_em")
    @patch("python_cli_starter.fund_info.fund_fee.get_fund_fee")
    def test_fee_degraded_when_none(self, mock_fee, mock_history, mock_get):
        """测试费率详情获取失败时降级（仅用基本信息费率摘要）"""
        mock_get.return_value = _mock_response(
            _make_basic_html(management_fee="0.80%（每年）", custody_fee="0.20%（每年）")
        )
        mock_history.return_value = _mock_history_df()
        mock_fee.return_value = None  # 费率详情获取失败

        response = client.get("/fund/info/001111")
        assert response.status_code == 200
        fees = response.json()["fees"]
        # 降级：管理/托管费仍来自基本信息摘要
        assert fees["managementFee"] == "0.80%/年"
        assert fees["custodyFee"] == "0.20%/年"
        # 申购/赎回费无来源，为空
        assert fees["purchaseFee"] is None
        assert fees["redemptionFees"] == []


class TestFundInfoUnit:
    """fund_info 模块单元测试"""

    def test_determine_fund_type_open(self):
        from python_cli_starter.fund_info import _determine_fund_type

        assert _determine_fund_type("华夏成长混合", "混合型-灵活") == "open"
        assert _determine_fund_type("沪深300ETF", "指数型-股票") == "open"

    def test_determine_fund_type_qdii(self):
        from python_cli_starter.fund_info import _determine_fund_type

        # 类型含 QDII
        assert _determine_fund_type("某基金", "QDII-普通股票") == "qdii_lof"
        # 简称含 QDII
        assert _determine_fund_type("大成纳斯达克100(QDII)A", "指数型-海外股票") == "qdii_lof"
        # 简称含 LOF
        assert _determine_fund_type("融通四季添利(LOF)C", "债券型-混合一级") == "qdii_lof"

    def test_format_fee_to_percent(self):
        from python_cli_starter.fund_info import _format_fee_to_percent

        assert _format_fee_to_percent("0.60%（每年）") == "0.60%/年"
        assert _format_fee_to_percent("0.15%(每年)") == "0.15%/年"
        assert _format_fee_to_percent("---") == ""
        assert _format_fee_to_percent("") == ""
        assert _format_fee_to_percent("0.00%") == "0.00%"

    def test_build_fees_purchase_fee_priority(self):
        """测试 purchaseFee 取值优先级：优惠 > 原费率 > 费率"""
        from python_cli_starter.fund_info import _build_fees

        # 单档无优惠（只有 费率 key）
        fee_data = {"purchase_fee_rate": [{"适用区间": "---", "费率": "0.00%"}]}
        fees = _build_fees(fee_data, "0.60%（每年）", "0.15%（每年）")
        assert fees["purchaseFee"] == "0.00%"

        # 有优惠费率
        fee_data = {
            "purchase_fee_rate": [
                {"适用区间": "小于100万", "原费率": "1.50%", "天天基金优惠费率": "0.15%"}
            ]
        }
        fees = _build_fees(fee_data, "0.60%（每年）", "0.15%（每年）")
        assert fees["purchaseFee"] == "0.15%"

        # 无申购费率档位
        fee_data = {"purchase_fee_rate": []}
        fees = _build_fees(fee_data, "0.60%（每年）", "0.15%（每年）")
        assert fees["purchaseFee"] is None
