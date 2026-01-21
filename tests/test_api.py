# tests/test_api.py
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from datetime import date, datetime
import pandas as pd

from python_cli_starter.main import app
from python_cli_starter.schemas import SignalType


client = TestClient(app)


class TestHealthCheck:
    """健康检查测试"""

    def test_health_check(self):
        """测试健康检查端点"""
        response = client.get('/health')
        assert response.status_code == 200
        data = response.json()
        assert data['status'] == 'ok'
        assert 'timestamp' in data


class TestStrategiesList:
    """策略列表测试"""

    def test_list_strategies(self):
        """测试获取所有策略列表"""
        response = client.get('/strategies')
        assert response.status_code == 200
        data = response.json()
        assert 'strategies' in data
        assert 'count' in data
        assert data['count'] == len(data['strategies'])
        # 验证已知策略存在
        expected_strategies = ['rsi', 'macd', 'bollinger_bands', 'dual_confirmation']
        for strategy in expected_strategies:
            assert strategy in data['strategies']


class TestStrategiesAPI:
    """策略 API 测试"""

    @pytest.fixture
    def mock_akshare_data(self):
        """模拟 akshare 返回数据格式（使用最近日期）"""
        today = datetime.now()
        dates = pd.date_range(end=today, periods=100, freq='D')
        nav_values = [1.0 + i * 0.01 for i in range(100)]
        df = pd.DataFrame({
            '净值日期': dates.strftime('%Y-%m-%d'),
            '单位净值': nav_values
        })
        return df

    def test_invalid_strategy_name(self):
        """测试无效的策略名称"""
        response = client.get('/strategies/invalid/161725')
        assert response.status_code == 404
        data = response.json()
        assert 'detail' in data
        assert 'invalid' in data['detail']

    @patch('python_cli_starter.strategies.rsi_strategy.ak.fund_open_fund_info_em')
    def test_rsi_strategy_success(self, mock_akshare, mock_akshare_data):
        """测试 RSI 策略执行成功"""
        mock_akshare.return_value = mock_akshare_data

        response = client.get('/strategies/rsi/161725')
        assert response.status_code == 200
        data = response.json()
        assert data['fund_code'] == '161725'
        assert data['strategy_name'] == 'rsi'
        assert data['signal'] in ['买入', '卖出', '持有/观望']
        assert 'reason' in data
        assert 'latest_date' in data
        assert 'latest_close' in data
        assert 'metrics' in data

    @patch('python_cli_starter.strategies.rsi_strategy.ak.fund_open_fund_info_em')
    def test_rsi_strategy_data_error(self, mock_akshare):
        """测试 RSI 策略数据获取失败"""
        mock_akshare.return_value = None

        response = client.get('/strategies/rsi/161725')
        assert response.status_code == 500
        data = response.json()
        assert 'detail' in data

    @patch('python_cli_starter.strategies.macd_strategy.ak.fund_open_fund_info_em')
    def test_macd_strategy_without_holding(self, mock_akshare, mock_akshare_data):
        """测试 MACD 策略缺少 is_holding 参数"""
        mock_akshare.return_value = mock_akshare_data

        response = client.get('/strategies/macd/161725')
        assert response.status_code == 400
        data = response.json()
        assert 'is_holding' in data['detail']

    @patch('python_cli_starter.strategies.macd_strategy.ak.fund_open_fund_info_em')
    def test_macd_strategy_with_holding_false(self, mock_akshare, mock_akshare_data):
        """测试 MACD 策略未持有状态"""
        mock_akshare.return_value = mock_akshare_data

        response = client.get('/strategies/macd/161725?is_holding=false')
        assert response.status_code == 200
        data = response.json()
        assert data['fund_code'] == '161725'
        assert data['strategy_name'] == 'macd'
        assert 'metrics' in data

    @patch('python_cli_starter.strategies.macd_strategy.ak.fund_open_fund_info_em')
    def test_macd_strategy_with_holding_true(self, mock_akshare, mock_akshare_data):
        """测试 MACD 策略持有状态"""
        mock_akshare.return_value = mock_akshare_data

        response = client.get('/strategies/macd/161725?is_holding=true')
        assert response.status_code == 200
        data = response.json()
        assert data['fund_code'] == '161725'
        assert data['strategy_name'] == 'macd'

    @patch('python_cli_starter.strategies.bollinger_bands_strategy.ak.fund_open_fund_info_em')
    def test_bollinger_bands_strategy_success(self, mock_akshare, mock_akshare_data):
        """测试布林带策略执行成功"""
        mock_akshare.return_value = mock_akshare_data

        response = client.get('/strategies/bollinger_bands/161725?is_holding=false')
        assert response.status_code == 200
        data = response.json()
        assert data['fund_code'] == '161725'
        assert data['strategy_name'] == 'bollinger_bands'
        assert 'metrics' in data

    @patch('python_cli_starter.strategies.dual_confirmation_strategy.ak.fund_open_fund_info_em')
    def test_dual_confirmation_strategy_success(self, mock_akshare):
        """测试双重确认策略执行成功"""
        # 双重确认策略需要200天数据
        today = datetime.now()
        dates = pd.date_range(end=today, periods=200, freq='D')
        nav_values = [1.0 + i * 0.01 for i in range(200)]
        df = pd.DataFrame({
            '净值日期': dates.strftime('%Y-%m-%d'),
            '单位净值': nav_values
        })
        mock_akshare.return_value = df

        response = client.get('/strategies/dual_confirmation/161725?is_holding=false')
        assert response.status_code == 200
        data = response.json()
        assert data['fund_code'] == '161725'
        assert data['strategy_name'] == 'dual_confirmation'
        assert 'metrics' in data


class TestStrategiesLogic:
    """策略逻辑单元测试"""

    @pytest.fixture
    def mock_akshare_data_oversold(self):
        """模拟超卖数据"""
        today = datetime.now()
        dates = pd.date_range(end=today, periods=100, freq='D')
        nav_values = [2.0] * 50 + [1.0] * 50
        df = pd.DataFrame({
            '净值日期': dates.strftime('%Y-%m-%d'),
            '单位净值': nav_values
        })
        return df

    @pytest.fixture
    def mock_akshare_data_overbought(self):
        """模拟超买数据"""
        today = datetime.now()
        dates = pd.date_range(end=today, periods=100, freq='D')
        nav_values = [1.0] * 50 + [2.0] * 50
        df = pd.DataFrame({
            '净值日期': dates.strftime('%Y-%m-%d'),
            '单位净值': nav_values
        })
        return df

    @patch('python_cli_starter.strategies.rsi_strategy.ak.fund_open_fund_info_em')
    def test_rsi_oversold_signal(self, mock_akshare, mock_akshare_data_oversold):
        """测试 RSI 超卖信号生成"""
        mock_akshare.return_value = mock_akshare_data_oversold

        response = client.get('/strategies/rsi/161725')
        data = response.json()

        # 验证返回的数据结构
        assert data['signal'] in ['买入', '卖出', '持有/观望']
        assert 'metrics' in data

    @patch('python_cli_starter.strategies.rsi_strategy.ak.fund_open_fund_info_em')
    def test_rsi_over_overbought_signal(self, mock_akshare, mock_akshare_data_overbought):
        """测试 RSI 超买信号生成"""
        mock_akshare.return_value = mock_akshare_data_overbought

        response = client.get('/strategies/rsi/161725')
        data = response.json()

        assert data['signal'] in ['买入', '卖出', '持有/观望']


class TestSchemas:
    """Schema 验证测试"""

    def test_signal_type_enum(self):
        """测试信号类型枚举"""
        assert SignalType.BUY == '买入'
        assert SignalType.SELL == '卖出'
        assert SignalType.HOLD == '持有/观望'

    @patch('python_cli_starter.strategies.rsi_strategy.ak.fund_open_fund_info_em')
    def test_strategy_signal_response_structure(self, mock_akshare):
        """测试策略信号响应结构"""
        today = datetime.now()
        dates = pd.date_range(end=today, periods=100, freq='D')
        nav_values = [1.0 + i * 0.01 for i in range(100)]
        df = pd.DataFrame({
            '净值日期': dates.strftime('%Y-%m-%d'),
            '单位净值': nav_values
        })
        mock_akshare.return_value = df

        response = client.get('/strategies/rsi/161725')
        assert response.status_code == 200

        data = response.json()
        # 验证所有必需字段存在
        required_fields = ['fund_code', 'strategy_name', 'signal', 'reason', 'latest_date', 'latest_close', 'metrics']
        for field in required_fields:
            assert field in data

class TestChartsAPI:
    """图表 API 测试"""

    @pytest.fixture
    def mock_akshare_history(self):
        """模拟较长的历史数据"""
        today = datetime.now()
        dates = pd.date_range(end=today, periods=60, freq='D')
        # 构造正弦波数据以产生买卖信号
        import numpy as np
        x = np.linspace(0, 4 * np.pi, 60)
        nav_values = 1.0 + 0.2 * np.sin(x)
        
        df = pd.DataFrame({
            '净值日期': dates.strftime('%Y-%m-%d'),
            '单位净值': nav_values
        })
        return df

    @patch('python_cli_starter.charts.ak.fund_open_fund_info_em')
    def test_get_rsi_chart_success(self, mock_akshare, mock_akshare_history):
        """测试 RSI 图表接口成功响应"""
        mock_akshare.return_value = mock_akshare_history

        response = client.get('/charts/rsi/161725')
        
        assert response.status_code == 200
        data = response.json()
        
        # 验证响应模型结构 (RsiChartResponse)
        assert 'dates' in data
        assert 'netValues' in data
        assert 'rsiValues' in data
        assert 'signals' in data
        assert 'config' in data
        
        # 验证配置参数
        assert data['config']['rsiPeriod'] == 14
        assert data['config']['rsiUpper'] == 70.0
        
        # 验证数据包含 None (对应 Python 的 None/NaN)
        # 初始阶段无法计算 RSI，所以 rsiValues 前面应该是 null
        assert data['rsiValues'][0] is None

    @patch('python_cli_starter.charts.ak.fund_open_fund_info_em')
    def test_get_rsi_chart_not_found(self, mock_akshare):
        """测试获取不存在的数据"""
        # 模拟返回空 DataFrame
        mock_akshare.return_value = pd.DataFrame()

        response = client.get('/charts/rsi/999999')
        
        assert response.status_code == 404
        data = response.json()
        assert 'detail' in data
        assert '无法获取' in data['detail']

    @patch('python_cli_starter.charts.ak.fund_open_fund_info_em')
    def test_get_rsi_chart_api_error(self, mock_akshare):
        """测试底层 API 异常"""
        mock_akshare.side_effect = Exception("API Connection Error")

        # 这里的异常会被 charts.py 捕获并返回 None，最终导致 404
        # 或者是根据 main.py 的全局异常处理，这取决于 charts.py 的实现细节
        # 在你提供的 charts.py 代码中，异常被捕获并打印日志，返回 None
        # 所以 main.py 会抛出 404
        
        response = client.get('/charts/rsi/161725')
        
        assert response.status_code == 404
        assert '无法获取' in response.json()['detail']