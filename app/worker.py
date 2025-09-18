import os, time, json, asyncio
from typing import List
from .groq_client import call_groq_batch, call_groq_single
from .utils import extract_json_from_text

BATCH_SIZE = int(os.getenv("BATCH_SIZE", "8"))
BATCH_TIMEOUT = float(os.getenv("BATCH_TIMEOUT", "0.12"))

async def worker_loop(app, idx: int):
    queue = app.state.queue
    client = app.state.http_client
    store = app.state.store

    while True:
        item = await queue.get()
        # item = {id, text, submitted_at}
        batch = [item]
        # collect extra items (non-blocking wait up to BATCH_TIMEOUT)
        t0 = time.monotonic()
        try:
            while len(batch) < BATCH_SIZE:
                timeout = max(0, BATCH_TIMEOUT - (time.monotonic() - t0))
                more = await asyncio.wait_for(queue.get(), timeout=timeout)
                batch.append(more)
        except asyncio.TimeoutError:
            pass

        ids = [b["id"] for b in batch]
        texts = [b["text"] for b in batch]

        # Call Groq batch
        try:
            responses = await call_groq_batch(client, texts, ids)
            # responses[0] should be a JSON array string — try parse
            parsed = extract_json_from_text(responses[0])
            if isinstance(parsed, list) and len(parsed) == len(batch):
                # good: write each result
                for obj in parsed:
                    rid = obj.get("id")
                    if rid and rid in store:
                        store[rid]["status"] = "done"
                        store[rid]["result"] = obj
                        store[rid]["finished_at"] = time.time()
            else:
                # fallback: call per-item
                for i, b in enumerate(batch):
                    try:
                        out = await call_groq_single(client, b["text"])
                        parsed_single = extract_json_from_text(out)
                        store[b["id"]]["status"] = "done"
                        store[b["id"]]["result"] = parsed_single or {"raw": out}
                        store[b["id"]]["finished_at"] = time.time()
                    except Exception as e:
                        store[b["id"]]["status"] = "error"
                        store[b["id"]]["error"] = str(e)
        except Exception as e:
            # mark batch items as error (or requeue depending on policy)
            for b in batch:
                store[b["id"]]["status"] = "error"
                store[b["id"]]["error"] = str(e)
        finally:
            for _ in batch:
                queue.task_done()