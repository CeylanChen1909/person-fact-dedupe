# ceylanchen1909.person-fact-dedupe

**人物事实近重写入去重** — skip near-duplicate person facts before they are written into MaiBot long-term memory.

在人物事实（`person_fact`）写回长期记忆**之前**做近重复检测，避免同一件事被写成多条略有不同的事实（例如「想成为xx / 想当xx / 有成为xx的意愿」）。

| | |
|---|---|
| Plugin ID | `ceylanchen1909.person-fact-dedupe` |
| Suggested folder | `plugins/ceylanchen1909_person-fact-dedupe/` |
| Version | `1.0.0` |
| License | MIT |
| Author | [CeylanChen1909](https://github.com/CeylanChen1909) |

## Why a host patch is required

MaiBot 1.3.x 没有 `person_fact` / A_Memorix ingest 的**官方写前 Hook**。插件跑在独立 Runner 子进程里，无法直接 monkey-patch Host 的写入路径。

因此本插件需要在 Host 的 `src/person_info/person_info.py` 里给 `store_person_memory_from_answer` 打一小段补丁：在确认 `person.is_known` 之后、调用 `memory_service.ingest_text` 之前，调用 `dedupe_core.should_skip_person_fact_write(...)`。异常时 **fail-open**（照常写入并打 warning），不会因为插件挂了而丢事实。

我们已准备向上游提交 Feature Request，请求官方提供写前 Hook；有了 Hook 后即可去掉 Host 补丁。见 `docs/upstream-feature-request.md`。

## Behavior

1. Host 补丁拦截 `store_person_memory_from_answer`。
2. 解析 `person_id` 后，列出该人当前 `active` claims。
3. 优先用 A_Memorix EmbeddingAPI 算余弦相似度；不可用时回退到规范化文本相似度。
4. 相似度 ≥ `similarity_threshold`（默认 `0.88`）时跳过本次写入。
5. 可选 `sweep`：按间隔对存量 active claims 聚类，撤回较新近重（`fact_admin.retract`）。默认关闭。

## Install（手动）

推荐目录名（与市场惯例一致，下划线文件夹）：

```text
<maibot>/plugins/ceylanchen1909_person-fact-dedupe/
```

### 1) 复制插件

```bash
# 从 Release 源码包，或 git clone
git clone https://github.com/CeylanChen1909/person-fact-dedupe.git \
  /opt/maibot/plugins/ceylanchen1909_person-fact-dedupe
```

也兼容旧目录名 `local_person-fact-dedupe`（补丁脚本会自动查找）。

### 2) 打 Host 补丁（必须）

在 MaiBot 主机上执行（会先备份 `person_info.py`）：

```bash
cd /opt/maibot/plugins/ceylanchen1909_person-fact-dedupe
python3 scripts/apply_host_patch.py --maibot-root /opt/maibot
# 或: MAIBOT_ROOT=/opt/maibot python3 scripts/apply_host_patch.py
```

脚本是幂等的：已打过补丁会直接退出。可用环境变量：

- `MAIBOT_ROOT` — MaiBot 安装根目录
- `PERSON_FACT_DEDUPE_PLUGIN_DIR` — 插件目录（覆盖自动发现）

### 3) 重启服务

```bash
systemctl restart maibot
systemctl start maibot-weixin-ilink   # 若使用微信适配器
```

加载日志里应看到类似：

```text
person-fact-dedupe loaded: enabled=True host_patch=ok
```

若显示 `host_patch=MISSING`，说明第 2 步未成功，写前去重不会生效。

### 插件市场（后续）

提交到 [Mai-with-u/plugin-repo](https://github.com/Mai-with-u/plugin-repo) 通过后，可从 WebUI 插件市场安装。**即使市场装上了插件目录，仍须手动跑一次 `scripts/apply_host_patch.py`**，直到上游提供官方 Hook。

## Config

编辑插件目录下的 `config.toml`（改完重启 `maibot`）：

```toml
[plugin]
enabled = true

[dedupe]
similarity_threshold = 0.88
mode = "skip_duplicate"   # 或 merge_keep_oldest（存量清理时保留最早）
dry_run = false
person_id_allowlist = []  # 空=全部；填 person_id 则仅名单内

[sweep]
enabled = false           # 定时存量近重清理，默认关
interval_hours = 6
max_persons_per_run = 20
max_retracts_per_run = 50
```

## Uninstall

```bash
# 1) 去掉 Host 补丁（优先从 .bak-pre-factdedupe-* 恢复）
python3 scripts/remove_host_patch.py --maibot-root /opt/maibot

# 2) 删除插件目录
rm -rf /opt/maibot/plugins/ceylanchen1909_person-fact-dedupe

# 3) 重启
systemctl restart maibot
systemctl start maibot-weixin-ilink
```

## Upgrade risk

- **升级 MaiBot** 若覆盖了 `src/person_info/person_info.py`，Host 补丁会丢失 → 再跑一次 `scripts/apply_host_patch.py`。
- 补丁锚点依赖当前 `store_person_memory_from_answer` 源码结构；大版本改动可能导致脚本找不到锚点（脚本会中止且不改文件）。届时请对照 `NOTES-local-patches.md` 手工合并，或等官方 Hook。
- 本插件不修改已有事实的内容；`sweep` 撤回走 `fact_admin(retract)`，可按记忆系统规则恢复/审计。

## Offline smoke（可选）

```bash
MAIBOT_ROOT=/opt/maibot python3 smoke_offline.py
```

只读检查文本近重，不写库。

## Files

| Path | Role |
|------|------|
| `_manifest.json` | 插件清单 v2 |
| `plugin.py` | SDK 入口；校验 Host 补丁、刷新配置 |
| `dedupe_core.py` | 写前过滤与可选 sweep（由 Host 进程加载） |
| `config.toml` | 运行时配置 |
| `scripts/apply_host_patch.py` | 幂等打补丁 |
| `scripts/remove_host_patch.py` | 恢复备份或反补丁 |
| `NOTES-local-patches.md` | 补丁技术说明 |
| `docs/` | 市场上架 / 上游 Issue 草稿 |

## Disclaimer

Host 补丁会改 MaiBot 自带源文件。请先备份，并在非生产环境验证。作者不对因补丁或升级覆盖导致的问题承担责任——按 MIT 许可「按现状」提供。
