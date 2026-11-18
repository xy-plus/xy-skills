---
description: 按功能范围审查仓库，让代码相对功能清单最小，并一次问清需要用户决定的事。
---

# 清扫仓库功能

## 开始

用户须点名仓库，并提供仓根绝对路径及功能 id 列表或「全部」；范围不可省。画图、更新图、看图转来的清扫均用「全部」；用户要一次看全部时加重审。用户自定范围时，「算图」不改范围。按 `xy-workflow` 建工作树。

## 清扫流程

| 阶段 | 做什么 |
|---|---|
| 算图 | 缺少 `repo-map/features.json` 时按仓库现状起草清单；`why` 无法在用户原话或规范中核实时标 `起草`，功能粒度按人的需要定。README 没有「实际使用」时，按现状拟定记录来源（如部署记录、维护中的 baseline、会话和契约记录、运行目录）及跳过名单，在同一审查前决定页请用户确认或修改一次；确认后写入 README「实际使用」，之后只按用户要求修改，审查按其内容找证据。范围内 `items` 为 `[]` 的功能，按 `xy-repo-map` 条目格式起草、标 `status: 起草` 并写入清单；有子功能且没有任何直属文件的上级除外。直属文件按 `map-data.json` 的 `files` 各组计算，TODO 也算。这类上级只作分组，没有直属实体可判，结果由子功能代表；没有子功能的未实现叶功能仍须记录自己的目标。`check` 通过后再审查，剃刀 `keep` 可引用这些条目。缺 `repo-map/skip.json` 时按目录拟定跳过名单；首次建功能清单时一并创建。`check` 通过后用 `render` 生成清扫前图、用 `publish --as 清扫前` 发布并给用户链接。按 README 跑全套测试，记下命令、结果和失败项作为行为基线。再运行 `approvals.py plan`；用户要求重审时用 `--review 全部`，否则用 `--review 变过的`。 |
| 审查 | 按 `xy-review` 定义的主审、剃刀角色和派法，审 `plan` 列出的每个功能（含上级），分别填表并逐条审直属 TODO；派审前先另派子代理产出 `test-candidates.json`，再派子代理逐条读 README「实际使用」来源，推出可在副本短跑的实际场景（每条来源可有零个、一个或多个；如维护中的 baseline 可推出短回测，会话记录未必能推出可运行命令），短跑并统计非测试代码覆盖；Python 用 coverage.py 并统计子进程，Rust 用 llvm-tools。只有确认不碰真实账户、不写生产数据库、不部署服务才运行；真实数据只读、产出写入副本、可用时用 dry-run。确认不了就不跑，记 `ran: false` 和原因。生成 `usage-coverage.json` 交剃刀逐段判断未执行到的代码。派审时提供 README「实际使用」、本仓历次清扫决定原件及范围内功能 `why.from` 原件；重做一轮时，还要把上轮未实施且未由用户决定的报告交给复审者；找不到须注明。首轮剃刀有 `keep` 或任一首轮报告有孤儿发现的功能逐个另派新剃刀复审：逐条推翻 `keep` 并挑战 `smallest`（实体是否必要、是否做到、能否用更小做法或现成内容替代），在副本删除实现并跑测试、看测试执行位置、查同分支或同断言的用例及重复规则；孤儿目标逐条在副本删除并跑测试，在 `attempt` 写明测试命令和结果。孤儿判「站得住」表示删除会使测试或实际使用出错，或实际使用本来就用到，建议补条目；判「站不住」表示删除不影响使用，建议删除。只有目标是测试或文档，或目标是代码且每行都在 `usage-coverage.json` 对应文件的 `missed` 范围内，孤儿才能判「站不住」；实际使用执行到目标行或文件在 `unmeasured` 中都不能这样判。孤儿目标本身就是发现，不另写重叠发现；每条结论都须附实体范围外的有效证据。检查器列出待复审功能；复审无新发现才算无可删项，不设删减数量。主审还读完整清单和 `map-data.json`，检查范围内重叠、分组、措辞、粒度，以及条目未实现或实现漏入图；代码条目无测试即未实现。复审也挑战 `keep`：删除会使条目不成立的交用户决定；目标不在 TODO 文件、不是功能级删除或合并、`changes_behavior` 为 `false` 且类型不是「孤儿」「改清单」时「直接做」，其余「问你」。审查、找删减、判理由由审查子代理做；编排者只派审、核证、汇总决定并按选择实施，不补结论、不另写基准；有疑问续派审查者。 |
| 询问 | 将待决定事项汇总成清单，每项写编号、标题、说明、适配的选项、建议及来源，均据报告。用户决定条目层面的事项，以及会改变用法或对外的行为改动、孤儿是否补图、改功能清单。改写自已确认条目的起草条目，用 `compare` 附上次确认的文字：`lines` 写该条目在当前清单中的行，`before` 为上次确认的文字，`after` 为当前文字。建议按判删标准给证据；孤儿决定项写复审试删的测试命令和结果，建议照判定给；两报告相反时列双方理由。问是否行动须提供「先不做」。每份文档改写单列一项，带 `compare` 逐块对照，选项为「用改写稿」「不用」「按我写的改」，建议照剃刀报告。用户让 AI 代选时，只核对要求是否保留及字数是否减少，不因原文是用户规则而直接否决。理由来源为「起草」的功能本轮不逐实体审；用户确认或改写后先更新理由（确认标「确认」，改写记原话，均写回答出处），再派剃刀逐实体审，以便检查器按已确认理由核对覆盖与复审范围。复审发现须反驳首轮剃刀的 `keep`、`smallest` 或发现，双方理由都写入清单；新发现再问。后台运行 `decision_page.py ask` 并发送链接；退出码 0 后读取回答文件，留在事项目录作决定原件。 |
| 修改 | 按报告实施决定：「直接做」直接实施，「问你」只按用户选择。`changes_behavior` 为 `true` 时，表示对外行为或用法改变；对外命令、选项、脚本或文件名、入口和用法有任何变化都算 `true`。其他改动只有在目标不在 TODO 文件、不是功能级删除或合并且类型不是「孤儿」「改清单」时，才标为「直接做」。功能级「删除」「合并」会改 `repo-map/features.json`，且无法引用删后仍成立的原条目，必须问你；缩小实现须按审查规则提「重构」。用户确认的起草条目标为「确认」；用户决定删除条目时连同专属实现一起删除；决定补图的孤儿写成「确认」条目。选「用改写稿」时逐块替换原文并照改备注；终审前按下方「注释改写」步骤处理本次范围内的代码和测试，再由主审逐项核对要求、约束和例外。按 `xy-workflow` 实施并终审；终审通过、合入前按 `plan` 列出的本轮实际审过功能运行 `approvals.py record`，使用 `--features` 逐项列出功能 id（只记列出项、不递归；单独 `全部` 表示清单全部功能），`plan --scope` 仍递归；`--kept-markers` 只列询问阶段用户决定保留的标记，汇报照抄该列；将 `repo-map/approved.json` 与改动一并提交。 |
| 汇报 | 用 `render` 和 `publish --as 清扫后` 发布清扫后图，再用 `publish --as 当前` 更新固定站点。对比顶层功能的代码、测试、文档、注释行数及标记数量；汇报全仓前后总行数、净减与比例、根功能 `doc_chars.total` 前后值与净减/比例，以及测试结果和遗留问题。 |

功能清单格式及 `check`、`render`、`publish` 命令与参数见 `xy-repo-map`，本 skill 不重复定义。

审查前在副本里另派子代理为本次范围产出 `test-candidates.json`。Python（含 unittest 写法）用 pytest：先以 `pytest --setup-plan -q -o addopts= --junitxml=<list> -o junit_family=xunit1` 在同一 rootdir 生成完整清单和 JUnit，并保存终端输出；不加 `-k`、`-m`、`--deselect` 等筛选参数。实际运行另出 JUnit。Rust 用 cargo-nextest：以 `cargo nextest list --message-format json` 生成完整清单；仓库配置了 default-filter 时加 `--ignore-default-filter`；运行时生成 JUnit。需要的工具装在副本环境，如 `pip install pytest coverage`、`cargo install cargo-nextest --locked`、`rustup component add llvm-tools-preview`；装不上就把对应文件记入 `unmeasured_files` 并说明原因。

每个测试项分别统计非测试代码覆盖。Python 用 coverage.py 按测试项记录上下文；Rust 测试二进制用 `-C instrument-coverage` 插桩，按 nextest 测试名为每个测试进程设置 `LLVM_PROFILE_FILE`，再用 `llvm-profdata merge`、`llvm-cov export` 汇总。子进程执行的行归到启动它的测试项；归属不准时把启动子进程的函数记入 `unmeasured_functions`。抽样变异逐个运行，每个变异都跑完全部测试、不提前停止，并记录哪些测试项失败（即杀死变异）。其他运行器或语言的测试文件记入 `unmeasured_files`；量不了的文件和函数都写原因，不得将未测结果记成空覆盖。

### 注释改写

对本次清扫范围内、未命中 `repo-map/skip.json` 且含注释或文档串的功能直属的代码文件和测试文件执行；`xy-repo-map` 文件按代码或测试处理。用代码改完后的文件。先运行 `check_comment_rewrite.py blocks --original <文件>`。检查器认不出语言的文件不改，在汇报中列出。

每份文件交给新开的改写者。它只看原文、代码及 `blocks` 输出，逐块判断删、压或留：删对读代码、改代码的人没有影响的信息；有影响的信息全部保留并换成最短写法，包括代码看不出的原因、前置条件、约束和不变量、单位和格式、副作用、对外约定、警告、引用及公开接口用法。不要补原文没有的说法。给工具看的注释（如 `noqa`、`type:`）和注释里的代码示例原样保留；函数体只有一个文档串时只能压短，不能删除。改写者只交块清单 JSON [{"id":"<块 id>","text":"<含注释记号的新文字；整块删除写空字符串>"}]，不交整份文件，也不修改代码。

运行 `check_comment_rewrite.py splice --original <文件> --blocks <块清单> --output <改写稿>` 生成候选稿。再把原文先单独交给另一位新开的评审，让它逐条列出注释和文档串中的信息及原文行号，并按「在」「变了」「丢了」判断，给变了或丢了的内容标明是否影响读代码、改代码的人；从代码、名字和附近看不出来时判为有影响；另列原文没有的说法。评审 JSON 按 `gate` 的格式填写：每项带原文 `block` id、`lines`、`original`、`status`、`rewritten`、`impact`、`why`，另有 `added`。运行 `check_comment_rewrite.py gate --original <文件> --blocks <块清单> --rewrite <改写稿> --judgement <评审 JSON>`。通过才直接替换原文件，不进决定清单。没通过时把有影响的条目交回原改写者补改一次块清单、重新拼接，再派新评审重评；仍没通过就不改该文件。评审负责核对检查器无法识别的代码示例写法。

汇报改了几份文件、每份注释和文档串的非空白字数前后、没通过的文件和原因，以及因语言不支持而未改的文件。

### 功能审查问题

| 字段 | 问题 |
|---|---|
| `what_true` | 对照「要什么」和条目，是否有写了却没做到的，或做了却没写的？代码类条目没有测试也算未实现。上级功能须对照子功能合起来的工作。 |
| `why_holds` | 「为什么」是否真是本功能的理由（非进度、旧版状态或「要什么」的改写）？`quote`、`from` 和原件是否支持它？原话是否正确、合理、与其他原话矛盾或存在更好办法？如今还成立吗？标「确认」「原话」只说明理由出处，仍须判断。 |
| `structure` | 按第一性原理检查同一「要什么」能否用更清楚的结构实现；是否该重构、合并（含跨功能）、拆分或新建抽象。逐项检查本功能共用文件（`map-data.json` 的 `files.shared`）是否应按功能拆开。 |

剃刀先判功能是否必要、最小规模，再逐项说明直属实体为何保留；说不出理由就建议删除，拿不准就问你。

| 字段 | 问题 |
|---|---|
| `necessity` | 功能在实际使用中有没有真意义（按判删标准，不看原话措辞）？少做能否达目的？复杂度和行数代价相称吗？ |
| `smallest` | 分两步。先不看代码，按本功能「要什么」「为什么」及 `map-data.json` 行数估最小实现：列出部分、职责和各自行数；按需验证的行为估测试，每种一两个用例。现状中的路径、阶段、模块数不是要求；只保留要求的结果和约束，可合并或省去的就简化；不确定变体是否必需则提问。再逐块比代码，列出多余分支、兼容、配置、层次、仓库或依赖中可复用的实现及重复项，并写行数；多出项须列发现或引用明确要求它的原句。「没有证据支持删除」「各测试不完全相同」「没找到替代」均非保留理由。用文件和行说明如何缩减；功能保留时无需证明无人使用。上级功能还须检查子功能重复或整块多余，发现目标写子功能 id。`design` 写两步结论，`lines` 估本功能直属代码、文档、注释、测试行数，键同 `map-data.json` 的 `lines.own`。只删冗余且行为不变时，行数填当前值并列实体发现；估算低于当前时，以本功能 id 提「重构」发现；整功能不需则提「删除」，并入其他功能则提「合并」，`benefit` 写改前后行数。 |
| `rewrites` | 覆盖本功能直属文档（排除 `TODO.md` 和跳过名单）。按原文段落逐块改写，每块用 `lines` 标范围、`text` 写新文；相邻段落可合块，整块删除留空。只删重复、铺垫或换更短说法；保留要求、约束、例外、术语和数字，不添内容；反引号内代码、路径、命令、字段名及代码块原样保留，用户规则也改写。按序拼接后若更短，对整文件（`路径:1-末行`）提「删除」或「重构」，并写改前后字数。 |
| `keep` | 每个保留实体单列一条：`lines` 写 `路径:起始行-结束行`；`requirement` 逐字引服务条目；`achieved` 说明完整做到；`cost` 说明为何不能删、并或复用本仓/依赖现成内容，复杂度和代价是否配得上收益。 |

逐块对照显示增删；检查器配对原文反引号和波浪号围栏并核对改写全文，防止为减字删掉代码、路径或字段名。
检查器按 Markdown 语法识别表格行；同一张表不得跨两个改写块；每个非空块的表格行数、各行列数和首格文字须与原文一致，整块删除不查，代码块内的行不计。

若 `keep` 范围与仓内另一处在去掉缩进、空行及不含字母数字的行后连续 6 行相同，且 6 行合计至少 40 个字母或数字，`cost` 须写另一处位置并说明为何两份都留；可合并的提「合并」。检查器会列出另一处。

每条 `keep` 的 `achieved`、`cost` 都写该实体自己的话；不同条目的两项分别不得逐字相同。

`keep` 覆盖本功能直属代码、测试、文档（按 `xy-repo-map` 分类；TODO 由 `todo_items` 审）的每个非空行：每行落在一条 `keep`，或「删除」「合并」「孤儿」发现的目标范围中；「重构」「拆分」「改名」「挪位置」等发现不免除 `keep`。`keep` 互不重叠，「删除」目标不得与其重叠。`repo-map/skip.json` 命中的文件不做行覆盖、重复扫描或改写；命中 TODO 不进 `todo_items`。每条只含一个实体：Python 至多一个 `def` 或 `class` 的开头（类方法各算一条），Markdown 至多一个标题。`requirement.source` 写 `功能 id:条目 id`，可引用任意功能的条目；`requirement.text` 必须与条目文本完全相同。多个条目共用的代码可引其中一条，在 `achieved` 中写清它还服务哪些条目。理由为「起草」时 `keep` 须为 `[]`，等用户确认后再审。被调用、文档描述、测试覆盖或所在文件对得上某功能都不构成需要；测试跟着被测实体，重复做同一事的实体留一个删一个。

### 判删标准

实体是否保留，看删后功能图条目在实际使用中是否仍成立。未映射实体按实际价值判断：用 README「实际使用」记录说明执行或使用次数，或说明防住的故障是否发生过；也可写防护机制、发生频率、依据或假设及单次损失。用户旧原话和决定只说明其想要；调用、测试、文档或文件归属都不证明必要，「可能有用」「以防万一」不算证据。落不到任何功能条目的实体只能作为孤儿发现交由用户决定，不能作为删除或合并直接执行；复审在副本删除每个目标并跑测试，记录测试命令和结果；测试或实际使用受影响时建议补条目，否则建议删除。按文件范围提出的每条删除或合并都逐字引用目标所属、删后仍成立的功能条目；其他提议不引用条目。

主审和剃刀用 `todo_items` 逐条审直属 TODO。TODO 仅指文件名为 `TODO.md` 的跟踪文件，归属按 `xy-repo-map` 规则；没有直属 TODO 时写 `[]`。每条回答：

| 字段 | 问题 |
|---|---|
| `lines` | 条目位置（`路径:起始行-结束行`）；互不重叠并覆盖所有非空行，文件开头约定也单列。 |
| `needed` | 还要做吗？缺整块功能时建议改功能清单「还没实现」项；修 bug、重构、实验、部署核对仍留在 TODO。 |
| `smaller` | 能否精简？TODO 只写问题、未做的代价、完成条件；理由进代码注释，步骤进 README，结果进报告，TODO 只留链接。 |
| `why_holds` | 理由仍成立吗？ |

删改 TODO 均列为发现，目标写 `TODO 文件:行号`，决定一律「问你」。

清扫结束时，范围内不得有「等你拍板」；其他标记也须清零，除非用户在询问阶段明确决定保留，并在汇报中列明。

## 审查报告

主审、剃刀各交一份或多份报告；需复审时，每个功能另交仅含该功能对象的「剃刀复审」报告。结构如下：`plan` 列出的功能在两种首轮报告中各出现一次，检查器列出的待复审功能也各出现在复审报告中：

```json
{
  "role": "主审",
  "repo": "/绝对/仓库/路径",
  "features": [
    {
      "feature": "功能-id",
      "what_true": "回答",
      "why_holds": "回答",
      "structure": "回答",
      "todo_items": [
        {
          "lines": "TODO.md:1-6",
          "needed": "回答",
          "smaller": "回答",
          "why_holds": "回答"
        }
      ],
      "findings": [
        {
          "kind": "删除",
          "target": "tool/main.py:10-12",
          "requirement": {"source": "功能-id:条目-id", "text": "功能条目原文"},
          "problem": "问题",
          "proposal": "建议",
          "changes_behavior": false,
          "decision": "直接做",
          "basis": {
            "source": "xy-iron-law 第 3 条",
            "text": "如无必要，勿增实体（奥卡姆剃刀）"
          },
          "evidence": "证据",
          "benefit": "收益",
          "cost": "代价"
        }
      ]
    }
  ]
}
```

```json
{
  "role": "剃刀",
  "repo": "/绝对/仓库/路径",
  "features": [
    {
      "feature": "功能-id",
      "necessity": "回答",
      "smallest": {
        "design": "最小做法",
        "lines": {"code": 0, "docs": 0, "comments": 0, "tests": 0}
      },
      "rewrites": [
        {"lines": "tool/guide.md:1-3", "text": "意思不变的更短写法。"},
        {"lines": "tool/guide.md:4-4", "text": ""}
      ],
      "usage_candidates": [
        {"lines": "tool/main.py:10-12", "verdict": "用到", "evidence": "调用入口见 tool/cli.py:30-36。"}
      ],
      "keep": [
        {
          "lines": "tool/main.py:1-20",
          "requirement": {"source": "alpha:core", "text": "条目原文"},
          "achieved": "怎样做到这句",
          "cost": "为什么没有更小的做法；复杂度和代价配得上收益"
        }
      ],
      "todo_items": [],
      "findings": []
    }
  ]
}
```

审查子代理另交 `usage-coverage.json`，顶层仅有 `sources`、`files`、`unmeasured`：

```json
{
  "sources": [
    {
      "source": "README.md:10-12",
      "scenarios": [{"command": "副本内运行的命令", "ran": true, "why_not": ""}],
      "why_none": ""
    }
  ],
  "files": {"tool/main.py": {"missed": ["10-12", "20-20"]}},
  "unmeasured": [{"path": "tool/remote.py", "why": "入口涉及真实账户，安全边界无法确认。"}]
}
```

`sources` 必须逐条对应 README「实际使用」节：有表格时按每个数据行、有列表时按每个列表项，否则按每个段落；`source` 写对应的 `README.md:起始行-结束行`。每个场景含非空 `command`、布尔值 `ran` 和 `why_not`；成功运行时 `why_not` 为空，没跑成时写原因。`scenarios` 为空时 `why_none` 写不能推出副本场景的原因；不为空时 `why_none` 必须为空。`files` 只列量到的直属非测试代码文件；`missed` 是成功场景都没执行到的行段，相邻行合成一个范围，执行过的行会隔开范围，不按函数扩大，全执行到写 `[]`。没有任何成功场景时 `files` 必须是 `{}`：没有运行观察就不能声称任何代码行未覆盖，直属代码文件都放进 `unmeasured` 并说明原因。每份直属代码文件必须且只能出现在 `files` 或 `unmeasured` 一次。

剃刀报告每个功能对象必须有 `usage_candidates` 数组。每条候选恰有 `lines`、`verdict`、`evidence`：对 `files` 中每个 `missed` 范围恰好一条 `lines` 为 `路径:起始行-结束行` 的候选；`verdict` 只能是「用到」或「删」。判「用到」时 `evidence` 必须引用本范围以外的有效仓内位置；判「删」时必须有与候选范围重叠的发现，或本功能 id 的「删除」「合并」发现。不得添加不对应 missed 范围的候选。检查器逐项核对 README 来源、场景字段、覆盖文件完整性、未测文件原因和上述候选判定。

审查子代理另交 `test-candidates.json`，顶层键恰为 `runs`、`unmeasured_files`、`generated_mutants`、`mutants`、`functions`、`unmeasured_functions`、`candidates`。`runs` 可按不同 `root` 重复 runner；pytest 项恰有 `runner`、`root`、`command`、`junit`、`list`、`list_log`，nextest 项恰有 `runner`、`root`、`command`、`junit`、`list`。`root` 是仓内目录，其他产物路径相对 JSON 所在目录；`command` 写在副本里实际跑测试、产出这份 `junit` 的那条命令。pytest 的 `list` 是完整收集 JUnit，`list_log` 最后一个非空行去掉两边 `=` 和空白后须以 `no tests ran` 开头且不含 `deselected` 或 `error`；nextest 的 `list` 是完整 JSON 测试清单，所有二进制的 `status` 须为 `listed`，测试 `filter-match` 只能是 `matches` 或原因 `ignored`。JUnit 测试套件名是 binary id、用例名是测试路径；pytest 按 `classname`、`name`、`file`、`line` 精确核对完整清单与 JUnit，nextest 按测试路径核对。每个 `runs` 项的 JUnit 来自按测试项测量覆盖的运行。

```json
{
  "runs": [
    {"runner": "pytest", "root": ".", "command": "pytest -q", "junit": "artifacts/pytest-run.xml", "list": "artifacts/pytest-list.xml", "list_log": "artifacts/pytest-list.log"},
    {"runner": "nextest", "root": "rust", "command": "cargo nextest run", "junit": "artifacts/nextest-run.xml", "list": "artifacts/nextest-list.json"}
  ],
  "unmeasured_files": [{"path": "tests/test_remote.py", "why": "副本没有可用的测试运行器。"}],
  "generated_mutants": 12,
  "mutants": [{"id": "m-1", "location": "src/api.py:24", "change": "将比较符替换为等号。"}],
  "functions": {
    "tests/test_api.py:18::test_create": {"file": "tests/test_api.py", "line": 18, "covers": ["src/api.py:20-25"], "kills": ["m-1"]},
    "tests/test_api.py:22::test_create_alt": {"file": "tests/test_api.py", "line": 22, "covers": ["src/api.py:20-25"], "kills": ["m-1"]}
  },
  "unmeasured_functions": [{"id": "tests/test_api.py:31::test_remote", "file": "tests/test_api.py", "why": "副本里访问真实账户的用例未运行。"}],
  "candidates": [{"function": "tests/test_api.py:18::test_create", "covered_by": ["tests/test_api.py:22::test_create_alt"]}]
}
```

`functions` 以函数 id 为键，值恰有 `file`、`line`、`covers`、`kills`。pytest 测试项 id 按 `<文件>::<classname>::<name>` 推出，函数 id 按 `<文件>:<line>::<去掉参数后缀的 name>` 推出；JUnit 的 `file` 相对 `root`，`line` 从 0 起，写入 JSON 时加 1 对齐源文件行号。参数化实例及继承方法若来自同一文件同一声明行，合并为一个函数。nextest 的测试项和函数 id 都是 `<root>::<binary-id>::<test path>`，`file`、`line` 必须指向源代码里的测试函数声明，且上方紧邻测试属性。`covers` 是本函数实际覆盖的非测试代码行段，`kills` 是本函数单独运行时杀死的变异 id。每个变异项恰有 `id`、`location`、`change`；`generated_mutants` 记录生成数，`mutants` 只记录抽样并跑完所有测试的变异，超时或没跑完的不算。测试函数本身的行不能记作覆盖或变异位置。

`unmeasured_files` 项恰有 `path`、`why`；`unmeasured_functions` 项恰有 `id`、`file`、`why`。JUnit 缺项、测试失败、忽略或有非系统输出以外的子节点时，该函数必须记为未量测；无法识别完整函数范围的候选函数也须先转为未量测，再重算候选。每个范围内直属测试文件及含测试属性的 Rust 源文件，恰好要有函数记录或 `unmeasured_files` 记录。完整函数范围包括 Python 的装饰器和函数体；Rust 范围从连续的属性/文档注释开始，到函数末尾闭合大括号。

`candidates` 项恰有 `function`、`covered_by`。先按覆盖代码行数降序、函数 id 升序排列已量测函数；如果其余尚未选择的函数合起来覆盖该函数所有 `covers` 行并杀死所有 `kills` 变异，该函数就是候选。每选出一个候选就从后续见证集合移除，再继续计算。清单必须与检查器重算结果完全一致；`covered_by` 只列已量测且非候选的函数，并且这些函数合起来要覆盖候选的所有代码行和变异。候选不能互相作见证，因为逐个移除候选后，证据仍须由留下的测试提供。

剃刀须逐个回应每个候选：要么「删除」或「合并」发现完整覆盖候选测试函数，要么用其 `keep` 说明保留完整函数。保留时 `keep.lines` 覆盖完整函数范围，`cost` 写出所有 `covered_by` 测试函数的有效定义位置；逐项列出的见证应共同解释候选的覆盖与变异证据。检查器沿用 `cost` 的重复位置引用校验。部分删除/合并不能回应候选；「重构」等其他发现只说明实现调整，不能说明该测试函数是否保留，因此不算回应。

检查器不重跑测试，只核清单、JUnit、覆盖行和变异结果之间的一致性，并信任这些产物来自同一副本及同一 `root`。它从 pytest 的终端统计行和 nextest 的 `filter-match` 核验清单未被筛选；pytest 规范筛选（`-k`、`-m`、配置和调用 `pytest_deselected` 的钩子）会反映在统计行中。若钩子静默地从收集结果移除测试，检查器看不到这些项；整份未被收集的直属文件会由文件级检查发现。`root` 由产出者填写，检查器只核拼出的文件存在。检查器看不到子进程执行行是否归到正确的测试项。nextest 的函数文件和行号由产出者填写，检查器只核该行确有对应测试声明和属性；完整函数范围按 rustfmt 缩进识别，不符合格式的候选无法识别，必须记量不了。变异是抽样的，`generated_mutants` 和 `mutants` 数量由剃刀与用户查看；检查器不评估样本是否充分。剃刀按断言语义判断测试是否重复，不重新计算候选的覆盖包含关系。未量测项不得推断为没有覆盖或没有杀死变异。

```json
{
  "role": "剃刀复审",
  "repo": "/绝对/仓库/路径",
  "features": [
    {
      "feature": "功能-id",
      "verdicts": [
        {
          "lines": "tool/main.py:1-20",
          "attempt": "试着删去这段实现并运行相关测试。",
          "evidence": "tool/tests/test_main.py:42 的断言覆盖同一分支，删去后该断言失败。",
          "verdict": "站不住"
        }
      ],
      "findings": [
        {
          "kind": "删除",
          "target": "tool/main.py:1-3",
          "requirement": {"source": "功能-id:条目-id", "text": "功能条目原文"},
          "problem": "这段代码与首轮认定应留下的实体重复。",
          "proposal": "删除重复实现。",
          "changes_behavior": false,
          "decision": "直接做",
          "basis": {
            "source": "xy-iron-law 第 3 条",
            "text": "如无必要，勿增实体（奥卡姆剃刀）"
          },
          "evidence": "两个实现处理同一输入并产生相同结果。",
          "benefit": "减少重复代码。",
          "cost": "需要确认调用点后再修改。"
        }
      ]
    }
  ]
}
```

所有报告顶层仅有 `role`、`repo`、`features`，键须与对应示例完全一致。`role` 只能是「主审」「剃刀」「剃刀复审」；功能对象键按角色分别为：主审 `feature`、`what_true`、`why_holds`、`structure`、`todo_items`、`findings`；剃刀 `feature`、`necessity`、`smallest`、`rewrites`、`usage_candidates`、`keep`、`todo_items`、`findings`；复审 `feature`、`verdicts`、`findings`，且一份复审报告只含一个功能对象。复审 `verdicts` 项恰有 `lines`、`attempt`、`evidence`、`verdict`；`lines` 须与同功能首轮某条 `keep` 或孤儿目标完全一致，孤儿单行目标写成 `路径:N-N`；每条 `keep` 和孤儿目标恰配一条判定；`attempt`、`evidence` 非空。`evidence` 至少引用一个本实体范围外、位置有效的准备仓跟踪文件，格式为 `路径:行号` 或 `路径:起始行-结束行`；文件与行号由检查器核验。`verdict` 仅可为「站得住」或「站不住」；普通 `keep` 判站不住须有重叠发现，孤儿不用另写发现，但判站不住必须符合前述实际使用覆盖条件。主审 `what_true`、`why_holds`、`structure` 及每条 TODO 的三个回答不得空；`what_true` 对照「要什么」和条目，代码类条目没有测试也算未实现。剃刀 `necessity` 不得空；`smallest` 恰有 `design`、`lines` 两键且 `design` 非空；`lines` 恰为 `code`、`docs`、`comments`、`tests` 四个非负整数（布尔值不算），估算可高于当前值。四项合计低于 `map-data.json` 本功能 `lines.own` 时，须有目标为本功能 id、类型为「删除」「合并」或「重构」的发现；报错须列当前和估算行数。`keep` 须符合前文规则：覆盖、不重叠、单实体、`requirement.source` 指向清单中存在的条目且 `requirement.text` 与其完全相同；每条 `achieved`、`cost` 非空，跨条目去掉首尾空白后，各自不得逐字重复；理由为「起草」时须为 `[]`。若 `keep` 范围与仓内另一处去掉缩进、空行和无字母数字行后连续 6 行相同，且 6 行合计至少含 40 个字母或数字，`cost` 须引用另一份的有效位置。`todo_items` 须为直属 TODO 文件的有效行段，互不重叠并覆盖每个非空行。复审发现的规则同剃刀；每条都须反驳首轮 `keep`、`smallest` 或发现，决定按同一判删规则填写。其目标须在本功能逐行覆盖文件中或为本功能 id。发现 `target` 可写功能 id、`文件:行号` 或 `文件:起始行-结束行`；「删除」「合并」目标为本功能 id 时覆盖其全部文件，其他类型写功能 id 不覆盖文件。`kind` 仅可为「删除」「合并」「改名」「挪位置」「拆分」「重构」「修根因」「加约束」「改清单」「孤儿」「其他」。按文件范围提出的每条删除或合并都逐字引用目标所属、删后仍成立的功能条目；其他提议不引用条目。这类发现必须多出 `requirement` 对象，恰含 `source` 和 `text`：`source` 写 `功能 id:条目 id`，`text` 与条目原文完全相同；其余发现不得有 `requirement` 键。目标为功能 id 的「删除」「合并」一律「问你」；目标不在 TODO 文件、不是功能级删除或合并、行为不变且类型不是孤儿或改清单时直接执行；其余由用户决定。对外命令、选项、脚本或文件名、入口和用法有任何变化都视为行为改变。每条发现须有证据、收益、代价、依据。`basis.source` 按 `xy-review/rule_references.py` 格式引用规范条款，`basis.text` 须为条款逐字连续原文。

重做一轮时，显式用 `--carry-over` 列出上一轮未实施且未由用户决定的一个或多个报告；没有就写 `--carry-over 无`。不自动搜索运行目录，因为其中报告是否已实施或被用户否决无法判定。有旧发现的本范围功能自动列入必复审集合。每条旧发现都要在对应复审对象的 `carried` 数组中恰出现一次，不能多列：`old` 写 `<旧报告路径>#<功能 id>#<发现序号>`（从 1 起），`verdict` 写「仍成立」或「不成立」，`target` 在仍成立时写同一复审报告里对应发现的目标、不成立时写空字符串，`evidence` 写非空说明。仍成立的 `target` 必须与该复审报告的一条 `findings[].target` 相同。没有旧发现时不得写 `carried` 键。

剃刀 `rewrites` 每项恰有 `lines`（`路径:起始行-结束行`）和 `text` 两字段。直属文档每个非空行须被一块覆盖；范围有效、互不重叠且属于该文档，规则同 `keep.lines`。检查器在原文全篇配对同长度反引号串及长度至少 3 的波浪号串（可跨行），去重后要求每个片段连同分隔符在改写块按序拼成的全文中原样出现至少一次；若片段完全位于 `text` 为空的块则不查。缩进代码块没有围栏标记，不由检查器检查；由改写规则、逐段对照和终审把关。按块 `text` 非空白字符总数与原文比较；若更短，`findings` 须目标为 `路径:1-末行`，全空则类型「删除」，否则「重构」，并写改前、改后字数。「重构」发现的整文件范围仍须由 `keep` 覆盖；「删除」按前述规则覆盖其目标。

校验所有报告：

```sh
python3 <xy-cleanup目录>/check_reports.py \
  --repo <仓根绝对路径> \
  --scope <功能-id> <另一个功能-id> \
  --review 变过的 \
  --usage-coverage <usage-coverage.json> \
  --carry-over 无 \
  --test-candidates <test-candidates.json> \
  --reports <主审报告.json> <剃刀报告.json>
```

只交一个角色报告时，另一角色报「缺少功能」属预期；剃刀报告已交但复审未交时，「缺剃刀复审」及对应功能 id 也属预期。

全仓用 `--scope 全部`；仓根、范围、报告路径均须显式给出。退出码：`0` 通过，`1` 报告内容有误，`2` 参数有误。未通过先修报告再继续。

## 认可记录

`repo-map/approved.json` 保存各功能最近认可时的 64 位小写十六进制指纹；无此文件即无认可记录。指纹包括功能清单条目（`features.json` 中除 `children` 外的字段）、子功能 id 列表及直属文件；`load_features` 加入的 `parent` 不计。文件部分为五种归属组中的路径及内容哈希。功能清单、认可记录、跳过名单及命中路径不参与文件哈希；其他功能的变化不传到本功能。

规划审查时显式指定范围和审查方式：

```sh
python3 <xy-cleanup目录>/approvals.py plan \
  --repo <仓根绝对路径> \
  --scope <功能-id> <另一个功能-id> \
  --review 变过的
```

`--review 变过的` 只列无认可记录或指纹与记录不同的功能；`--review 全部` 列范围内所有功能。选择上级功能或「全部」会包含其所有子孙。

终审通过后、合入前，用同一范围记录认可指纹：

```sh
python3 <xy-cleanup目录>/approvals.py record \
  --repo <仓根绝对路径> \
  --features <功能-id> <另一个功能-id> \
  --kept-markers 无
```

`record` 只更新 `--features` 列出的功能指纹，不递归；参数名与递归的 `plan --scope` 不同，是为避免审了上级却误把未审子功能记为已认可。单独写 `--features 全部` 会更新清单里的全部功能。保留其余仍在清单中的记录并删除已不在清单的 id。`--kept-markers` 必须逐项等于本次功能图中所选功能的「还没实现」和「没归到功能的代码或测试」标记；没有标记时写 `无`。「等你拍板」仍由起草检查拒绝，不能列为保留。这里只能列用户在询问阶段决定保留的标记，汇报也照此列出；检查器直接使用功能图算出的标记，不另算一遍。范围内有 `why.source` 为 `起草` 或 `status` 为 `起草` 的条目时，拒绝并列出功能 id。两命令退出码相同：`0` 成功；`1` 清单、仓库或认可记录有问题，或记录被拒；`2` 参数错误。参数须显式提供；`--review` 无默认值。

## 网页决定

需用户决定的事项写入 JSON 决定清单；运行时显式提供清单路径、平台站点名及一个尚不存在的回答文件路径：

```sh
python3 <xy-cleanup目录>/decision_page.py ask \
  --decisions <决定清单.json> \
  --name <站点名> \
  --answers <尚不存在的回答文件.json>
```

将命令打印的链接发给用户。退出码：`0` 回答文件已写好；`1` 挂载或运行失败；`2` 参数或决定清单错误、回答文件已存在，或回答文件目录不存在、不可写或不支持硬链接。修正输入后重试。

决定清单对象键须如下，不得多字段或少字段，字符串不可空，条目编号全局唯一；每条至少有两个不重复选项且建议项属于其中之一；至少一组，每组至少一条。

每条 `items` 恰有 `id`、`title`、`lines`、`options`、`recommended`、`compare` 六字段。普通项 `compare` 为 `null`；文档改写项和改写过的起草条目为非空数组，每项恰有字符串字段 `lines`、`before`、`after`；`lines` 写原文范围（如 `guide.md:1-4`），`before` 非空，`after` 可空。一个 `compare` 的范围都须指向同一路径，格式有效、起始行递增且不重叠；页面在每块旁显示范围。数组顺序即显示顺序：相同相邻块折叠为「没改的 N 段」；`after` 为空时划线显示原文为删除，否则先划线原文，再显示新写法。页面顶部汇总所有块的非空白字符数，显示原文、改写及减少量；不自动猜段落对应关系。无对照的条目照旧显示。

```json
{
  "title": "清扫决定",
  "intro": ["页面顶部说明，一段一个字符串"],
  "groups": [
    {
      "title": "功能条目",
      "notes": ["这一组的说明"],
      "items": [
        {
          "id": "policy-item",
          "title": "确认规则",
          "lines": ["要什么：……", "为什么：……"],
          "options": ["确认", "按我写的改"],
          "recommended": "确认",
          "compare": null
        }
      ]
    }
  ],
  "footnotes": [
    {"title": "不用决定的事项", "lines": ["……"]}
  ]
}
```

回答文件含标题及每个条目的选择和自填内容：

```json
{
  "title": "清扫决定",
  "answers": {
    "policy-item": {"option": "确认", "note": ""}
  }
}
```

按条目编号读取回答。`note` 非空时以用户填写为准，`option` 仅作参考；意思不明时先询问。回答文件留在事项目录作决定原件，引用时写其路径。
