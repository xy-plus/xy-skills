# xy-skills

个人 skill 都在这里维护，一个目录一个 skill；Claude Code 通过 `~/.claude/skills/<名字>` 软链加载，系统和各仓库的 CLAUDE.md 只写引用。

| skill | 用途 |
|---|---|
| `xy-iron-law` | 三条铁律及检查方法 |
| `xy-repo-rules` | 仓库规范 |
| `xy-workflow` | 标准工作流程 |
| `adversarial-review` | 对抗审查的模板与结果检查脚本（`xy-workflow` 引用） |
| `xy-goal` | 长目标契约与定时自查 |
| `sync-think` | 拿不准的事做成选择题问用户 |

新加 skill：建目录、写 `SKILL.md`，再 `ln -s ~/xy-skills/<名字> ~/.claude/skills/<名字>`。
