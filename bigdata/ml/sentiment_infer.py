# sentiment_infer.py

import os, json, time, logging
from collections import defaultdict, deque
from typing import List

import torch, torch.nn as nn
from confluent_kafka import Consumer, Producer, KafkaError
from transformers import AutoTokenizer

# ── config ──────────────────────────────────────────────────────────────
BROKER          = f"{os.getenv('KAFKA_URL', 'kafka')}:9092"
PROCESSED_TOPIC = os.getenv("PROCESSED_TOPIC", "reddit_comments_processed")
AGG_TOPIC       = os.getenv("AGG_TOPIC", "sentiment_agg")
MAX_LEN         = 64
DEVICE          = "cuda" if torch.cuda.is_available() else "cpu"

# UPDATED CHECKPOINT PATH TO USE BEST MODEL
CKPT_PATH       = "./ckpt/ckpt_best.pt"  # Use best model!

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s | %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("infer")

# ── tokenizer ───────────────────────────────────────────────────────────
tok = AutoTokenizer.from_pretrained("bert-base-uncased", use_fast=True)
VOCAB = tok.vocab_size

# ── model ───────────────────────────────────────────────────────────────
class GRUSent(nn.Module):
    def __init__(self):
        super().__init__()
        self.emb  = nn.Embedding(VOCAB, 64, padding_idx=0)
        self.gru  = nn.GRU(64, 128, batch_first=True, bidirectional=True)
        self.head = nn.Sequential(
            nn.Linear(128 * 2, 128), nn.ReLU(), nn.Dropout(0.2),
            nn.Linear(128, 1))

    def forward(self, ids: torch.Tensor):
        _, h = self.gru(self.emb(ids))
        h_cat = torch.cat((h[-2], h[-1]), dim=1)
        return self.head(h_cat).squeeze(1)

# ── UPDATED CHECKPOINT LOADING FUNCTION ────────────────────────────────
def load_checkpoint(model, ckpt_path):
    """Load checkpoint with proper error handling and format detection"""
    try:
        checkpoint = torch.load(ckpt_path, map_location=DEVICE)
        
        # Handle both old format (direct state_dict) and new format (dict with metadata)
        if isinstance(checkpoint, dict) and 'model_state_dict' in checkpoint:
            # New format with metadata
            model.load_state_dict(checkpoint['model_state_dict'])
            log.info(f" Loaded BEST model from step {checkpoint.get('step', 'unknown')} "
                    f"with loss {checkpoint.get('loss', 'unknown'):.4f}")
            return checkpoint.get('step', 0), checkpoint.get('loss', 0.0)
        else:
            # Old format (direct state_dict)
            model.load_state_dict(checkpoint)
            log.info(" Loaded model (old format)")
            return 0, 0.0
            
    except FileNotFoundError:
        log.error(f" Checkpoint not found: {ckpt_path}")
        log.info(" Available checkpoints:")
        import glob
        for f in glob.glob("./ckpt/*.pt"):
            log.info(f"  - {f}")
        
        # Try fallback checkpoints
        fallbacks = ["./ckpt/ckpt_latest.pt", "./ckpt/ckpt_final.pt"]
        for fallback in fallbacks:
            if os.path.exists(fallback):
                log.info(f" Trying fallback: {fallback}")
                return load_checkpoint(model, fallback)
        
        raise FileNotFoundError("No usable checkpoint found!")
    except Exception as e:
        log.error(f" Error loading checkpoint: {e}")
        raise

# ── load trained model ──────────────────────────────────────────────────
model = GRUSent().to(DEVICE)
model_step, model_loss = load_checkpoint(model, CKPT_PATH)
model.eval()
model = torch.jit.script(model)

# ── kafka clients ───────────────────────────────────────────────────────
cons = Consumer({
    "bootstrap.servers": BROKER,
    "group.id": f"ml-infer-{int(time.time())}",
    "auto.offset.reset": "earliest",
})
cons.subscribe([PROCESSED_TOPIC])
log.info(" Consuming from %s on %s using model from step %d (loss: %.4f)", 
         PROCESSED_TOPIC, BROKER, model_step, model_loss)

prod = Producer({"bootstrap.servers": BROKER})

# ── global counters: keyword → (pos_total, sample_total) ────────────────
global_totals = defaultdict(lambda: [0, 0])   # mutable [pos, total]

# ── main loop ───────────────────────────────────────────────────────────
BATCH = int(os.getenv("BATCH", 32))

try:
    while True:
        # pull up to BATCH records at once (returns ≤ BATCH)
        records = cons.consume(num_messages=BATCH, timeout=1.0)
        if not records:
            continue

        # ----- unpack ----------------------------------------------------
        rows   = [json.loads(r.value()) for r in records]
        texts  = [row["cleaned"] for row in rows if row.get("cleaned")]
        nounss = [row.get("nouns", "") for row in rows]

        if not texts:          # all rows were blank → skip
            continue

        # ----- vectorised tokenisation -----------------------------------
        tok_out = tok(
            texts,
            max_length=MAX_LEN,
            truncation=True,
            padding="max_length",
            return_tensors="pt"
        )
        ids = tok_out["input_ids"].to(DEVICE)

        # ----- one forward pass for the whole batch ----------------------
        with torch.no_grad():
            probs = torch.sigmoid(model(ids)).flatten()
            preds = probs.tolist()        

        # ----- walk through batch results --------------------------------
        for row, p in zip(rows, preds):
            for keyword in (k.strip().lower() for k in row.get("nouns","").split(",") if k):
                pos, tot = global_totals[keyword]
                tot += 1
                pos += int(p >= 0.5)
                global_totals[keyword] = [pos, tot]

                out_msg = {
                    "keyword": keyword,
                    "positive": pos,
                    "total":    tot,
                    "percent_positive": round(100*pos/tot, 2),
                    "timestamp": int(time.time())
                }
                prod.produce(AGG_TOPIC, value=json.dumps(out_msg).encode())

        prod.poll(0)   # flush the async producer queue fast
        if len(preds) > 0:  # Only log when we actually processed something
            log.info(f" Processed {len(preds)} comments, sent to {AGG_TOPIC}")

except KeyboardInterrupt:
    log.info("Interrupted by user; exiting.")
finally:
    cons.close()
    prod.flush()