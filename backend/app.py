import logging

import pyfiglet
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI

from routers.question_gen_routes import router as question_gen_router
from routers.question_edit_routes import router as question_edit_router
from routers.format_convert_routes import router as format_convert_router
from routers.retrieval_routes import router as retrieval_router
from routers.evaluation_routes import router as evaluation_router
from routers.pipeline_routes import router as pipeline_router
from routers.ragflow_routes import router as ragflow_router

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Disable httpx HTTP request logs (only show errors)
# This must be set before any httpx imports
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)  # httpcore is used by httpx

# Disable uvicorn access logs (GET/POST requests) - 只显示错误和警告
# 这样可以减少日志噪音，专注于评测相关的业务日志
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("uvicorn.access").disabled = False  # 仍然记录WARNING级别以上的访问错误

# 确保评测服务的日志清晰可见
logging.getLogger("services.evaluation_service").setLevel(logging.INFO)
logging.getLogger("services.ragas_evaluator").setLevel(logging.INFO)
logging.getLogger("services.retrieval_service").setLevel(logging.INFO)

# Print figlet banner on startup
figlet_text = pyfiglet.figlet_format("GPT-Evaluation System", font="standard")
print("\n" + figlet_text)
print("=" * 60)
print("GPT-Evaluation System - Backend Server")
print("=" * 60 + "\n")

app = FastAPI(title="GPT-Evaluation System", version="0.1.0")

# Mount routers
app.include_router(question_gen_router)
app.include_router(question_edit_router)
app.include_router(format_convert_router)
app.include_router(retrieval_router)
app.include_router(evaluation_router)
app.include_router(pipeline_router)
app.include_router(ragflow_router)


if __name__ == "__main__":  # pragma: no cover - manual run helper
    # For development, use uvicorn directly
    # For production, use: gunicorn -c gunicorn_config.py app:app
    uvicorn.run("app:app", host="0.0.0.0", port=8180, reload=True)

