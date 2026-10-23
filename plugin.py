"""MaiBot plugin: person-fact near-duplicate filter (host patch does the write-path work)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, ClassVar, Iterable, List

from maibot_sdk import Field, MaiBotPlugin, PluginConfigBase

PLUGIN_DIR = Path(__file__).resolve().parent
PLUGIN_MARK = "person-fact-dedupe"


def _candidate_maibot_roots() -> List[Path]:
    roots: List[Path] = []
    env = os.environ.get("MAIBOT_ROOT", "").strip()
    if env:
        roots.append(Path(env))
    # plugin lives at <maibot>/plugins/<folder>/
    roots.append(PLUGIN_DIR.parent.parent)
    roots.append(Path("/opt/maibot"))
    roots.append(Path.cwd())
    # de-dupe while preserving order
    seen = set()
    out: List[Path] = []
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


def find_person_info_py() -> Path | None:
    for root in _candidate_maibot_roots():
        candidate = root / "src" / "person_info" / "person_info.py"
        if candidate.is_file():
            return candidate
    return None


class PluginSection(PluginConfigBase):
    __ui_label__ = "插件"
    __ui_order__ = 0

    enabled: bool = Field(default=True, description="是否启用（host 补丁也会读 config.toml）")
    config_version: str = Field(default="1.0.0", description="配置版本")


class DedupeSection(PluginConfigBase):
    __ui_label__ = "去重"
    __ui_order__ = 1

    similarity_threshold: float = Field(default=0.88, description="近重相似度阈值")
    mode: str = Field(default="skip_duplicate", description="skip_duplicate | merge_keep_oldest")
    dry_run: bool = Field(default=False, description="只记日志不拦截/不撤回")
    person_id_allowlist: list[str] = Field(default_factory=list, description="为空=全部；否则仅名单内")


class SweepSection(PluginConfigBase):
    __ui_label__ = "存量清理"
    __ui_order__ = 2

    enabled: bool = Field(default=False, description="是否启用定时存量近重清理")
    interval_hours: float = Field(default=6.0, description="清理间隔（小时）")
    max_persons_per_run: int = Field(default=20, description="每轮最多处理多少人")
    max_retracts_per_run: int = Field(default=50, description="每轮最多撤回多少条")


class PersonFactDedupeConfig(PluginConfigBase):
    plugin: PluginSection = Field(default_factory=PluginSection)
    dedupe: DedupeSection = Field(default_factory=DedupeSection)
    sweep: SweepSection = Field(default_factory=SweepSection)


class PersonFactDedupePlugin(MaiBotPlugin):
    """声明型插件：真正的写前过滤在 Host 的 person_info 补丁里。"""

    config_model = PersonFactDedupeConfig
    config_reload_subscriptions: ClassVar[Iterable[str]] = ()

    async def on_load(self) -> None:
        host_patch = find_person_info_py()
        patch_ok = False
        try:
            if host_patch is not None:
                text = host_patch.read_text(encoding="utf-8")
                patch_ok = PLUGIN_MARK in text and "should_skip_person_fact_write" in text
        except Exception as exc:
            self.ctx.logger.warning("person-fact-dedupe: cannot verify host patch: %s", exc)

        enabled = True
        try:
            enabled = bool(self.config.plugin.enabled)
        except Exception:
            enabled = True

        # Refresh file-based config cache used by host patch
        try:
            import sys

            if str(PLUGIN_DIR) not in sys.path:
                sys.path.insert(0, str(PLUGIN_DIR))
            from dedupe_core import _load_config, ensure_sweep_started

            _load_config(force=True)
            ensure_sweep_started()
        except Exception as exc:
            self.ctx.logger.warning("person-fact-dedupe: config/sweep init: %s", exc)

        self.ctx.logger.info(
            "person-fact-dedupe loaded: enabled=%s host_patch=%s config=%s",
            enabled,
            "ok" if patch_ok else "MISSING",
            PLUGIN_DIR / "config.toml",
        )
        if not patch_ok:
            self.ctx.logger.error(
                "person-fact-dedupe: host patch missing in person_info.py — "
                "write-path dedupe will NOT run. See README.md / NOTES-local-patches.md "
                "and run scripts/apply_host_patch.py"
            )

    async def on_unload(self) -> None:
        self.ctx.logger.info("person-fact-dedupe unloaded")

    async def on_config_update(self, scope: str, config_data: dict[str, Any], version: str) -> None:
        del config_data
        try:
            import sys

            if str(PLUGIN_DIR) not in sys.path:
                sys.path.insert(0, str(PLUGIN_DIR))
            from dedupe_core import _load_config

            cfg = _load_config(force=True)
            self.ctx.logger.info(
                "person-fact-dedupe config updated: scope=%s version=%s enabled=%s threshold=%s",
                scope,
                version,
                cfg.get("plugin", {}).get("enabled"),
                cfg.get("dedupe", {}).get("similarity_threshold"),
            )
        except Exception as exc:
            self.ctx.logger.warning("person-fact-dedupe config reload failed: %s", exc)


def create_plugin() -> MaiBotPlugin:
    return PersonFactDedupePlugin()
