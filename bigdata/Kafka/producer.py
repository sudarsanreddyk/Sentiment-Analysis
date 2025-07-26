import json, time
from itertools import islice
import polars as pl
from confluent_kafka import Producer
import os

# ─── CONFIG ─────────────────────────────────────────────────────────────
INPUT       = os.getenv("INPUT", "C:/Bigdata/Reddit_datasetv1.txt")   # NDJSON source
BOOTSTRAP   = os.getenv("BOOTSTRAP", "kafka:9092")
TOPIC       = os.getenv("TOPIC", "reddit_comments_raw")
SPEED       = float(os.getenv("SPEED", "5.0"))      
FLUSH_INTERVAL = float(os.getenv("FLUSH_INTERVAL", "2"))
CHUNK_SIZE  = int(os.getenv("CHUNK_SIZE", "100000"))
# ────────────────────────────────────────────────────────────────────────

def make_producer() -> Producer:
    return Producer(
        {
            "bootstrap.servers":   BOOTSTRAP,
            "compression.type":    "lz4",
            "linger.ms":           100,          
            "batch.num.messages":  20000,     
            "batch.size":          2000000,  
            "acks":                "all",
        }
    )
last_flush_ts = 0.0

def send_batch(producer: Producer, batch: list[dict], ts: int, prev_ts: int | None):
    """Send all rows in batch (same timestamp) then sleep according to SPEED."""
    global last_flush_ts
    for rec in batch:
        producer.produce(
            TOPIC,
            key=rec["id"].encode(),
            value=json.dumps(rec).encode(),
        )

    producer.poll(0)
    now = time.time()
    if now - last_flush_ts >= FLUSH_INTERVAL:
        producer.flush()
        last_flush_ts = now
    print(
        f"[{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(ts))}] "
        f"sent {len(batch):>6} comments"
    )

    if prev_ts is not None and SPEED > 0:
        pause = (ts - prev_ts) / SPEED
        if pause > 0:
            time.sleep(pause)

def replay() -> None:
    producer      = make_producer()
    pending_ts    = None   # timestamp currently buffering
    pending_buf   = []     # list[dict] for that timestamp
    last_sent_ts  = None   # timestamp just flushed

    with open(INPUT, "r", encoding="utf-8") as fh:
        while True:
            # read next block of lines
            block = list(islice(fh, CHUNK_SIZE))
            if not block:
                break

            # parse, cast, sort
            df = (
                pl.DataFrame([json.loads(l) for l in block])
                  .with_columns(pl.col("created_utc").cast(pl.Int64))
                  .sort("created_utc")
            )

            # iterate groups in ascending order
            for key, gdf in df.group_by("created_utc", maintain_order=True):
                ts   = int(key[0]) if isinstance(key, tuple) else int(key)
                recs = gdf.to_dicts()


                if pending_ts is None:           # first group ever
                    pending_ts, pending_buf = ts, recs
                    continue

                if ts == pending_ts:             # same second continues
                    pending_buf.extend(recs)
                else:                            # new second begins
                    send_batch(producer, pending_buf, pending_ts, prev_ts=last_sent_ts)
                    last_sent_ts = pending_ts
                    pending_ts, pending_buf = ts, recs

    # flush final group at EOF
    if pending_buf:
        send_batch(producer, pending_buf, pending_ts, prev_ts=last_sent_ts)
        producer.flush()
    print("Replay complete")

if __name__ == "__main__":
    startup_delay = float(os.getenv("STARTUP_DELAY", "0"))
    if startup_delay > 0:
        print(f"Waiting {startup_delay}s for Kafka to come up…")
        time.sleep(startup_delay)
    replay()
