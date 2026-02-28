"""
Root conftest -- adds function_app/ and project root to sys.path so tests
can import both function_app modules and the backtesting package.
"""
import sys
import os

_root = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(_root, "function_app"))
sys.path.insert(0, _root)
