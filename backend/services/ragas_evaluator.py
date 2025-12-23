"""
Ragas 评测器封装 - 使用 Ragas 进行 AI 自动评测
"""
import logging
import os
from typing import Dict, List, Optional, Any
from ragas.metrics import AspectCritic
from ragas.llms import llm_factory
from ragas.dataset_schema import SingleTurnSample

logger = logging.getLogger(__name__)


class RagasEvaluator:
    """Ragas 评测器封装类"""
    
    def __init__(self, llm_model: Optional[str] = None, api_key: Optional[str] = None, base_url: Optional[str] = None):
        """
        初始化 Ragas 评测器
        
        Args:
            llm_model: LLM 模型名称（默认从环境变量读取）
            api_key: API 密钥（默认从环境变量读取）
            base_url: API 基础 URL（默认从环境变量读取）
        """
        # 从环境变量读取配置
        self.llm_model = llm_model or os.getenv("OPENAI_MODEL", "gpt-4o")
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.base_url = base_url or os.getenv("OPENAI_BASE_URL")
        
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY 环境变量未设置，无法初始化 Ragas 评测器")
        
        # 初始化 LLM
        try:
            # Ragas 新版本要求使用 OpenAI client 实例
            from openai import OpenAI
            
            # 创建 OpenAI client
            client_kwargs = {
                "api_key": self.api_key,
            }
            if self.base_url:
                client_kwargs["base_url"] = self.base_url
            
            client = OpenAI(**client_kwargs)
            
            # 使用 client 初始化 llm_factory
            self.llm = llm_factory(self.llm_model, client=client)
            
            logger.info(f"Ragas 评测器初始化成功，使用模型: {self.llm_model}")
        except Exception as e:
            logger.error(f"初始化 Ragas LLM 失败: {e}", exc_info=True)
            raise
    
    async def evaluate_answer_quality(
        self,
        question: str,
        answer: str,
        reference: Optional[str] = None,
        context: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        评测答案质量
        
        Args:
            question: 问题文本
            answer: 系统生成的答案
            reference: 参考答案（章节信息）
            context: 上下文信息（可选）
        
        Returns:
            评测结果字典，包含 score 和 reason
        """
        try:
            metric = AspectCritic(
                name="answer_quality",
                definition="评估答案的准确性、完整性和相关性。答案应该准确回答用户的问题，包含必要的信息，并且与参考信息一致。",
                llm=self.llm
            )
            
            # 构造 SingleTurnSample
            sample = SingleTurnSample(
                user_input=question,
                response=answer,
                retrieved_contexts=[context] if context else ([reference] if reference else None)
            )
            
            score_result = await metric.single_turn_ascore(sample)
            score = float(score_result) if isinstance(score_result, (int, float)) else 0.0
            
            logger.info(f"评测答案质量: 问题={question[:50]}... 得分={score:.2f}")
            
            return {
                "score": score,
                "reason": f"答案质量评分: {score_result}",
                "metric": "answer_quality"
            }
        except Exception as e:
            logger.error(f"评测答案质量失败: {e}", exc_info=True)
            return {
                "score": 0.0,
                "reason": f"评测失败: {str(e)}",
                "metric": "answer_quality",
                "error": str(e)
            }
    
    async def evaluate_answer_relevancy(
        self,
        question: str,
        answer: str
    ) -> Dict[str, Any]:
        """
        评测答案相关性（用户满意度指标）
        
        Args:
            question: 问题文本
            answer: 系统生成的答案
        
        Returns:
            评测结果字典
        """
        try:
            metric = AspectCritic(
                name="answer_relevancy",
                definition="评估答案与问题的相关性。答案应该直接回答用户的问题，不包含无关信息，并且对用户有帮助。",
                llm=self.llm
            )
            
            # 构造 SingleTurnSample
            sample = SingleTurnSample(
                user_input=question,
                response=answer
            )
            
            score_result = await metric.single_turn_ascore(sample)
            score = float(score_result) if isinstance(score_result, (int, float)) else 0.0
            
            logger.info(f"评测答案相关性: 问题={question[:50]}... 得分={score:.2f}")
            
            return {
                "score": score,
                "reason": f"答案相关性评分: {score_result}",
                "metric": "answer_relevancy"
            }
        except Exception as e:
            logger.error(f"评测答案相关性失败: {e}", exc_info=True)
            return {
                "score": 0.0,
                "reason": f"评测失败: {str(e)}",
                "metric": "answer_relevancy",
                "error": str(e)
            }
    
    async def evaluate_faithfulness(
        self,
        answer: str,
        context: str
    ) -> Dict[str, Any]:
        """
        评测答案的忠实度（是否基于上下文）
        
        Args:
            answer: 系统生成的答案
            context: 上下文信息
        
        Returns:
            评测结果字典
        """
        try:
            metric = AspectCritic(
                name="faithfulness",
                definition="评估答案是否忠实于提供的上下文。答案应该基于上下文信息，不包含上下文之外的信息或编造的内容。",
                llm=self.llm
            )
            
            # 构造 SingleTurnSample
            sample = SingleTurnSample(
                user_input="",  # faithfulness 不需要问题
                response=answer,
                retrieved_contexts=[context] if context else None
            )
            
            score_result = await metric.single_turn_ascore(sample)
            score = float(score_result) if isinstance(score_result, (int, float)) else 0.0
            
            logger.info(f"评测答案忠实度: 答案={answer[:50]}... 得分={score:.2f}")
            
            return {
                "score": score,
                "reason": f"答案忠实度评分: {score_result}",
                "metric": "faithfulness"
            }
        except Exception as e:
            logger.error(f"评测忠实度失败: {e}", exc_info=True)
            return {
                "score": 0.0,
                "reason": f"评测失败: {str(e)}",
                "metric": "faithfulness",
                "error": str(e)
            }
    
    async def evaluate_comprehensive(
        self,
        question: str,
        answer: str,
        reference: Optional[str] = None,
        context: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        综合评测（包含多个指标）
        
        Args:
            question: 问题文本
            answer: 系统生成的答案
            reference: 参考答案（章节信息）
            context: 上下文信息（可选）
        
        Returns:
            综合评测结果字典
        """
        results = {}
        
        # 评测答案质量
        quality_result = await self.evaluate_answer_quality(question, answer, reference, context)
        results["quality"] = quality_result
        
        # 评测答案相关性
        relevancy_result = await self.evaluate_answer_relevancy(question, answer)
        results["relevancy"] = relevancy_result
        
        # 如果有上下文，评测忠实度
        if context:
            faithfulness_result = await self.evaluate_faithfulness(answer, context)
            results["faithfulness"] = faithfulness_result
        
        # 计算综合得分（平均值）
        scores = [r["score"] for r in results.values() if "score" in r and isinstance(r["score"], (int, float))]
        avg_score = sum(scores) / len(scores) if scores else 0.0
        
        results["overall_score"] = avg_score
        
        return results

