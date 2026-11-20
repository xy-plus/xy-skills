# xy-skills

Claude Code skill 集合，每目录一个 skill。

| skill | 用途 |
|---|---|
| `rules/xy-iron-law` | 三条铁律 |
| `rules/xy-repo-rules` | 仓库规范 |
| `xy-workflow` | 标准工作流程 |
| `workflow-capabilities/sync-think` | 把只有用户能定的事做成带建议的选择题 |
| `workflow-capabilities/xy-review` | 主审和剃刀的对抗审查、改动扫描、报告校验 |
| `workflow-capabilities/xy-goal` | 长目标契约与定时自查 |
| `xy-runs` | 文件去处、runs 布局与定期清理规则 |
| `xy-handoff` | 会话大任务归属、拆分交接与后台会话收尾 |
| `repository-care/xy-repo-map` | 从功能清单推导文件归属并生成离线功能树 |
| `repository-care/xy-cleanup` | 定期按功能审批清扫 |

## 安装

```bash
git clone https://github.com/xy-plus/xy-skills ~/xy-skills
mkdir -p ~/.claude/skills
while IFS= read -r -d '' skill_dir; do
  ln -sfn "$skill_dir" "$HOME/.claude/skills/$(basename "$skill_dir")"
done < <(find "$HOME/xy-skills" -mindepth 2 -maxdepth 3 -type f -name SKILL.md -printf '%h\0' | sort -z)
```

安装并固定 superpowers 7.0.2：

```bash
claude plugin marketplace add obra/superpowers#v7.0.2
claude plugin install superpowers@superpowers-dev --scope user
```

官方市场版会自动更新，所以这里固定标签版。已安装官方市场版的，按安装范围停用：

```bash
claude plugin disable superpowers@claude-plugins-official --scope user
claude plugin disable superpowers@claude-plugins-official --scope project
claude plugin list
```

最后核对仅启用 `superpowers@superpowers-dev` 7.0.2；未装在项目范围时，不运行带 `--scope project` 的停用命令。

改动扫描使用固定版本的 ruff：

```bash
python3 -m pip install --user ruff==0.12.12
```

在全局 `~/.claude/settings.json` 的 `env` 中显式设置 runs 绝对路径并保留现有配置，例如：

```json
{
  "env": {
    "RUNS_ROOT": "/data/user/xy/runs"
  }
}
```

重启会话后先创建该根目录，再按 [xy-goal README](workflow-capabilities/xy-goal/README.md) 的安装步骤，将 `UserPromptSubmit` 与 `PostToolUse`（matcher `AskUserQuestion`）两个 hook 合并到全局 settings.json。hook 和命令都用 `--root "$RUNS_ROOT"`，不另设根目录。配置后，用户原话和选择题回答会自动记录，开契约时领取之前的消息。

```bash
mkdir -p "${RUNS_ROOT:?请先在全局 settings.json 的 env 中设置 RUNS_ROOT}"
```

按顺序把下段写进全局 `~/.claude/CLAUDE.md`，让每个会话常驻加载文件去处、交接规则、铁律和仓库规范：

```markdown
@~/xy-skills/xy-runs/SKILL.md
@~/xy-skills/xy-handoff/SKILL.md

## 规则

- 铁律：skill `xy-iron-law`（https://github.com/xy-plus/xy-skills/blob/master/rules/xy-iron-law/SKILL.md），任何情况下都要遵守。
- 仓库规范：skill `xy-repo-rules`（https://github.com/xy-plus/xy-skills/blob/master/rules/xy-repo-rules/SKILL.md）。
- 工作流程：讨论问题并可能写代码时，按 skill `xy-workflow`（https://github.com/xy-plus/xy-skills/blob/master/xy-workflow/SKILL.md）走。

@~/xy-skills/rules/xy-iron-law/SKILL.md
@~/xy-skills/rules/xy-repo-rules/SKILL.md
```

`xy-workflow` 依赖 Claude Code 插件 `superpowers`（必须是 7.0.2）及本仓库的 `workflow-capabilities/xy-goal`、`workflow-capabilities/xy-review`、`workflow-capabilities/sync-think`；`xy-goal` 依赖 Claude Code 的 hook 和定时任务工具 `CronCreate`、`CronList`、`CronDelete`，运行前提见其 README。`xy-handoff` 使用 `xy-runs` 的运行目录，并依赖 Claude Code 自带的 `claude --bg`、`claude agents`、`claude respawn`、`claude rm`、`SendMessage`、`PushNotification`。

按 [定期清理说明](xy-runs/scheduled_cleanup/README.md) 安装清理定时器，并配置 runs 清理范围。

新增 skill 时建目录、写 `SKILL.md`，再按上文软链。仅当 `SKILL.md` 放不下代码或前提等内容时，才在 skill 目录加 README。

## 仓库功能清单

有功能图的仓库在根目录放 `repo-map/features.json`；有该文件时，每次改动按 `xy-workflow` 维护清单。格式、命令和推导规则见 [`repository-care/xy-repo-map/SKILL.md`](repository-care/xy-repo-map/SKILL.md)。

发布功能图需要 `web-portal`。清扫收尾时，`repository-care/xy-cleanup` 将决定清单中已回答功能当时的提交写入 `repo-map/approved.json`，供下次按提交间的改动规划审查范围。

## 实际使用

清扫判删时，按下面这些记录找某条规则、某段代码在实际使用中被用到、被触发或防住故障的证据：

| 记录 | 位置 | 看什么 |
|---|---|---|
| 会话记录 | `~/.claude/projects/*/*.jsonl` | 加载了哪些 skill，跑了哪些脚本和命令、报了什么错，用户纠正过什么 |
| 长目标契约 | `$RUNS_ROOT/*/*/xy-goal/` | 原话、事项、验收结论和 `links.md` |
| 运行目录 | `$RUNS_ROOT/*/*/` | 审查报告、决定页面的回答、交接说明、子代理日志 |
| 记忆 | `~/.claude/projects/*/memory/*.md` | 用户的纠正和偏好；只当线索，要追到会话记录原文 |
| 已装的入口 | `~/.claude/CLAUDE.md` 的引入、`~/.claude/settings.json` 的 hook、`~/.claude/skills/` 下的软链、`~/.config/systemd/user/runs-cleaner.*` | 哪些 skill 和脚本会被自动加载、触发或定时运行 |
| 清理日志 | `journalctl --user -u runs-cleaner` | 定期清理实际删了什么、拦下了什么 |
| 发布的页面 | `repository-care/xy-repo-map` 发布用的门户站点目录 | 功能图和决定页面的发布记录 |
| 提交历史 | 本仓及用到这些 skill 的仓库的 `git log` | 修过哪些故障、为什么加了某条规则 |

运行目录满 30 天未改动会被定期清理（见 `xy-runs`），更早的证据只能在会话记录、记忆和提交历史里找。

## 测试

`unittest discover` 不会进入没有 `__init__.py` 的功能子目录，因此按每个含测试文件的目录分别运行；以后新增测试目录会自动加入。任一目录失败时循环立即返回非零状态。从仓库根目录运行：

```bash
mapfile -t test_dirs < <(find . -path './.git' -prune -o -path './.worktrees' -prune -o -type f -name 'test_*.py' -printf '%h\n' | sort -u)
for test_dir in "${test_dirs[@]}"; do
  python3 -B -m unittest discover -s "$test_dir" -p 'test_*.py' || exit $?
done
```

浏览器测试需要 Python Playwright 和 Chromium。创建独立环境并安装依赖：

```bash
python3 -m venv ~/.venvs/xy-repo-map-playwright
source ~/.venvs/xy-repo-map-playwright/bin/activate
python -m pip install playwright
python -m playwright install chromium
```

将 `XY_REPO_MAP_CHROMIUM` 设为 Chromium 可执行文件绝对路径后运行：

```bash
export XY_REPO_MAP_CHROMIUM=/path/to/chrome
mapfile -t browser_dirs < <(find . -path './.git' -prune -o -path './.worktrees' -prune -o -type f -name 'browser_*_test.py' -printf '%h\n' | sort -u)
for browser_dir in "${browser_dirs[@]}"; do
  python3 -B -m unittest discover -s "$browser_dir" -p 'browser_*_test.py' || exit $?
done
```

路径缺失或浏览器不可用时测试失败，不跳过。

修改清扫卡片的问法后，按 `repository-care/xy-cleanup/review-misses.md` 对照。
格式检查通过不代表判断正确；修改 `workflow-capabilities/xy-review` 的主审、剃刀规则后，按 `workflow-capabilities/xy-review/review-misses.md` 对照。

分组目录或清理脚本路径变化后，部署时要重建个人技能软链、更新全局 `CLAUDE.md` 中的规则引入并重新安装用户级定时清理服务；只合并仓库提交不会更新这些仓外配置。
