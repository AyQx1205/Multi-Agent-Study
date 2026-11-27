# multi-agent-lite

[![ci](https://github.com/AyQx1205/Multi-Agent-Study/actions/workflows/ci.yml/badge.svg)](https://github.com/AyQx1205/Multi-Agent-Study/actions/workflows/ci.yml)

一个可运行的多智能体自适应练习后端：5 个 Agent、60 个初中数学知识点、180 道题、FastAPI REST + WebSocket、SQLite/PostgreSQL 双存储。

本版在原有 Demo 上补齐了：

- 登录、退出、当前用户；
- 学校（organization）/班级（class）/选课（enrollment）数据模型；
- 学生、老师、管理员的 RBAC 与租户隔离；
- 提交幂等键 `Idempotency-Key`；
- CORS 白名单、登录限流/锁定、一次性 WebSocket ticket、审计日志；
- Alembic 数据库迁移；
- Docker / docker compose 一键启动；
- 对应的 API、存储层和测试。

## 0. 最快跑起来：Docker

装了 Docker 的前提下，不需要 Python、不需要本地数据库：

```bash
docker compose up --build
```

然后打开 <http://127.0.0.1:8000/demo>（演示控制台）或 <http://127.0.0.1:8000/docs>（Swagger）。

容器里做了这几件事：起 `postgres:16` → 等数据库就绪 → `alembic upgrade head` → 启动 uvicorn。
应用侧 `AUTO_APPLY_SCHEMA=false`，所以**表结构只来自迁移**，和线上口径一致；迁移失败容器直接退出，不会带着半张表提供服务。

默认 `MOCK_LLM=true`（离线、确定性、不花钱）。要接真模型，在 `.env` 里写 `MOCK_LLM=false` 和 `LLM_API_KEY=...` 再 `docker compose up`——compose 会自动把这两项插值进容器。

常用命令：

```bash
docker compose logs -f app        # 看日志（迁移与启动信息都在这）
docker compose exec db psql -U multi_agent -d multi_agent   # 进库
curl http://127.0.0.1:8000/ready  # 就绪探针（含数据库）
docker compose down -v            # 停掉并清空数据卷
```

> 端口：应用固定 8000；数据库映射到宿主机的 **55432**（避免和本机已有的 5432 抢端口），可用 `POSTGRES_HOST_PORT` 覆盖。

## 1. 本地开发启动

```powershell
cd D:\Multiagent\multi-agent-lite
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt -c constraints.txt
```

> `constraints.txt` 锁定的是「本地验证过的那一组版本」。不锁的话 CI 每次会装到当天最新的
> pytest / fastapi，很容易出现「本地全绿、CI 红」。升级依赖：改 `constraints.txt` →
> 本地跑 ruff/mypy/pytest → 推上去让 CI 验收。

开发环境可以用 SQLite：

```powershell
$env:DATABASE_URL = ""
$env:SQLITE_PATH = "knowledge.db"
uvicorn app.api.main:app --reload --port 8000
```

生产/联调建议 PostgreSQL：

```dotenv
DATABASE_URL=postgresql://multi_agent:multi_agent_dev@127.0.0.1:5432/multi_agent
DB_POOL_MIN=1
DB_POOL_MAX=10
SESSION_TTL_HOURS=24
CORS_ORIGINS=https://your-frontend.example.com
LOGIN_MAX_FAILURES=5
LOGIN_IP_MAX_FAILURES=30
LOGIN_WINDOW_SECONDS=900
LOGIN_LOCK_SECONDS=900
WS_TICKET_TTL_SECONDS=30
WS_ALLOW_QUERY_TOKEN=false
WS_MAX_MESSAGE_CHARS=4096
SUBMIT_RATE_PER_MINUTE=30
MESSAGE_RATE_PER_MINUTE=10
# 答错后第几次尝试才揭示答案
ANSWER_REVEAL_AFTER_ATTEMPTS=2
```

LLM 相关（`.env`）：

```dotenv
MOCK_LLM=false
LLM_API_KEY=sk-xxx
LLM_MODEL=deepseek-v4.1-flash
# 答题链路只在模板里回复；打开后会在事务提交后额外补一条 LLM 讲解
TUTOR_LLM_FOR_ANSWERS=false
```

> `MOCK_LLM=false` 但没配 `LLM_API_KEY` 时**启动直接失败**，不再静默退回 Mock：
> 静默降级在生产里等于“模型没接上，但没人知道”。`GET /health` 会报告当前生效的 `llm` 模式。

首次启动或升级后执行迁移：

```powershell
alembic upgrade head
alembic current
```

> 开发模式 `AUTO_APPLY_SCHEMA=true` 会自动建表，方便快速启动；生产环境建议设为 `false`，先执行 `alembic upgrade head`，只允许迁移改表。

启动时会幂等导入题库：**只写内容有变化的题**，日志里打印的是实际写入条数（`180 questions unchanged` 表示题库没变）。

## 2. 认证与租户

### 角色

| 角色 | 能做什么 |
|---|---|
| `admin` | 管理本学校的老师/学生、班级、选课；查看本学校所有学生 |
| `teacher` | 创建自己的班级、把自己班的学生加入班级、查看自己班学生 |
| `student` | 只能提交自己的答案、查看自己的进度/事件/答题记录 |

`learner_id` 现在**不再由客户端传**：HTTP 从 Bearer token 里取当前用户，WebSocket 用一次性 ticket 校验，学生不能改别人的 ID。

### 首次创建管理员

系统没有任何用户时，可以调用一次（检查与写入在同一个事务里，并发调用只会成功一个，其余返回 `409`）：

```powershell
$body = @{
  org_name = "示例学校"
  org_slug = "demo-school"
  admin_email = "admin@demo.local"
  password = "change-this-password"
  display_name = "管理员"
} | ConvertTo-Json

Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8000/auth/bootstrap `
  -ContentType "application/json" -Body $body
```

返回 `access_token`。后续请求：

```powershell
$headers = @{ Authorization = "Bearer <access_token>" }
```

### 登录 / 当前用户 / 退出

```http
POST /auth/login
GET  /auth/me
POST /auth/logout
```

登录请求：

```json
{
  "org_slug": "demo-school",
  "email": "student@demo.local",
  "password": "student-password"
}
```

### 登录保护与审计

- 登录失败按“学校 + 邮箱 + 来源 IP”计数：默认 15 分钟内失败 5 次锁定 15 分钟，返回 `429` 和 `Retry-After`；
- 同一来源 IP 还有 15 分钟内 30 次的跨账号总量阈值，防止撞库；
- **IP 阈值只作用在会失败的请求上**：账号密码正确的登录永远放行，避免学校/公司共用出口 IP 时被别人的失败连带锁死；
- 可通过 `LOGIN_MAX_FAILURES`、`LOGIN_IP_MAX_FAILURES`、`LOGIN_WINDOW_SECONDS`、`LOGIN_LOCK_SECONDS` 调整；
- `GET /auth/audit` 仅管理员可读，记录登录成功/失败/锁定、bootstrap、开学校、建用户/班级、选课、WebSocket ticket 和退出；**被锁事件（`login.blocked`）带 `org_id`**，管理员能看到本组织有人被锁；
- CORS 只允许 `CORS_ORIGINS` 白名单，默认只放行本地开发端口；启动时发现 `*` 会直接报错拒绝启动。

### 交互限流

`/submit`、`/question`、`/message` 都能触发 LLM 调用，因此按用户限流：

| 变量 | 默认 | 作用 |
|---|---|---|
| `SUBMIT_RATE_PER_MINUTE` | 30 | 每用户每分钟提交次数 |
| `MESSAGE_RATE_PER_MINUTE` | 10 | 每用户每分钟提问/消息次数（WS 与 HTTP 共用） |
| `WS_MAX_MESSAGE_CHARS` | 4096 | WebSocket 单帧最大字符数 |

超限返回 `429` + `Retry-After`；WebSocket 上返回 `{"error":"rate_limited"}`。
限流器是**进程内**实现，多实例时各算各的（与内存态 LearnerModel 是同一类限制）。

### 管理员创建老师和学生

```http
POST /auth/users          # admin
GET  /auth/users          # admin
```

```json
{
  "email": "teacher@demo.local",
  "password": "teacher-password",
  "display_name": "王老师",
  "role": "teacher"
}
```

### 班级和选课

```http
POST /auth/classes                         # admin / teacher
GET  /auth/classes                         # 当前用户可见班级
POST /auth/classes/{class_id}/enroll       # admin / 该班老师
GET  /auth/classes/{class_id}/students
```

老师创建班级时只能把自己设为班主任；管理员可以在本学校内指定老师。

### 多学校开通

`/auth/bootstrap` 只用于第一个管理员。之后由平台运维调用：

```http
POST /auth/organizations
X-Platform-Token: <PLATFORM_ADMIN_TOKEN>
```

需要设置环境变量 `PLATFORM_ADMIN_TOKEN`，未设置时该接口返回 403。新学校的 admin 再登录、创建自己的老师和学生。

### 隔离规则

- 学生访问 `/progress/{other_id}`、`/attempts/{other_id}`、`/events/{other_id}` → 403；
- 老师只能访问自己班级里已选课的学生；
- 管理员只能访问同一 `org_id` 的学生；
- 跨学校访问一律 403；
- 老师/管理员不能调用学生答题接口 `/submit`、`/question`、`/message`。

## 3. 提交答案与幂等

`POST /submit` 必须携带 `Idempotency-Key` 请求头：

```powershell
$headers = @{
  Authorization = "Bearer <student_token>"
  "Idempotency-Key" = "homework-2026-10-29-q17"
}

$body = @{
  question_id = "q-rational_numbers-2"
  answer = "7"
  time_spent_seconds = 42
} | ConvertTo-Json

Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8000/submit `
  -Headers $headers -ContentType "application/json" -Body $body
```

语义：

- 同一个学生 + 同一个 key + 同样的题目/答案：返回第一次的 `attempt_id`，`replayed=true`，`events_triggered=0`，不会重复更新掌握度；
- 同一个 key 用于不同题目/答案：`409`；
- 唯一索引 `(learner_id, idempotency_key)` 在数据库层兜底，应用层锁 + 数据库约束共同防重；
- **答案延迟揭示**：第一次答错不返回 `correct_answer`/`explanation`（`answer_revealed=false`），只有在答对或达到 `ANSWER_REVEAL_AFTER_ATTEMPTS`（默认 2）次尝试后才揭示。目的是避免「乱答一次换答案 → 换 key 重交 → 刷掌握度」；
- **只有首次作答计入掌握度**：响应里的 `counts_toward_mastery` 表示这次作答是否作为 BKT 的证据。同一道题的重复作答会被记为练习（`attempts`、正确率仍然更新），但不再拉高掌握度；
- **只有一条提交入口**：客户端不能传 `is_correct`，也没有「直接声称答对」的旁路；`scripts/demo.py` 和测试都走这条真实链路；
- **原子提交**：`attempts`、`knowledge_states`、`review_items` 和本次事件在同一事务内写入。任一 Agent 处理或数据库写入失败，整笔提交回滚，不会出现“有答题记录但没有掌握度”的半更新；
- **多实例一致性**：PostgreSQL 用 `pg_advisory_xact_lock(learner_id)` 串行化同一学生的提交，不同学生可并行；SQLite 开发模式用 `BEGIN IMMEDIATE` + 适配器锁串行化，适合单实例开发/测试；
- 事件随事务落库到 `events` 表，可直接作为审计/回放日志；目前没有独立 Outbox Worker，外部消息投递不在本轮范围；
- 客户端重试时应复用原 key，不要每次重试生成新 key。

> 时间口径：所有时间戳一律是 aware UTC（`app/core/timeutil.py`）。早期不带偏移量的历史数据按 UTC 解释，SQLite 与 PostgreSQL 返回同一格式的 `isoformat()`。

WebSocket 的提交也要求 `idempotency_key`：

```json
{
  "action": "submit",
  "idempotency_key": "ws-2026-10-29-001",
  "question_id": "q-rational_numbers-2",
  "answer": "7"
}
```

## 4. WebSocket

WebSocket 不再接受长期 Bearer token。学生先登录，再换一个 **30 秒有效、只能消费一次** 的 ticket：

```http
POST /auth/ws-ticket
Authorization: Bearer <student_token>
```

```json
{"ticket": "...", "expires_at": "2026-10-29T20:00:00+00:00"}
```

然后用 ticket 连接自己的 `learner_id`：

```text
ws://127.0.0.1:8000/ws/{learner_id}?ticket={ticket}
```

ticket 用过即失效；默认不再支持 `?token=`。只有本地兼容旧客户端时才设置 `WS_ALLOW_QUERY_TOKEN=true`。

连接建立后：

- **每条消息都会复查会话**：登出、账号被禁用、会话过期之后，已建立的连接不能再提交任何东西，服务端返回 `{"error":"session_expired"}` 并关闭；
- `POST /auth/logout` 会主动关闭该用户所有存活的 WebSocket（审计里记录 `websockets_closed`）；
- 单帧上限 `WS_MAX_MESSAGE_CHARS`（默认 4096），字段上限：`question_id`/`knowledge_id` 80 字符、`answer`/`question`/`message` 500 字符；
- 提交/提问/消息都按用户限流，超限返回 `{"error":"rate_limited"}`。

> 已知缺口：会话**自然过期**时，静默挂着的连接不会主动收到关闭帧，要等到它下一次发消息才会被拒。

脚本会自动登录并申请 ticket：

```powershell
python scripts\ws_client.py `
  --org demo-school `
  --email student@demo.local `
  --password student-password
```

或使用环境变量 `WS_ORG`、`WS_EMAIL`、`WS_PASSWORD`，或直接传 `--token` + `--learner-id`。

其他 action：

```json
{"action":"question","knowledge_id":"rational_numbers","question":"为什么负数的绝对值是正数？"}
{"action":"message","message":"我不会这道题"}
{"action":"ping"}
```

## 5. 主要 API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 公开健康检查（进程/内容，不查库） |
| GET | `/live` | 存活探针（不查库） |
| GET | `/ready` | 就绪探针（数据库与核心表可用返回 200，否则 503） |
| GET | `/metrics` | Prometheus 文本指标 |
| GET | `/demo` | 演示用单页控制台（静态 HTML，数据需登录访问） |
| POST | `/auth/bootstrap` | 首次创建学校+管理员 |
| POST | `/auth/login` | 登录 |
| POST | `/auth/logout` | 撤销当前会话 |
| GET | `/auth/me` | 当前用户 |
| POST | `/auth/ws-ticket` | 学生申请一次性 WebSocket ticket |
| GET | `/auth/audit` | 管理员查看本组织审计日志 |
| POST | `/auth/organizations` | 平台开通新学校 |
| POST/GET | `/auth/users` | 管理员管理用户 |
| POST/GET | `/auth/classes` | 班级 |
| POST | `/auth/classes/{id}/enroll` | 选课 |
| GET | `/auth/classes/{id}/students` | 班级学生 |
| GET | `/questions` | 题目列表，隐藏答案/解析 |
| GET | `/questions/{id}` | 单题，隐藏答案/解析 |
| POST | `/submit` | 服务端判分 + 幂等 |
| POST | `/question` | 学生提问 |
| POST | `/message` | 学生消息 |
| GET | `/progress/{learner_id}` | 学习进度 |
| GET | `/events/{learner_id}` | 事件记录 |
| GET | `/attempts/{learner_id}` | 答题记录 |
| GET | `/knowledge-graph` | 知识图谱 |
| WS | `/ws/{learner_id}?ticket=...` | 实时答题/事件（一次性 ticket） |

交互文档：<http://127.0.0.1:8000/docs>。在 Swagger 里先登录拿 token，再点右上角 **Authorize** 填 `Bearer <token>`；`Idempotency-Key` 在 `/submit` 的请求头里填。

### 演示控制台

起服务后打开 <http://127.0.0.1:8000/demo>：一个**单文件**演示控制台（无构建、无外部 CDN，断网可用）。

- 登录后自动申请一次性 ticket 建 WebSocket；
- 左侧选知识点/题目、作答；右侧实时滚动 `student.submission → assessment.mastery_updated → curriculum.review_scheduled → tutor.teaching_response` 的完整 Agent 事件链；
- 「同 key 重放」按钮演示幂等：同一个 `Idempotency-Key` 再交一次，返回 `replayed=true` 且不重复计分；
- WS 不可用时自动回退到 HTTP 提交（事件按顺序逐条渲染）。

默认填充 `org=demo-school`、`email=student@demo.local`。先按 §2 建好这个学生：

```powershell
# 1) 首次 bootstrap（只允许一次）
#    org_slug = demo-school, admin_email = admin@demo.local
# 2) 用管理员 token 建学生
$body = @{ email = "student@demo.local"; password = "demo-student-123";
           display_name = "演示学生"; role = "student" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/auth/users `
  -Headers @{ Authorization = "Bearer <admin_token>" } `
  -ContentType "application/json" -Body $body
```

> 页面本身是静态文件，不含任何数据；所有接口仍然要登录 + 角色校验。

## 6. Alembic 迁移

目录：

```text
alembic.ini
migrations/env.py
migrations/versions/0001_baseline.py
migrations/versions/0002_auth_tenancy_idempotency.py
migrations/versions/0003_security_hardening.py
migrations/versions/0004_learners_created_at_default.py
```

常用命令：

```powershell
alembic upgrade head
alembic current
alembic history --verbose
alembic downgrade -1
```

环境变量优先级：`DATABASE_URL` > `SQLITE_PATH`。SQLite 示例：

```powershell
$env:DATABASE_URL = ""
$env:SQLITE_PATH = "knowledge.db"
alembic upgrade head
```

已有旧库也支持升级：`0001` 使用 `CREATE TABLE IF NOT EXISTS` 兼容存量表，`0002` 用 inspector 判断后再补幂等列，`0003` 新增 `ws_tickets`、`login_throttle`、`audit_logs`，`0004` 补上 `learners.created_at` 的默认值。当前 head 是 `0004_learners_created_at_default`。

> `0004` 修的是一个只在生产口径下才会暴露的缺陷：运行时建表（`AUTO_APPLY_SCHEMA=true`）给
> `learners.created_at` 带了 `DEFAULT now()`，而 `0001` 基线漏了它；写入语句又依赖这个默认值，
> 于是「只跑迁移」的库（容器/生产）第一次创建学习者就会违反 `NOT NULL`。修复分两层：写入
> 显式带上 `created_at`，迁移补默认值——两条路径的 schema 现在逐列一致（可用
> `information_schema.columns` 对比验证）。

## 7. 运维与可观测性

### 指标

`GET /metrics` 暴露 Prometheus 文本格式（内网抓取，不要暴露到公网）：

| 指标 | 含义 |
|---|---|
| `events_published_total{type}` | 各类型事件发布数 |
| `events_dropped_total{reason,type}` | 因深度上限被丢弃的事件（正常应恒为 0） |
| `submissions_total{result}` | 提交次数（`issued` / `replayed`） |
| `rate_limited_total{bucket,channel}` | 被限流的请求 |
| `llm_calls_total{result,model}` | LLM 调用成功 / 失败 |
| `cache_evictions_total{cache}` | 内存缓存淘汰次数 |
| `ws_connections` / `ws_learners` | 在线连接数 / 在线学习者数 |
| `purged_rows_total{kind}` | 维护任务清理的行数 |

### 后台维护

每 `MAINTENANCE_INTERVAL_SECONDS`（默认 300 秒）跑一轮：

- 给所有 WebSocket 连接发心跳，发失败的连接就地回收；
- 关闭超过 `WS_IDLE_TIMEOUT_SECONDS` 没有消息的连接；
- 清理过期/已撤销的 `auth_sessions`、已用/过期的 `ws_tickets`、失效的 `login_throttle`。

学习记录默认**不删**。要启用留存策略，设置 `EVENTS_RETENTION_DAYS` / `ATTEMPTS_RETENTION_DAYS`（天，`0` 表示永久保留）。

### 内存

学习者模型、学习者锁、各 Agent 的按人状态都是 **LRU 有界** 的（`LEARNER_CACHE_SIZE` / `AGENT_STATE_CACHE_SIZE`，默认 2000 / 5000），冷数据淘汰后从数据库重新加载。锁缓存只淘汰**未被持有**的锁，不会破坏互斥。

## 8. 开发与测试

```powershell
pip install -r requirements-dev.txt -c constraints.txt   # ruff / mypy / pytest

ruff check .                          # 静态检查（配置在 pyproject.toml）
mypy                                  # 类型检查（范围见 pyproject 的 files）
pytest -q                             # 单元 + 集成（PG 需设 TEST_DATABASE_URL）

# PostgreSQL 集成测试
$env:TEST_DATABASE_URL = "postgresql://multi_agent:multi_agent_dev@127.0.0.1:5432/multi_agent"
pytest tests/test_store_postgres.py -q
```

CI（`.github/workflows/ci.yml`）在每次 push / PR / 手动触发上跑三个 job：

| job | 做什么 |
|---|---|
| `lint & typecheck` | `ruff check` + `mypy`，依赖走 `constraints.txt` 锁定版本 |
| `pytest (sqlite + postgres)` | 全量 pytest，带 `postgres:16` service（PG 集成测试不会被跳过）；日志里打印 `pip list` 便于核对版本；JUnit 报告作为 artifact 保留 7 天 |
| `docker build & smoke` | `docker compose up --wait` 真起一套环境，探 `/health`、`/ready`、`/demo`，并校验 `alembic_version` 到达 head 后销毁 |

任一步失败即阻塞合并。失败时先去 Artifacts 下载 `pytest-report/junit.xml`——它会直接点名是哪个用例、哪一行。

覆盖点：

- Docker：镜像构建、容器内「只跑迁移」建表、`/ready` 通过、迁移到达 head；

- 登录成功/失败、token 撤销、`/auth/me`；
- 登录失败限流/锁定、成功登录清零、`/auth/audit` 仅管理员可读且不含密码；
- CORS 白名单放行/拒绝；
- 学生跨学生、老师跨班级、管理员跨学校隔离；
- 平台开通第二所学校；
- 幂等重放、同 key 冲突、WebSocket 一次性 ticket、ticket 重放拒绝、旧 `?token=` 默认拒绝；
- 提交事务回滚：Agent 异常、attempt 已插入但 mastery 写入失败时，attempt/mastery/review/event 全部不落库；
- PostgreSQL 同一学生并发提交的 `advisory lock` 串行化（两个任务最终 attempts=2，不丢更新）；
- `/live`、`/ready` 探针；
- Alembic 空库升级 + 旧 `attempts` 表升级；
- SQLite/PostgreSQL 存储层 roundtrip；
- 并发提交不死锁、事件深度按链隔离、答案延迟揭示、重复作答不计掌握度；
- 时间口径统一（同一事件在内存与库中时间戳一致）、bootstrap 原子性、登录限流不误锁；
- 登出切断 WebSocket、交互限流、幂等指纹归一化、LLM 不进锁/不进事务；
- 内存 LRU 淘汰、过期数据清理、WS 连接上限与心跳、`/metrics` 指标。

## 9. 还没做的生产项

- 密码强度策略、密码重置/找回；
- HttpOnly Cookie / OIDC 接入；
- PII 脱敏、未成年人监护同意、审计日志长期归档；
- 异步 Outbox Dispatcher / 死信队列（当前事件已随提交事务落库，但没有外部消息投递与重试）；
- 正式 CD 流水线（目前是容器入口执行迁移 + CI 冒烟验证，还没有 staging/prod 发布与回滚演练）；
- 完整教师/家长报表和内容审核流程。

安全提示：当前 `PLATFORM_ADMIN_TOKEN` 是运维级静态令牌，生产环境应放在 Secrets Manager，并配合 IP 白名单/审计；不要把真实学生密码写进脚本或仓库。