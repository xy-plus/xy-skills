# xy-skills

一组 Claude Code skill，一个目录一个 skill。

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

在全局 `~/.claude/settings.json` 的 `env` 里显式设置 runs 的绝对路径，保留已有配置，例如：

```json
{
  "env": {
    "RUNS_ROOT": "/data/user/xy/runs"
  }
}
```

重启会话后，先创建这个根目录，再按 [xy-goal/README.md](xy-goal/README.md) 的安装步骤，将 `UserPromptSubmit` 与 `PostToolUse`（matcher `AskUserQuestion`）两个 hook 合并到全局 settings.json。hook 和命令都用 `--root "$RUNS_ROOT"`，不另设根目录。配置后，用户原话和选择题回答会自动记录，开契约时领取之前的消息。

```bash
mkdir -p "${RUNS_ROOT:?请先在全局 settings.json 的 env 中设置 RUNS_ROOT}"
```

把下面这段按顺序写进全局 `~/.claude/CLAUDE.md`，让每个会话常驻加载文件去处、交接规则、铁律和仓库规范：

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

`xy-workflow` 依赖 Claude Code 插件 `superpowers` 不低于 6.4.1，以及本仓库的 `xy-goal`、`xy-review`、`sync-think`；`xy-goal` 依赖 Claude Code 的 hook 与定时任务工具 `CronCreate`、`CronList`、`CronDelete`，运行前提见它的 README；`xy-review/check.py` 复用同仓 `xy-handoff` 的 JSON 校验函数。`xy-handoff` 单向依赖 `xy-runs`，以及 Claude Code 自带的 `claude --bg`、`claude agents`、`claude respawn`、`claude rm`、`SendMessage`、`PushNotification`。

按 [xy-runs/README.md](xy-runs/README.md) 安装清理定时器，并按其中说明配置 runs 的清理范围。

新加 skill：建目录、写 `SKILL.md`，再按上面的方式软链。只有 `SKILL.md` 装不下的代码或前提等内容时，才给 skill 目录加 README。

## 仓库功能清单

有功能图的仓库把唯一由人维护的功能清单放在根目录 `repo-map/features.json`。清单只写功能、目的、理由出处、入口和根功能的独立子仓登记；文件归属、行数、依赖、标记与页面由 `xy-repo-map` 从 Git 跟踪文件推导。

清扫收尾时由 `xy-cleanup` 写 `repo-map/approved.json`（每个功能被认可时的指纹），下次清扫据此只审变过的功能。

最小清单示例：

```json
{
  "id": "sample",
  "title": "示例仓库",
  "what": "说明仓库提供什么。",
  "why": {
    "text": "需要一个可校验的功能清单。",
    "source": "起草",
    "quote": "",
    "from": ""
  },
  "directory": "",
  "subrepositories": [],
  "entries": [],
  "children": []
}
```

在仓库中使用 `xy-repo-map/repo_map.py`，并给出仓库根目录的绝对路径：

```bash
python3 /absolute/path/to/xy-skills/xy-repo-map/repo_map.py check \
  --repo /absolute/path/to/repository

python3 /absolute/path/to/xy-skills/xy-repo-map/repo_map.py render \
  --repo /absolute/path/to/repository \
  --out /absolute/path/to/new-output-directory

python3 /absolute/path/to/xy-skills/xy-repo-map/repo_map.py publish \
  --out /absolute/path/to/render-output-directory \
  --name site-name
```

`check` 校验清单并推导文件归属。`render` 先执行同样的检查，再生成可离线打开的 `index.html` 与 `map-data.json`。`publish` 检查这两个文件存在后，调用平台发布脚本。

## 测试

在仓库根目录依次运行：

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

把 `XY_REPO_MAP_CHROMIUM` 设为 Chromium 可执行文件的绝对路径后运行两个浏览器测试命令。缺少路径或浏览器不可用时测试会失败，不会跳过。

改清扫的审查问题后，按 `xy-cleanup/review-misses.md` 做一次对照。
