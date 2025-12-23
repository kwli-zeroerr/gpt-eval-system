"""RagFlow cleanup helper.

用途：
- 列出当前 RagFlow 中的知识库（datasets）
- 按名称精确或前缀删除指定知识库，方便“重置后重新导入”

使用示例（在 backend 目录下，已配置 .env）：

  # 列出所有数据集
  python -m scripts.ragflow_cleanup --list

  # 精确删除一个数据集（例如 "eCoder 用户手册"）
  python -m scripts.ragflow_cleanup --delete-dataset "eCoder 用户手册"

  # 按前缀批量删除（例如所有以 "eCoder" 开头的知识库）
  python -m scripts.ragflow_cleanup --delete-prefix "eCoder"
"""

from __future__ import annotations

import argparse
import os
from typing import Any, Dict, List

import requests
from dotenv import load_dotenv


def _request(
    method: str,
    api_url: str,
    api_key: str,
    path: str,
    params: Dict[str, Any] | None = None,
    json_body: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    url = f"{api_url.rstrip('/')}{path}"
    headers = {"Authorization": f"Bearer {api_key}"}
    if json_body is not None:
        headers["Content-Type"] = "application/json"
    resp = requests.request(method, url, headers=headers, params=params, json=json_body, timeout=60)
    resp.raise_for_status()
    if resp.content:
        try:
            result = resp.json()
            # 检查 RagFlow API 的 code 字段
            if isinstance(result, dict) and "code" in result:
                if result.get("code") != 0:
                    error_msg = result.get("message", "Unknown error")
                    raise RuntimeError(f"RagFlow API error (code={result.get('code')}): {error_msg}")
            return result
        except Exception as e:
            if isinstance(e, RuntimeError):
                raise
            return {}
    return {}


def list_datasets(api_url: str, api_key: str) -> List[Dict[str, Any]]:
    """获取所有数据集（遍历所有分页）"""
    all_datasets: List[Dict[str, Any]] = []
    page = 1
    page_size = 200
    
    while True:
        data = _request("GET", api_url, api_key, "/api/v1/datasets", params={"page": page, "page_size": page_size})
        
        # 解析响应
        datasets = []
        if isinstance(data, dict):
            # RagFlow 可能返回 {"code": 0, "data": [...]} 或 {"data": [...]}
            if "data" in data:
                datasets = data["data"]
            elif "datasets" in data:
                datasets = data["datasets"]
            # 检查是否有 total 或 total_count 字段来判断是否还有更多页
            total = data.get("total") or data.get("total_count") or data.get("count")
        elif isinstance(data, list):
            datasets = data
            total = None
        
        all_datasets.extend(datasets)
        
        # 如果这一页的数据少于 page_size，说明已经是最后一页
        if len(datasets) < page_size:
            break
        
        # 如果有 total 信息，检查是否已经获取完所有数据
        if total is not None and len(all_datasets) >= total:
            break
        
        page += 1
    
    return all_datasets


def delete_datasets_batch(api_url: str, api_key: str, dataset_ids: List[str] | None) -> Dict[str, Any]:
    """批量删除数据集（使用 RagFlow 的批量删除 API）"""
    # 如果 dataset_ids 为空列表，不删除任何东西
    if dataset_ids is not None and len(dataset_ids) == 0:
        return {"message": "No datasets to delete"}
    
    # 使用批量删除 API: DELETE /api/v1/datasets with body {"ids": [...]}
    return _request("DELETE", api_url, api_key, "/api/v1/datasets", json_body={"ids": dataset_ids})


def delete_dataset(api_url: str, api_key: str, dataset_id: str) -> None:
    """单个删除数据集（兼容旧接口）"""
    _request("DELETE", api_url, api_key, f"/api/v1/datasets/{dataset_id}")


def main() -> None:
    load_dotenv()
    api_url = os.getenv("RAGFLOW_API_URL", "").rstrip("/")
    api_key = os.getenv("RAGFLOW_API_KEY", "")
    if not api_url or not api_key:
        raise RuntimeError("请先在 backend/.env 中配置 RAGFLOW_API_URL 和 RAGFLOW_API_KEY")

    parser = argparse.ArgumentParser(description="RagFlow cleanup helper")
    parser.add_argument("--list", action="store_true", help="列出所有数据集")
    parser.add_argument("--delete-dataset", type=str, help="按名称精确删除某个数据集")
    parser.add_argument("--delete-prefix", type=str, help="删除名称以该前缀开头的所有数据集")
    parser.add_argument("--delete-all", action="store_true", help="删除所有数据集（危险操作，需确认）")
    args = parser.parse_args()

    datasets = list_datasets(api_url, api_key)

    if args.list or (not args.delete_dataset and not args.delete_prefix and not args.delete_all):
        print("=== 当前数据集列表 ===")
        for ds in datasets:
            ds_id = ds.get("id") or ds.get("dataset_id") or ds.get("_id")
            name = ds.get("name") or ds.get("dataset_name")
            count = ds.get("doc_num") or ds.get("documents_count") or ds.get("file_num")
            print(f"- id={ds_id}  name={name}  docs={count}")
        return

    to_delete: List[Dict[str, Any]] = []
    if args.delete_all:
        to_delete = datasets
    elif args.delete_dataset:
        name_target = args.delete_dataset.strip()
        to_delete = [ds for ds in datasets if (ds.get("name") or ds.get("dataset_name")) == name_target]
    elif args.delete_prefix:
        prefix = args.delete_prefix.strip()
        to_delete = [ds for ds in datasets if (ds.get("name") or ds.get("dataset_name") or "").startswith(prefix)]

    if not to_delete:
        print("未找到需要删除的知识库。")
        return

    print(f"\n将删除以下 {len(to_delete)} 个数据集：")
    dataset_ids_to_delete: List[str] = []
    for ds in to_delete:
        ds_id = ds.get("id") or ds.get("dataset_id") or ds.get("_id")
        name = ds.get("name") or ds.get("dataset_name")
        if ds_id:
            dataset_ids_to_delete.append(ds_id)
            print(f"- id={ds_id}  name={name}")
        else:
            print(f"- [WARN] 跳过无ID的数据集: {name}")

    if not dataset_ids_to_delete:
        print("没有有效的数据集ID，无法删除。")
        return

    confirm = input("\n确认删除？输入 'yes' 确认：").strip().lower()
    if confirm != "yes":
        print("已取消删除操作。")
        return

    # 使用批量删除 API（更高效且可靠）
    print(f"\n正在批量删除 {len(dataset_ids_to_delete)} 个数据集...")
    try:
        result = delete_datasets_batch(api_url, api_key, dataset_ids_to_delete)
        print(f"[OK] 批量删除请求已提交")
        if isinstance(result, dict):
            if "message" in result:
                print(f"  消息: {result['message']}")
            if "success_count" in result:
                print(f"  成功删除: {result['success_count']} 个")
            if "errors" in result:
                errors = result["errors"]
                if errors:
                    print(f"  错误: {len(errors)} 个")
                    for err in errors[:5]:  # 只显示前5个错误
                        print(f"    - {err}")
    except Exception as e:
        print(f"[FAIL] 批量删除失败: {e}")
        print("尝试逐个删除...")
        # 回退到逐个删除
        success_count = 0
        for ds in to_delete:
            ds_id = ds.get("id") or ds.get("dataset_id") or ds.get("_id")
            name = ds.get("name") or ds.get("dataset_name")
            if not ds_id:
                continue
            try:
                delete_dataset(api_url, api_key, ds_id)
                print(f"[OK] 已删除数据集: {name} ({ds_id})")
                success_count += 1
            except Exception as e:
                print(f"[FAIL] 删除数据集失败: {name} ({ds_id}) - {e}")
        print(f"\n删除完成: {success_count}/{len(dataset_ids_to_delete)} 成功")

    # 验证删除结果
    print("\n验证删除结果...")
    remaining = list_datasets(api_url, api_key)
    remaining_ids = {ds.get("id") or ds.get("dataset_id") or ds.get("_id") for ds in remaining}
    deleted_ids = set(dataset_ids_to_delete) - remaining_ids
    still_exist = remaining_ids & set(dataset_ids_to_delete)
    
    if deleted_ids:
        print(f"[OK] 确认已删除: {len(deleted_ids)} 个数据集")
    if still_exist:
        print(f"[WARN] 以下数据集仍然存在（可能删除失败）:")
        for ds in remaining:
            ds_id = ds.get("id") or ds.get("dataset_id") or ds.get("_id")
            if ds_id in still_exist:
                name = ds.get("name") or ds.get("dataset_name")
                print(f"  - {name} ({ds_id})")
    
    print(f"\n当前剩余数据集总数: {len(remaining)}")


if __name__ == "__main__":
    main()


