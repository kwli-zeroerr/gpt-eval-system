"""Export OCR segments from MinIO knowledge bucket into RagFlow import-friendly CSV.

Usage (from backend directory, after activating venv and configuring .env for MinIO):

    python -m scripts.export_minio_ocr_to_ragflow

逻辑说明（与 RagFlow 的 dataset -> document -> chunk 对齐）：

- MinIO knowledge 桶中的结构大致为：

  <case_id>/ocr_result/<ocr_task_id>/IO转接模块用户手册_V1.0.pdf
  <case_id>/ocr_result/<ocr_task_id>/segments/index.json
  <case_id>/ocr_result/<ocr_task_id>/segments/segment_001.mmd
  <case_id>/ocr_result/<ocr_task_id>/segments/segment_002.mmd
  ...

- 每个 OCR 结果有一个 `segments/index.json`，结构类似：

  {
    "source_file": "IO转接模块用户手册_V1.0.pdf",
    "segment_count": 13,
    "segments": [
      {
        "id": "segment_001",
        "heading": "1. 产品描述",
        "level": 1,
        "file": "ocr_result/077c1bfc/segments/segment_001.mmd",
        "preview": "..."
      },
      ...
    ]
  }

- 本脚本的映射规则：
  - dataset_name  = index.json 中的 `source_file`（即 PDF 文件名）
  - document_name = 每个 segment 的 `heading`，若为空则用 `id`（如 "segment_001"）
  - content       = 对应 `file` 指向的 .mmd 文件内容
  - segment_index = 在同一 document 下的序号（0, 1, 2, ...）

- 最终输出 CSV：backend/data/ragflow_import/segments.csv，列为：
  dataset_name, document_name, segment_index, content
"""

import csv
import json
from pathlib import Path
from typing import List, Tuple, Dict, Optional

from dotenv import load_dotenv

from services.minio_client import MinIOClient
from services.chapter_matcher import ChapterMatcher


def load_dataset_mapping(config_path: Optional[Path] = None) -> List[Dict[str, str]]:
    """加载数据集名称映射配置"""
    if config_path is None:
        base_dir = Path(__file__).resolve().parent.parent
        config_path = base_dir / "config" / "dataset_mapping.json"
    
    if not config_path.exists():
        # 如果配置文件不存在，返回空列表（使用默认行为）
        return []
    
    try:
        with config_path.open("r", encoding="utf-8") as f:
            config = json.load(f)
            return config.get("mappings", [])
    except (json.JSONDecodeError, KeyError, IOError) as e:
        print(f"[WARN] 无法加载数据集映射配置 {config_path}: {e}，使用默认行为")
        return []


def map_source_file_to_dataset_name(source_file: str, mappings: List[Dict[str, str]]) -> str:
    """根据映射规则将源文件名映射到数据集名称"""
    for mapping in mappings:
        pattern = mapping.get("pattern", "")
        if pattern and pattern in source_file:
            return mapping.get("dataset_name", source_file)
    # 默认返回原始文件名
    return source_file


def export_minio_ocr_to_ragflow_csv(output_dir: Path) -> Path:
    """Export OCR segments from MinIO to a single CSV suitable for RagFlow import."""
    load_dotenv()

    # 加载数据集名称映射配置
    base_dir = Path(__file__).resolve().parent.parent
    mapping_config_path = base_dir / "config" / "dataset_mapping.json"
    dataset_mappings = load_dataset_mapping(mapping_config_path)
    
    if dataset_mappings:
        print(f"[INFO] 已加载 {len(dataset_mappings)} 条数据集映射规则")
    else:
        print("[INFO] 未找到数据集映射配置，使用默认行为（使用原始文件名）")

    client = MinIOClient()
    # 列出 knowledge 桶中的所有对象
    all_objects = client.list_files(max_items=5000)

    # 找出所有 segments/index.json
    index_keys: List[str] = [
        name
        for name in all_objects
        if "/ocr_result/" in name and "/segments/index.json" in name
    ]

    rows: List[Tuple[str, str, int, str]] = []

    for index_key in index_keys:
        index_text = client.get_file_content(index_key)
        if not index_text:
            continue

        try:
            data = json.loads(index_text)
        except json.JSONDecodeError:
            continue

        if not isinstance(data, dict):
            continue

        # 推导当前 OCR 结果在 MinIO 中的根前缀，用于补全 seg_file
        # 典型 index_key: "<case_id>/ocr_result/<ocr_task_id>/segments/index.json"
        # root_prefix 应为 "<case_id>"
        parts = index_key.split("/")
        root_prefix = parts[0] if parts else ""

        source_file = data.get("source_file")
        if not source_file:
            # 回退：用 ocr_task_id 或 index_key 作为原始文件名
            source_file = data.get("ocr_task_id") or Path(index_key).stem

        source_file = str(source_file)

        # 根据映射规则将 source_file 映射到更抽象的主题名（dataset 名称）
        dataset_name = map_source_file_to_dataset_name(source_file, dataset_mappings)

        segments = data.get("segments") or []
        if not isinstance(segments, list):
            continue

        # segments 中每个元素包含：id, heading, file, preview 等
        for idx, seg in enumerate(segments):
            if not isinstance(seg, dict):
                continue

            seg_file = seg.get("file")
            if not seg_file:
                continue

            # 文档名：希望按“大章”聚合，例如：
            #   dataset = eCoder 用户手册
            #   document_name = eCoder编码器用户手册V2.4.pdf__13   （代表第 13 章）
            #   chunk       = heading 对应的小节内容（如 13.2 配件）
            heading = (seg.get("heading") or "").strip()
            seg_id = (seg.get("id") or "").strip()

            # 从 heading 中提取章节信息，如 "13.2 配件" -> "13.2" 或 "13"
            chapter_info = ChapterMatcher.extract_chapter_info(heading) or ""
            major_chapter = ""
            if chapter_info:
                # 如果是类似 "13.2" 的结构，只取第一层 "13" 作为大章编号
                if "." in chapter_info:
                    major_chapter = chapter_info.split(".")[0]
                else:
                    major_chapter = chapter_info

            pdf_base = Path(source_file).stem  # 去掉 .pdf
            if major_chapter:
                document_name = f"{pdf_base}__{major_chapter}"
            else:
                # 没有可靠章节信息时，退回到 heading / seg_id
                document_name = heading or seg_id or f"{pdf_base}__segment_{idx:03d}"

            # seg_file 在 index.json 中通常为相对路径（如 "ocr_result/077c1bfc/segments/segment_001.mmd"
            # 或仅 "segments/segment_001.mmd"），需要补上 case_id 前缀
            if root_prefix:
                seg_key = f"{root_prefix}/{seg_file.lstrip('/')}"
            else:
                seg_key = seg_file

            content = client.get_file_content(seg_key)
            if not content or not content.strip():
                continue

            rows.append((dataset_name, document_name, idx, content.strip()))

    output_dir.mkdir(parents=True, exist_ok=True)
    output_csv = output_dir / "segments.csv"

    with output_csv.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["dataset_name", "document_name", "segment_index", "content"])
        writer.writerows(rows)

    return output_csv


if __name__ == "__main__":
    base_dir = Path(__file__).resolve().parent.parent
    out_dir = base_dir / "data" / "ragflow_import"
    csv_path = export_minio_ocr_to_ragflow_csv(out_dir)
    print(f"[OK] 导出完成: {csv_path}")

