# Draft: Add Plugin issue for Mai-with-u/plugin-repo

**Do not file until parent/user confirms.**

- Target: https://github.com/Mai-with-u/plugin-repo/issues/new?template=add-plugin.yml
- Template: `Add Plugin / 添加插件`

## Form fields

**插件 ID / Plugin ID**

```text
ceylanchen1909.person-fact-dedupe
```

**仓库地址 / Repository URL**

```text
https://github.com/CeylanChen1909/person-fact-dedupe
```

**确认清单 / Checklist**

- [x] 我的仓库是公开的
- [x] 仓库根目录包含 `_manifest.json`
- [x] manifest 使用 `manifest_version: 2`，并包含 v2 必需字段
- [x] 仓库包含 LICENSE 文件

**补充说明 / Additional Information**

```markdown
## 简介

人物事实（person_fact）写回前的近重复过滤：用向量/文本相似度跳过「同一件事多种说法」的重复入库。

## 重要限制（请审核时注意）

MaiBot 目前没有 person_fact 写前官方 Hook，本插件**必须**配合仓库内 `scripts/apply_host_patch.py` 对 Host `src/person_info/person_info.py` 打一小段 fail-open 补丁后才能生效。市场仅安装插件目录时，用户仍需手动跑补丁脚本；README 已醒目标注。

已另起草上游 Feature Request，请求官方提供写前 Hook，以便去掉 Host 补丁依赖。

## 版本

- manifest `version`: `1.0.0`
- GitHub Release / Tag: `v1.0.0`（与 manifest 三段式一致）
- License: MIT
```

## CLI equivalent (after confirmation)

```bash
gh issue create -R Mai-with-u/plugin-repo \
  --title "[Plugin] ceylanchen1909.person-fact-dedupe" \
  --body-file docs/plugin-repo-add-plugin-issue.md
```

（正式提交时建议用网页模板勾选 checklist，或按模板字段精简 body。）
