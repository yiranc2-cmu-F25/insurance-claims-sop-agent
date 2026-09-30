"""Application composition and static UI; business endpoints live in routes.py."""

import asyncio
from contextlib import asynccontextmanager, suppress
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..paths import WEB_DIR
from .routes import router
from ..workflow.graph import graph


@asynccontextmanager
async def lifespan(app):
    await asyncio.to_thread(graph.checkpointer.cleanup)
    async def maintain_memory():
        while True:
            await asyncio.sleep(60)
            await asyncio.to_thread(graph.checkpointer.cleanup)
    task = asyncio.create_task(maintain_memory())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="Insurance Claims SOP Agent", lifespan=lifespan)
app.include_router(router)


@app.middleware("http")
async def no_stale_frontend(request, call_next):
    """The page and its script change with every deploy; browsers must revalidate them."""
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response
app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")


@app.get("/", response_class=FileResponse)
def index():
    return FileResponse(WEB_DIR / "index.html")
