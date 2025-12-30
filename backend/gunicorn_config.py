"""
Gunicorn configuration for GPT-Evaluation System
"""
import os
import multiprocessing
import logging

# Bind address - 默认端口 8180（与前端代理一致），可通过环境变量 PORT 覆盖
# 注意：不要使用 8000 端口，已被其他服务占用
bind = f"0.0.0.0:{os.getenv('PORT', '8180')}"

# Worker configuration
# Default to CPU core count, minimum 4 workers for better concurrency
workers = int(os.getenv('GUNICORN_WORKERS', max(4, multiprocessing.cpu_count() or 4)))
worker_class = 'uvicorn.workers.UvicornWorker'
worker_connections = 1000

# Timeouts
# Increase timeout for question generation (600 questions can take 15-20 minutes)
# For large batches (600 questions), increase timeout to 20 minutes
timeout = int(os.getenv('GUNICORN_TIMEOUT', 1200))  # 20 minutes default (was 600)
keepalive = 5
graceful_timeout = 30

# Logging
# 禁用访问日志以减少日志噪音，专注于评测相关的业务日志
accesslog = None  # 不记录访问日志（GET/POST请求），只显示业务日志
errorlog = '-'    # 错误日志输出到stderr
loglevel = 'info'  # 日志级别

# Performance tuning
max_requests = 10000  # Restart worker after this many requests
max_requests_jitter = 1000  # Add randomness to avoid all workers restarting at once

# Don't preload - let each worker initialize its own resources
preload_app = False

# Hooks
def on_starting(server):
    """Called just before the master process is initialized."""
    logging.info("=" * 60)
    logging.info("  GPT-Evaluation System Gunicorn Server Starting")
    logging.info("=" * 60)
    logging.info(f"  Workers: {workers}")
    logging.info(f"  Bind: {bind}")
    logging.info(f"  Worker class: {worker_class}")
    logging.info("=" * 60)

def when_ready(server):
    """Called just after the server is started."""
    logging.info(f"✅ Gunicorn server ready on {bind}")

def on_exit(server):
    """Called just before exiting Gunicorn."""
    logging.info("Gunicorn shutting down...")

print(f"Gunicorn config loaded: {workers} workers on {bind}")



