"""Import MinIO-exported OCR segments into RagFlow via HTTP API.

前提条件：
1. RagFlow 已运行，并在 .env 中配置好：
   - RAGFLOW_API_URL
   - RAGFLOW_API_KEY
2. 已运行过 `export_minio_ocr_to_ragflow.py`，生成：
   - backend/data/ragflow_import/segments.csv

脚本功能：
1. 读取 segments.csv（列：dataset_name, document_name, segment_index, content）
2. 按 (dataset_name, document_name) 聚合段落，拼成纯文本文件
3. 调用 RagFlow HTTP API：
   - 为每个 dataset_name 创建或复用一个数据集
   - 上传每个文档对应的 txt 文件
   - 调用 /api/v1/file/convert 将文件转换为文档并关联到对应数据集
4. 自动生成 / 更新 backend/datasets.json，格式与 services.ragflow_client 约定一致：

   {
     "eRob": {
       "id": "dataset-id-for-erob",
       "documents": {
         "eRob用户手册": "doc-id-1"
       }
     },
     ...
   }
"""

from __future__ import annotations

import csv
import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple
import hashlib

import requests
from dotenv import load_dotenv


@dataclass
class SegmentRow:
  dataset_name: str
  document_name: str
  segment_index: int
  content: str


def load_segments(csv_path: Path) -> List[SegmentRow]:
  # 有些段落非常长，超过 csv 模块默认的 128KB 限制，这里放宽限制
  try:
    csv.field_size_limit(sys.maxsize)
  except (OverflowError, ValueError):
    csv.field_size_limit(10_000_000)

  rows: List[SegmentRow] = []
  with csv_path.open("r", encoding="utf-8-sig") as f:
    reader = csv.DictReader(f)
    required = {"dataset_name", "document_name", "segment_index", "content"}
    if not required.issubset(reader.fieldnames or []):
      raise ValueError(f"segments.csv 缺少必要列: {required}")
    for r in reader:
      try:
        seg_idx = int(r["segment_index"])
      except (TypeError, ValueError):
        seg_idx = 0
      rows.append(
        SegmentRow(
          dataset_name=r["dataset_name"].strip(),
          document_name=r["document_name"].strip(),
          segment_index=seg_idx,
          content=(r["content"] or "").strip(),
        )
      )
  return rows


class RagFlowImporter:
  def __init__(self, api_url: str, api_key: str):
    self.api_url = api_url.rstrip("/")
    self.api_key = api_key
    self.headers_json = {
      "Authorization": f"Bearer {api_key}",
      "Content-Type": "application/json",
    }

  def _post_json(self, path: str, payload: Dict) -> Dict:
    url = f"{self.api_url}{path}"
    resp = requests.post(url, headers=self.headers_json, json=payload, timeout=60)
    resp.raise_for_status()
    return resp.json()

  def _get_json(self, path: str, params: Dict | None = None) -> Dict:
    url = f"{self.api_url}{path}"
    resp = requests.get(url, headers={"Authorization": f"Bearer {self.api_key}"}, params=params, timeout=60)
    resp.raise_for_status()
    return resp.json()

  def _delete_json(self, path: str) -> Dict:
    url = f"{self.api_url}{path}"
    resp = requests.delete(url, headers={"Authorization": f"Bearer {self.api_key}"}, timeout=60)
    resp.raise_for_status()
    if resp.content:
      return resp.json()
    return {}

  # ---- Dataset management -------------------------------------------------

  def list_datasets(self) -> List[Dict]:
    data = self._get_json("/api/v1/datasets", params={"page": 1, "page_size": 100})
    if isinstance(data, dict):
      return data.get("data", []) or data.get("datasets", []) or []
    return []

  def find_or_create_dataset(self, name: str, desc: str | None = None, delete_existing: bool = False) -> str:
    import time
    
    # Try find by name first（包括所有可能的变体：name, name(1), name(2) 等）
    existing_ds_id = None
    all_datasets = self.list_datasets()
    
    # 检查精确匹配
    for ds in all_datasets:
      ds_name = ds.get("name") or ds.get("dataset_name")
      ds_id = ds.get("id") or ds.get("dataset_id") or ds.get("_id")
      if ds_name == name and ds_id:
        existing_ds_id = str(ds_id)
        break
    
    # 如果找到同名数据集且需要删除，先删除它（包括所有变体）
    if delete_existing:
      import re
      # 删除所有同名变体（name, name(1), name(2), name(3) 等）
      to_delete = []
      for ds in all_datasets:
        ds_name = ds.get("name") or ds.get("dataset_name")
        ds_id = ds.get("id") or ds.get("dataset_id") or ds.get("_id")
        # 检查是否是 name 或其变体（name(1), name(2) 等）
        if ds_name == name:
          if ds_id:
            to_delete.append((ds_id, ds_name))
        elif ds_name.startswith(name + "(") and ds_name.endswith(")"):
          # 验证括号内是数字
          match = re.match(rf"^{re.escape(name)}\((\d+)\)$", ds_name)
          if match and ds_id:
            to_delete.append((ds_id, ds_name))
      
      # 批量删除所有同名变体
      if to_delete:
        delete_ids = [ds_id for ds_id, _ in to_delete]
        try:
          # 使用批量删除 API
          url = f"{self.api_url}/api/v1/datasets"
          payload = {"ids": delete_ids}
          resp = requests.delete(url, headers=self.headers_json, json=payload, timeout=60)
          resp.raise_for_status()
          result = resp.json()
          if isinstance(result, dict) and result.get("code") not in (None, 0):
            raise RuntimeError(f"批量删除数据集失败: {result}")
          print(f"[INFO] 已删除 {len(to_delete)} 个同名数据集变体: {[ds_name for _, ds_name in to_delete]}")
          # 等待一下，确保删除操作完成并让数据库更新
          time.sleep(2)
          existing_ds_id = None
          
          # 验证删除是否成功
          remaining_datasets = self.list_datasets()
          remaining_names = {ds.get("name") or ds.get("dataset_name") for ds in remaining_datasets}
          deleted_names = {ds_name for _, ds_name in to_delete}
          still_exist = remaining_names & deleted_names
          if still_exist:
            print(f"[WARN] 仍有 {len(still_exist)} 个数据集未完全删除: {list(still_exist)}")
        except Exception as e:
          print(f"[WARN] 批量删除同名数据集失败: {e}，尝试逐个删除...")
          # 回退到逐个删除
          for ds_id, ds_name in to_delete:
            try:
              self._delete_json(f"/api/v1/datasets/{ds_id}")
              print(f"[INFO] 已删除数据集: {ds_name} ({ds_id})")
              time.sleep(0.5)
            except Exception as e2:
              print(f"[WARN] 删除数据集失败 {ds_name}: {e2}")

    # 如果找到同名数据集且不需要删除，直接返回
    if existing_ds_id:
      return existing_ds_id

    # Create new dataset
    # 注意：根据当前 RagFlow HTTP API，只有少数字段允许传入，多余字段会报
    # "Extra inputs are not permitted"，所以这里只传 name。
    payload = {"name": name}
    created = self._post_json("/api/v1/datasets", payload)

    # 兼容不同返回结构：
    # - {"code": 0, "data": {"id": "...", "name": "..."}}
    # - {"id": "...", "name": "..."}
    if isinstance(created, dict):
      if "code" in created and created.get("code") != 0:
        raise RuntimeError(f"创建数据集失败: {created}")
      data = created.get("data") or created
      ds_id = data.get("id") or data.get("dataset_id") or data.get("_id")
    else:
      ds_id = None

    if not ds_id:
      raise RuntimeError(f"无法从创建数据集响应中获取 dataset_id: {created}")
    return str(ds_id)

  # ---- File & document management -----------------------------------------

  def upload_file(self, file_path: Path, expected_doc_name: str | None = None, dataset_id: str | None = None) -> str:
    """
    Upload a local file to RagFlow file system, return file_id.
    
    Args:
        file_path: Path to the file to upload
        expected_doc_name: Expected document name (without .txt). If provided, will delete any existing documents with this name or variants before uploading.
        dataset_id: Dataset ID. Required if expected_doc_name is provided.
    """
    # 如果提供了预期文档名称，先彻底删除所有同名文档和文件（包括所有状态）
    if expected_doc_name and dataset_id:
      import psycopg2
      from psycopg2.extras import RealDictCursor
      try:
        db_conn = psycopg2.connect(
          host="localhost", port=5432, database="zeroerr_meta",
          user="zeroerr", password="zero0000",
          options="-c search_path=ragflow"
        )
        with db_conn.cursor(cursor_factory=RealDictCursor) as db_cur:
          import re
          
          # 1. 删除所有同名文档变体（包括所有状态）
          db_cur.execute("""
            SELECT id, name FROM document
            WHERE kb_id = %s AND (
              name = %s OR
              name LIKE %s
            )
          """, (dataset_id, expected_doc_name + ".txt", expected_doc_name + "(%).txt"))
          db_docs = db_cur.fetchall()
          doc_ids_to_delete = []
          for db_doc in db_docs:
            doc_name = db_doc["name"]
            doc_base = doc_name[:-4] if doc_name.endswith(".txt") else doc_name
            if doc_base == expected_doc_name:
              doc_ids_to_delete.append(db_doc["id"])
            elif doc_base.startswith(expected_doc_name + "(") and doc_base.endswith(")"):
              match = re.match(rf"^{re.escape(expected_doc_name)}\((\d+)\)$", doc_base)
              if match:
                doc_ids_to_delete.append(db_doc["id"])
          
          if doc_ids_to_delete:
            # 先删除 file2document 关联
            db_cur.execute("DELETE FROM file2document WHERE document_id = ANY(%s)", (doc_ids_to_delete,))
            # 再删除文档
            db_cur.execute("DELETE FROM document WHERE id = ANY(%s)", (doc_ids_to_delete,))
            print(f"[INFO] 从数据库硬删除 {len(doc_ids_to_delete)} 个同名文档变体（包括所有状态）")
          
          # 2. 删除所有同名文件变体（File 表，避免文件上传时产生变体）
          expected_file_name = file_path.name  # 例如: "eCoder 用户手册__eCoder编码器用户手册V2.4__13.txt"
          db_cur.execute("""
            SELECT id, name FROM file
            WHERE name = %s OR name LIKE %s
          """, (expected_file_name, expected_file_name.replace(".txt", "(%).txt")))
          db_files = db_cur.fetchall()
          file_ids_to_delete = []
          for db_file in db_files:
            file_name = db_file["name"]
            if file_name == expected_file_name:
              file_ids_to_delete.append(db_file["id"])
            elif file_name.startswith(expected_file_name.replace(".txt", "(")) and file_name.endswith(").txt"):
              file_base = file_name.rsplit("(", 1)[0] if "(" in file_name else file_name
              if file_base == expected_file_name.replace(".txt", ""):
                match = re.match(rf"^{re.escape(expected_file_name.replace('.txt', ''))}\((\d+)\)\.txt$", file_name)
                if match:
                  file_ids_to_delete.append(db_file["id"])
          
          if file_ids_to_delete:
            # 先删除 file2document 关联
            db_cur.execute("DELETE FROM file2document WHERE file_id = ANY(%s)", (file_ids_to_delete,))
            # 再删除文件
            db_cur.execute("DELETE FROM file WHERE id = ANY(%s)", (file_ids_to_delete,))
            print(f"[INFO] 从数据库硬删除 {len(file_ids_to_delete)} 个同名文件变体（包括所有状态）")
          
          if doc_ids_to_delete or file_ids_to_delete:
            db_conn.commit()
            import time
            time.sleep(0.5)  # 等待数据库更新
        db_conn.close()
      except Exception as e:
        print(f"[WARN] 数据库硬删除失败: {e}，继续上传文件")
    
    url = f"{self.api_url}/api/v1/file/upload"
    headers = {"Authorization": f"Bearer {self.api_key}"}
    with file_path.open("rb") as f:
      resp = requests.post(url, headers=headers, files={"file": (file_path.name, f)}, timeout=120)
    resp.raise_for_status()
    data = resp.json()
    # API 返回结构可能有多种形式：
    # 1) {"code": 0, "data": {"id": "...", "file_id": "..."}}
    # 2) {"code": 0, "data": [{"id": "...", "file_id": "..."}]}
    # 3) {"id": "...", "file_id": "..."}
    if isinstance(data, dict):
      if "code" in data and data.get("code") != 0:
        raise RuntimeError(f"上传文件失败: {data}")
      inner = data.get("data") or data
    else:
      inner = data

    # 如果 inner 是列表，取第一项
    if isinstance(inner, list):
      if not inner:
        raise RuntimeError(f"文件上传响应 data 为空列表: {data}")
      inner = inner[0]

    if not isinstance(inner, dict):
      raise RuntimeError(f"无法解析文件上传响应结构: {data}")

    file_id = inner.get("id") or inner.get("file_id")
    if not file_id:
      raise RuntimeError(f"无法从文件上传响应中获取 file_id: {data}")
    return str(file_id)

  def list_documents(self, dataset_id: str) -> List[Dict]:
    """List all documents in a dataset."""
    data = self._get_json(f"/api/v1/datasets/{dataset_id}/documents", params={"page": 1, "page_size": 200})
    if isinstance(data, dict):
      # 返回结构可能是 {"code": 0, "data": {"docs": [...]}} 或 {"data": [...]}
      inner = data.get("data") or {}
      if isinstance(inner, dict) and "docs" in inner:
        return inner["docs"]
      elif isinstance(inner, list):
        return inner
    return []

  def delete_document(self, dataset_id: str, document_id: str | List[str]) -> None:
    """Delete a document (or multiple documents) from a dataset."""
    # RagFlow API: DELETE /api/v1/datasets/{dataset_id}/documents with body {"ids": [...]}
    url = f"{self.api_url}/api/v1/datasets/{dataset_id}/documents"
    if isinstance(document_id, str):
      payload = {"ids": [document_id]}
    else:
      payload = {"ids": document_id}
    resp = requests.delete(url, headers=self.headers_json, json=payload, timeout=60)
    resp.raise_for_status()
    result = resp.json()
    if isinstance(result, dict) and result.get("code") not in (None, 0):
      raise RuntimeError(f"删除文档失败: {result}")

  def convert_files_to_documents(self, file_ids: List[str], dataset_ids: List[str], expected_doc_name: str | None = None) -> List[Dict]:
    """
    Call /api/v1/file/convert to link files to datasets and create documents.
    
    Args:
        file_ids: List of file IDs to convert
        dataset_ids: List of dataset IDs to link to
        expected_doc_name: Expected document name (without .txt). If provided, will delete any existing documents with this name or variants before converting.
    """
    # 如果提供了预期文档名称，先删除所有同名变体
    if expected_doc_name and dataset_ids:
      for ds_id in dataset_ids:
        try:
          existing_docs = self.list_documents(ds_id)
          docs_to_delete = []
          for doc in existing_docs:
            doc_name = doc.get("name") or doc.get("document_name")
            if not doc_name:
              continue
            doc_base = doc_name[:-4] if doc_name.endswith(".txt") else doc_name
            # 检查是否是预期名称或其变体
            if doc_base == expected_doc_name:
              docs_to_delete.append(doc.get("id") or doc.get("document_id"))
            elif doc_base.startswith(expected_doc_name + "(") and doc_base.endswith(")"):
              import re
              match = re.match(rf"^{re.escape(expected_doc_name)}\((\d+)\)$", doc_base)
              if match:
                docs_to_delete.append(doc.get("id") or doc.get("document_id"))
          
          if docs_to_delete:
            try:
              self.delete_document(ds_id, docs_to_delete)
              import time
              time.sleep(1.0)  # 等待删除完成
            except Exception as e:
              print(f"[WARN] 删除同名文档失败: {e}")
        except Exception as e:
          print(f"[WARN] 检查同名文档失败: {e}")
    
    payload = {"file_ids": file_ids, "kb_ids": dataset_ids}
    data = self._post_json("/api/v1/file/convert", payload)
    # 预期结构: {"code": 0, "data": [{"id": "...", "file_id": "...", "document_id": "..."}]}
    if isinstance(data, dict):
      return data.get("data") or []
    return []

  def add_chunk(
    self,
    dataset_id: str,
    document_id: str,
    content: str,
    important_keywords: List[str] | None = None,
    questions: List[str] | None = None,
  ) -> Dict:
    """Call /api/v1/datasets/{dataset_id}/documents/{document_id}/chunks to add a chunk."""
    path = f"/api/v1/datasets/{dataset_id}/documents/{document_id}/chunks"
    payload: Dict[str, Any] = {"content": content}
    if important_keywords:
      payload["important_keywords"] = important_keywords
    if questions:
      payload["questions"] = questions
    data = self._post_json(path, payload)
    # 预期结构：{"code": 0, "data": {...}} 或直接返回 chunk 对象
    if isinstance(data, dict) and data.get("code") not in (None, 0):
      raise RuntimeError(f"创建 chunk 失败: {data}")
    return data


def build_temp_docs_from_segments(
  segments: List[SegmentRow],
  tmp_root: Path,
) -> Dict[Tuple[str, str], Path]:
  """Group segments by (dataset_name, document_name) and build temp txt files."""
  grouped: Dict[Tuple[str, str], List[SegmentRow]] = defaultdict(list)
  for row in segments:
    if not row.dataset_name or not row.document_name or not row.content:
      continue
    grouped[(row.dataset_name, row.document_name)].append(row)

  tmp_root.mkdir(parents=True, exist_ok=True)
  doc_files: Dict[Tuple[str, str], Path] = {}

  for (ds_name, doc_name), rows in grouped.items():
    # 按 segment_index 排序，拼成一个大文本
    rows_sorted = sorted(rows, key=lambda r: r.segment_index)
    parts = [r.content for r in rows_sorted if r.content]
    text = "\n\n".join(parts)
    if not text.strip():
      continue

    # 构造相对安全、长度可控的文件名：
    # - 替换斜杠
    # - 截断过长部分
    # - 追加短哈希防止冲突
    safe_ds = ds_name.replace("/", "_")
    safe_doc = doc_name.replace("/", "_")
    base_name = f"{safe_ds}__{safe_doc}"
    if len(base_name) > 120:
      digest = hashlib.sha1(base_name.encode("utf-8")).hexdigest()[:8]
      safe_ds_short = safe_ds[:40]
      safe_doc_short = safe_doc[:40]
      base_name = f"{safe_ds_short}__{safe_doc_short}__{digest}"

    file_path = tmp_root / f"{base_name}.txt"
    file_path.write_text(text, encoding="utf-8")
    doc_files[(ds_name, doc_name)] = file_path

  return doc_files


def sync_datasets_json_from_api(
  datasets_json_path: Path,
  api_url: str,
  api_key: str,
) -> Dict[str, Dict[str, any]]:
  """
  从 RagFlow API 同步所有当前 API key 有权限的数据集和文档到 datasets.json。
  
  返回: 同步后的数据集映射
  """
  headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
  datasets_result = {}
  page = 1
  page_size = 100
  
  print(f"[INFO] 正在从 RagFlow API 同步数据集...")
  
  # 1. 获取所有数据集
  while True:
    try:
      response = requests.get(
        f"{api_url}/api/v1/datasets",
        headers=headers,
        params={"page": page, "page_size": page_size},
        timeout=30
      )
      response.raise_for_status()
      data = response.json()
      
      api_datasets = data.get("data", {}).get("data", []) if isinstance(data.get("data"), dict) else data.get("data", [])
      if not api_datasets and isinstance(data, list):
        api_datasets = data
      
      if not api_datasets:
        break
      
      # 2. 对每个数据集，获取其文档列表
      for ds in api_datasets:
        ds_id = ds.get("id") or ds.get("dataset_id") or ds.get("_id")
        ds_name = ds.get("name") or ds.get("dataset_name") or "Unknown"
        
        if not ds_id:
          continue
        
        # 获取文档列表
        docs_map = {}
        docs_page = 1
        while True:
          try:
            docs_response = requests.get(
              f"{api_url}/api/v1/datasets/{ds_id}/documents",
              headers=headers,
              params={"page": docs_page, "page_size": 200},
              timeout=30
            )
            docs_response.raise_for_status()
            docs_data = docs_response.json()
            
            # RagFlow API 返回格式: {"code": 0, "data": {"docs": [...], "total": ...}}
            docs_list = []
            if isinstance(docs_data, dict) and docs_data.get("code") == 0:
              data_obj = docs_data.get("data", {})
              if isinstance(data_obj, dict):
                docs_list = data_obj.get("docs", []) or data_obj.get("data", [])
              elif isinstance(data_obj, list):
                docs_list = data_obj
            elif isinstance(docs_data, list):
              docs_list = docs_data
            
            if not docs_list:
              break
            
            for doc in docs_list:
              doc_id = doc.get("id") or doc.get("document_id")
              doc_name = doc.get("name") or doc.get("document_name") or "Unknown"
              if doc_id:
                docs_map[doc_name] = doc_id
            
            # 检查是否还有更多页
            if isinstance(docs_data, dict) and docs_data.get("code") == 0:
              data_obj = docs_data.get("data", {})
              if isinstance(data_obj, dict):
                total = data_obj.get("total", 0)
                if docs_page * 200 >= total:
                  break
            else:
              break
            docs_page += 1
          except Exception as e:
            print(f"[WARN] 获取数据集 {ds_name} ({ds_id}) 的文档列表失败: {e}")
            break
        
        datasets_result[ds_name] = {
          "id": ds_id,
          "documents": docs_map
        }
      
      # 检查是否还有更多页
      total_datasets = data.get("total_datasets", 0) or data.get("total", 0)
      if page * page_size >= total_datasets:
        break
      page += 1
      
    except Exception as e:
      print(f"[ERROR] 获取数据集列表失败: {e}")
      break
  
  # 3. 写入 datasets.json
  datasets_json_path.write_text(
    json.dumps(datasets_result, ensure_ascii=False, indent=2),
    encoding="utf-8"
  )
  
  total_docs = sum(len(ds["documents"]) for ds in datasets_result.values())
  print(f"[OK] 已同步 datasets.json: {len(datasets_result)} 个数据集, {total_docs} 个文档")
  
  return datasets_result


def update_datasets_json(
  datasets_json_path: Path,
  mapping: Dict[str, Tuple[str, Dict[str, str]]],
  api_url: str,
  api_key: str,
) -> None:
  """
  合并新导入的数据集/文档映射到 datasets.json。
  先同步当前 API key 的所有数据集，然后合并新导入的数据。
  """
  # 1. 先同步当前 API key 的所有数据集（删除旧的无权限数据）
  existing = sync_datasets_json_from_api(datasets_json_path, api_url, api_key)
  
  # 2. 合并新导入的数据
  for theme, (dataset_id, documents) in mapping.items():
    if theme not in existing or not isinstance(existing[theme], dict):
      existing[theme] = {"id": dataset_id, "documents": {}}
    if "id" not in existing[theme] or not existing[theme]["id"]:
      existing[theme]["id"] = dataset_id
    docs_map = existing[theme].get("documents") or {}
    docs_map.update(documents)
    existing[theme]["documents"] = docs_map
  
  # 3. 写入更新后的数据
  datasets_json_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
  print(f"[OK] 已更新 datasets.json，包含 {len(existing)} 个数据集")


def main() -> None:
  load_dotenv()

  api_url = os.getenv("RAGFLOW_API_URL", "").rstrip("/")
  api_key = os.getenv("RAGFLOW_API_KEY", "")
  if not api_url or not api_key:
    raise RuntimeError("请先在 backend/.env 中配置 RAGFLOW_API_URL 和 RAGFLOW_API_KEY")

  base_dir = Path(__file__).resolve().parent.parent
  seg_csv = base_dir / "data" / "ragflow_import" / "segments.csv"
  if not seg_csv.exists():
    raise FileNotFoundError(f"未找到 segments.csv: {seg_csv}")

  segments = load_segments(seg_csv)
  if not segments:
    raise RuntimeError("segments.csv 中没有有效数据")

  tmp_root = base_dir / "data" / "ragflow_import" / "tmp_docs"
  doc_files = build_temp_docs_from_segments(segments, tmp_root)
  if not doc_files:
    raise RuntimeError("未能从 segments.csv 生成任何临时文档")

  importer = RagFlowImporter(api_url, api_key)
  
  # ========== 步骤 1: 删除 ==========
  # 同步 datasets.json，删除当前 API key 无权限的旧数据集
  datasets_json_path = base_dir / "datasets.json"
  print(f"\n{'='*80}")
  print("[步骤 1/2] 删除：同步 datasets.json，清理无权限的旧数据集")
  print(f"{'='*80}")
  sync_datasets_json_from_api(datasets_json_path, api_url, api_key)

  # ========== 步骤 2: 上传 ==========
  print(f"\n{'='*80}")
  print("[步骤 2/2] 上传：导入新数据到 RagFlow")
  print(f"{'='*80}")

  # dataset_name -> dataset_id
  dataset_ids: Dict[str, str] = {}
  # theme -> (dataset_id, {doc_name: doc_id})  # 供 datasets.json 使用
  theme_mapping: Dict[str, Tuple[str, Dict[str, str]]] = {}
  # (dataset_name, document_name) -> document_id  # 供后续写 chunk 使用
  doc_ids: Dict[Tuple[str, str], str] = {}

  for (ds_name, doc_name), file_path in doc_files.items():
    # 1) 找或建 dataset（如果已存在同名数据集，先删除它以避免名称冲突）
    if ds_name not in dataset_ids:
      ds_id = importer.find_or_create_dataset(ds_name, delete_existing=True)
      dataset_ids[ds_name] = ds_id
    else:
      ds_id = dataset_ids[ds_name]

    # 1.5) 检查并删除同名文档（包括所有变体：doc_name, doc_name(1), doc_name(2) 等）
    import time
    import re
    
    # 构建预期的文档名称格式（RagFlow 中可能是 "数据集名__文档名.txt" 或 "数据集名__文档名(数字).txt"）
    # 从 file_path 提取文件名（不含路径和扩展名）
    expected_base_name = file_path.stem  # 例如: "eCoder 用户手册__eCoder编码器用户手册V2.4__13"
    
    # 多次尝试删除，确保彻底清理
    max_retries = 3
    for attempt in range(max_retries):
      existing_docs = importer.list_documents(ds_id)
      docs_to_delete = []
      
      for doc in existing_docs:
        doc_name_in_ragflow = doc.get("name") or doc.get("document_name")
        doc_id_to_delete = doc.get("id") or doc.get("document_id")
        
        if not doc_name_in_ragflow or not doc_id_to_delete:
          continue
        
        # 提取文档名称的基础部分（去掉 .txt 扩展名）
        doc_base = doc_name_in_ragflow
        if doc_base.endswith(".txt"):
          doc_base = doc_base[:-4]
        
        # 检查是否是预期文档名称或其变体
        # 1. 精确匹配
        if doc_base == expected_base_name:
          docs_to_delete.append((doc_id_to_delete, doc_name_in_ragflow))
        # 2. 检查是否是变体（如 expected_base_name(1), expected_base_name(2) 等）
        elif doc_base.startswith(expected_base_name + "(") and doc_base.endswith(")"):
          # 验证括号内是数字
          match = re.match(rf"^{re.escape(expected_base_name)}\((\d+)\)$", doc_base)
          if match:
            docs_to_delete.append((doc_id_to_delete, doc_name_in_ragflow))
      
      if not docs_to_delete:
        # 没有需要删除的文档，退出循环
        break
      
      # 批量删除所有同名文档变体
      delete_ids = [doc_id for doc_id, _ in docs_to_delete]
      try:
        # 使用批量删除
        importer.delete_document(ds_id, delete_ids)
        print(f"[INFO] [尝试 {attempt + 1}/{max_retries}] 已删除 {len(docs_to_delete)} 个同名文档变体: {[doc_name for _, doc_name in docs_to_delete[:5]]}{'...' if len(docs_to_delete) > 5 else ''} (dataset: {ds_name})")
        # 等待删除完成并让数据库更新
        time.sleep(1.0)
        
        # 验证删除是否成功（重新查询）
        remaining_docs = importer.list_documents(ds_id)
        remaining_names = {doc.get("name") or doc.get("document_name") for doc in remaining_docs}
        deleted_names = {doc_name for _, doc_name in docs_to_delete}
        still_exist = remaining_names & deleted_names
        
        if still_exist:
          print(f"[WARN] 仍有 {len(still_exist)} 个文档未完全删除: {list(still_exist)[:3]}...")
          if attempt < max_retries - 1:
            time.sleep(1.0)  # 再等待一下
            continue
        else:
          # 删除成功，退出循环
          break
          
      except Exception as e:
        print(f"[WARN] 批量删除同名文档失败: {e}，尝试逐个删除...")
        # 回退到逐个删除
        for doc_id, doc_name_var in docs_to_delete:
          try:
            importer.delete_document(ds_id, doc_id)
            time.sleep(0.3)
          except Exception as e2:
            print(f"[WARN] 删除文档变体失败 {doc_name_var}: {e2}")
        
        if attempt < max_retries - 1:
          time.sleep(1.0)
          continue

    # 2) 上传文件（在上传前先删除所有同名文档变体）
    expected_base_name = file_path.stem  # 例如: "eCoder 用户手册__eCoder编码器用户手册V2.4__13"
    file_id = importer.upload_file(file_path, expected_doc_name=expected_base_name, dataset_id=ds_id)

    # 3) 转换为文档并挂到 dataset
    convert_results = importer.convert_files_to_documents([file_id], [ds_id])
    doc_id = None
    if convert_results:
      one = convert_results[0]
      doc_id = one.get("document_id")
    if not doc_id:
      # 有些版本可能直接在 convert 之后通过列表接口获取文档 ID，这里简化为必需存在
      raise RuntimeError(f"无法从 file/convert 响应中获取 document_id: {convert_results}")

    # 记录到 theme 映射
    mapping_entry = theme_mapping.get(ds_name)
    if not mapping_entry:
      theme_mapping[ds_name] = (ds_id, {doc_name: doc_id})
    else:
      _, docs_map = mapping_entry
      docs_map[doc_name] = doc_id

    # 记录文档 ID
    doc_ids[(ds_name, doc_name)] = doc_id

  # 4) 使用 chunks API 将 segments.csv 中的每一条内容写入对应文档
  for row in segments:
    ds_id = dataset_ids.get(row.dataset_name)
    doc_id = doc_ids.get((row.dataset_name, row.document_name))
    if not ds_id or not doc_id:
      # 如果找不到对应的文档，跳过这一条
      continue
    if not row.content:
      continue
    # 构造 important_keywords，便于后续在检索结果中解析章节信息：
    # [0] 知识库名称 (dataset)，[1] 大章标题/编号 (document_name)，[2] 再次附加一份，方便使用 ChapterMatcher 提取章节号
    keywords = [row.dataset_name, row.document_name, row.document_name]
    importer.add_chunk(ds_id, doc_id, row.content, important_keywords=keywords)

  # 5) 更新 backend/datasets.json（合并新导入的数据）
  update_datasets_json(datasets_json_path, theme_mapping, api_url, api_key)

  print(f"[OK] 已导入 {len(doc_files)} 个文档并写入 {len(segments)} 个 chunks 到 RagFlow，并更新 {datasets_json_path}")


if __name__ == "__main__":
  main()


