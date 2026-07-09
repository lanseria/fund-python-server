# tests/test_fund_fee.py
"""基金手续费接口测试。

通过 mock fund_fee.requests.get 注入伪造的天天基金 HTML 页面，
覆盖正常解析、部分费率缺失（ETF 场景）、页面无数据、请求异常等分支。
"""
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

from python_cli_starter.main import app

client = TestClient(app)


# 一个包含全部 7 类费率区块的最小 HTML（仿真实页面 .boxitem 结构）
FULL_HTML = """
<html><body>
<div class="boxitem"><h4>交易状态</h4>
  <table><tr><td>申购状态</td><td>开放申购</td><td>赎回状态</td><td>开放赎回</td><td>定投状态</td><td>支持</td></tr></table>
</div>
<div class="boxitem"><h4>申购与赎回金额</h4>
  <table><tr><td>申购起点</td><td>10.00元</td><td>定投起点</td><td>10.00元</td><td>日累计申购限额</td><td>无限额</td></tr></table>
  <table><tr><td>最小赎回份额</td><td>1.00份</td><td>部分赎回最低保留份额</td><td>1.00份</td></tr></table>
</div>
<div class="boxitem"><h4>交易确认日</h4>
  <table><tr><td>买入确认日</td><td>T+1</td><td>卖出确认日</td><td>T+1</td></tr></table>
</div>
<div class="boxitem"><h4>运作费用</h4>
  <table><tr><td>管理费率</td><td>1.20%（每年）</td><td>托管费率</td><td>0.20%（每年）</td><td>销售服务费率</td><td>---</td></tr></table>
</div>
<div class="boxitem"><h4>认购费率</h4>
  <table>
    <tr><th>适用金额</th><th>费率</th></tr>
    <tr><td>小于100万元</td><td>1.00%</td></tr>
    <tr><td>大于等于100万元</td><td>每笔1000元</td></tr>
  </table>
</div>
<div class="boxitem"><h4>申购费率（前端）</h4>
  <table>
    <tr><th>适用金额</th><th>原费率|天天基金优惠费率</th></tr>
    <tr><td>小于100万元</td><td>1.50% | 0.15%</td></tr>
    <tr><td>大于等于100万元</td><td>每笔1000元</td></tr>
  </table>
</div>
<div class="boxitem"><h4>赎回费率</h4>
  <table>
    <tr><th>适用期限</th><th>赎回费率</th></tr>
    <tr><td>小于1年</td><td>1.80%</td></tr>
    <tr><td>大于等于1年，小于2年</td><td>1.50%</td></tr>
    <tr><td>大于等于2年</td><td>0.00%</td></tr>
  </table>
</div>
</body></html>
"""


def _mock_response(text: str, status_code: int = 200):
    """构造一个 mock 的 requests.Response。"""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    return resp


class TestFundFeeAPI:
    """基金手续费 API 端点测试"""

    @patch("python_cli_starter.fund_fee.requests.get")
    def test_get_fee_success(self, mock_get):
        """测试成功获取完整手续费信息"""
        mock_get.return_value = _mock_response(FULL_HTML)

        response = client.get("/funds/000001/fee")
        assert response.status_code == 200
        data = response.json()

        # 基础字段
        assert data["fund_code"] == "000001"
        required = [
            "trade_status",
            "purchase_redemption_amount",
            "trade_confirm_days",
            "operation_fees",
            "subscription_fee_rate",
            "purchase_fee_rate",
            "redemption_fee_rate",
        ]
        for field in required:
            assert field in data

        # 交易状态键值对解析正确
        assert data["trade_status"]["申购状态"] == "开放申购"
        assert data["trade_status"]["赎回状态"] == "开放赎回"

        # 申购与赎回金额（合并两个表格）
        assert data["purchase_redemption_amount"]["申购起点"] == "10.00元"
        assert data["purchase_redemption_amount"]["最小赎回份额"] == "1.00份"

        # 交易确认日
        assert data["trade_confirm_days"]["买入确认日"] == "T+1"

        # 运作费用
        assert data["operation_fees"]["管理费率"] == "1.20%（每年）"

        # 认购费率分档（普通单费率列）
        assert len(data["subscription_fee_rate"]) == 2
        assert data["subscription_fee_rate"][0] == {
            "适用区间": "小于100万元",
            "费率": "1.00%",
        }

        # 申购费率分档（含原费率与优惠费率，修复 akshare bug 的核心点）
        assert len(data["purchase_fee_rate"]) == 2
        assert data["purchase_fee_rate"][0]["原费率"] == "1.50%"
        assert data["purchase_fee_rate"][0]["天天基金优惠费率"] == "0.15%"
        # 无优惠费率时回退为原费率
        assert data["purchase_fee_rate"][1]["原费率"] == "每笔1000元"

        # 赎回费率分档
        assert len(data["redemption_fee_rate"]) == 3
        assert data["redemption_fee_rate"][0]["费率"] == "1.80%"

    @patch("python_cli_starter.fund_fee.requests.get")
    def test_get_fee_etf_partial(self, mock_get):
        """测试 ETF 场景：缺少申购与赎回金额、赎回费率等区块"""
        etf_html = """
        <html><body>
        <div class="boxitem"><h4>交易状态</h4>
          <table><tr><td>申购状态</td><td>场内交易</td><td>赎回状态</td><td>场内交易</td><td>定投状态</td><td>不支持</td></tr></table>
        </div>
        <div class="boxitem"><h4>运作费用</h4>
          <table><tr><td>管理费率</td><td>0.15%（每年）</td><td>托管费率</td><td>0.05%（每年）</td></tr></table>
        </div>
        <div class="boxitem"><h4>认购费率</h4>
          <table><tr><th>适用金额</th><th>费率</th></tr><tr><td>小于50万元</td><td>1.00%</td></tr></table>
        </div>
        </body></html>
        """
        mock_get.return_value = _mock_response(etf_html)

        response = client.get("/funds/510300/fee")
        assert response.status_code == 200
        data = response.json()

        assert data["trade_status"]["申购状态"] == "场内交易"
        assert data["operation_fees"]["管理费率"] == "0.15%（每年）"
        # 缺失区块应返回空 dict / 空 list
        assert data["purchase_redemption_amount"] == {}
        assert data["trade_confirm_days"] == {}
        assert data["purchase_fee_rate"] == []
        assert data["redemption_fee_rate"] == []
        assert len(data["subscription_fee_rate"]) == 1

    @patch("python_cli_starter.fund_fee.requests.get")
    def test_get_fee_not_found(self, mock_get):
        """测试基金代码不存在（页面提示无此基金）"""
        mock_get.return_value = _mock_response(
            "<html><body>没有找到该基金 无此基金</body></html>"
        )

        response = client.get("/funds/999999/fee")
        assert response.status_code == 404
        assert "无法获取" in response.json()["detail"]

    @patch("python_cli_starter.fund_fee.requests.get")
    def test_get_fee_empty_page(self, mock_get):
        """测试页面无任何费率区块"""
        mock_get.return_value = _mock_response(
            "<html><body><div>没有任何 boxitem 内容</div></body></html>"
        )

        response = client.get("/funds/000001/fee")
        assert response.status_code == 404

    @patch("python_cli_starter.fund_fee.requests.get")
    def test_get_fee_http_error(self, mock_get):
        """测试源页面 HTTP 错误"""
        mock_get.return_value = _mock_response("", status_code=503)

        response = client.get("/funds/000001/fee")
        assert response.status_code == 404

    @patch("python_cli_starter.fund_fee.requests.get")
    def test_get_fee_request_exception(self, mock_get):
        """测试请求异常（网络错误等）"""
        mock_get.side_effect = Exception("Connection timeout")

        response = client.get("/funds/000001/fee")
        assert response.status_code == 404


class TestFundFeeUnit:
    """fund_fee 模块单元测试"""

    @patch("python_cli_starter.fund_fee.requests.get")
    def test_module_returns_dict(self, mock_get):
        """测试模块直接调用返回字典结构"""
        mock_get.return_value = _mock_response(FULL_HTML)

        from python_cli_starter.fund_fee import get_fund_fee

        result = get_fund_fee("000001")
        assert result is not None
        assert isinstance(result["trade_status"], dict)
        assert isinstance(result["purchase_fee_rate"], list)
        assert isinstance(result["redemption_fee_rate"], list)

    def test_split_original_and_discount(self):
        """测试「原费率 | 优惠费率」分隔解析（修复 akshare bug 的核心逻辑）"""
        from python_cli_starter.fund_fee import _split_original_and_discount

        # 正常组合
        r = _split_original_and_discount("1.50% | 0.15%")
        assert r == {"原费率": "1.50%", "天天基金优惠费率": "0.15%"}

        # 多余空格
        r = _split_original_and_discount("  0.80%  |  0.08% ")
        assert r == {"原费率": "0.80%", "天天基金优惠费率": "0.08%"}

        # 无优惠费率（仅原费率）
        r = _split_original_and_discount("每笔1000元")
        assert r["原费率"] == "每笔1000元"
        assert r["天天基金优惠费率"] == "每笔1000元"

        # 空值
        r = _split_original_and_discount("")
        assert r == {"原费率": "", "天天基金优惠费率": ""}
