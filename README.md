# xy-skills

个人 skill 都在这里维护，一个目录一个 skill；Claude Code 通过 `~/.claude/skills/<名字>` 软链加载，系统和各仓库的 CLAUDE.md 只写引用。

| skill | 用途 |
|---|---|
| `xy-iron-law` | 三条铁律 |
| `xy-repo-rules` | 仓库规范 |
| `xy-workflow` | 标准工作流程 |
| `xy-review` | 对抗审查：怎么派、怎么审、报告格式和格式检查脚本、怎么处理结果（`xy-workflow` 的所有审查都用它） |
| `xy-goal` | 长目标契约与定时自查 |
| `sync-think` | 拿不准的事做成选择题问用户 |

新加 skill：建目录、写 `SKILL.md`，再 `ln -s ~/xy-skills/<名字> ~/.claude/skills/<名字>`。
