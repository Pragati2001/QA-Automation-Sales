from fastapi import FastAPI

from app.api import audio, calls, review
from app.config import settings

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
)

app.include_router(calls.router, prefix=settings.api_prefix)
app.include_router(review.router, prefix=settings.api_prefix)
app.include_router(audio.router, prefix=settings.api_prefix)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": settings.app_name,
        "environment": settings.app_env,
    }
