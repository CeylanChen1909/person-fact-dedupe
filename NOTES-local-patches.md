# Host patch notes — ceylanchen1909.person-fact-dedupe v1.0.0

MaiBot 当前没有 person_fact / A_Memorix ingest 的官方写前 Hook。插件 Runner 是独立子进程，无法 monkey-patch Host 写入路径。

## What the patch does

- **File:** `<maibot>/src/person_info/person_info.py`
- **Function:** `store_person_memory_from_answer`
- **Where:** after `person.is_known` check, before `memory_service.ingest_text`
- **Call:** `dedupe_core.should_skip_person_fact_write(person_id=..., memory_content=..., chat_id=...)`
- **If True:** return early (skip write)
- **On exception:** fail-open (log warning, continue write)
- **Config:** plugin dir `config.toml` (read by `dedupe_core`, not only SDK config UI)
- **Plugin dir discovery (runtime):** `PERSON_FACT_DEDUPE_PLUGIN_DIR` → path baked by apply script → `/opt/maibot/plugins/ceylanchen1909_person-fact-dedupe` → `/opt/maibot/plugins/local_person-fact-dedupe`

## Apply / remove

```bash
python3 scripts/apply_host_patch.py --maibot-root /opt/maibot
python3 scripts/remove_host_patch.py --maibot-root /opt/maibot
```

`apply_host_patch.py` writes backup `person_info.py.bak-pre-factdedupe-<timestamp>` beside the file before modifying. `remove_host_patch.py` prefers restoring the newest such backup; otherwise reverses the known patch block.

## After MaiBot upgrades

If `person_info.py` was overwritten, re-run `apply_host_patch.py`. If the anchor text changed, the script aborts without writing — merge manually using this document, or wait for an official pre-write hook.
