import os

# Bind to Render's assigned port
bind = f"0.0.0.0:{os.getenv('PORT', '10000')}"

# Use ASGI worker for FastAPI
workers = 2  # Reduced for free tier (512 MB RAM)
worker_class = "uvicorn.workers.UvicornWorker"

# Logging
loglevel = "info"
accesslog = "-"
errorlog = "-"