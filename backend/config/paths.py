"""Centralized data path configuration for GPT-Evaluation System backend."""
from pathlib import Path

# Root data directory (relative to backend/)
DATA_ROOT = Path("data")

# Frontend-visible JSON logs (for format conversion module)
DATA_FRONTEND_DIR = DATA_ROOT / "frontend"

# Backend TXT logs (human-readable logs)
DATA_BACKEND_DIR = DATA_ROOT / "backend"

# Exported CSV files from format conversion
DATA_EXPORT_DIR = DATA_ROOT / "export"

# Retrieval output CSV files (with answers)
DATA_RETRIEVAL_DIR = DATA_ROOT / "retrieval"

# Evaluation results (CSV + summary JSON)
DATA_EVALUATION_DIR = DATA_ROOT / "evaluation"


