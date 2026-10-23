"""Person-fact near-duplicate filter used by the host-side patch.

Loaded from Host process (not the plugin runner). Soft-deletes via
memory_service.fact_admin(retract); never prints secrets.
"""

from __future__ import annotations

import asyncio
import re
import time
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

PLUGIN_DIR = Path(__file__).resolve().parent
CONFIG_PATH = PLUGIN_DIR / "config.toml"

_config_cache: Dict[str, Any] = {}
_config_mtime: float = 0.0
_sweep_task: Optional[asyncio.Task] = None
_hook_logged = False
_logger = None


def _get_logger():
    global _logger
    if _logger is None:
        try:
            from src.common.logger import get_logger

            _logger = get_logger("person_fact_dedupe")
        except Exception:
            import logging

            _logger = logging.getLogger("person_fact_dedupe")
    return _logger


def _load_config(force: bool = False) -> Dict[str, Any]:
    global _config_cache, _config_mtime
    try:
        mtime = CONFIG_PATH.stat().st_mtime
    except FileNotFoundError:
        mtime = 0.0
    if not force and _config_cache and mtime == _config_mtime:
        return _config_cache

    defaults: Dict[str, Any] = {
        "plugin": {"enabled": True},
        "dedupe": {
            "similarity_threshold": 0.88,
            "mode": "skip_duplicate",
            "dry_run": False,
            "person_id_allowlist": [],
        },
        "sweep": {
            "enabled": False,
            "interval_hours": 6.0,
            "max_persons_per_run": 20,
            "max_retracts_per_run": 50,
        },
    }
    data = defaults
    try:
        import tomllib

        with CONFIG_PATH.open("rb") as fh:
            loaded = tomllib.load(fh)
        if isinstance(loaded, dict):
            for section, section_defaults in defaults.items():
                merged = dict(section_defaults)
                raw = loaded.get(section) if isinstance(loaded.get(section), dict) else {}
                merged.update(raw or {})
                data[section] = merged
    except Exception as exc:
        _get_logger().warning("person-fact-dedupe config load failed, using defaults: %s", exc)
        data = defaults

    _config_cache = data
    _config_mtime = mtime
    return data


def normalize_fact_text(text: str) -> str:
    cleaned = unicodedata.normalize("NFKC", str(text or "")).strip().casefold()
    cleaned = re.sub(r"\s+", "", cleaned)
    return cleaned


def _char_bigram_jaccard(a: str, b: str) -> float:
    """Character bigram Jaccard — stronger for short Chinese paraphrases."""
    if len(a) < 2 or len(b) < 2:
        return 1.0 if a == b else 0.0
    sa = {a[i : i + 2] for i in range(len(a) - 1)}
    sb = {b[i : i + 2] for i in range(len(b) - 1)}
    inter = len(sa & sb)
    union = len(sa | sb)
    return float(inter) / float(union) if union else 0.0


def text_similarity(a: str, b: str) -> float:
    na, nb = normalize_fact_text(a), normalize_fact_text(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    # containment bonus for short paraphrases
    if na in nb or nb in na:
        shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
        ratio = len(shorter) / max(1, len(longer))
        if ratio >= 0.72:
            return max(0.90, ratio)
    seq = float(SequenceMatcher(None, na, nb).ratio())
    jac = _char_bigram_jaccard(na, nb)
    # Bias slightly toward Jaccard for CJK-heavy short facts
    return max(seq, jac)


def _cosine(u: Any, v: Any) -> float:
    import numpy as np

    a = np.asarray(u, dtype=np.float32).reshape(-1)
    b = np.asarray(v, dtype=np.float32).reshape(-1)
    if a.size == 0 or b.size == 0 or a.size != b.size:
        return 0.0
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom <= 1e-12:
        return 0.0
    return float(np.dot(a, b) / denom)


async def _embed_texts(texts: Sequence[str]) -> Optional[Any]:
    """Return NxD embeddings via A_Memorix embedding_manager, or None.

    Prefer a ready kernel, but still try encode when the memory kernel is in
    degraded mode — embeddings often remain usable even then.
    """
    if not texts:
        return None
    try:
        from src.A_memorix.host_service import a_memorix_host_service

        kernel = getattr(a_memorix_host_service, "_kernel", None)
        if kernel is None:
            return None
        manager = getattr(kernel, "embedding_manager", None)
        if manager is None:
            return None
        encode = getattr(manager, "encode", None)
        if encode is None:
            return None
        result = encode(list(texts))
        if asyncio.iscoroutine(result):
            result = await result
        return result
    except Exception as exc:
        _get_logger().debug("person-fact-dedupe embed unavailable: %s", exc)
        return None


async def _list_active_claims(person_id: str, limit: int = 400) -> List[Dict[str, Any]]:
    from src.services.memory_service import memory_service

    payload = await memory_service.fact_admin(
        action="list",
        scope_type="person",
        scope_id=person_id,
        statuses=["active"],
        limit=max(1, int(limit)),
    )
    if not isinstance(payload, dict) or not payload.get("success"):
        return []
    items = payload.get("items") or []
    return [item for item in items if isinstance(item, dict)]


async def find_near_duplicate(
    *,
    person_id: str,
    new_text: str,
    threshold: float,
) -> Optional[Tuple[Dict[str, Any], float, str]]:
    """Return (claim, score, method) for best near-duplicate above threshold."""
    claims = await _list_active_claims(person_id)
    if not claims:
        return None

    candidates: List[Tuple[Dict[str, Any], str]] = []
    for claim in claims:
        value = str(claim.get("value_text") or claim.get("value_normalized") or "").strip()
        if value:
            candidates.append((claim, value))
    if not candidates:
        return None

    # Exact / text gate first (cheap)
    best: Optional[Tuple[Dict[str, Any], float, str]] = None
    for claim, value in candidates:
        score = text_similarity(new_text, value)
        if best is None or score > best[1]:
            best = (claim, score, "text")
        if score >= 0.999:
            return claim, score, "text_exact"

    # Embedding refine when available
    texts = [new_text] + [value for _, value in candidates]
    vectors = await _embed_texts(texts)
    if vectors is not None:
        try:
            import numpy as np

            arr = np.asarray(vectors, dtype=np.float32)
            if arr.ndim == 1:
                arr = arr.reshape(1, -1)
            if arr.shape[0] == len(texts):
                query = arr[0]
                for idx, (claim, _value) in enumerate(candidates):
                    score = _cosine(query, arr[idx + 1])
                    if best is None or score > best[1]:
                        best = (claim, score, "embedding")
        except Exception as exc:
            _get_logger().debug("person-fact-dedupe embedding compare failed: %s", exc)

    if best is not None and best[1] >= float(threshold):
        return best
    return None


def _person_allowed(person_id: str, allowlist: Sequence[str]) -> bool:
    cleaned = [str(item).strip() for item in allowlist if str(item).strip()]
    if not cleaned:
        return True
    return person_id in cleaned


async def should_skip_person_fact_write(
    *,
    person_id: str,
    memory_content: str,
    chat_id: str = "",
) -> bool:
    """Host-patch entry: True => skip ingest_text."""
    global _hook_logged
    logger = _get_logger()
    cfg = _load_config()
    if not bool(cfg.get("plugin", {}).get("enabled", True)):
        return False

    dedupe = cfg.get("dedupe", {})
    threshold = float(dedupe.get("similarity_threshold", 0.88) or 0.88)
    mode = str(dedupe.get("mode", "skip_duplicate") or "skip_duplicate").strip()
    dry_run = bool(dedupe.get("dry_run", False))
    allowlist = dedupe.get("person_id_allowlist") or []

    pid = str(person_id or "").strip()
    text = str(memory_content or "").strip()
    if not pid or not text:
        return False
    if not _person_allowed(pid, allowlist if isinstance(allowlist, list) else []):
        return False

    if not _hook_logged:
        logger.info(
            "[person-fact-dedupe] host hook active threshold=%.2f mode=%s dry_run=%s",
            threshold,
            mode,
            dry_run,
        )
        _hook_logged = True

    # Kick optional sweep (no-op if disabled / already running)
    ensure_sweep_started()

    try:
        hit = await find_near_duplicate(person_id=pid, new_text=text, threshold=threshold)
    except Exception as exc:
        logger.warning("[person-fact-dedupe] lookup failed (fail-open): %s", exc)
        return False

    if hit is None:
        return False

    claim, score, method = hit
    existing = str(claim.get("value_text") or "")[:80]
    preview = text[:80]
    logger.info(
        "[person-fact-dedupe] near-duplicate person_id=%s score=%.3f method=%s mode=%s "
        "existing_claim=%s existing=%r new=%r chat_id=%s dry_run=%s",
        pid,
        score,
        method,
        mode,
        str(claim.get("claim_id") or "")[:16],
        existing,
        preview,
        chat_id,
        dry_run,
    )

    if dry_run:
        return False

    # Both modes skip writing the newer fact on the write path.
    if mode in {"skip_duplicate", "merge_keep_oldest"}:
        return True
    logger.warning("[person-fact-dedupe] unknown mode %r, fail-open", mode)
    return False


async def _retract_claim(claim_id: str, reason: str) -> bool:
    from src.services.memory_service import memory_service

    result = await memory_service.fact_admin(
        action="retract",
        claim_id=claim_id,
        reason=reason,
    )
    return bool(isinstance(result, dict) and result.get("success"))


async def sweep_person_duplicates(
    person_id: str,
    *,
    threshold: float,
    dry_run: bool,
    max_retracts: int,
) -> int:
    """Cluster near-duplicates for one person; keep oldest, retract newer."""
    logger = _get_logger()
    claims = await _list_active_claims(person_id)
    if len(claims) < 2:
        return 0

    # Sort oldest first
    def _created(claim: Dict[str, Any]) -> float:
        for key in ("created_at", "first_observed_at", "last_confirmed_at"):
            try:
                return float(claim.get(key) or 0.0)
            except (TypeError, ValueError):
                continue
        return 0.0

    ordered = sorted(claims, key=_created)
    texts = [str(c.get("value_text") or "").strip() for c in ordered]
    vectors = await _embed_texts(texts)

    retracted = 0
    kept_indices: List[int] = []
    for i, claim in enumerate(ordered):
        text_i = texts[i]
        if not text_i:
            continue
        duplicate_of = None
        for j in kept_indices:
            score = text_similarity(text_i, texts[j])
            if vectors is not None:
                try:
                    import numpy as np

                    arr = np.asarray(vectors, dtype=np.float32)
                    if arr.ndim == 2 and arr.shape[0] == len(texts):
                        score = max(score, _cosine(arr[i], arr[j]))
                except Exception:
                    pass
            if score >= threshold:
                duplicate_of = (j, score)
                break
        if duplicate_of is None:
            kept_indices.append(i)
            continue

        j, score = duplicate_of
        keep = ordered[j]
        logger.info(
            "[person-fact-dedupe] sweep retract person_id=%s score=%.3f keep=%s drop=%s "
            "keep_text=%r drop_text=%r dry_run=%s",
            person_id,
            score,
            str(keep.get("claim_id") or "")[:16],
            str(claim.get("claim_id") or "")[:16],
            str(keep.get("value_text") or "")[:60],
            text_i[:60],
            dry_run,
        )
        if dry_run:
            retracted += 1
            continue
        ok = await _retract_claim(
            str(claim.get("claim_id") or ""),
            reason="person_fact_dedupe_sweep_keep_oldest",
        )
        if ok:
            retracted += 1
        if retracted >= max_retracts:
            break
    return retracted


async def _collect_person_ids(limit_persons: int) -> List[str]:
    """Best-effort: distinct scope_ids from a broad list via sqlite read-only."""
    try:
        import sqlite3

        db = Path("/opt/maibot/data/a-memorix/metadata/metadata.db")
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        cur = con.cursor()
        cur.execute(
            """
            SELECT scope_id, COUNT(*) AS c
            FROM fact_claims
            WHERE scope_type='person' AND status='active'
            GROUP BY scope_id
            ORDER BY c DESC
            LIMIT ?
            """,
            (max(1, int(limit_persons)),),
        )
        rows = [str(r[0]) for r in cur.fetchall() if r and r[0]]
        con.close()
        return rows
    except Exception as exc:
        _get_logger().warning("[person-fact-dedupe] sweep person scan failed: %s", exc)
        return []


async def run_sweep_once() -> Dict[str, Any]:
    cfg = _load_config(force=True)
    sweep = cfg.get("sweep", {})
    dedupe = cfg.get("dedupe", {})
    if not bool(sweep.get("enabled", False)):
        return {"success": True, "skipped": True, "reason": "sweep_disabled"}

    threshold = float(dedupe.get("similarity_threshold", 0.88) or 0.88)
    dry_run = bool(dedupe.get("dry_run", False))
    mode = str(dedupe.get("mode", "skip_duplicate") or "skip_duplicate")
    max_persons = int(sweep.get("max_persons_per_run", 20) or 20)
    max_retracts = int(sweep.get("max_retracts_per_run", 50) or 50)
    allowlist = dedupe.get("person_id_allowlist") or []

    person_ids = list(allowlist) if allowlist else await _collect_person_ids(max_persons)
    person_ids = [str(p).strip() for p in person_ids if str(p).strip()][:max_persons]

    total = 0
    for pid in person_ids:
        if mode != "merge_keep_oldest" and not dry_run:
            # In skip_duplicate mode, sweep still allowed but only retracts when
            # explicitly using merge_keep_oldest OR dry_run preview.
            # For skip_duplicate + sweep.enabled we still soft-dedupe stockpile.
            pass
        total += await sweep_person_duplicates(
            pid,
            threshold=threshold,
            dry_run=dry_run,
            max_retracts=max(0, max_retracts - total),
        )
        if total >= max_retracts:
            break
    _get_logger().info(
        "[person-fact-dedupe] sweep done persons=%d retracted_or_preview=%d dry_run=%s",
        len(person_ids),
        total,
        dry_run,
    )
    return {"success": True, "persons": len(person_ids), "retracted": total, "dry_run": dry_run}


async def _sweep_loop() -> None:
    logger = _get_logger()
    while True:
        cfg = _load_config()
        sweep = cfg.get("sweep", {})
        if not bool(sweep.get("enabled", False)):
            await asyncio.sleep(60)
            continue
        hours = float(sweep.get("interval_hours", 6) or 6)
        hours = max(0.25, hours)
        try:
            await run_sweep_once()
        except Exception as exc:
            logger.warning("[person-fact-dedupe] sweep iteration failed: %s", exc)
        await asyncio.sleep(hours * 3600)


def ensure_sweep_started() -> None:
    global _sweep_task
    cfg = _load_config()
    if not bool(cfg.get("sweep", {}).get("enabled", False)):
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if _sweep_task is not None and not _sweep_task.done():
        return
    _sweep_task = loop.create_task(_sweep_loop(), name="person_fact_dedupe.sweep")
    _get_logger().info("[person-fact-dedupe] sweep task scheduled")
