"""
扩展的Faithfulness指标 - 提取中间变量

基于ragas.metrics.Faithfulness扩展，返回包含statements和verdicts等中间变量的结果
"""
import logging
from typing import Dict, List, Optional, Any, TYPE_CHECKING

from ragas.metrics import Faithfulness
from ragas.dataset_schema import SingleTurnSample
from ragas.metrics._faithfulness import (
    StatementGeneratorOutput,
    NLIStatementOutput,
)

if TYPE_CHECKING:
    from langchain_core.callbacks import Callbacks
else:
    # 运行时类型别名
    Callbacks = Any

logger = logging.getLogger(__name__)


class FaithfulnessExtended(Faithfulness):
    """
    扩展的Faithfulness指标，返回中间变量
    
    继承自ragas.metrics.Faithfulness，重写_ascore方法以返回包含中间变量的结果
    """
    
    async def _ascore_with_intermediates(
        self, row: Dict, callbacks: Optional[Callbacks] = None
    ) -> Dict[str, Any]:
        """
        计算faithfulness得分并返回中间变量
        
        Args:
            row: 包含user_input, response, retrieved_contexts的字典
            callbacks: LangChain callbacks（可选）
        
        Returns:
            包含以下字段的字典：
            - score: float - faithfulness得分
            - statements: List[str] - 提取的statements列表
            - verdicts: List[Dict] - 每个statement的verdict详情（包含statement, reason, verdict）
            - faithful_count: int - 忠实statements的数量
            - total_count: int - 总statements数量
        """
        assert self.llm is not None, "LLM is not set"
        
        # 调用父类方法生成statements
        statements_output: StatementGeneratorOutput = await self._create_statements(row, callbacks)
        statements = statements_output.statements
        
        if not statements:
            logger.warning("No statements were generated from the answer.")
            return {
                "score": 0.0,
                "statements": [],
                "verdicts": [],
                "faithful_count": 0,
                "total_count": 0,
            }
        
        # 调用父类方法生成verdicts
        verdicts_output: NLIStatementOutput = await self._create_verdicts(row, statements, callbacks)
        
        # 计算得分（使用父类方法）
        score = self._compute_score(verdicts_output)
        
        # 提取verdicts详情
        verdicts_list = []
        faithful_count = 0
        for answer in verdicts_output.statements:
            verdict_dict = {
                "statement": answer.statement,
                "reason": answer.reason,
                "verdict": bool(answer.verdict),  # 转换为bool (0->False, 1->True)
                "verdict_value": int(answer.verdict),  # 保留原始数值
            }
            verdicts_list.append(verdict_dict)
            if answer.verdict:
                faithful_count += 1
        
        # 如果score是nan，设置为0.0
        import numpy as np
        if np.isnan(score):
            score = 0.0
        
        return {
            "score": float(score),
            "statements": statements,
            "verdicts": verdicts_list,
            "faithful_count": faithful_count,
            "total_count": len(statements),
        }
    
    async def _single_turn_ascore_with_intermediates(
        self, sample: SingleTurnSample, callbacks: Optional[Callbacks] = None
    ) -> Dict[str, Any]:
        """
        对单个样本计算faithfulness得分并返回中间变量
        
        Args:
            sample: SingleTurnSample对象
            callbacks: LangChain callbacks（可选）
        
        Returns:
            包含score和中间变量的字典
        """
        row = sample.to_dict()
        return await self._ascore_with_intermediates(row, callbacks)

