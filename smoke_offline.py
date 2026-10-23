#!/usr/bin/env python3
"""Offline smoke: text-similarity near-dup detection against active claims (read-only).

Usage (on a MaiBot host with A_Memorix data):
  python smoke_offline.py
  MAIBOT_ROOT=/opt/maibot python smoke_offline.py
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PLUGIN_DIR))
from dedupe_core import text_similarity, _load_config  # noqa: E402

cfg = _load_config(force=True)
threshold = float(cfg["dedupe"]["similarity_threshold"])
print("config threshold=", threshold, "mode=", cfg["dedupe"]["mode"], "enabled=", cfg["plugin"]["enabled"])

maibot_root = Path(os.environ.get("MAIBOT_ROOT", "/opt/maibot"))
db = maibot_root / "data" / "a-memorix" / "metadata" / "metadata.db"
if not db.is_file():
    print("SMOKE_SKIP: metadata.db not found at", db)
    raise SystemExit(0)

con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
cur = con.cursor()
cur.execute(
    "SELECT scope_id, value_text FROM fact_claims WHERE status='active' AND scope_type='person' ORDER BY created_at"
)
rows = cur.fetchall()
con.close()
print("active claims=", len(rows))

samples = [
    "用户有成为rapper的意愿",
    "用户想当rapper",
    "用户想成为rapper",
    "完全不相关的一句话用于负例测试xyz123",
]

by_person: dict = {}
for pid, text in rows:
    by_person.setdefault(pid, []).append(text)

if not by_person:
    print("SMOKE_SKIP: no active person claims")
    raise SystemExit(0)

pid = max(by_person, key=lambda k: len(by_person[k]))
facts = by_person[pid]
print("test person_id=", pid, "n=", len(facts))

for sample in samples:
    best = (0.0, "")
    for f in facts:
        s = text_similarity(sample, f)
        if s > best[0]:
            best = (s, f)
    decision = "SKIP" if best[0] >= threshold else "WRITE"
    print(f"  sample={sample!r} best={best[0]:.3f} => {decision} vs={best[1][:50]!r}")

near = 0
for i in range(len(facts)):
    for j in range(i + 1, len(facts)):
        if text_similarity(facts[i], facts[j]) >= threshold:
            near += 1
print("pairwise text-near pairs among active=", near)
print("SMOKE_OFFLINE_OK")
