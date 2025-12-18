"""
RagFlow API 客户端 - 从 ragflow-evaluation-tool 集成
适配到 gpt-eval-system 项目
"""
import requests
import json
import time
import logging
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class RetrievalConfig:
    """检索配置参数"""
    dataset_ids: List[str]  # 必需：数据集ID列表
    top_k: int = 5
    similarity_threshold: float = 0.0
    vector_similarity_weight: Optional[float] = None
    document_ids: Optional[List[str]] = None
    page: Optional[int] = None
    page_size: Optional[int] = None
    rerank_id: Optional[str] = None
    keyword: Optional[bool] = None
    highlight: Optional[bool] = None
    cross_languages: Optional[List[str]] = None
    metadata_condition: Optional[Dict[str, Any]] = None
    use_kg: Optional[bool] = None


class RagFlowClient:
    """RagFlow API客户端 - 适配版本"""
    
    def __init__(self, api_url: str, api_key: str):
        self.api_url = api_url.rstrip('/')
        self.api_key = api_key
        self.headers = {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json'
        }
        self._local_dataset_cache: Optional[Dict[str, Any]] = None
        self._cached_dataset_ids: Optional[List[str]] = None
        self._cached_document_ids: Optional[List[str]] = None
    
    def _load_local_datasets(self, datasets_json_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        从本地 datasets.json 加载数据集映射
        """
        if self._local_dataset_cache is not None:
            return self._local_dataset_cache
        
        if datasets_json_path:
            path = Path(datasets_json_path)
        else:
            # 默认在 backend 目录下查找
            path = Path(__file__).parent.parent / "datasets.json"
        
        if not path.exists():
            return None
        
        try:
            with open(path, "r", encoding="utf-8") as f:
                self._local_dataset_cache = json.load(f)
            return self._local_dataset_cache
        except Exception as e:
            logger.warning(f"加载本地 datasets.json 失败: {e}")
            return None
    
    def get_datasets_by_theme(self, theme: Optional[str], datasets_json_path: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        根据主题(theme)筛选数据集，按数据集名称精确匹配
        """
        if not theme:
            return []
        
        local = self._load_local_datasets(datasets_json_path)
        if isinstance(local, dict) and theme in local and isinstance(local[theme], dict):
            ds_obj = local[theme]
            ds_id = ds_obj.get("id")
            if ds_id:
                return [{"id": ds_id, "name": theme}]
        
        # 回退到远端API列举
        result = self.list_datasets()
        datasets = result.get("data", [])
        if not datasets and isinstance(result, list):
            datasets = result
        
        matched: List[Dict[str, Any]] = []
        for ds in datasets:
            ds_id = ds.get("id") or ds.get("dataset_id") or ds.get("_id")
            ds_name = ds.get("name") or ds.get("dataset_name") or ""
            if ds_name == theme:
                matched.append({"id": ds_id, "name": ds_name})
        return matched
    
    def list_datasets(self, page: int = 1, page_size: int = 100) -> Dict[str, Any]:
        """列出所有数据集"""
        endpoint = f"{self.api_url}/api/v1/datasets"
        params = {
            "page": page,
            "page_size": page_size
        }
        try:
            response = requests.get(endpoint, headers=self.headers, params=params, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"获取数据集列表失败: {str(e)}")
            return {"error": str(e), "data": []}
    
    def get_all_datasets_and_documents(self, datasets_json_path: Optional[str] = None) -> Tuple[List[str], List[str]]:
        """
        从本地 datasets.json 提取所有 dataset_ids 和 document_ids
        返回: (dataset_ids, document_ids)
        """
        if self._cached_dataset_ids is not None and self._cached_document_ids is not None:
            return self._cached_dataset_ids, self._cached_document_ids
        
        local = self._load_local_datasets(datasets_json_path)
        if not local or not isinstance(local, dict):
            logger.warning("无法从本地 datasets.json 加载数据，返回空列表")
            self._cached_dataset_ids = []
            self._cached_document_ids = []
            return [], []
        
        dataset_ids = []
        document_ids = []
        
        for dataset_name, dataset_info in local.items():
            if not isinstance(dataset_info, dict):
                continue
            
            dataset_id = dataset_info.get('id')
            if dataset_id:
                dataset_ids.append(dataset_id)
            
            documents = dataset_info.get('documents', {})
            if isinstance(documents, dict):
                for doc_name, doc_id in documents.items():
                    if doc_id:
                        document_ids.append(doc_id)
        
        self._cached_dataset_ids = dataset_ids
        self._cached_document_ids = document_ids
        
        logger.info(f"从本地数据集加载: {len(dataset_ids)} 个数据集, {len(document_ids)} 个文档")
        return dataset_ids, document_ids
    
    def search(self, question: str, theme: str, config: RetrievalConfig, 
               datasets_json_path: Optional[str] = None,
               max_retries: int = 3,
               retry_delay: float = 1.0,
               use_exponential_backoff: bool = True) -> Dict[str, Any]:
        """
        调用RagFlow检索API
        
        Args:
            question: 问题文本
            theme: 主题（用于筛选数据集）
            config: 检索配置
            datasets_json_path: datasets.json 文件路径
            max_retries: 最大重试次数
            retry_delay: 重试延迟（秒）
            use_exponential_backoff: 是否使用指数退避
        """
        endpoint = f"{self.api_url}/api/v1/retrieval"
        
        # 从本地 datasets.json 加载所有 dataset_ids 和 document_ids
        all_dataset_ids, all_document_ids = self.get_all_datasets_and_documents(datasets_json_path)
        
        # 优先使用本地加载的数据，如果本地没有则使用config中的
        effective_dataset_ids = all_dataset_ids if all_dataset_ids else (config.dataset_ids or [])
        effective_document_ids = all_document_ids if all_document_ids else (config.document_ids or [])
        
        # 如果指定了 theme，尝试根据 theme 筛选数据集
        if theme:
            theme_datasets = self.get_datasets_by_theme(theme, datasets_json_path)
            if theme_datasets:
                effective_dataset_ids = [ds["id"] for ds in theme_datasets]
        
        payload = {
            "question": question,
            "dataset_ids": effective_dataset_ids,
            "top_k": config.top_k,
            "similarity_threshold": config.similarity_threshold
        }
        
        # 添加可选参数
        if effective_document_ids:
            payload["document_ids"] = effective_document_ids
        if config.page is not None:
            payload["page"] = config.page
        if config.page_size is not None:
            payload["page_size"] = config.page_size
        if config.vector_similarity_weight is not None:
            payload["vector_similarity_weight"] = config.vector_similarity_weight
        if config.rerank_id is not None:
            payload["rerank_id"] = config.rerank_id
        if config.keyword is not None:
            payload["keyword"] = config.keyword
        if config.highlight is not None:
            payload["highlight"] = config.highlight
        if config.cross_languages is not None:
            payload["cross_languages"] = config.cross_languages
        if config.metadata_condition is not None:
            payload["metadata_condition"] = config.metadata_condition
        if config.use_kg is not None:
            payload["use_kg"] = config.use_kg
        
        # 重试机制
        last_error = None
        for attempt in range(max_retries + 1):
            try:
                response = requests.post(endpoint, headers=self.headers, json=payload, timeout=30)
                response.raise_for_status()
                return response.json()
            except requests.exceptions.RequestException as e:
                last_error = e
                if attempt < max_retries:
                    if use_exponential_backoff:
                        delay = retry_delay * (2 ** attempt)
                    else:
                        delay = retry_delay
                    
                    logger.warning(f"API调用失败 (尝试 {attempt + 1}/{max_retries + 1}): {question[:50]}... - {str(e)}，{delay:.2f}秒后重试")
                    time.sleep(delay)
                else:
                    logger.error(f"API调用失败 (已重试 {max_retries} 次): {question[:50]}... - {str(e)}")
        
        return {"error": str(last_error), "chunks": []}
