# app/main.py
import os
import time
import asyncio
import hashlib
from typing import Dict, Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
import httpx
from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST
from loguru import logger

from .schemas import AnalyzeRequest
from .utils import make_request_id
from .cache import cache
from .worker import worker_loop

# Config (environment)
MAX_QUEUE_SIZE = int(os.getenv("MAX_QUEUE_SIZE", "20000"))
WORKER_COUNT = int(os.getenv("WORKER_COUNT", "2"))
PORT = int(os.getenv("PORT", "8000"))
# Safety: fraction of queue fill to start returning 429
BACKPRESSURE_THRESHOLD = float(os.getenv("BACKPRESSURE_THRESHOLD", "0.9"))

# Metrics
REQUESTS_TOTAL = Counter("requests_total", "Total number of incoming analyze requests")
REQUESTS_QUEUED = Counter("requests_queued", "Requests accepted and placed into the queue")
REQUESTS_CACHE_HIT = Counter("requests_cache_hit", "Requests served from cache")
REQUESTS_ERRORS = Counter("requests_errors", "Requests that resulted in error")
QSIZE_GAUGE = Gauge("queue_size", "Size of internal queue")
IN_FLIGHT_GAUGE = Gauge("in_flight_requests", "Currently in-flight processing")
PROCESS_LATENCY = Histogram("processing_latency_seconds", "Time to process request (worker measured)")

app = FastAPI(title="Scalable AI Agent")

# NOTE: store is a simple in-memory dict: request_id -> metadata
# In production swap to Redis or other durable store (so restarts don't lose state)
app_state_keys = ("queue", "store", "http_client", "worker_tasks")


def _hash_text(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


@app.on_event("startup")
async def startup_event():
    # sanity checks
    if "GROQ_API_KEY" not in os.environ:
        logger.warning("GROQ_API_KEY not found in environment; set it before making LLM calls.")

    # initialize state objects
    logger.info("Starting up: initializing queue, store, http client, and workers")
    app.state.queue = asyncio.Queue(maxsize=MAX_QUEUE_SIZE)
    app.state.store: Dict[str, Dict[str, Any]] = {}
    limits = httpx.Limits(max_keepalive_connections=10, max_connections=20)
    app.state.http_client = httpx.AsyncClient(timeout=120.0, limits=limits)
    app.state.worker_tasks = []

    # create worker coroutines (background)
    for i in range(WORKER_COUNT):
        task = asyncio.create_task(worker_loop(app, i))
        app.state.worker_tasks.append(task)
    logger.info("Startup complete. Workers started: {}", WORKER_COUNT)


@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Shutting down: cancelling worker tasks and closing http client")
    # Cancel worker tasks
    for t in getattr(app.state, "worker_tasks", []):
        t.cancel()
    # close http client
    http_client = getattr(app.state, "http_client", None)
    if http_client:
        await http_client.aclose()
    logger.info("Shutdown complete.")


@app.post("/analyze")
async def analyze(req: AnalyzeRequest):
    """
    Accepts a research-paper fragment (title, abstract, text, or URL).
    Returns immediately with a request_id. Worker(s) will process in background.
    """
    REQUESTS_TOTAL.inc()

    # Build a single text blob for the LLM
    parts = []
    if req.title:
        parts.append(f"Title: {req.title}")
    if req.abstract:
        parts.append(f"Abstract: {req.abstract}")
    if req.text:
        parts.append(req.text)
    if req.url:
        parts.append(f"URL: {req.url}")
    text_blob = "\n\n".join(parts).strip()

    if not text_blob:
        REQUESTS_ERRORS.inc()
        raise HTTPException(status_code=400, detail="No text provided in request")

    # Quick cache check to return immediate result if we've already processed same content
    cache_key = "analyze:" + _hash_text(text_blob)
    cached = await cache.get(cache_key)
    if cached is not None:
        # Create a request_id but mark as done immediately with cached result
        rid = make_request_id()
        now = time.time()
        app.state.store[rid] = {
            "status": "done",
            "queued_at": now,
            "finished_at": now,
            "result": cached,
        }
        REQUESTS_CACHE_HIT.inc()
        return {"request_id": rid, "status": "done", "cached": True}

    # Backpressure: check queue occupancy
    q = app.state.queue
    qsize = q.qsize()
    QSIZE_GAUGE.set(qsize)
    if qsize >= int(MAX_QUEUE_SIZE * BACKPRESSURE_THRESHOLD):
        # Reject to prevent memory exhaustion; clients should retry with backoff
        logger.warning("Queue full ({}). Returning 429.", qsize)
        raise HTTPException(status_code=429, detail="Server overloaded — try again later")

    # Otherwise accept and enqueue
    rid = make_request_id()
    now = time.time()
    app.state.store[rid] = {"status": "queued", "queued_at": now, "finished_at": None, "result": None}
    # Put a small payload; worker will enrich the state with finished_at/result
    payload = {"id": rid, "text": text_blob, "submitted_at": now, "cache_key": cache_key}
    await q.put(payload)
    REQUESTS_QUEUED.inc()
    QSIZE_GAUGE.set(q.qsize())
    return {"request_id": rid, "status": "queued"}


@app.get("/result/{request_id}")
async def get_result(request_id: str):
    """
    Poll for the result. Returns the store record for the request_id.
    Example response: {status: queued|processing|done|error, queued_at, finished_at, result/error}
    """
    rec = app.state.store.get(request_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Unknown request_id")
    return rec


@app.get("/health")
async def health():
    qsize = app.state.queue.qsize() if hasattr(app.state, "queue") else 0
    workers = len(getattr(app.state, "worker_tasks", []))
    return {"status": "ok", "queue_size": qsize, "workers": workers}


@app.get("/metrics")
async def metrics():
    """
    Prometheus metrics endpoint.
    """
    data = generate_latest()
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)


# Optional: lightweight readiness endpoint that confirms workers exist
@app.get("/ready")
async def ready():
    tasks = getattr(app.state, "worker_tasks", [])
    alive = [not t.done() for t in tasks]
    if not tasks:
        return {"ready": False, "reason": "no-workers"}
    if all(alive):
        return {"ready": True, "workers_alive": len(tasks)}
    return {"ready": False, "workers_alive": sum(alive), "total_workers": len(tasks)}
