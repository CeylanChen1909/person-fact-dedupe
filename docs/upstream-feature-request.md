# Draft: Upstream feature request — person_fact pre-write hook

**Do not file until parent/user confirms.** Prefer filing on the MaiBot host repo first (write path lives there). Optional CC / second issue on A_memorix if maintainers want ingest-level hooks.

## Target repositories

| Priority | Repo | Why | New issue URL |
|----------|------|-----|----------------|
| 1 (primary) | [Mai-with-u/MaiBot](https://github.com/Mai-with-u/MaiBot) | `store_person_memory_from_answer` in `src/person_info/person_info.py` is the writeback entry; plugins run in a separate Runner and cannot patch Host | https://github.com/Mai-with-u/MaiBot/issues/new |
| 2 (optional) | [A-Dawn/A_memorix](https://github.com/A-Dawn/A_memorix) | If ingest should expose a filter/hook before `ingest_text` commits claims; MaiBot currently vendors/bundles A_memorix under `src/A_memorix` | https://github.com/A-Dawn/A_memorix/issues/new |

Related community plugin that needs this: https://github.com/CeylanChen1909/person-fact-dedupe

---

## Proposed title

```text
feat: official pre-write hook for person_fact / memory ingest (so plugins need no host patch)
```

## Proposed body

```markdown
### Summary / 摘要

MaiBot 插件运行在独立 Runner 子进程，无法对 Host 侧 `store_person_memory_from_answer` → `memory_service.ingest_text` 做可靠的 monkey-patch。社区若要做「人物事实写前查重 / 合并近义事实」之类能力，目前只能改 Host 源码（升级易丢补丁）。

请求提供**官方写前 Hook / 过滤器 API**，让插件在不修改 `person_info.py` 的前提下拦截或改写即将写入的 person_fact。

### Motivation / 动机

同一用户事实常被多次提取成近义句（如「想成为xx / 想当xx」），长期记忆膨胀、检索噪声变大。写前去重比事后定时清理更干净。

参考实现（需 Host 补丁）：https://github.com/CeylanChen1909/person-fact-dedupe

### Proposed API (sketch)

任选其一或组合：

1. **Host callback / plugin capability**（推荐）  
   例如在 `store_person_memory_from_answer` 于 `person.is_known` 之后、`ingest_text` 之前：
   ```python
   # pseudo
   decision = await plugin_hooks.emit(
       "memory.person_fact.pre_write",
       person_id=person_id,
       text=clean_content,
       chat_id=clean_chat_id,
       metadata={...},
   )
   if decision and decision.skip:
       return
   ```
   - 约定：异常 **fail-open**（不影响写入）
   - 支持多个监听者；任一 `skip` 即可跳过，或返回合并后的 canonical text

2. **A_Memorix ingest filter**  
   在 `ingest_text` / claim 提交前提供同步或异步 filter，按 `source_type=="person_fact"` 生效。

3. **内置近重合并**  
   官方在写回路径做 embedding/文本近重合并（阈值可配）。有 Hook 后社区也能自研策略。

### Acceptance criteria

- [ ] 文档化的稳定 Hook 名称与 payload
- [ ] 插件市场纯目录安装即可生效，**无需**改 `src/person_info/person_info.py`
- [ ] Hook 失败 fail-open，不阻断记忆写入
- [ ] （可选）SDK 示例插件：skip near-duplicate person facts

### Environment

- Observed on MaiBot 1.3.x-class installs with bundled A_memorix
- Write path: `src/person_info/person_info.py` → `store_person_memory_from_answer`

### Willingness to help

可提供补丁草稿、测试用例，或把现有 `person-fact-dedupe` 改成纯 Hook 实现作为示例。
```

## CLI (after confirmation)

```bash
gh issue create -R Mai-with-u/MaiBot \
  --title "feat: official pre-write hook for person_fact / memory ingest (so plugins need no host patch)" \
  --body-file docs/upstream-feature-request.md
```
