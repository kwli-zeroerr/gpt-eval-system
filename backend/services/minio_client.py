"""MinIO API client for reading files from knowledge bucket via S3-compatible API.

Data layout definition (knowledge bucket):

Each object represents one OCR result (one company manual) with the following
logical structure INSIDE the OCR result file (typically JSON):

1. pdf        : the source PDF document (metadata or path)
2. images     : list of page images
3. page_results: OCR results per page (structured)
4. pages      : raw OCR text per page
5. segment    : segmented content (per big/small chapter)
6. tables     : images of tables
7. text       : full OCR text content

For question generation and retrieval, we mainly care about the text hierarchy:
pdf -> page_results -> segment (file -> big chapter -> small chapter).

This client focuses on extracting useful text chunks from such OCR results.
"""

import os
from typing import List, Optional, Tuple
from pathlib import Path
from dotenv import load_dotenv
from minio import Minio
from minio.error import S3Error
import json
from services.chapter_matcher import ChapterMatcher

# Load environment variables
load_dotenv()


class MinIOClient:
    """Client for accessing MinIO knowledge bucket via S3-compatible API."""

    def __init__(self):
        endpoint = os.getenv("MINIO_ENDPOINT", "localhost:9000")
        access_key = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
        secret_key = os.getenv("MINIO_SECRET_KEY", "zero0000")
        self.bucket_name = os.getenv("MINIO_BUCKET_NAME", "knowledge")
        secure = os.getenv("MINIO_SECURE", "false").lower() == "true"

        # Initialize MinIO client using S3-compatible API
        self.client = Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
        )

    def list_files(self, prefix: Optional[str] = None, max_items: int = 100) -> List[str]:
        """List files in knowledge bucket via MinIO API.

        - If prefix is provided, only list objects under that prefix.
        - If prefix is None, list all objects and filter for OCR result files (containing 'ocr_result/').
        - Only returns files from segments/ directory (not page_results/).
        """
        try:
            files = []
            
            if prefix is None:
                # 列出所有文件，然后过滤出包含 ocr_result/ 的文件
                # 文件路径格式可能是：{dataset_id}/ocr_result/{subdir}/segments/segment_XXX.mmd
                objects = self.client.list_objects(
                    self.bucket_name, recursive=True
                )
                for obj in objects:
                    if len(files) >= max_items:
                        break
                    # 只返回包含 ocr_result/ 的文件，排除其他格式（如 6e20ee5f-68c6-4990-8cee-398cb13bf23f/file/）
                    if "/ocr_result/" in obj.object_name:
                        # 只返回 segments/ 目录下的 .mmd 文件（用于生成问题）
                        # 排除 page_results/ 目录下的文件
                        if obj.object_name.endswith(".mmd") and "/segments/" in obj.object_name:
                            files.append(obj.object_name)
            else:
                # 如果指定了 prefix，直接使用，但仍只返回 segments/ 目录下的文件
                objects = self.client.list_objects(
                    self.bucket_name, prefix=prefix, recursive=True
                )
                for obj in objects:
                    if len(files) >= max_items:
                        break
                    # 只返回 segments/ 目录下的文件
                    if "/segments/" in obj.object_name and obj.object_name.endswith(".mmd"):
                        files.append(obj.object_name)
            
            return files
        except S3Error as e:
            print(f"Error listing MinIO files via API: {e}")
            return []

    def get_file_content(self, object_name: str, max_size: int = 10 * 1024 * 1024) -> Optional[str]:
        """Get file content from MinIO via API as text."""
        try:
            response = self.client.get_object(self.bucket_name, object_name)
            content = response.read(max_size)
            response.close()
            response.release_conn()
            # Try to decode as UTF-8 text
            try:
                return content.decode("utf-8")
            except UnicodeDecodeError:
                # If not text, return None (binary file)
                return None
        except S3Error as e:
            print(f"Error reading MinIO file {object_name} via API: {e}")
            return None

    def _extract_segments_from_ocr_result(
        self, content: str, object_name: str
    ) -> List[Tuple[str, str]]:
        """Try to interpret content as a structured OCR result JSON and extract segments.

        Expected high-level schema inside the JSON (keys are optional, format may vary):
        - pdf: source document info
        - images: list of page images
        - page_results: per-page OCR results (structured)
        - pages: per-page raw OCR text
        - segment: segmented content (per big/small chapter)
        - tables: table images
        - text: full OCR text

        We primarily use:
        - segment: fine-grained chunks (preferred)
        - text: as a fallback single long chunk
        """
        segments: List[Tuple[str, str]] = []

        try:
            data = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return []

        # 推断当前 OCR 结果关联的 PDF 名称，作为"dataset/theme" 基础
        # 格式参考 ragflow_import_from_segments：只使用 ocr_result 中的 PDF 名称
        pdf_path = data.get("pdf") or data.get("source_file") or ""
        dataset_name = ""
        if isinstance(pdf_path, str) and pdf_path.strip():
            # 提取 PDF 文件名（去掉路径）
            dataset_name = Path(pdf_path).name  # e.g. "eCoder编码器用户手册V2.4.pdf"
        else:
            # 如果 OCR 结果中没有 PDF 路径，尝试从 object_name 提取
            # object_name 格式可能是：ocr_result/xxx/xxx.json 或 ocr_result/xxx.pdf.json
            # 提取文件名部分（去掉 .json 扩展名和路径）
            if "ocr_result" in object_name or "/" in object_name:
                # 提取最后一部分作为文件名
                name_part = Path(object_name).stem  # 去掉 .json
                # 如果 name_part 是 PDF 文件名，使用它；否则使用整个文件名
                if name_part.endswith(".pdf"):
                    dataset_name = name_part
                else:
                    # 尝试从路径中提取 PDF 名称
                    parts = object_name.split("/")
                    for part in reversed(parts):
                        if part.endswith(".pdf") or part.endswith(".pdf.json"):
                            dataset_name = part.replace(".json", "")
                            break
                    if not dataset_name:
                        dataset_name = name_part
            else:
                dataset_name = object_name

        # Prefer segmented content if available
        seg_data = data.get("segment")
        if isinstance(seg_data, list):
            for idx, seg in enumerate(seg_data):
                text = ""
                heading = ""
                if isinstance(seg, dict):
                    # Common keys for segment text
                    text = (
                        seg.get("text")
                        or seg.get("content")
                        or seg.get("segment_text")
                        or ""
                    )
                    heading = (seg.get("heading") or seg.get("title") or "").strip()
                elif isinstance(seg, str):
                    text = seg

                if text and text.strip():
                    # 从 heading 或正文中抽取章节号，作为 chunk 对应的小节标记
                    chapter_info = ""
                    if heading:
                        chapter_info = ChapterMatcher.extract_chapter_info(heading) or ""
                    if not chapter_info:
                        chapter_info = ChapterMatcher.extract_chapter_info(text) or ""

                    # reference 结构："<dataset_name>|<chapter_info>"（chapter_info 可能为空）
                    if chapter_info:
                        ref = f"{dataset_name}|{chapter_info}"
                    else:
                        ref = dataset_name

                    segments.append((text.strip(), ref))

        # If no segment extracted, fall back to full text content
        if not segments:
            full_text = data.get("text")
            if isinstance(full_text, str) and full_text.strip():
                # 只有整本全文时，同样使用 dataset_name 作为 reference
                segments.append((full_text.strip(), dataset_name))

        return segments

    def get_file_chunks(
        self,
        object_names: Optional[List[str]] = None,
        max_chunks: int = 3,
        chunk_size: int = 3000,
    ) -> List[Tuple[str, str]]:
        """Get text chunks from OCR results in knowledge bucket via MinIO API.

        Returns list of tuples: (chunk_text, reference)
        where reference is object_name or object_name:segment:index.
        """
        chunks: List[Tuple[str, str]] = []

        if object_names is None:
            # List files if not specified (could be multiple ocr_result files)
            object_names = self.list_files(max_items=50)

        if not object_names:
            print("Warning: No files found in MinIO knowledge bucket")
            return []

        for obj_name in object_names:
            if len(chunks) >= max_chunks:
                break

            content = self.get_file_content(obj_name)
            if not content:
                continue

            # .mmd 文件是纯文本格式，直接使用内容作为 segment
            if obj_name.endswith(".mmd"):
                # 从路径中提取 PDF 名称和章节信息
                # 路径格式：{dataset_id}/ocr_result/{subdir}/page_results/page_XXX.mmd
                parts = obj_name.split("/")
                pdf_name = ""
                for part in parts:
                    if part.endswith(".pdf"):
                        pdf_name = part
                        break
                
                # 如果没有找到 PDF 名称，尝试从路径推断
                if not pdf_name:
                    # 查找包含 PDF 名称的部分
                    for part in reversed(parts):
                        if ".pdf" in part:
                            pdf_name = part.split(".")[0] + ".pdf"
                            break
                
                # 提取章节信息（从文件名或内容）
                chapter_info = ChapterMatcher.extract_chapter_info(content[:500]) or ""
                
                # 构建 reference
                if chapter_info:
                    ref = f"{pdf_name}|{chapter_info}" if pdf_name else chapter_info
                else:
                    ref = pdf_name if pdf_name else obj_name
                
                # 将整个 .mmd 文件内容作为一个 chunk
                if content.strip():
                    chunks.append((content.strip(), ref))
                continue

            # First try to interpret as structured OCR result JSON
            ocr_segments = self._extract_segments_from_ocr_result(content, obj_name)
            if ocr_segments:
                for seg_text, ref in ocr_segments:
                    if len(chunks) >= max_chunks:
                        break
                    if seg_text and len(seg_text.strip()) > 50:
                        chunks.append((seg_text.strip(), ref))
                # Move to next object once we've used segments from this one
                continue

            # Fallback: treat as plain text file and do simple character-based chunking
            chunk_index = 0
            for i in range(0, len(content), chunk_size):
                if len(chunks) >= max_chunks:
                    break
                chunk = content[i : i + chunk_size]
                if chunk.strip() and len(chunk.strip()) > 50:
                    reference = f"{obj_name}:{chunk_index}" if chunk_index > 0 else obj_name
                    chunks.append((chunk.strip(), reference))
                    chunk_index += 1

        return chunks
