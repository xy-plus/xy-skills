---
description: 讨论问题并可能改代码时的标准工作流程；使用前单独加载，加载完再做别的；按 superpowers:brainstorming 定的规模走。
---

## 改动分档

加载本 skill 后，依次加载 `xy-goal`、`superpowers:brainstorming`、`sync-think`；四个都加载完再开始，包括读代码、读文档、跑命令和与用户讨论。

按 [superpowers:brainstorming v7.0.2](https://github.com/obra/superpowers/blob/v7.0.2/skills/brainstorming/SKILL.md) 原文定规模，不另加判据；在消息中说出规模和原因，不等回复。请求已含所需信息的快速任务，不讨论、不写或审 spec，从第 3 步开始，但仍终审、跑全套测试并合入；Spike 只答问题，调查代码不合入，不走编号步骤；小改动和项目走全部步骤，第 1 步 spec 是原文设计文档（小改动简写，项目写全）；项目 spec 写完后按原文做 builder check，再进第 2 步审查。

## 工作流程

superpowers 必须是 7.0.2。开工前用 `claude plugin list` 核对；启用的 superpowers 只有一个且版本为 7.0.2 才继续。否则停下，提醒用户按 README 安装固定版本，不带其他版本继续；引用的 skill 与本流程冲突时以本流程为准；派子代理时把它要守的边界写进任务说明。

本流程要求用 `xy-goal`；本会话未开契约先开。契约自动记录并编号用户原话；spec 和审查引用契约原话，不另抄。spec 和审查报告放在 `<runs>/<项目目录名>/<年月日-时分秒>-<仓库名>-<主题>/`，不入库；运行目录和子代理 brief 也放这里。事项指针登记该目录，并把目录和契约路径告诉每个子代理。契约登记每项产物：spec、代码；审查不单列，审查通过即 spec 项验收。代码项通过终审、快进合入、推送并完成收尾才算验收；按命令输出核实。

每项验收或取消前，收掉本项创建或使用、且清单中无其他开放事项在用的资源。先停掉不再需要的临时后台进程并确认已退出。`claude --bg` 起的后台会话用 `claude rm <短 id>` 删除，再确认它不在 `claude agents --json --all` 列表中。还要检查没有其他进程或配置在用这些资源。工作树和分支按 `superpowers:finishing-a-development-branch` 处置，不再需要的走放弃；运行目录和工作流目录交给 `xy-runs` 定期清理。仍有开放事项、进程或配置在用的，写明使用者，留给最后一个使用它的事项收尾；要保留的先移到长期位置并写明理由。把收尾结果写进该事项的指针或验收结论（`update <T> --pointer '<指针>'`／`accept --verdict`），并按命令输出核验验收。`xy-iron-law` 和 `xy-repo-rules` 已加载；子代理不继承，派出时把这两份 SKILL.md 交给它并说明照做。

自己只编排，尽量不写代码；自己写 spec 和任务说明。子代理只做分配的事，不合入、不再派子代理。第 1 步按 `sync-think` 问清要用户定的事，之后尽量不停下来等用户。所有审查按 `xy-review` 进行，以审查结论为闸；不通过不进下一步。

需拆给多个代理并行或换较弱模型实现时，把 spec 外由编排者定的决策（拆法、各段边界、先后）写进各段任务说明；派出前自查各段任务说明合起来覆盖 spec、互不重叠、没有占位且前后一致。并行时按 `superpowers:dispatching-parallel-agents` 判断能否并行及任务说明怎么写；各开工作树、各派一个，各审各合。

1. 被改仓有 `repo-map/features.json` 时，读清单说明改动归属哪个功能或要新增哪条，并运行 `python3 <xy-repo-map skill目录>/repo_map.py check --repo <仓绝对路径>`；没有该文件就都不做。用 `superpowers:brainstorming` 讨论并写 spec，每条要求注明依据的用户原话编号。原话没有直接要求、由派方或作者延伸调整的，另标「派方发起」或「作者发起」，关联来源原话，并写清证据：如何源于那句原话、不做会怎样、做了能得到什么、代价多少。改用户可见行为、用法或用户自己的规则时，先按 `sync-think` 附建议交用户定；其他由派方定，无派方时由作者定。用户只需决定会改变其可见行为、用法或自己规则的事项。小改动 spec 用几句说明做法、改哪些文件、怎么测，并逐句注明出处；项目写完整 spec。spec 开头说明任务拆分：不拆写理由；拆分则列各部分边界、验收标准、依赖和接口，并说明合起来覆盖整件事、互不重叠且适合独立验收。
2. spec 写完后按 `xy-review` 审查，通过后才实现。报告放 `<工作流目录>/spec/`；判因后拆出的报告放 `<工作流目录>/spec-<名>/`，并按 `xy-review` 的 `group<G>/round<R>/` 存放。
3. 开工作树前，确认被改子仓的 `.gitignore` 忽略 `.worktrees/`，再运行 `git worktree add .worktrees/<分支> -b <分支>`。在树里安装依赖、跑测试确认基线干净；不用 Claude Code 自带 worktree 工具。若被改仓有 `repo-map/features.json`，新功能须登记并填写出处；找不到用户原话或用户规范就标 `起草`。功能删除、合并、改名只在 `xy-cleanup` 清扫时做；只改已有功能的入口、格式或注释时，无需额外询问。按审过的 spec（快速任务按用户原话）派一个工作子代理实现，并带 `superpowers:test-driven-development`。任务说明写工作树绝对路径，命令用绝对路径或 `git -C`。卡住或遇 spec 未写的情况，停下报告编排者，由其决定，不等用户。任务说明还写明：互不依赖的测试并行跑；收掉自己启动的临时进程和后台会话，后台会话用 `claude rm` 删除，不用 `claude stop`，报告列出启动过的进程和会话；每轮报告只写本轮工作、结果和证据。续跑只写增量：本轮任务和依据；不重复首轮交代的规则、spec、任务说明，变化只指出位置。最后一个任务要把 spec 中需保留的信息和实现经验写进代码注释或 README、runbook、TODO 等文档，避免 spec 删除后丢失。
4. 工作树变基到主分支最新提交，再按 `xy-review` 终审。报告放 `<工作流目录>/code/`，与 `spec/`、`spec-<名>/` 平级，并按 `xy-review` 的 `group<G>/round<R>/` 存放；以审查结论作闸。
5. 按 `superpowers:verification-before-completion` 核实证据，再按 `superpowers:finishing-a-development-branch` 走「本地合并」；不出菜单、不等用户，合并后推主分支。主检出由大家共用，不在那里跑测试；只用 `git merge --ff-only` 快进，使主分支指向工作树里审过、测过的提交。不能快进就回第 4 步。删除工作树会一并删除其中被忽略的产物。
