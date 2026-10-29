# multi-agent-lite

一个可运行的多智能体自适应练习后端：5 个 Agent、60 个初中数学知识点、180 道题、FastAPI REST + WebSocket、SQLite/PostgreSQL 双存储。

本版在原有 Demo 上补齐了：

- 登录、退出、当前用户；
- 学校（organization）/班级（class）/选课（enrollment）数据模型；
- 学生、老师、管理员的 RBAC 与租户隔离；
- 提交幂等键 `Idempotency-Key`；
- Alembic 数据库迁移；
- 对应的 API、存储层和测试。

## 1. 启动

```powershell
cd D:\Multiagent\multi-agent-lite
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

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
```

首次启动或升级后执行迁移：

```powershell
alembic upgrade head
alembic current
```

> 开发模式 `AUTO_APPLY_SCHEMA=true` 会自动建表，方便快速启动；生产环境建议设为 `false`，先执行 `alembic upgrade head`，只允许迁移改表。

## 2. 认证与租户

### 角色

| 角色 | 能做什么 |
|---|---|
| `admin` | 管理本学校的老师/学生、班级、选课；查看本学校所有学生 |
| `teacher` | 创建自己的班级、把自己班的学生加入班级、查看自己班学生 |
| `student` | 只能提交自己的答案、查看自己的进度/事件/答题记录 |

`learner_id` 现在**不再由客户端传**：HTTP 从 Bearer token 里取当前用户，WebSocket 也从 token 里校验，学生不能改别人的 ID。

### 首次创建管理员

系统没有任何用户时，可以调用一次：

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
- **原子提交**：`attempts`、`knowledge_states`、`review_items` 和本次事件在同一事务内写入。任一 Agent 处理或数据库写入失败，整笔提交回滚，不会出现“有答题记录但没有掌握度”的半更新；
- **多实例一致性**：PostgreSQL 用 `pg_advisory_xact_lock(learner_id)` 串行化同一学生的提交，不同学生可并行；SQLite 开发模式用 `BEGIN IMMEDIATE` + 适配器锁串行化，适合单实例开发/测试；
- 事件随事务落库到 `events` 表，可直接作为审计/回放日志；目前没有独立 Outbox Worker，外部消息投递不在本轮范围；
- 客户端重试时应复用原 key，不要每次重试生成新 key。

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

WebSocket 需要登录 token，且只允许学生连接自己的 `learner_id`：

```text
ws://127.0.0.1:8000/ws/{learner_id}?token={access_token}
```

脚本：

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
| POST | `/auth/bootstrap` | 首次创建学校+管理员 |
| POST | `/auth/login` | 登录 |
| POST | `/auth/logout` | 撤销当前会话 |
| GET | `/auth/me` | 当前用户 |
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
| WS | `/ws/{learner_id}?token=...` | 实时答题/事件 |

交互文档：<http://127.0.0.1:8000/docs>。在 Swagger 里先登录拿 token，再点右上角 **Authorize** 填 `Bearer <token>`；`Idempotency-Key` 在 `/submit` 的请求头里填。

## 6. Alembic 迁移

目录：

```text
alembic.ini
migrations/env.py
migrations/versions/0001_baseline.py
migrations/versions/0002_auth_tenancy_idempotency.py
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

已有旧库也支持升级：`0001` 使用 `CREATE TABLE IF NOT EXISTS` 兼容存量表，`0002` 用 inspector 判断后再补幂等列。

## 7. 测试

```powershell
# 全量（PostgreSQL 测试默认跳过）
pytest -q

# PostgreSQL 集成测试
$env:TEST_DATABASE_URL = "postgresql://multi_agent:multi_agent_dev@127.0.0.1:5432/multi_agent"
pytest tests/test_store_postgres.py -q
```

覆盖点：

- 登录成功/失败、token 撤销、`/auth/me`；
- 学生跨学生、老师跨班级、管理员跨学校隔离；
- 平台开通第二所学校；
- 幂等重放、同 key 冲突、WebSocket 鉴权与重放；
- 提交事务回滚：Agent 异常、attempt 已插入但 mastery 写入失败时，attempt/mastery/review/event 全部不落库；
- PostgreSQL 同一学生并发提交的 `advisory lock` 串行化（两个任务最终 attempts=2，不丢更新）；
- `/live`、`/ready` 探针；
- Alembic 空库升级 + 旧 `attempts` 表升级；
- SQLite/PostgreSQL 存储层 roundtrip。

## 8. 还没做的生产项

- 登录限流、失败锁定、密码强度和重置；
- HttpOnly Cookie / OIDC 接入；
- 审计日志、PII 脱敏、未成年人监护同意；
- 异步 Outbox Dispatcher / 死信队列（当前事件已随提交事务落库，但没有外部消息投递与重试）；
- Alembic 纳入 CI/CD，并关闭启动时自动 DDL；
- 完整教师/家长报表和内容审核流程。

安全提示：当前 `PLATFORM_ADMIN_TOKEN` 是运维级静态令牌，生产环境应放在 Secrets Manager，并配合 IP 白名单/审计；不要把真实学生密码写进脚本或仓库。