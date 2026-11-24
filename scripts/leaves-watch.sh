#!/usr/bin/env bash
# Leaves upstream watch
#
# 只监控两个仓库：
#   - LeavesMC/Leaves       官方仓库（已停更，重点看是否复活/出现 26.x）
#   - HyacinthHaru/Leaves   26.x 移植跟踪 fork
#
# 行为：只创建/更新一个 tracking issue，不修改代码、不推送分支。
#
# 用法：
#   GH_REPO=owner/repo GH_TOKEN=... ./scripts/leaves-watch.sh
#   DRY_RUN=1 ...  打印 issue 内容，不写 GitHub
#   FORCE=1 ...    即使没有变化也刷新 issue
set -eo pipefail

if [ -z "$GH_REPO" ]; then echo 'GH_REPO is required' >&2; exit 1; fi
if [ -z "$GH_TOKEN" ]; then echo 'GH_TOKEN is required' >&2; exit 1; fi
if [ -z "$DRY_RUN" ]; then DRY_RUN=0; fi
if [ -z "$FORCE" ]; then FORCE=0; fi

LEAVES_REPO="LeavesMC/Leaves"
HARU_REPO="HyacinthHaru/Leaves"
ISSUE_TITLE="[upstream-watch] Leaves 上游状态"
LABEL="upstream-watch"
MAX_COMMITS=20
TODAY=$(date -u +%Y-%m-%d)

api() { gh api "$@"; }
short() { printf '%s' "$1" | cut -c1-8; }
date_short() { printf '%s' "$1" | cut -c1-10; }

branch_map() {
    local repo="$1" pattern="$2"
    api "repos/$repo/branches?per_page=100" --paginate 2>/dev/null \
        | jq -s --arg re "$pattern" 'add | map(select(.name | test($re))) | map({(.name): .commit.sha}) | add // {}'
}

patch_mark() {
    local repo="$1" sha="$2" n
    n=$(api "repos/$repo/commits/$sha" --jq '[.files[]?.filename] | map(select(test("(^|/)patches/"))) | length' 2>/dev/null || true)
    if [ -z "$n" ]; then n=0; fi
    if [ "$n" -gt 0 ]; then printf '🩹'; fi
}

new_commits() {
    local repo="$1" old="$2" new="$3"
    if [ -z "$old" ]; then
        printf -- '- 首次记录，当前 HEAD %s\n' "$(short "$new")"
        return 0
    fi
    if [ "$old" = "$new" ]; then return 0; fi

    local lines
    lines=$(api "repos/$repo/compare/$old...$new" --jq '.commits[] | [.sha, .commit.committer.date, (.commit.message | split("\n")[0])] | @tsv' 2>/dev/null || true)
    if [ -z "$lines" ]; then
        printf -- '- 无法自动比较 %s...%s，请手动查看 https://github.com/%s/compare/%s...%s\n' \
            "$(short "$old")" "$(short "$new")" "$repo" "$old" "$new"
        return 0
    fi

    local count=0 sha date msg mark ds
    while IFS=$'\t' read -r sha date msg; do
        if [ -z "$sha" ]; then continue; fi
        ds=$(date_short "$date")
        mark=$(patch_mark "$repo" "$sha")
        printf -- '- %s %s %s %s\n' "$(short "$sha")" "$ds" "$msg" "$mark"
        count=$((count + 1))
        if [ "$count" -ge "$MAX_COMMITS" ]; then
            printf -- '- ...（仅显示前 %s 条）\n' "$MAX_COMMITS"
            break
        fi
    done <<< "$lines"
}

branch_changes() {
    local old="$1" new="$2"
    jq -nr --argjson old "$old" --argjson new "$new" '
        (($new | keys) - ($old | keys)) as $added |
        (($old | keys) - ($new | keys)) as $removed |
        (if ($added | length) > 0 then "- 新增分支: " + ($added | join(", ")) else empty end),
        (if ($removed | length) > 0 then "- 删除分支: " + ($removed | join(", ")) else empty end)'
}

echo '==> collecting upstream state'
leaves_master=$(api "repos/$LEAVES_REPO/commits/master" --jq .sha)
leaves_branches=$(branch_map "$LEAVES_REPO" '^(ver/26|dev/26|26)')
haru_branches=$(branch_map "$HARU_REPO" '^(master$|dev/|upgrade-)')

pr_state="unknown"
pr_updated=""
pr_json=$(api "repos/$LEAVES_REPO/pulls/863" --jq '{state: .state, updated_at: .updated_at}' 2>/dev/null || true)
if [ -n "$pr_json" ]; then
    pr_state=$(printf '%s' "$pr_json" | jq -r '.state')
    pr_updated=$(printf '%s' "$pr_json" | jq -r '.updated_at' | cut -c1-10)
fi

latest_release=""
rel_tag=$(api "repos/$LEAVES_REPO/releases/latest" --jq '.tag_name + " (" + (.published_at[0:10]) + ")"' 2>/dev/null || true)
if [ -n "$rel_tag" ]; then latest_release="$rel_tag"; fi
newest_release=""
newest_tag=$(api "repos/$LEAVES_REPO/releases?per_page=1" --jq '.[0] | .tag_name + " (" + (.published_at[0:10]) + ")"' 2>/dev/null || true)
if [ -n "$newest_tag" ]; then newest_release="$newest_tag"; fi

current_state=$(jq -nc \
    --arg master "$leaves_master" \
    --argjson branches "$leaves_branches" \
    --arg prState "$pr_state" \
    --arg prUpdated "$pr_updated" \
    --arg release "$newest_release" \
    --arg stable "$latest_release" \
    --argjson haru "$haru_branches" \
    '{version: 1, leaves: {master: $master, branches: $branches, pr863: {state: $prState, updated_at: $prUpdated}, latestRelease: $release, latestStable: $stable}, haru: {branches: $haru}, lastRun: ""}')

if ! gh label list --repo "$GH_REPO" --json name --jq '.[].name' 2>/dev/null | grep -qx "$LABEL"; then
    gh label create "$LABEL" --repo "$GH_REPO" --color 0E8A16 --description 'Leaves upstream watch' >/dev/null 2>&1 || true
fi

issues_json=$(gh issue list --repo "$GH_REPO" --state open --limit 100 --json number,title,body 2>/dev/null || echo '[]')
issue_json=$(printf '%s' "$issues_json" | jq --arg t "$ISSUE_TITLE" '[.[] | select(.title == $t)][0] // empty' 2>/dev/null || true)
issue_number=""
prev_state=""
if [ -n "$issue_json" ]; then
    issue_number=$(printf '%s' "$issue_json" | jq -r '.number')
    prev_state=$(printf '%s' "$issue_json" | jq -r '.body' | sed -n 's/.*<!-- leaves-watch-state: \(.*\) -->.*/\1/p' | head -n1)
fi

prev_norm='null'
if [ -n "$prev_state" ]; then
    prev_norm=$(printf '%s' "$prev_state" | jq -S 'del(.lastRun)' 2>/dev/null || echo null)
fi
cur_norm=$(printf '%s' "$current_state" | jq -S 'del(.lastRun)')
changed=0
if [ "$prev_norm" != "$cur_norm" ]; then changed=1; fi
if [ "$FORCE" = "1" ]; then changed=1; fi
echo "==> changed=$changed issue=$issue_number"

summary="## Leaves 上游监控 · $TODAY

> 监控范围仅限 $LEAVES_REPO 与 ${HARU_REPO}，不监控其它社区分支。

### LeavesMC/Leaves
- master: $(short "$leaves_master")
- 26.x 分支: "
if [ "$(printf '%s' "$leaves_branches" | jq 'length')" -eq 0 ]; then
    summary="${summary}无"
else
    while IFS=$'\t' read -r name sha; do
        summary="$summary
  - $name: $(short "$sha")"
    done < <(printf '%s' "$leaves_branches" | jq -r 'to_entries | sort_by(.key)[] | [.key, .value] | @tsv')
fi
summary="$summary
- PR #863 (26.1.2 移植): ${pr_state}（最近更新 ${pr_updated}）
- 最新 release（含预发布）: "
if [ -n "$newest_release" ]; then summary="$summary$newest_release"; else summary="$summary"'无'; fi
summary="$summary
- 最新 stable: "
if [ -n "$latest_release" ]; then summary="$summary$latest_release"; else summary="$summary"'无'; fi
summary="$summary

### HyacinthHaru/Leaves"
if [ "$(printf '%s' "$haru_branches" | jq 'length')" -eq 0 ]; then
    summary="$summary
- （未发现匹配分支）"
else
    while IFS=$'\t' read -r name sha; do
        summary="$summary
- $name: $(short "$sha")"
    done < <(printf '%s' "$haru_branches" | jq -r 'to_entries | sort_by(.key)[] | [.key, .value] | @tsv')
fi

if [ "$changed" = "1" ]; then
    summary="$summary

### 本次变化"
    if [ -z "$prev_state" ]; then
        summary="$summary
- 首次记录，建立监控基线。"
    else
        prev_master=$(printf '%s' "$prev_state" | jq -r '.leaves.master // empty')
        if [ "$prev_master" != "$leaves_master" ]; then
            summary="$summary

#### LeavesMC/Leaves master 新提交
$(new_commits "$LEAVES_REPO" "$prev_master" "$leaves_master")"
        fi

        prev_branches=$(printf '%s' "$prev_state" | jq -c '.leaves.branches // {}')
        cur_branches=$(printf '%s' "$leaves_branches" | jq -c .)
        if [ "$prev_branches" != "$cur_branches" ]; then
            summary="$summary

#### LeavesMC/Leaves 分支变化
$(branch_changes "$prev_branches" "$leaves_branches")"
            while IFS=$'\t' read -r name sha; do
                prev_sha=$(printf '%s' "$prev_branches" | jq -r --arg n "$name" '.[$n] // empty')
                if [ -n "$prev_sha" ] && [ "$prev_sha" != "$sha" ]; then
                    summary="$summary
- $name $(short "$prev_sha") -> $(short "$sha")
$(new_commits "$LEAVES_REPO" "$prev_sha" "$sha")"
                fi
            done < <(printf '%s' "$leaves_branches" | jq -r 'to_entries[] | [.key, .value] | @tsv')
        fi

        prev_pr=$(printf '%s' "$prev_state" | jq -r '.leaves.pr863.state // "unknown"')
        if [ "$prev_pr" != "$pr_state" ]; then
            summary="$summary
- PR #863 状态变化: $prev_pr -> $pr_state"
        fi

        prev_rel=$(printf '%s' "$prev_state" | jq -r '.leaves.latestRelease // empty')
        if [ "$prev_rel" != "$newest_release" ]; then
            summary="$summary
- 最新 release 变化（含预发布）: $prev_rel -> $newest_release"
        fi

        prev_stable=$(printf '%s' "$prev_state" | jq -r '.leaves.latestStable // empty')
        if [ "$prev_stable" != "$latest_release" ]; then
            summary="$summary
- 最新 stable 变化: $prev_stable -> $latest_release"
        fi

        prev_haru=$(printf '%s' "$prev_state" | jq -c '.haru.branches // {}')
        cur_haru=$(printf '%s' "$haru_branches" | jq -c .)
        if [ "$prev_haru" != "$cur_haru" ]; then
            summary="$summary

#### HyacinthHaru/Leaves 变化
$(branch_changes "$prev_haru" "$haru_branches")"
            while IFS=$'\t' read -r name sha; do
                prev_sha=$(printf '%s' "$prev_haru" | jq -r --arg n "$name" '.[$n] // empty')
                if [ -n "$prev_sha" ] && [ "$prev_sha" != "$sha" ]; then
                    summary="$summary
- $name $(short "$prev_sha") -> $(short "$sha")
$(new_commits "$HARU_REPO" "$prev_sha" "$sha")"
                fi
            done < <(printf '%s' "$haru_branches" | jq -r 'to_entries[] | [.key, .value] | @tsv')
        fi
    fi
    summary="$summary

### 建议动作
- [ ] 对照 docs/upstream/leaves-patch-map.md 评估新提交是否可移植
- [ ] 若出现 26.x 分支或 PR #863 合并：评估把 Leaves 监控升级为同步上游
- [ ] 若只是 1.21.x 修复：判断是否同样影响我们的 26.x 代码"
fi

body="$summary

<!-- leaves-watch-state: $(printf '%s' "$current_state" | jq -c .) -->"

if [ "$DRY_RUN" = "1" ]; then
    echo '===== DRY RUN: issue body ====='
    printf '%s\n' "$body"
    exit 0
fi

if [ -z "$issue_number" ]; then
    gh issue create --repo "$GH_REPO" --title "$ISSUE_TITLE" --label "$LABEL" --body "$body"
elif [ "$changed" = "1" ]; then
    gh issue comment "$issue_number" --repo "$GH_REPO" --body "$summary"
    gh issue edit "$issue_number" --repo "$GH_REPO" --body "$body"
else
    echo "no changes; issue #$issue_number left untouched"
fi
