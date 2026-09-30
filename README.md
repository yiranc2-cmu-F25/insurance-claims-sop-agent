# Insurance Claims SOP Agent

A minimal LangGraph demo for a claims support workflow:

```text
VERIFY_ID -> RESOLVE_INTENT -> PROCESS_CASE -> POST_PROCESS
```

The app requires an OpenAI-compatible LLM with structured-output support. The LLM interprets identity fields, intent, scope, emotion and claim hints. Email consent comes only from UI buttons. Business permissions, identity matching and phase transitions stay in code. Without a working model, the UI shows `LLM: Unavailable` and the chat API returns 503; it does not guess the request or query claims.

## Project structure / 目录说明

```text
insurance_claims/
├── api.py                       # 启动入口，保留 uvicorn api:app
├── claim_agent/
│   ├── agent.py                 # 兼容原来的 LangGraph 入口
│   ├── paths.py                 # fixtures、前端等资源的位置
│   ├── api/                     # HTTP 接口层
│   │   ├── app.py               # 创建 FastAPI、挂载前端
│   │   ├── routes.py            # 聊天、邮件按钮、转人工接口
│   │   ├── schemas.py           # 校验客户端请求格式
│   │   ├── sessions.py          # Cookie 会话与并发锁
│   │   └── presenters.py        # 只返回允许前端看到的状态
│   ├── workflow/                # SOP 流程层
│   │   ├── graph.py             # 节点连接与阶段路由，建议从这里看
│   │   ├── state.py             # 会话状态与跨阶段记忆字段
│   │   ├── intake.py            # 每轮提取信息并更新记忆
│   │   ├── gates.py             # 安全、格式、范围检查节点
│   │   ├── verify_id.py         # VERIFY_ID
│   │   ├── resolve_intent.py    # RESOLVE_INTENT 与操作授权
│   │   ├── process_case.py      # PROCESS_CASE
│   │   ├── post_process.py      # POST_PROCESS
│   │   └── common.py            # 节点共用的小函数
│   ├── llm/                     # 模型相关
│   │   ├── client.py            # API 配置、调用和失败处理
│   │   ├── prompts.py           # 理解、规划、回答、审核的提示词
│   │   └── schemas.py           # 模型结构化输出格式
│   ├── guardrails/              # 输入清理、PII 标准化、安全与权限规则
│   ├── services/                # 受控业务操作
│   │   ├── case_harness.py      # 工具白名单、调用预算、回答审核
│   │   ├── email_followup.py    # 邮件选择、当前确认、防重复发送
│   │   ├── verification_session.py # 认证时效、失败冷却、前端倒计时
│   │   ├── identity_corrections.py # 身份信息纠错，确认后重新匹配
│   │   ├── request_queue.py     # 多问题待办与每轮处理上限
│   │   ├── checkpoints.py       # 存档前拦截完整 SSN、密钥/token
│   │   ├── sqlite_memory.py     # 加密数据库、保留期限、删除会话
│   │   ├── memory_policy.py     # 聊天长度限制、近期脱敏上下文
│   │   ├── case_memory.py       # 按案件隔离的历史笔记
│   │   └── handoff.py           # 转人工、暂停、恢复与交接摘要
│   └── tools/                   # 底层数据访问与工具
│       ├── fixtures.py          # 读取 demo 数据
│       ├── identity.py          # 客户查询、身份匹配
│       ├── claims.py            # 案件归属检查与字段级查询
│       ├── documents.py         # 文档要求与后续操作指引
│       └── email.py             # 模拟邮件发送
├── web/
│   ├── index.html               # 页面结构
│   └── static/
│       ├── styles.css           # 页面样式
│       └── app.js               # 聊天、邮件和转人工按钮交互
├── fixtures/                    # 示例数据，含 demo 代办身份与授权
├── tests/                       # 行为与安全回归测试
├── langgraph.json               # LangGraph 入口配置
├── Dockerfile
└── requirements.txt
```

建议阅读顺序：[流程入口](claim_agent/workflow/graph.py) →
[状态定义](claim_agent/workflow/state.py) → 对应阶段文件 →
[受控执行](claim_agent/services/case_harness.py) → [工具实现](claim_agent/tools/)。

`workflow` 决定下一步走哪里；`services` 约束并执行业务操作；`tools` 实际读取数据或模拟发送。
修改提示词看 `llm/prompts.py`，修改权限看 `guardrails/policy.py`，修改按钮看 `web/static/app.js`。
`api/schemas.py` 是前端请求格式，`llm/schemas.py` 是模型输出格式，两者不要混用。
工具统一从 `claim_agent.tools` 导入；内部实现按业务拆开，不需要在调用处了解数据文件结构。

本次目录重构保留所有 HTTP 路径、Cookie 名称、四阶段顺序和按钮行为。
`api.py` 和 `claim_agent/agent.py` 只转发到新位置，不会创建第二份应用或会话图。
原来导入内部模块（例如 `claim_agent.nodes`）的自定义脚本需要改用上述新位置。
已有容器需要重新构建、重启才会加载新目录和静态文件；现在请挂载下面的 Docker 数据卷以保留会话与加密密钥。

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn api:app --reload
```

Open http://127.0.0.1:8000.

## Enable an LLM

To enable conversations, configure a model token:

```bash
cp .env.example .env
```

Then edit `.env`:

```bash
MODEL_API_KEY=your-api-token
MODEL_NAME=your-model-name
MODEL_BASE_URL=
```

For an OpenAI-compatible provider, set `MODEL_BASE_URL` to that provider's API base URL. Rebuild and restart the container after changing `.env`:

```bash
docker build -t insurance-claims-agent .
docker rm -f insurance-claims-agent-demo
docker run -d --name insurance-claims-agent-demo -p 8000:8000 --env-file .env -v insurance-claims-data:/app/data insurance-claims-agent
```

## Run with Docker

```bash
docker build -t insurance-claims-agent .
docker run --rm -p 8000:8000 --env-file .env -v insurance-claims-data:/app/data insurance-claims-agent
```

## Demo input

```text
I'm the policyholder. My name is Margaret Chen, policy POL-9921.
I'm calling about my denied healthcare claim from January.
DOB is 1985-03-15, SSN last four is 4472.
```

The demo verifies three PII fields, remembers the claim hint, finds claim `CL-2048`, explains the grounded denial reason, and asks whether to send an email summary.

### Conversational security clarification

The safety model classifies threat intent, not whether identity has already
been verified. Ordinary self-identification and partial PII answers proceed to
`VERIFY_ID`. A caller's declared role is stored separately from verified identity;
it never grants claim access or substitutes for a delegate authorization check.
The classifier receives only bounded role/phase metadata and the last approved
clarification question, not saved PII, claim records, or raw conversation history.

Ambiguous protected-data requests produce an ownership or requested-access
question selected by the model. After two unresolved assessments the UI offers
human support instead of repeating the question indefinitely. Claim lookup and
email sending remain disabled until a new explicit low-risk assessment and all
normal verification/authorization gates pass. Model failures remain unavailable;
there is no keyword override or automatic low-risk fallback.

Manual check in the chat UI: send `I'm Margaret Chen, the policyholder. My policy
is POL-9921.` and expect a request for two more identity fields. Send a DOB next
and expect one more field. A clarification such as `It's my own policy` must not
authenticate the caller. For ambiguous access, two unresolved turns should offer
the **Transfer to human** button. These checks use the configured model; wording
may vary.

Identity and business understanding use separate structured model calls. The
identity call receives only the current message, a declared role, and names of
pending correction fields: no saved PII, transcripts, or case notes. Identity
values must be literal source spans in the current message before formatting;
unsourced values and redaction placeholders cannot update identity or trigger
customer-facing PII format errors. Invalid values actually volunteered by the
caller still receive format feedback. The history-aware business schema cannot
return identity fields, change roles, or confirm identity corrections. Existing
three-field matching, correction confirmation, expiration, and delegate grants
remain enforced. This separation adds one model call per conversational turn. In VERIFY_ID one more
best-effort call phrases the reply from code-supplied facts; if it fails or goes
off-script, a fixed template is used, so the gate never depends on it.

New claim hints are source-bound too: `CL-...` and `POL-...` have distinct types;
a year must occur in the current message outside its identity spans. Semantic
type/status/month hints require a literal supporting quote. A new question must
have a current-message source; stored requests use redacted user text rather
than an invented rewrite. Single-question turns retain their current context
(for example, that a lab cannot reissue a report). The LLM labels email-only
and identity-only replies separately, so they cannot recreate old questions.
Switching cases clears active filters and summaries while preserving separate
historical notes. Legacy unproven years and policy-as-claim IDs are discarded
on the next successfully understood turn; conversation history is not deleted.

For medium-risk ambiguity only, one bounded semantic review checks whether the
entire message merely supplies identity data. A source-bound confirmation lets
normal format/identity validation continue. High-risk decisions are never
overridden this way, and review/API failure remains unavailable, not low risk.

Follow up with `What is the appeal deadline?` or `What documents should I send?`.
In `POST_PROCESS`, click **Yes, send summary** or **No, skip**. Only these
buttons confirm the email choice; typing "yes", "no" or "send it" in chat
does not send or skip an email. A new claim question continues the conversation
and invalidates the previous offer; a successful answer creates a new offer.

The buttons call `POST /api/email-choice` with `{ "offer_id": "...", "choice":
"send" }` (or `"skip"`). The server checks the browser session, business phase,
verified identity and current offer. Recipient, claim and summary come from
server state, never the request body. An offer is bound to the validated summary
shown at that point; stale or cross-session offers are rejected. Repeated clicks
on the same choice return the saved result without another tool call.

Clicking does not invoke the LLM. An existing validated offer can still be
confirmed if a later intent/answer call fails **after a successful safety check**.
If the safety check itself fails, requests clarification, or blocks the request,
new sends and approval polling are disabled until a later message passes that
check; the previous low-risk result is not reused. Skip remains available.
Identity and authorization checks still apply. Buttons are hidden while the bot is paused for a simulated human transfer
and restored after returning. Skip never calls the email tool. The tool remains
a mock: no actual email or original document attachment is sent.

State and recorded email decisions are now saved in encrypted SQLite by default,
with per-session locking for one server process. A file lock rejects a second
worker using the same data directory. Saved decisions survive restart, but a
crash between a real external send and saving its receipt still needs provider-side
idempotency and a transactional outbox; the mock is not an exactly-once mail service.

## Fixture-backed workflow / 本次接入

四阶段不变，语言理解继续由 LLM 负责；代码负责数据归属、授权和允许操作。

- **代办人**：`caller_role=delegate` 与“请求人工客服”是两回事；先匹配来电者本人的至少三项 PII，再按客户姓名/保单号查授权，核对有效期、可查案件和可用操作，每次查询及邮件操作重新检查，亲属关系不等于授权。
- **选案**：支持直接提供 `case_id`，只在本人或获授权客户的可访问案件里匹配；多条让用户选，零条明确说明；换案件清掉当前线索和邮件摘要，同时按案件保留有限的历史笔记，不混用旧事实。
- **金额**：`payment_question → read_claim_amounts`，同时读取金额和 `claim_schema.json` 中的含义，示例数字不进入证据；`net_fee` 不被当成自付金额，预计支付不作为承诺。
- **材料**：读取具体替代方案、通用建议和需要人工复核的条件；用户说原件和替代材料都拿不到时提供转人工按钮；没有诊断报告专用说明时明确缺失，不编造，也不把车险通用材料当成案件必需材料。
- **邮件**：仍只由 Yes/No 按钮确认，收件人固定为客户登记邮箱，代办人不能改成自己的邮箱；授权必须包含 `send_email_summary`，否则不能发送。

`representatives.json` 新增的 David 身份信息及授权全部是**人工编写的演示数据**，不是原始数据已证明的授权，也不是生产验证规则；演示授权为 2026-01-01 至 2030-12-31，对 P9 的列明案件提供只读查询及摘要发送权限，不支持改理赔或上传材料。

新会话中测试代办（用无痕窗口可获得独立会话）：

```text
I'm David Chen, calling on behalf of my mother Margaret Chen, policy POL-9921.
My own DOB is 2004-06-20 and my SSN last four is 6028.
Why was her healthcare claim from January denied?
```

验证后可继续测试：

```text
For claim CL-2011, how much did the insurer actually pay, and what does net_fee mean?
Now switch to CL-2048. I cannot get the pathology report or any readable replacement from the lab. What can I do?
```

删除授权、将 `status` 改成 `revoked`、设置过期日期或移除操作/案件，就会阻止对应访问；测试完成后还原演示配置。

### Mock email approval

客户的 Yes/No **同意发送**，与 `consent_scenarios.json` 被此 demo 用作的**模拟发送审批**是两层独立状态；它不代表代办授权，也不接任何真实审批或邮件服务。

默认从 `pending` 开始；页面每隔约 1.5 秒调用 `POST /api/email-status`，下次读取进入 `approved`；服务器限制读取频率并保存进度，最多 5 次检查（含初次），或经过 30 秒后在下一次检查时报告 `timeout` 并提供人工帮助，不重复调用发送工具。

可在 `.env` 设置 `DEMO_EMAIL_SCENARIO=timeout` 后重启服务测试一直等待的场景；`default` 恢复默认，未知配置会失败而不是自动批准。
刷新页面可以恢复等待状态；关闭页面后不会有后台轮询，重新打开再检查时执行超时判断。
等待时可以继续提问，但同一会话只能有一个等待中的摘要，完成旧审批不会覆盖新问题或新邮件选择；暂停转人工时不推进审批。
所有结果始终标注 **No real email was sent**，也没有原始文件附件。

## PROCESS_CASE harness details

The model proposes `CaseReadOptions`: whether document guidance is needed and
which optional follow-up topic applies. Code maps these capabilities to a
`CasePlan`; the harness inserts the mandatory claim read first, document guidance
for document requests, and field definitions for amount questions. The model
cannot omit these SOP prerequisites or mismatch a topic with its tool. The
complete plan is validated before executing any tools. Each action has an explicit tool allowlist;
the verified party, selected claim and action are injected by the server.
The model cannot supply identity, arbitrary tool arguments, shell commands or
an email recipient. Every claim adapter checks ownership and returns only the
action's permitted fields.

If originals and reasonable substitutes are exhausted, the authorized document
workflow uses a fixed claim-and-guidance read plan before offering manual human
review. This recovery does not depend on another discretionary planning decision.

Each queued question is bounded to at most one model plan, **3 unique read tool calls**, one
answer-generation call and one independent model review. A chat turn processes
at most **3 questions / 9 read calls**; remaining questions wait for the next turn.
Model calls have a
12-second timeout and no automatic retries. There is no autonomous execution
loop. The current tools read local fixtures; when replacing them with HTTP/DB
adapters, configure timeouts in those adapters as well.

Answers require valid evidence references; claim IDs and numeric facts are
checked against evidence, then the model reviews factual support and relevance.
Markdown numbered-list ordinals are formatting, not business numbers; numeric
facts inside those items are still checked.
Rejected output, invalid plans, tool errors and unavailable models stop the
turn without presenting the rejected answer or offering email. Semantic model
review reduces hallucinations but is not a proof of factual correctness.

Audit events record request ID, event/outcome, allowed action/tool and execution
time; they omit PII, prompts, answers and raw exception text. The latest 200
events per session are kept in graph state and also emitted through Python
logging (`claim_agent.services.case_harness`, INFO). This is demo auditing, not a persistent
audit store. Graph checkpoints contain bounded conversation and identity data;
their payloads, metadata and pending writes are encrypted in SQLite by default.

## Semantic security / 安全判断

`guardrails/security.py` 不再使用关键词或正则表达式判断用户是否恶意，也没有失败后默认低风险的回退。
每条通过输入保护的消息会进行一次结构化 LLM 安全判断，使用统一的超时和错误处理；只额外传当前阶段、来电者角色、是否完成验证、是否正在澄清，不追加客户资料或历史理赔内容。

- 正常咨询、抱怨、拒绝提供个人信息、合法代办、保险网站密码找回，不应因为出现某个词被拦截；不相关话题留给范围检查处理。
- 只有涉及受保护数据访问、且对象/代办关系确实不清楚时才澄清，不自动转人工，也不把缺少身份信息当成恶意。
- 明确请求越权数据、内部秘密或绕过控制时拒绝该请求并提供人工按钮，用户仍可继续合法咨询。
- 模型缺失、超时、API 异常、缺字段、输出格式或风险分类矛盾时，`/api/chat` 返回 503、页面显示不可用，停止后续业务步骤；查询和邮件不能沿用旧的安全通过状态。
- 低风险只代表允许进入业务流程，不代表身份通过或获得权限；三项 PII、代办授权、案件归属、工具白名单和邮件按钮确认保持不变。

`input_guard.py` 中完整 SSN、实际密钥/token 的过度收集保护，以及身份字段格式校验仍保留；这不是按聊天关键词猜测恶意。
安全分类仍可能误判，下面的自动测试使用模拟模型结果验证路由与权限，不证明真实模型的理解准确率；接入你的模型后需用正常对话、否定/引用语句、合法代办和越权请求做人工验收。

## Tests

```bash
pytest -q
node --test tests/frontend_verification.test.cjs
```

Tests use simulated structured model responses; no API token is needed for
`pytest`. They verify enforcement and failure handling, not a real model's
language accuracy. Run the conversation examples against your configured model
before submitting the demo.

An opt-in live check covers the screenshot's multi-turn document follow-up,
invalid PII, correction/reverification, delegate access, cross-customer denial,
email buttons, off-topic handling, and simulated human transfer:

```bash
docker build -t insurance-claims-agent .
docker run --rm --env-file .env insurance-claims-agent python -u -m scripts.smoke_live
```

This command consumes API credits (at most 40 chat turns), uses disposable
in-memory sessions, and never opens the normal conversation database. It does
not retry failed scenarios to hide model variability. It prints pass/fail
results without raw identity values or API credentials. Email and human
handoff remain simulated. Passing this smoke check is not a production safety
guarantee or a replacement for broader model evaluations.

The email action is mocked; no real email is sent. The mock summary uses the
validated case answers from the conversation.

The demo does not require login. Instead, the server creates a random HTTP-only browser cookie and uses it as the LangGraph thread ID. The chat API ignores client-supplied session IDs. This prevents casual session-ID guessing; production should additionally bind the session to an authenticated user or tenant, use HTTPS, and add durable session revocation and cross-session rate limits.

## Identity lifetime and memory / 认证有效期与记忆

- 默认聊天闲置 **15 分钟**或认证满 **1 小时**后，需要重新提供至少三项匹配信息；正常聊天更新闲置计时，但不能延长一小时的上限。页面刷新、状态轮询、邮件或人工按钮不会延长认证。
- 剩余 **2 分钟**时页面顶部出现黄色倒计时，到期后保留“请重新验证”的提醒，禁用发送邮件。后端在聊天、页面读取、按钮和工具边界独立检查，不依赖浏览器计时。
- 到期清除当前身份、案件详情、案件笔记、用于模型的近期聊天和旧邮件邀请，但保留未完成的问题及选案线索；已显示过的浏览器聊天不会自动擦除，旧存档按下面的保留策略清理。重新认证后继续处理待办。
- 完整身份信息匹配失败累计 **5 次**，当前会话暂停验证 **15 分钟**并提供人工按钮。只填一两项、普通聊天、格式不合规和模型故障不计匹配失败；成功验证或冷却结束重置次数，换角色/纠错不会重置。此限制按会话生效，不能代替生产的跨会话反暴力尝试措施。
- 修改已有身份字段时先保存候选值并暂停受保护访问，询问是否采用。LLM 理解确认/取消，代码负责确认后的重新验证；不会修改 fixture 中的客户资料。
- 一次提出多个问题，LLM 拆为独立待办，最多每条消息 5 个、积压 10 个，每轮处理 3 个。每题分别查权限和证据，只移除成功回答的项；失败项保留。可说 `continue`、明确替换问题或取消待办。处理完才出现邮件按钮；跨案件回答不混入同一邮件，邀请只绑定最后一个案件的已验证摘要。
- HTTP 入口在进入 LangGraph 前拦截完整 SSN 和已识别的密钥/token；checkpointer 同时清理新写入的检查点与 pending writes，覆盖同步/异步图调用。不是任意秘密的完备 DLP，也不清除旧进程历史或外部 tracing/logging；部署前另行管理这些记录。业务需要的 PII 在运行时内存中使用，并随检查点加密保存。

有效期和冷却可通过 `.env.example` 的 `VERIFICATION_*` 配置，修改后重启服务。快速演示倒计时：本地启动时设置 `VERIFICATION_IDLE_SECONDS=130`，认证后不发消息，约 10 秒出现提醒；不要将此测试值用于正常运行。

测试均模拟模型返回值并使用可控时钟；前端倒计时测试只需 Node.js，不发送网络请求，也不消耗真实 API token。

## Bounded, persistent memory / 记忆管理

默认 `MEMORY_BACKEND=sqlite`，数据库位于 `data/conversations.sqlite3`；不用单独启动数据库服务。首次启动自动生成仅当前系统用户可读写的 `data/memory.key`，后续启动复用它；数据载荷、元数据、流程中间写入均使用 Fernet 加密，索引中的随机会话号、流程节点名等不是密文。密钥丢失或不匹配会报错，不会静默清空数据库或降级到明文。

本地演示把密钥放在数据目录是便利性取舍；**不能防御同时取得数据库和密钥的人**，正式部署请用 `MEMORY_ENCRYPTION_KEY` 注入独立管理的 Fernet 密钥，并配置加密备份、访问控制和密钥轮换方案。不要提交、打印或发送密钥；`data/` 已从 Git 和 Docker 构建上下文排除。数据库连接只支持一个应用进程，暂不支持多副本或 Windows 的文件锁实现。

- **聊天和存档**：每个当前状态保留最近 20 条聊天，单条会话保留最近 30 个节点存档（不是 30 轮）；聊天和 SQLite 存档默认保留不超过 24 小时，读页面不续期，24 小时没有新写入的会话会失效，未验证状态也适用。
- **自动清理**：启动、读取/写入数据库时检查过期，服务运行期间还每分钟清理一次；关闭服务时不运行清理，重新启动先清理再提供数据。旧版本中无期限的内存数据不会自动迁移。
- **近期上下文**：最多最近 6 条脱敏消息，每条最多 800 字符，帮助理解“第二个”“刚才那个”；已提取的个人字段、常见邮箱/电话号码等会屏蔽，但不是对任意文本的完备匿名化。当前用户消息仍需交给 LLM 提取身份，不会从旧消息恢复认证或邮件同意。
- **案件笔记**：最多 5 个案件，每案最近 5 条已通过工具核对的问答，标记时间、来源、来电者和客户；给模型最多每案 2 条，并重新核对当前权限。笔记只作历史背景，回答仍查询工具，邮件只包含当前案件本次有效讨论，不从历史笔记恢复旧邀请。
- **清除按钮**：页面的 `Clear saved conversation` 经确认后调用 `DELETE /api/conversation`，只删除当前浏览器会话的所有存档、中间写入和案件笔记，同时换会话编号、清空页面聊天；旧邮件按钮失效，不能撤回已经执行的外部操作。删除不可恢复，不负责删除另行保存的备份、日志或其他打开标签页已经显示的内容。
- **测试模式**：`MEMORY_BACKEND=memory` 保留原来的纯内存方式；自动测试强制使用独立内存或临时 SQLite，不打开你的真实会话数据库，不调用真实模型。

部署必须保留同一数据卷和密钥，才能在容器重建后恢复；认证时钟也会保存，重启不会延长身份有效期。刷新页面恢复当前会话状态与最后回复，不会自动重绘全部历史聊天。
