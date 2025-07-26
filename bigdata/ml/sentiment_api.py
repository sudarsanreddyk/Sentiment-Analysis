# ml/sentiment_api.py
from __future__ import annotations
import os, time, asyncio, json, logging
from collections import defaultdict, deque
from typing import Deque

from fastapi import FastAPI, Query
from aiokafka import AIOKafkaConsumer, errors as kafka_errors
import re, unicodedata

def canonicalize(token: str) -> str:
    token = unicodedata.normalize("NFKD", token).encode("ascii", "ignore").decode()
    return re.sub(r"[^\w]+", "", token.lower())

# ── Logging ───────────────────────────────────────────────
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s | %(message)s")
log = logging.getLogger("sentiment_api")

# ── Config ────────────────────────────────────────────────
BROKER    = f"{os.getenv('KAFKA_URL', 'kafka')}:9092"
AGG_TOPIC = os.getenv("AGG_TOPIC", "sentiment_agg")

# keyword → deque[(timestamp, pos_total, sample_total)]
window: dict[str, Deque[tuple[float, int, int]]] = defaultdict(
    lambda: deque(maxlen=1)          # keep **only latest** snapshot
)
store_lock = asyncio.Lock()

# ── FastAPI app ───────────────────────────────────────────
app = FastAPI()

@app.get("/aggregate")
async def aggregate(keyword: str = Query(..., min_length=1),
                    minutes: int = Query(60, gt=0, le=1440)):
    kw = canonicalize(keyword)
    cutoff = time.time() - minutes * 60

    async with store_lock:
        dq = window[kw]
        # discard snapshot if it’s older than the time-window
        while dq and dq[0][0] < cutoff:
            dq.popleft()

        if dq:
            _, pos, total = dq[0]            # single latest entry
        else:
            pos, total = 0, 0

    if total == 0:
        return {
            "keyword": keyword,
            "positive": 0,
            "negative": 0,
            "percent_positive": None,
            "num_samples_total": 0
        }

    return {
        "keyword": keyword,
        "positive": pos,
        "negative": total - pos,
        "percent_positive": round(100 * pos / total, 2),
        "num_samples_total": total
    }

# ── Kafka listener ────────────────────────────────────────
async def kafka_listener():
    consumer = AIOKafkaConsumer(
        AGG_TOPIC,
        bootstrap_servers=BROKER,
        group_id="ml-aggregator",
        auto_offset_reset="earliest",
    )

    while True:
        try:
            log.info(f"Connecting to Kafka broker at {BROKER} …")
            await consumer.start()
            log.info(f"Kafka consumer started on topic {AGG_TOPIC}")
            break
        except kafka_errors.KafkaConnectionError as e:
            log.error(f"Kafka connection failed: {e} – retrying in 3 s")
            await asyncio.sleep(3)

    try:
        async for msg in consumer:
            try:
                row = json.loads(msg.value.decode())
                kw         = canonicalize(row.get("keyword", ""))
                pos_cnt    = int(row.get("positive", 0))
                total_cnt  = int(row.get("total",    0))
                ts         = float(row.get("timestamp", time.time()))

                # store *latest* snapshot (overwrite any old one)
                async with store_lock:
                    window[kw].clear()
                    window[kw].append((ts, pos_cnt, total_cnt))

            except Exception as err:
                log.error(f"Bad message skipped: {err}")
                continue
    finally:
        await consumer.stop()

# ── Startup task ──────────────────────────────────────────
@app.on_event("startup")
async def on_start():
    asyncio.create_task(kafka_listener())
