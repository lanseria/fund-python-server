# tests/test_strategies.py
"""策略模块单元测试"""
import pytest
from unittest.mock import patch, MagicMock
import pandas as pd
from datetime import datetime, timedelta


class TestRSIStrategy:
    """RSI 策略测试"""

    @pytest.fixture
    def strategy(self):
        from python_cli_starter.strategies import rsi_strategy
        return rsi_strategy

    @pytest.fixture
    def mock_akshare_data(self):
        """创建模拟 akshare 数据格式"""
        today = datetime.now()
        dates = pd.date_range(end=today, periods=100, freq='D')
        nav_values = [1.0 + i * 0.01 for i in range(100)]
        df = pd.DataFrame({
            '净值日期': dates.strftime('%Y-%m-%d'),
            '单位净值': nav_values
        })
        return df

    @patch('python_cli_starter.strategies.rsi_strategy.ak.fund_open_fund_info_em')
    def test_run_strategy_success(self, mock_akshare, strategy, mock_akshare_data):
        """测试策略成功执行"""
        mock_akshare.return_value = mock_akshare_data

        result = strategy.run_strategy('161725')

        assert 'signal' in result
        assert 'reason' in result
        assert 'latest_date' in result
        assert 'latest_close' in result
        assert 'metrics' in result
        assert result['signal'] in ['买入', '卖出', '持有/观望']

    @patch('python_cli_starter.strategies.rsi_strategy.ak.fund_open_fund_info_em')
    def test_run_strategy_data_error(self, mock_akshare, strategy):
        """测试数据获取失败"""
        mock_akshare.return_value = None

        result = strategy.run_strategy('161725')

        assert 'error' in result
        assert '无法获取' in result['error']

    def test_calculate_rsi(self, strategy, mock_akshare_data):
        """测试 RSI 计算"""
        # 转换为策略内部使用的格式
        df = mock_akshare_data.copy()
        df['净值日期'] = pd.to_datetime(df['净值日期'])
        df = df.set_index('净值日期')
        df = df[['单位净值']]
        df.columns = ['close']
        df['close'] = pd.to_numeric(df['close'])

        df = strategy.calculate_rsi(df, period=14)

        assert 'rsi' in df.columns

        # 验证 RSI 值在合理范围内 (0-100)
        valid_rsi = df['rsi'].dropna()
        if len(valid_rsi) > 0:
            assert (valid_rsi >= 0).all()
            assert (valid_rsi <= 100).all()


class TestMACDStrategy:
    """MACD 策略测试"""

    @pytest.fixture
    def strategy(self):
        from python_cli_starter.strategies import macd_strategy
        return macd_strategy

    @pytest.fixture
    def mock_akshare_data(self):
        """创建模拟 akshare 数据格式"""
        today = datetime.now()
        dates = pd.date_range(end=today, periods=150, freq='D')
        nav_values = [1.0 + i * 0.01 for i in range(150)]
        df = pd.DataFrame({
            '净值日期': dates.strftime('%Y-%m-%d'),
            '单位净值': nav_values
        })
        return df

    @patch('python_cli_starter.strategies.macd_strategy.ak.fund_open_fund_info_em')
    def test_run_strategy_success(self, mock_akshare, strategy, mock_akshare_data):
        """测试策略成功执行"""
        mock_akshare.return_value = mock_akshare_data

        result = strategy.run_strategy('161725', is_holding=False)

        assert 'signal' in result
        assert 'reason' in result
        assert 'metrics' in result
        assert result['signal'] in ['买入', '卖出', '持有/观望']

    def test_calculate_macd(self, strategy, mock_akshare_data):
        """测试 MACD 计算"""
        # 转换为策略内部使用的格式
        df = mock_akshare_data.copy()
        df['净值日期'] = pd.to_datetime(df['净值日期'])
        df = df.set_index('净值日期')
        df = df[['单位净值']]
        df.columns = ['close']
        df['close'] = pd.to_numeric(df['close'])

        df = strategy.calculate_macd(
            df,
            short_period=12,
            long_period=26,
            signal_period=9
        )

        assert 'macd' in df.columns
        assert 'macd_signal' in df.columns
        assert 'macd_hist' in df.columns


class TestBollingerBandsStrategy:
    """布林带策略测试"""

    @pytest.fixture
    def strategy(self):
        from python_cli_starter.strategies import bollinger_bands_strategy
        return bollinger_bands_strategy

    @pytest.fixture
    def mock_akshare_data(self):
        """创建模拟 akshare 数据格式"""
        today = datetime.now()
        dates = pd.date_range(end=today, periods=200, freq='D')
        nav_values = [1.0 + (i % 20) * 0.1 for i in range(200)]
        df = pd.DataFrame({
            '净值日期': dates.strftime('%Y-%m-%d'),
            '单位净值': nav_values
        })
        return df

    @patch('python_cli_starter.strategies.bollinger_bands_strategy.ak.fund_open_fund_info_em')
    def test_run_strategy_success(self, mock_akshare, strategy, mock_akshare_data):
        """测试策略成功执行"""
        mock_akshare.return_value = mock_akshare_data

        result = strategy.run_strategy('161725', is_holding=False)

        assert 'signal' in result
        assert 'reason' in result
        assert 'metrics' in result
        assert result['signal'] in ['买入', '卖出', '持有/观望']

    def test_calculate_bollinger_bands(self, strategy, mock_akshare_data):
        """测试布林带计算"""
        # 转换为策略内部使用的格式
        df = mock_akshare_data.copy()
        df['净值日期'] = pd.to_datetime(df['净值日期'])
        df = df.set_index('净值日期')
        df = df[['单位净值']]
        df.columns = ['close']
        df['close'] = pd.to_numeric(df['close'])

        df = strategy.calculate_bollinger_bands(
            df,
            period=50,
            dev_factor=2.0
        )

        assert 'bband_upper' in df.columns
        assert 'bband_mid' in df.columns
        assert 'bband_lower' in df.columns

        # 验证上轨 >= 中轨 >= 下轨
        valid_data = df.dropna().head()
        if len(valid_data) > 0:
            assert (valid_data['bband_upper'] >= valid_data['bband_mid']).all()
            assert (valid_data['bband_mid'] >= valid_data['bband_lower']).all()


class TestDualConfirmationStrategy:
    """双重确认策略测试"""

    @pytest.fixture
    def strategy(self):
        from python_cli_starter.strategies import dual_confirmation_strategy
        return dual_confirmation_strategy

    @pytest.fixture
    def mock_akshare_data(self):
        """创建模拟 akshare 数据格式"""
        today = datetime.now()
        dates = pd.date_range(end=today, periods=200, freq='D')
        nav_values = [1.0 + i * 0.01 for i in range(200)]
        df = pd.DataFrame({
            '净值日期': dates.strftime('%Y-%m-%d'),
            '单位净值': nav_values
        })
        return df

    @patch('python_cli_starter.strategies.dual_confirmation_strategy.ak.fund_open_fund_info_em')
    def test_run_strategy_success(self, mock_akshare, strategy, mock_akshare_data):
        """测试策略成功执行"""
        mock_akshare.return_value = mock_akshare_data

        result = strategy.run_strategy('161725', is_holding=False)

        assert 'signal' in result
        assert 'reason' in result
        assert 'metrics' in result
        assert result['signal'] in ['买入', '卖出', '持有/观望']


class TestStrategyRegistry:
    """策略注册表测试"""

    @pytest.fixture
    def registry(self):
        from python_cli_starter.strategies import STRATEGY_REGISTRY
        return STRATEGY_REGISTRY

    def test_registry_contains_all_strategies(self, registry):
        """测试注册表包含所有策略"""
        expected_strategies = ['rsi', 'macd', 'bollinger_bands', 'dual_confirmation']
        for strategy in expected_strategies:
            assert strategy in registry

    def test_registry_functions_are_callable(self, registry):
        """测试注册的策略函数可调用"""
        for name, func in registry.items():
            assert callable(func)

    def test_registry_rsi_signature(self, registry):
        """测试 RSI 策略签名"""
        import inspect
        func = registry['rsi']
        sig = inspect.signature(func)
        params = list(sig.parameters.keys())
        assert 'fund_code' in params
        assert 'is_holding' not in params

    def test_registry_macd_signature(self, registry):
        """测试 MACD 策略签名"""
        import inspect
        func = registry['macd']
        sig = inspect.signature(func)
        params = list(sig.parameters.keys())
        assert 'fund_code' in params
        assert 'is_holding' in params
