# sentiment_model.py
"""
Plugs directly into Kafka → Flink → Kafka stream.

* tiny bidirectional-GRU sentiment head (trained from scratch)
* fast WordPiece tokenisation (BertTokenizerFast)
* trains one optimiser step every 64 processed comments
* SAVES BEST CHECKPOINTS automatically
* early-stop: saves /app/ckpt/ckpt_final.pt after 1200 s of idle topic
"""

from __future__ import annotations
import os, json, time, logging
from typing import List

import torch, torch.nn as nn
from confluent_kafka import Consumer, KafkaError
from transformers import AutoTokenizer

# ── config ──────────────────────────────────────────────────────────────
BROKER      = f"{os.getenv('KAFKA_URL', 'kafka')}:9092"
TOPIC       = os.getenv("PROCESSED_TOPIC", "reddit_comments_processed")
BATCH       = 64
IDLE_LIMIT  = 1200            # seconds with no new data → checkpoint & exit
MAX_LEN     = 64
EMB_DIM     = 64
HID_DIM     = 128
LR          = 1e-3           # KEEP THE GOOD LEARNING RATE
DEVICE      = "cuda" if torch.cuda.is_available() else "cpu"

# ── CHECKPOINT CONFIG ──────────────────────────────────────────────────
SAVE_BEST_EVERY = 50        # Check every 50 steps
best_loss = float('inf')    # Track best loss
best_step = 0              # Track best step

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s | %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("trainer")

# ── tokeniser ───────────────────────────────────────────────────────────
tok   = AutoTokenizer.from_pretrained("bert-base-uncased", use_fast=True)
VOCAB = tok.vocab_size

# ── model ───────────────────────────────────────────────────────────────
class GRUSent(nn.Module):
    def __init__(self):
        super().__init__()
        self.emb  = nn.Embedding(VOCAB, EMB_DIM, padding_idx=0)
        self.gru  = nn.GRU(EMB_DIM, HID_DIM, batch_first=True,
                           bidirectional=True)
        self.head = nn.Sequential(
            nn.Linear(HID_DIM * 2, HID_DIM), nn.ReLU(), nn.Dropout(0.2),
            nn.Linear(HID_DIM, 1))

    def forward(self, ids: torch.Tensor):
        _, h = self.gru(self.emb(ids))               # h shape [2, B, H]
        h_cat = torch.cat((h[-2], h[-1]), dim=1)     # [B, 2H]
        return self.head(h_cat).squeeze(1)

# ── CHECKPOINT SAVING FUNCTION ─────────────────────────────────────────
def save_checkpoint(model, step, loss, is_best=False):
    """Save model checkpoint with metadata"""
    os.makedirs("/app/ckpt", exist_ok=True)
    
    if is_best:
        # Save best model
        best_path = "/app/ckpt/ckpt_best.pt"
        torch.save({
            'model_state_dict': model.state_dict(),
            'step': step,
            'loss': loss,
            'timestamp': int(time.time())
        }, best_path)
        log.info(f" NEW BEST MODEL saved at step {step} with loss {loss:.4f}")
    
    # Always save latest (for recovery)
    latest_path = "/app/ckpt/ckpt_latest.pt"
    torch.save({
        'model_state_dict': model.state_dict(),
        'step': step,
        'loss': loss,
        'timestamp': int(time.time())
    }, latest_path)

model   = GRUSent().to(DEVICE)
opt     = torch.optim.Adam(model.parameters(), lr=LR)
loss_fn = nn.BCEWithLogitsLoss()

# ── kafka consumer ──────────────────────────────────────────────────────
cons = Consumer({
    "bootstrap.servers": BROKER,
    "group.id": f"ml-trainer-{int(time.time())}",
    "auto.offset.reset": "earliest",
})
cons.subscribe([TOPIC])
log.info("Consuming from %s on %s", TOPIC, BROKER)

# ── training loop ───────────────────────────────────────────────────────
ids_buf: List[List[int]] = []
lab_buf: List[float]    = []
step        = 0
idle_secs   = 0
last_log    = time.time()

try:
    while True:
        msg = cons.poll(1.0)

        # heartbeat every 10 s even if no data yet
        if time.time() - last_log > 10 and len(ids_buf) == 0:
            log.info("waiting for data…")
            last_log = time.time()

        if msg is None:
            idle_secs += 1
            if idle_secs >= IDLE_LIMIT:
                # Save final model with metadata
                final_path = "/app/ckpt/ckpt_final.pt"
                os.makedirs(os.path.dirname(final_path), exist_ok=True)
                torch.save({
                    'model_state_dict': model.state_dict(),
                    'step': step,
                    'final_loss': current_loss if 'current_loss' in locals() else 0.0,
                    'best_loss': best_loss,
                    'best_step': best_step,
                    'timestamp': int(time.time())
                }, final_path)
                log.info("No new data %ds → saved %s (best: %.4f at step %d)", 
                        IDLE_LIMIT, final_path, best_loss, best_step)
                break
            continue

        idle_secs = 0
        if msg.error():
            if msg.error().code() != KafkaError._PARTITION_EOF:
                log.error("Kafka error: %s", msg.error())
            continue

        row = json.loads(msg.value())
        txt, lbl = row.get("cleaned"), row.get("sentiment")
        if not txt or lbl is None:
            continue

        tok_ids = tok(txt,
                      max_length=MAX_LEN,
                      truncation=True,
                      padding="max_length",
                      return_tensors="pt")["input_ids"][0]
        ids_buf.append(tok_ids.tolist())
        lab_buf.append(float(lbl))          # already 0…1

        if len(ids_buf) < BATCH:
            continue

        # ---- update ----
        x = torch.tensor(ids_buf, dtype=torch.long, device=DEVICE)
        y = torch.tensor(lab_buf, dtype=torch.float32, device=DEVICE)
        opt.zero_grad()
        loss = loss_fn(model(x), y)
        loss.backward(); opt.step()

        step += 1
        current_loss = loss.item()
        
        # Regular logging
        if step % 50 == 0:
            log.info("step %-6d | loss %.4f", step, current_loss)
        
        # *** CHECKPOINT SAVING LOGIC ***
        if step % SAVE_BEST_EVERY == 0:
            if current_loss < best_loss:
                best_loss = current_loss
                best_step = step
                save_checkpoint(model, step, current_loss, is_best=True)
            else:
                save_checkpoint(model, step, current_loss, is_best=False)
        
        # *** EARLY STOPPING FOR EXCELLENT PERFORMANCE ***
        if current_loss < 0.55:
            log.info(f"EXCELLENT loss {current_loss:.4f} reached! Saving and stopping.")
            save_checkpoint(model, step, current_loss, is_best=True)
            break
            
        ids_buf.clear(); lab_buf.clear()

except KeyboardInterrupt:
    log.info("Interrupted by user; exiting.")
finally:
    cons.close()
