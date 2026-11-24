# Leaves 上游补丁映射与监控基线

- 更新时间：2026-11-24
- 监控范围：**只监控 LeavesMC/Leaves 与 HyacinthHaru/Leaves**；不监控其它社区 fork/分支。
  由 .github/workflows/leaves-watch.yml 每周检查并维护 tracking issue，只读、不改代码。
- 版本同步由 .github/workflows/paper-sync.yml 负责（Paper ver/<mcVersion> 分支）。

## 当前基线

| 项 | 值 |
|---|---|
| Sapling 26.1.2 移植 fork 点 | 2026-07-12（paperRef e4e17fc9，Paper ver/26.1.2 HEAD 2026-07-06） |
| LeavesMC/Leaves master | a8bb2728（2026-08-13），无 26.x 分支 |
| LeavesMC/Leaves PR #863（26.1.2 移植） | open，mergeable_state=dirty（2026-09-10 后无进展） |
| HyacinthHaru/Leaves dev/26.2 | aa8ee07b（2026-09-16） |
| HyacinthHaru/Leaves dev/26.3 | 61984ac3（2026-11-11） |

## fork 点之后 Leaves 的待评估提交

这些是 26.1.2 基线之后 Leaves 官方 master 新增的提交，需逐项判断是否已包含/是否影响 26.x 代码：

| commit | 日期 | 说明 | 状态 / 结论 |
|---|---|---|---|
| a8bb2728 | 2026-08-13 | fix: correct region-unload-idle-ms unit handling (#878) | 待评估 |
| d3ab8e26 | 2026-07-20 | Fix Copper BlockEntity remove (#868) | 待评估 |
| 8278a373 | 2026-07-20 | 修复 #858/#859/#861/#869，port Winds-Studio/Leaf#769，关闭 #860 (#862) | 待评估 |

评估要求：
1. 确认问题在 26.1.2 上是否仍存在；
2. 若存在，能否直接移植为 Sapling L1 补丁；
3. 移植后在本地 parity 工具（tools/parity，不随仓库分发）下补充/运行相关用例；
4. 记录来源 commit、许可证与结论到下表。

## Leaves patch 到 Sapling 的映射（初始示例，持续补充）

| Leaves patch（编号/名称） | Sapling 对应实现 | 状态 | 最后核对 |
|---|---|---|---|
| 0007-Leaves-Fakeplayer / api 0003 fakeplayer API | Sapling 0141/0142（Carpet fake player 替换 Leaves bot） | 已替代 | 2026-11-24 |
| 0002-Leaves-Server-Config / 0008-Leaves-Config-API | LeavesConfig + GlobalConfigManager（保留，另做死配置清理） | 已保留 | 2026-11-24 |
| 0004-Leaves-Protocol-Core 及其 9 个子协议 | 保留（Jade/Xaero/Syncmatica/Servux/PCA/BBOR/REI/Bladeren/替代放置） | 已保留 | 2026-11-24 |
| 0070-Replay-Mod-API / Photographer | 保留（Replay API + PhotographerManager） | 已保留 | 2026-11-24 |
| 0130-Lithium-Sleeping-Block-Entity / 0090-Vanilla-hopper | 保留 | 已保留 | 2026-11-24 |
| （全部其余 Leaves 补丁） | 见 leaves-server/minecraft-patches 编号 | 待逐项补录 | — |

维护规则：
- 每次从 Leaves 上游 cherry-pick 个补丁时，必须在本表登记来源 commit 与许可证，并在 PR 中说明行为影响；
- 已被 Paper 原生吸收的补丁及时删除（参考 cache-climb-check、preventMovingIntoUnloadedChunks 等案例）；
- 若 Leaves 官方恢复 26.x（出现 26.x 分支或 PR #863 合并），停止 cherry-pick 模式，评估整体 rebase/re-anchor（预计 10–25 人日）。

## 变更记录

- 2026-11-24：建立本表；记录 fork 点后 3 个待评估 Leaves 提交。
