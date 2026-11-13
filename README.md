# xy-skills

Claude Code skill 集合，每目录一个 skill。

| skill | 用途 |
|---|---|
| `xy-iron-law` | 三条铁律 |
| `xy-repo-rules` | 仓库规范 |
| `xy-workflow` | 标准工作流程 |
| `xy-review` | 主审和剃刀的对抗审查、报告校验 |
| `xy-goal` | 长目标契约与定时自查 |
| `xy-runs` | 文件去处、runs 布局与定期清理规则 |
| `xy-handoff` | 会话大任务归属、拆分交接与后台会话收尾 |
| `xy-repo-map` | 从功能清单推导文件归属并生成离线功能树 |
| `xy-cleanup` | 按范围审查仓库并清理功能实现 |
| `sync-think` | 把只有用户能定的事做成带建议的选择题 |

## 安装

```bash
git clone https://github.com/xy-plus/xy-skills ~/xy-skills
mkdir -p ~/.claude/skills
for d in ~/xy-skills/*/; do
  [[ -f "${d}SKILL.md" ]] || continue
  ln -sfn "${d%/}" ~/.claude/skills/"$(basename "$d")"
done
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

在全局 `~/.claude/settings.json` 的 `env` 中显式设置 runs 绝对路径并保留现有配置，例如：

```json
{
  "env": {
    "RUNS_ROOT": "/data/user/xy/runs"
  }
}
```

重启会话后先创建该根目录，再按 [xy-goal/README.md](xy-goal/README.md) 的安装步骤，将 `UserPromptSubmit` 与 `PostToolUse`（matcher `AskUserQuestion`）两个 hook 合并到全局 settings.json。hook 和命令都用 `--root "$RUNS_ROOT"`，不另设根目录。配置后，用户原话和选择题回答会自动记录，开契约时领取之前的消息。

```bash
mkdir -p "${RUNS_ROOT:?请先在全局 settings.json 的 env 中设置 RUNS_ROOT}"
```

按顺序把下段写进全局 `~/.claude/CLAUDE.md`，让每个会话常驻加载文件去处、交接规则、铁律和仓库规范：

```markdown
@~/xy-skills/xy-runs/SKILL.md
@~/xy-skills/xy-handoff/SKILL.md

## 规则

- 铁律：skill `xy-iron-law`（https://github.com/xy-plus/xy-skills/blob/master/xy-iron-law/SKILL.md），任何情况下都要遵守。
- 仓库规范：skill `xy-repo-rules`（https://github.com/xy-plus/xy-skills/blob/master/xy-repo-rules/SKILL.md）。
- 工作流程：讨论问题并可能写代码时，按 skill `xy-workflow`（https://github.com/xy-plus/xy-skills/blob/master/xy-workflow/SKILL.md）走。

@~/xy-skills/xy-iron-law/SKILL.md
@~/xy-skills/xy-repo-rules/SKILL.md
```

`xy-workflow` 依赖 Claude Code 插件 `superpowers`（必须是 7.0.2）及本仓库的 `xy-goal`、`xy-review`、`sync-think`；`xy-goal` 依赖 Claude Code 的 hook 和定时任务工具 `CronCreate`、`CronList`、`CronDelete`，运行前提见其 README；`xy-review/check.py` 与 `xy-repo-map` 复用同仓 `xy-handoff` 的 JSON 校验函数。`xy-handoff` 单向依赖 `xy-runs` 及 Claude Code 自带的 `claude --bg`、`claude agents`、`claude respawn`、`claude rm`、`SendMessage`、`PushNotification`。

按 [xy-runs/README.md](xy-runs/README.md) 安装清理定时器，并配置 runs 清理范围。

新增 skill 时建目录、写 `SKILL.md`，再按上文软链。仅当 `SKILL.md` 放不下代码或前提等内容时，才在 skill 目录加 README。

## 仓库功能清单

有功能图的仓库在根目录放 `repo-map/features.json`；有该文件时，每次改动按 `xy-workflow` 维护清单。格式、命令和推导规则见 [`xy-repo-map/SKILL.md`](xy-repo-map/SKILL.md)。

清扫收尾时，`xy-cleanup` 将认可的功能指纹写入 `repo-map/approved.json`，供下次只审查有变化的功能。

## 测试

从仓库根目录依次运行：

```bash
python3 -B -m unittest discover -s xy-goal/tests
python3 -B -m unittest discover -s xy-handoff
python3 -B -m unittest discover -s xy-review
python3 -B -m unittest discover -s xy-cleanup/tests
XY_REPO_MAP_CHROMIUM=/path/to/chrome python3 -B -m unittest discover -s xy-cleanup/tests -p 'browser_*_test.py'
python3 -B -m unittest discover -s xy-runs/tests
python3 -B -m unittest discover -s xy-repo-map/tests -p 'test_*.py'
XY_REPO_MAP_CHROMIUM=/path/to/chrome python3 -B -m unittest discover -s xy-repo-map/tests -p 'browser_*_test.py'
```

浏览器测试需要 Python Playwright 和 Chromium。创建独立环境并安装依赖：

```bash
python3 -m venv ~/.venvs/xy-repo-map-playwright
source ~/.venvs/xy-repo-map-playwright/bin/activate
python -m pip install playwright
python -m playwright install chromium
```

将 `XY_REPO_MAP_CHROMIUM` 设为 Chromium 可执行文件绝对路径后运行两个浏览器测试命令。路径缺失或浏览器不可用时测试失败，不跳过。

修改清扫审查问题后，按 `xy-cleanup/review-misses.md` 对照。
格式检查通过不代表判断正确；修改 `xy-review` 的主审、剃刀规则后，按 `xy-review/review-misses.md` 对照。
