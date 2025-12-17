"""MinIO API client for reading files from knowledge bucket via S3-compatible API."""
import os
import io
from typing import List, Optional
from dotenv import load_dotenv
from minio import Minio
from minio.error import S3Error

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
        """List files in knowledge bucket via MinIO API."""
        try:
            objects = self.client.list_objects(
                self.bucket_name, prefix=prefix, recursive=True
            )
            files = []
            for obj in objects:
                if len(files) >= max_items:
                    break
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

    def get_file_chunks(
        self, object_names: Optional[List[str]] = None, max_chunks: int = 3, chunk_size: int = 3000
    ) -> List[tuple[str, str]]:
        """Get text chunks from files in knowledge bucket via MinIO API.
        
        Returns list of tuples: (chunk_text, reference)
        where reference is the object_name or object_name:chunk_index
        """
        chunks = []
        if object_names is None:
            # List all files if not specified
            object_names = self.list_files(max_items=50)  # Get more files for variety
        
        if not object_names:
            print("Warning: No files found in MinIO knowledge bucket")
            return []

        # Process files to get chunks
        for obj_name in object_names:
            if len(chunks) >= max_chunks:
                break
            content = self.get_file_content(obj_name)
            if content:
                # Simple chunking by character count
                chunk_index = 0
                for i in range(0, len(content), chunk_size):
                    if len(chunks) >= max_chunks:
                        break
                    chunk = content[i : i + chunk_size]
                    if chunk.strip() and len(chunk.strip()) > 50:  # Filter out very short chunks
                        # Create reference: filename or filename:chunk_index
                        reference = f"{obj_name}:{chunk_index}" if chunk_index > 0 else obj_name
                        chunks.append((chunk.strip(), reference))
                        chunk_index += 1

        return chunks
