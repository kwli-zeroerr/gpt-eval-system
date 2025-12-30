"""
Ragas 评测器封装 - 基于论文2407.12873v1优化的评估指标实现

论文核心发现：
1. Factual Correctness 和 Faithfulness 是判断RAG响应正确性的最佳指标
2. 同时使用这两个指标比单独使用更好
3. 需要基于明确的定义实现指标，而不是盲目使用RAGAS的所有指标

参考论文：Evaluation of RAG Metrics for Question Answering in the Telecom Domain
"""
import logging
import os
import json
import re
from typing import Dict, List, Optional, Any, Tuple
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
        # 读取max_tokens配置（默认128000，为模型最大上下文长度256000的一半）
        # 这样可以确保即使输入tokens很大，也不会超过上下文长度限制
        self.max_tokens = int(os.getenv("RAGAS_MAX_TOKENS", "128000"))
        
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY 环境变量未设置，无法初始化 Ragas 评测器")
        
        # 初始化 LLM
        try:
            # Ragas 新版本要求使用 OpenAI client 实例
            from openai import OpenAI
            
            # 创建 OpenAI client（注意：Ragas内部使用的LLM配置需要通过其API设置）
            client_kwargs = {
                "api_key": self.api_key,
            }
            if self.base_url:
                client_kwargs["base_url"] = self.base_url
            
            client = OpenAI(**client_kwargs)
            
            # 计算合理的 max_tokens：设置为模型最大上下文长度（256000）的一半，确保安全
            # 即使输入tokens很大（如60000+），也能保证不超过上下文长度限制
            safe_max_tokens = min(self.max_tokens, 128000) if self.max_tokens > 128000 else self.max_tokens
            
            # 使用 client 初始化 llm_factory
            # 直接传递 max_tokens 作为关键字参数，Ragas 会将其存储在 model_args 中
            # 并在调用时通过 _map_provider_params() 正确映射到 OpenAI API 参数
            self.llm = llm_factory(
                self.llm_model,
                client=client,
                max_tokens=safe_max_tokens
            )
            
            logger.info(f"Ragas 评测器初始化成功，使用模型: {self.llm_model}, max_tokens: {safe_max_tokens}")
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
        评测答案的忠实度（是否基于上下文）- 使用扩展版本提取中间变量
        
        Args:
            answer: 系统生成的答案
            context: 上下文信息
        
        Returns:
            评测结果字典，包含score和中间变量（statements、verdicts等）
        """
        try:
            # 使用扩展的Faithfulness类来提取中间变量
            from services.ragas_extended import FaithfulnessExtended
            from instructor.core.exceptions import IncompleteOutputException
            
            metric = FaithfulnessExtended(llm=self.llm)
            
            # 构造 SingleTurnSample
            sample = SingleTurnSample(
                user_input="",  # faithfulness 不需要问题
                response=answer,
                retrieved_contexts=[context] if context else None
            )
            
            # 使用扩展方法获取包含中间变量的结果
            try:
                result = await metric._single_turn_ascore_with_intermediates(sample)
            except IncompleteOutputException as e:
                # 输出不完整（max_tokens限制），使用fallback方法
                logger.warning(f"Faithfulness评测输出不完整（max_tokens限制），使用fallback方法: {e}")
                raise  # 重新抛出，让外层catch处理fallback
            
            score = result.get("score", 0.0)
            
            logger.info(
                f"评测答案忠实度: 答案={answer[:50]}... "
                f"得分={score:.2f}, "
                f"statements={result.get('total_count', 0)}, "
                f"faithful={result.get('faithful_count', 0)}"
            )
            
            return {
                "score": score,
                "metric": "faithfulness",
                "reason": f"答案忠实度评分: {score:.3f} ({result.get('faithful_count', 0)}/{result.get('total_count', 0)} statements faithful)",
                # 中间变量
                "statements": result.get("statements", []),
                "verdicts": result.get("verdicts", []),
                "faithful_count": result.get("faithful_count", 0),
                "total_count": result.get("total_count", 0),
            }
        except Exception as e:
            logger.error(f"评测忠实度失败: {e}", exc_info=True)
            # 如果扩展类失败，回退到AspectCritic
            try:
                metric = AspectCritic(
                    name="faithfulness",
                    definition="评估答案是否忠实于提供的上下文。答案应该基于上下文信息，不包含上下文之外的信息或编造的内容。",
                    llm=self.llm
                )
                
                sample = SingleTurnSample(
                    user_input="",
                    response=answer,
                    retrieved_contexts=[context] if context else None
                )
                
                score_result = await metric.single_turn_ascore(sample)
                score = float(score_result) if isinstance(score_result, (int, float)) else 0.0
                
                logger.warning(f"使用fallback方法评测忠实度: {score:.2f}")
                
                return {
                    "score": score,
                    "reason": f"答案忠实度评分（fallback）: {score_result}",
                    "metric": "faithfulness",
                    "fallback": True,
                    # 中间变量不可用
                    "statements": [],
                    "verdicts": [],
                    "faithful_count": None,
                    "total_count": None,
                }
            except Exception as e2:
                logger.error(f"Fallback方法也失败: {e2}", exc_info=True)
                return {
                    "score": 0.0,
                    "reason": f"评测失败: {str(e)}",
                    "metric": "faithfulness",
                    "error": str(e),
                    "statements": [],
                    "verdicts": [],
                    "faithful_count": None,
                    "total_count": None,
                }
    
    async def evaluate_factual_correctness(
        self,
        question: str,
        answer: str,
        ground_truth: str
    ) -> Dict[str, Any]:
        """
        评测事实正确性（Factual Correctness）- 基于论文定义
        
        根据论文，Factual Correctness基于TP/FP/FN计算：
        - TP (True Positive): 同时出现在答案和ground truth中的事实
        - FP (False Positive): 出现在答案但不在ground truth中的事实
        - FN (False Negative): 出现在ground truth但不在答案中的事实
        - FacCor = |TP| / (|TP| + 0.5 * (|FP| + |FN|))
        
        Args:
            question: 问题文本
            answer: 系统生成的答案
            ground_truth: 标准答案（ground truth）
        
        Returns:
            评测结果字典，包含score、tp、fp、fn等详细信息
        """
        try:
            # 使用LLM提取TP/FP/FN
            prompt = f"""Extract following from given question and ground truth. "TP": statements that are present in both the answer and the ground truth, "FP": statements present in the answer but not found in the ground truth, "FN": relevant statements found in the ground truth but omitted in the answer.

question: {question}
answer: {answer}
ground truth: {ground_truth}

Extracted statements:
"TP": [statement 1, statement 2, ...],
"FP": [statement 1, ...],
"FN": [statement 1, statement 2, ...]

Please respond in JSON format with keys: "TP", "FP", "FN". Each value should be a list of statements."""

            # 调用LLM提取事实（使用异步客户端以支持并发）
            from openai import AsyncOpenAI
            async_client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url) if self.base_url else AsyncOpenAI(api_key=self.api_key)
            
            # 计算合理的 max_tokens：设置为模型最大上下文长度（256000）的一半，确保安全
            # 即使输入tokens很大（如60000+），也能保证不超过上下文长度限制
            safe_max_tokens = min(self.max_tokens, 128000) if self.max_tokens > 128000 else self.max_tokens
            
            response = await async_client.chat.completions.create(
                model=self.llm_model,
                messages=[
                    {"role": "system", "content": "You are an expert at extracting factual statements from text. Extract statements accurately and return only valid JSON."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.0,
                max_tokens=safe_max_tokens
            )
            
            result_text = response.choices[0].message.content.strip()
            
            # 尝试解析JSON
            try:
                # 尝试提取JSON部分
                json_match = re.search(r'\{.*"TP".*"FP".*"FN".*\}', result_text, re.DOTALL)
                if json_match:
                    result_json = json.loads(json_match.group())
                else:
                    result_json = json.loads(result_text)
                
                tp_list = result_json.get("TP", [])
                fp_list = result_json.get("FP", [])
                fn_list = result_json.get("FN", [])
                
                num_tp = len(tp_list) if isinstance(tp_list, list) else 0
                num_fp = len(fp_list) if isinstance(fp_list, list) else 0
                num_fn = len(fn_list) if isinstance(fn_list, list) else 0
                
                # 计算Factual Correctness分数
                if num_tp + 0.5 * (num_fp + num_fn) > 0:
                    score = num_tp / (num_tp + 0.5 * (num_fp + num_fn))
                else:
                    score = 0.0
                
                logger.info(f"事实正确性评测: TP={num_tp}, FP={num_fp}, FN={num_fn}, 得分={score:.2f}")
                
                return {
                    "score": float(score),
                    "num_tp": num_tp,
                    "num_fp": num_fp,
                    "num_fn": num_fn,
                    "tp_statements": tp_list if isinstance(tp_list, list) else [],
                    "fp_statements": fp_list if isinstance(fp_list, list) else [],
                    "fn_statements": fn_list if isinstance(fn_list, list) else [],
                    "metric": "factual_correctness",
                    "reason": f"TP={num_tp}, FP={num_fp}, FN={num_fn}, FacCor={score:.3f}"
                }
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning(f"解析事实正确性结果失败，使用fallback方法: {e}")
                # Fallback: 使用AspectCritic作为备选
                metric = AspectCritic(
                    name="factual_correctness",
                    definition=f"评估答案与标准答案的事实正确性。答案应该包含与标准答案相同的事实，不包含错误事实，也不遗漏重要事实。标准答案：{ground_truth[:200]}",
                    llm=self.llm
                )
                # 将ground_truth作为context传入
                sample = SingleTurnSample(
                    user_input=f"问题：{question}\n标准答案：{ground_truth}",
                    response=answer,
                    retrieved_contexts=[ground_truth]
                )
                score_result = await metric.single_turn_ascore(sample)
                score = float(score_result) if isinstance(score_result, (int, float)) else 0.0
                return {
                    "score": score,
                    "metric": "factual_correctness",
                    "reason": f"使用fallback方法: {score_result}",
                    "fallback": True
                }
        except Exception as e:
            logger.error(f"评测事实正确性失败: {e}", exc_info=True)
            return {
                "score": 0.0,
                "metric": "factual_correctness",
                "reason": f"评测失败: {str(e)}",
                "error": str(e)
            }
    
    async def evaluate_context_relevance(
        self,
        question: str,
        context: str
    ) -> Dict[str, Any]:
        """
        评测上下文相关性（Context Relevance）- 基于论文定义
        
        评估检索到的上下文与问题的相关性
        
        Args:
            question: 问题文本
            context: 检索到的上下文信息
        
        Returns:
            评测结果字典
        """
        try:
            metric = AspectCritic(
                name="context_relevance",
                definition="评估检索到的上下文与问题的相关性。上下文应该包含回答问题所需的信息，并且与问题高度相关。",
                llm=self.llm
            )
            
            sample = SingleTurnSample(
                user_input=question,
                response="",  # context relevance不关心答案
                retrieved_contexts=[context] if context else None
            )
            
            score_result = await metric.single_turn_ascore(sample)
            score = float(score_result) if isinstance(score_result, (int, float)) else 0.0
            
            logger.info(f"上下文相关性评测: 问题={question[:50]}... 得分={score:.2f}")
            
            return {
                "score": score,
                "reason": f"上下文相关性评分: {score_result}",
                "metric": "context_relevance"
            }
        except Exception as e:
            logger.error(f"评测上下文相关性失败: {e}", exc_info=True)
            return {
                "score": 0.0,
                "reason": f"评测失败: {str(e)}",
                "metric": "context_relevance",
                "error": str(e)
            }

    async def evaluate_comprehensive(
        self,
        question: str,
        answer: str,
        reference: Optional[str] = None,
        context: Optional[str] = None,
        ground_truth: Optional[str] = None,
        metrics_config: Optional[Dict[str, bool]] = None
    ) -> Dict[str, Any]:
        """
        综合评测（基于论文优化）- 重点关注Factual Correctness和Faithfulness
        
        根据论文发现：
        - Factual Correctness 和 Faithfulness 是最佳指标组合
        - 同时使用这两个指标比单独使用更好
        
        Args:
            question: 问题文本
            answer: 系统生成的答案
            reference: 参考答案（章节信息，可选）
            context: 上下文信息（用于Faithfulness评测）
            ground_truth: 标准答案（用于Factual Correctness评测，如果有）
        
        Returns:
            综合评测结果字典
        """
        results = {}
        
        # 核心指标1：Faithfulness（如果有上下文）
        if context:
            faithfulness_result = await self.evaluate_faithfulness(answer, context)
            results["faithfulness"] = faithfulness_result
        
        # 核心指标2：Factual Correctness（如果有ground truth）
        # 如果没有ground_truth，尝试使用reference作为ground_truth
        gt = ground_truth or reference
        if gt:
            factual_result = await self.evaluate_factual_correctness(question, answer, gt)
            results["factual_correctness"] = factual_result
        
        # 辅助指标：Context Relevance（评估检索质量）
        if context:
            context_relevance_result = await self.evaluate_context_relevance(question, context)
            results["context_relevance"] = context_relevance_result
        
        # 辅助指标：Answer Relevancy（用户满意度）
        relevancy_result = await self.evaluate_answer_relevancy(question, answer)
        results["answer_relevancy"] = relevancy_result
        
        # 计算综合得分：优先使用Factual Correctness + Faithfulness的组合
        # 根据论文，这两个是最重要的指标
        core_scores = []
        if "factual_correctness" in results:
            core_scores.append(results["factual_correctness"]["score"])
        if "faithfulness" in results:
            core_scores.append(results["faithfulness"]["score"])
        
        if core_scores:
            # 使用核心指标的平均值作为主要得分
            results["core_score"] = sum(core_scores) / len(core_scores)
            results["overall_score"] = results["core_score"]
        else:
            # 如果没有核心指标，使用所有可用指标的平均值
            all_scores = [r["score"] for r in results.values() if "score" in r and isinstance(r["score"], (int, float))]
            results["overall_score"] = sum(all_scores) / len(all_scores) if all_scores else 0.0
        
        # 标记使用的指标组合
        results["metrics_used"] = list(results.keys())
        results["primary_metrics"] = [k for k in ["factual_correctness", "faithfulness"] if k in results]
        
        return results

