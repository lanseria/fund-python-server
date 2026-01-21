# tests/test_charts.py
"""图表模块单元测试"""
import pytest
from unittest.mock import patch, MagicMock
import pandas as pd
import numpy as np
from datetime import datetime

class TestRsiChartLogic:
    """RSI 图表数据逻辑测试"""

    @pytest.fixture
    def chart_module(self):
        from python_cli_starter import charts
        return charts

    @pytest.fixture
    def mock_akshare_data(self):
        """创建模拟历史数据，包含足够的数据点以计算 RSI"""
        # 生成 50 天的数据
        # 前 25 天上涨 (RSI 高)，后 25 天下跌 (RSI 低)
        dates = pd.date_range(end=datetime.now(), periods=50, freq='D')
        
        # 构造波动数据
        nav_values = []
        base = 1.0
        for i in range(50):
            if i < 25:
                base += 0.05  # 上涨
            else:
                base -= 0.05  # 下跌
            nav_values.append(base)
            
        df = pd.DataFrame({
            '净值日期': dates.strftime('%Y-%m-%d'),
            '单位净值': nav_values
        })
        return df

    def test_calculate_rsi_logic(self, chart_module, mock_akshare_data):
        """测试 RSI 计算逻辑"""
        # 预处理数据格式
        df = mock_akshare_data.copy()
        df['净值日期'] = pd.to_datetime(df['净值日期'])
        df = df.set_index('净值日期')
        df.columns = ['close']
        
        # 计算
        result_df = chart_module.calculate_rsi(df, period=14)
        
        assert 'rsi' in result_df.columns
        # 前 14 天应该是 NaN
        assert pd.isna(result_df['rsi'].iloc[0])
        # 后面的数据应该有值
        assert pd.notna(result_df['rsi'].iloc[-1])

    @patch('python_cli_starter.charts.ak.fund_open_fund_info_em')
    def test_get_rsi_chart_data_structure(self, mock_akshare, chart_module, mock_akshare_data):
        """测试最终输出的数据结构和 NaN 处理"""
        mock_akshare.return_value = mock_akshare_data
        
        result = chart_module.get_rsi_chart_data('161725')
        
        assert result is not None
        assert 'dates' in result
        assert 'netValues' in result
        assert 'rsiValues' in result
        assert 'signals' in result
        assert 'config' in result
        
        # 验证列表长度一致
        assert len(result['dates']) == len(result['netValues'])
        assert len(result['netValues']) == len(result['rsiValues'])
        
        # 验证 NaN 是否被正确转换为 None (JSON 兼容)
        # 前几个点的 RSI 应该是 None
        assert result['rsiValues'][0] is None
        
        # 验证信号结构
        assert isinstance(result['signals']['buy'], list)
        assert isinstance(result['signals']['sell'], list)

    @patch('python_cli_starter.charts.ak.fund_open_fund_info_em')
    def test_get_rsi_chart_data_empty(self, mock_akshare, chart_module):
        """测试无数据情况"""
        mock_akshare.return_value = pd.DataFrame()
        
        result = chart_module.get_rsi_chart_data('161725')
        
        assert result is None