import logging

import pyfiglet
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI

from routers.question_gen_routes import router as question_gen_router
from routers.format_convert_routes import router as format_convert_router
from routers.retrieval_routes import router as retrieval_router
from routers.evaluation_routes import router as evaluation_router
from routers.pipeline_routes import router as pipeline_router

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Print figlet banner on startup
figlet_text = pyfiglet.figlet_format("GPT-Evaluation System", font="standard")
print("\n" + figlet_text)
print("=" * 60)
print("GPT-Evaluation System - Backend Server")
print("=" * 60 + "\n")

app = FastAPI(title="GPT-Evaluation System", version="0.1.0")

# Mount routers
app.include_router(question_gen_router)
app.include_router(format_convert_router)
app.include_router(retrieval_router)
app.include_router(evaluation_router)
app.include_router(pipeline_router)


if __name__ == "__main__":  # pragma: no cover - manual run helper
    uvicorn.run("app:app", host="0.0.0.0", port=8180, reload=True)

