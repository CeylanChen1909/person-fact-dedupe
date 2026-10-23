#!/usr/bin/env python3
"""Remove the person-fact-dedupe host patch from person_info.py.

Prefer restoring the newest person_info.py.bak-pre-factdedupe-* beside the file.
If no backup exists, reverse the known patch block when the surrounding anchor matches.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from pathlib import Path

MARKER = "person-fact-dedupe"
HOOK_FN = "should_skip_person_fact_write"


def find_person_info(maibot_root: str | None) -> Path | None:
    roots: list[Path] = []
    if maibot_root:
        roots.append(Path(maibot_root))
    env = os.environ.get("MAIBOT_ROOT", "").strip()
    if env:
        roots.append(Path(env))
    roots.append(Path("/opt/maibot"))
    roots.append(Path.cwd())
    for root in roots:
        candidate = root / "src" / "person_info" / "person_info.py"
        if candidate.is_file():
            return candidate
    return None


ORIGINAL_BLOCK = (
    "        person = Person(person_id=person_id)\n"
    "        if not person.is_known:\n"
    '            logger.warning(f"用户 {clean_person_name or person_id} (person_id: {person_id}) 尚未认识，跳过写回")\n'
    "            return\n"
    "\n"
    '        participant_name = str(person.person_name or person.nickname or clean_person_name or "").strip()\n'
)

# Match from is_known block through participant_name, including our inserted try/except.
PATCH_RE = re.compile(
    r"(        person = Person\(person_id=person_id\)\n"
    r"        if not person\.is_known:\n"
    r"            logger\.warning\(f\"用户 \{clean_person_name or person_id\} \(person_id: \{person_id\}\) 尚未认识，跳过写回\"\)\n"
    r"            return\n"
    r"\n)"
    r"        # \[person-fact-dedupe\].*?\n"
    r"        try:\n"
    r".*?"
    r"        except Exception as _dedupe_exc:\n"
    r"            logger\.warning\(f\"\[person-fact-dedupe\] filter failed \(fail-open\): \{_dedupe_exc\}\"\)\n"
    r"\n"
    r"(        participant_name = str\(person\.person_name or person\.nickname or clean_person_name or \"\"\)\.strip\(\)\n)",
    re.DOTALL,
)


def newest_backup(person_info: Path) -> Path | None:
    backups = sorted(
        person_info.parent.glob("person_info.py.bak-pre-factdedupe-*"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return backups[0] if backups else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Remove person-fact-dedupe host patch")
    parser.add_argument("--maibot-root", default=None)
    parser.add_argument(
        "--mode",
        choices=("auto", "restore-bak", "reverse"),
        default="auto",
        help="auto: restore bak if present else reverse; restore-bak: only backup; reverse: only regex reverse",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    person_info = find_person_info(args.maibot_root)
    if person_info is None:
        print("ERROR: cannot find person_info.py", file=sys.stderr)
        return 1

    text = person_info.read_text(encoding="utf-8")
    if MARKER not in text and HOOK_FN not in text:
        print(f"no patch present: {person_info}")
        return 0

    bak = newest_backup(person_info)
    use_restore = args.mode in ("auto", "restore-bak") and bak is not None
    if args.mode == "restore-bak" and bak is None:
        print("ERROR: no .bak-pre-factdedupe-* found", file=sys.stderr)
        return 2

    if use_restore:
        print(f"restore from: {bak}")
        print(f"target:       {person_info}")
        if args.dry_run:
            print("dry-run: not writing")
            return 0
        # Keep a safety copy of the patched file
        safety = person_info.with_name(person_info.name + ".bak-before-unpatch")
        shutil.copy2(person_info, safety)
        shutil.copy2(bak, person_info)
        new_text = person_info.read_text(encoding="utf-8")
        if MARKER in new_text and HOOK_FN in new_text:
            print("WARNING: restored file still contains patch markers", file=sys.stderr)
        print("restored ok")
        return 0

    # reverse
    m = PATCH_RE.search(text)
    if not m:
        print(
            "ERROR: could not reverse patch block (pattern mismatch). "
            "Restore from .bak-pre-factdedupe-* manually.",
            file=sys.stderr,
        )
        return 3
    new_text = PATCH_RE.sub(r"\1\2", text, count=1)
    if not (MARKER not in new_text or HOOK_FN not in new_text):
        # If markers remain elsewhere, still ok if our block is gone
        pass
    if HOOK_FN in new_text:
        print("ERROR: reverse left hook call in place", file=sys.stderr)
        return 4

    print(f"reverse patch in: {person_info}")
    if args.dry_run:
        print("dry-run: not writing")
        return 0
    safety = person_info.with_name(person_info.name + ".bak-before-unpatch")
    shutil.copy2(person_info, safety)
    person_info.write_text(new_text, encoding="utf-8")
    print("reversed ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
