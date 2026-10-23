#!/usr/bin/env python3
"""Idempotently patch MaiBot store_person_memory_from_answer for person-fact dedupe.

Looks for the plugin under (in order):
  1. PERSON_FACT_DEDUPE_PLUGIN_DIR env
  2. <maibot>/plugins/ceylanchen1909_person-fact-dedupe
  3. <maibot>/plugins/local_person-fact-dedupe
  4. this repo's parent (when scripts/ lives inside the plugin dir)

MaiBot root candidates: MAIBOT_ROOT, /opt/maibot, cwd, and parents of this script.

Fail-open: the inserted code catches exceptions and lets writes proceed.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

MARKER = "person-fact-dedupe"
HOOK_FN = "should_skip_person_fact_write"
PLUGIN_FOLDER_NAMES = (
    "ceylanchen1909_person-fact-dedupe",
    "local_person-fact-dedupe",
)


def _ts() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def find_maibot_roots(explicit: str | None) -> list[Path]:
    roots: list[Path] = []
    if explicit:
        roots.append(Path(explicit))
    env = os.environ.get("MAIBOT_ROOT", "").strip()
    if env:
        roots.append(Path(env))
    roots.append(Path("/opt/maibot"))
    roots.append(Path.cwd())
    # scripts/ -> plugin -> plugins -> maibot
    here = Path(__file__).resolve()
    roots.append(here.parents[2] if len(here.parents) > 2 else here.parent)
    roots.append(here.parents[3] if len(here.parents) > 3 else here.parent)

    seen: set[str] = set()
    out: list[Path] = []
    for r in roots:
        try:
            key = str(r.resolve())
        except Exception:
            key = str(r)
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def find_person_info(roots: list[Path]) -> Path | None:
    for root in roots:
        candidate = root / "src" / "person_info" / "person_info.py"
        if candidate.is_file():
            return candidate
    return None


def find_plugin_dir(roots: list[Path], person_info: Path) -> Path | None:
    env = os.environ.get("PERSON_FACT_DEDUPE_PLUGIN_DIR", "").strip()
    if env:
        p = Path(env)
        if (p / "dedupe_core.py").is_file():
            return p

    # Prefer this checkout when apply script lives inside the plugin
    self_plugin = Path(__file__).resolve().parents[1]
    if (self_plugin / "dedupe_core.py").is_file() and self_plugin.name in PLUGIN_FOLDER_NAMES + (
        "person-fact-dedupe",
    ):
        # Still OK to use even if folder is the git checkout name
        pass

    maibot_from_pi = person_info.resolve().parents[2]  # .../src/person_info/file -> maibot
    search_roots = [maibot_from_pi, *roots]
    for root in search_roots:
        plugins = root / "plugins"
        for name in PLUGIN_FOLDER_NAMES:
            candidate = plugins / name
            if (candidate / "dedupe_core.py").is_file():
                return candidate

    if (self_plugin / "dedupe_core.py").is_file():
        return self_plugin
    return None


NEEDLE = (
    "        person = Person(person_id=person_id)\n"
    "        if not person.is_known:\n"
    '            logger.warning(f"用户 {clean_person_name or person_id} (person_id: {person_id}) 尚未认识，跳过写回")\n'
    "            return\n"
    "\n"
    '        participant_name = str(person.person_name or person.nickname or clean_person_name or "").strip()\n'
)

# Placeholder path replaced at apply time with the discovered plugin dir.
INSERT_TEMPLATE = '''        person = Person(person_id=person_id)
        if not person.is_known:
            logger.warning(f"用户 {clean_person_name or person_id} (person_id: {person_id}) 尚未认识，跳过写回")
            return

        # [person-fact-dedupe] host patch — see plugins/.../NOTES-local-patches.md
        try:
            import os as _dedupe_os
            import sys
            from pathlib import Path as _DedupePath

            _dedupe_candidates = []
            _env_dir = (_dedupe_os.environ.get("PERSON_FACT_DEDUPE_PLUGIN_DIR") or "").strip()
            if _env_dir:
                _dedupe_candidates.append(_DedupePath(_env_dir))
            _dedupe_candidates.extend(
                [
                    _DedupePath(__PLUGIN_DIR_LITERAL__),
                    _DedupePath("/opt/maibot/plugins/ceylanchen1909_person-fact-dedupe"),
                    _DedupePath("/opt/maibot/plugins/local_person-fact-dedupe"),
                ]
            )
            _dedupe_dir = None
            for _cand in _dedupe_candidates:
                if _cand.is_dir() and (_cand / "dedupe_core.py").is_file():
                    _dedupe_dir = _cand
                    break
            if _dedupe_dir is None:
                raise FileNotFoundError("person-fact-dedupe plugin dir not found")
            if str(_dedupe_dir) not in sys.path:
                sys.path.insert(0, str(_dedupe_dir))
            from dedupe_core import should_skip_person_fact_write as _should_skip_person_fact_write

            if await _should_skip_person_fact_write(
                person_id=person_id,
                memory_content=clean_content,
                chat_id=clean_chat_id,
            ):
                logger.info(
                    f"[person-fact-dedupe] skipped write person_id={person_id} chat_id={clean_chat_id}"
                )
                return
        except Exception as _dedupe_exc:
            logger.warning(f"[person-fact-dedupe] filter failed (fail-open): {_dedupe_exc}")

        participant_name = str(person.person_name or person.nickname or clean_person_name or "").strip()
'''


def build_insert(plugin_dir: Path) -> str:
    literal = repr(str(plugin_dir.resolve()))
    return INSERT_TEMPLATE.replace("__PLUGIN_DIR_LITERAL__", literal)


def already_patched(text: str) -> bool:
    return MARKER in text and HOOK_FN in text


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply person-fact-dedupe host patch")
    parser.add_argument("--maibot-root", default=None, help="MaiBot install root (default: auto)")
    parser.add_argument("--plugin-dir", default=None, help="Override plugin directory")
    parser.add_argument("--dry-run", action="store_true", help="Print actions only")
    args = parser.parse_args()

    roots = find_maibot_roots(args.maibot_root)
    person_info = find_person_info(roots)
    if person_info is None:
        print("ERROR: cannot find src/person_info/person_info.py", file=sys.stderr)
        print("Tried roots:", ", ".join(str(r) for r in roots), file=sys.stderr)
        print("Hint: set MAIBOT_ROOT or pass --maibot-root", file=sys.stderr)
        return 1

    if args.plugin_dir:
        plugin_dir = Path(args.plugin_dir)
    else:
        plugin_dir = find_plugin_dir(roots, person_info)
    if plugin_dir is None or not (plugin_dir / "dedupe_core.py").is_file():
        print("ERROR: cannot find plugin dir with dedupe_core.py", file=sys.stderr)
        print("Hint: set PERSON_FACT_DEDUPE_PLUGIN_DIR or pass --plugin-dir", file=sys.stderr)
        return 1

    text = person_info.read_text(encoding="utf-8")
    if already_patched(text):
        print(f"patch already present: {person_info}")
        print(f"plugin dir: {plugin_dir}")
        return 0

    if NEEDLE not in text:
        print(
            "ERROR: expected anchor not found in store_person_memory_from_answer — "
            "MaiBot version may differ. Aborting without changes.",
            file=sys.stderr,
        )
        return 2

    insert = build_insert(plugin_dir)
    new_text = text.replace(NEEDLE, insert, 1)
    if not already_patched(new_text):
        print("ERROR: patch insertion failed sanity check", file=sys.stderr)
        return 3

    bak = person_info.with_name(f"person_info.py.bak-pre-factdedupe-{_ts()}")
    print(f"person_info: {person_info}")
    print(f"plugin_dir:  {plugin_dir}")
    print(f"backup:      {bak}")
    if args.dry_run:
        print("dry-run: not writing")
        return 0

    shutil.copy2(person_info, bak)
    person_info.write_text(new_text, encoding="utf-8")
    assert already_patched(person_info.read_text(encoding="utf-8"))
    print("patched ok")
    print("Restart maibot (and adapters) for the patch to take effect.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
