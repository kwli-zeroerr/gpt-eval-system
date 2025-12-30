"""
Ragas扩展模块 - 用于提取中间变量

基于论文2407.12873v1的实现，扩展ragas库的指标以提取中间输出（statements、verdicts等）
"""
from .faithfulness_extended import FaithfulnessExtended

__all__ = ["FaithfulnessExtended"]

