# tests/conftest.py
import pytest
import sys
from pathlib import Path

# 添加 src 目录到 Python 路径
src_path = Path(__file__).parent.parent / 'src'
sys.path.insert(0, str(src_path))


@pytest.fixture
def app():
    """FastAPI 应用 fixture"""
    from python_cli_starter.main import app
    return app
