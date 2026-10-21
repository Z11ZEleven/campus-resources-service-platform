# M3 报修工单后端接入说明

实现依据：`docs/iteration-1/第一次功能迭代-M3报修工单表设计.md` 及公共层接入约定，基线提交 `dbb7883`。本轮提供文字工单、派单、维修、完工、验收、权限与最小历史。

## 1. 数据库迁移

在 `backend` 目录，使用已配置 `.env` 的 Python 3.12 环境执行：

```powershell
python -m alembic upgrade head
python -m alembic current
```

预期版本：`0002_m3_work_orders (head)`。

新增一张 `m3_work_orders` 表；复用原有八张 D 表。主表字段与设计一致，包含五个外键、`UNIQUE(reporter_id, request_key)` 和三个查询索引。时间为 UTC `DATETIME(3)`，ID 为 `BIGINT UNSIGNED`。本迁移不重建公共层表或种子用户。

回滚到 `0001_public_layer` 会删除 M3 主表及其中工单数据；应先完成所需备份和业务处置。历史操作表仍属于公共层，迁移回滚不会自动删除其中日志。

## 2. 权限和可见范围

| 操作 | 条件 |
|---|---|
| 上报 | 当前有效用户与身份，并有 USER 的 `m3:create` 权限；教室须通过公共可见性检查 |
| 派单 | PROPERTY_ADMIN；目标人员、身份有效且具有 REPAIRER 权限 |
| 开始、提交完工 | 当前有 REPAIRER 权限，且确为本单 assignee_id |
| 验收通过、驳回 | PROPERTY_ADMIN；不得验收本人负责维修的工单 |
| 查询 | 本人上报的工单；REPAIRER 被分配的工单；PROPERTY_ADMIN 可看全部测试工单 |

多角色查询取上述范围的并集。SYS_ADMIN 不隐式获得 M3 管理权限。不可查看的详情返回 404；用户、身份、角色每次请求均由公共层重新加载。

## 3. HTTP 接口

请求带 `Authorization: Bearer <登录令牌>`；登录复用 `POST /api/v1/auth/login`。JSON 中建议用字符串传 ID，响应中所有 ID 都是字符串，时间以 `Z` 表示 UTC。

前缀：`/api/v1/m3/work-orders`。

| 方法 | 相对路径 | 成功响应 |
|---|---|---|
| POST | 空 | 201，工单 |
| GET | 空 | 200，分页列表 |
| GET | `/{id}` | 200，工单及 history |
| POST | `/{id}/assign` | 200，工单 |
| POST | `/{id}/start` | 200，工单 |
| POST | `/{id}/submit-completion` | 200，工单 |
| POST | `/{id}/acceptance` | 200，工单 |

### 3.1 创建

```json
{
  "room_id": "1",
  "title": "空调无法启动",
  "description": "按下开关后没有反应，请检查。",
  "category": "AIR_CONDITIONING",
  "request_key": "349f53ab-29d8-45aa-b907-cd90d5872483"
}
```

- 标题 1～100 字；描述 1～2000 字；首尾空白会清除，清除后不能为空。
- 分类：`AIR_CONDITIONING`、`LIGHTING`、`POWER_SOCKET`、`FURNITURE`、`OTHER`。
- 每次新建由客户端生成一个 UUID，网络重试复用原 UUID。
- `reporter_id`、状态和时间由服务端设置，不能作为创建字段提交。
- 同一上报人使用同一请求键和相同内容，返回原工单，仍为 201；内容变化返回 409。重放已完成工单不会重新打开。

### 3.2 分页查询与详情

查询参数：`page` 默认 1；`page_size` 默认 20、最大 100；可选 `status`、`category`、`room_id`。筛选始终受当前用户的可见范围约束，按创建时间及 ID 倒序排列。

列表结构：

```json
{
  "items": [],
  "total": 0,
  "page": 1,
  "page_size": 20
}
```

工单响应包含设计主表所有字段，包括分配、维修、验收人员 ID 和时间、最近完工说明、最近验收意见、当前 `completion_round` 和原 `request_key`。

详情额外包含 `history` 数组，字段为 `id/module/business_id/action/actor_id/from_status/to_status/note/created_at`。先授权工单，后读取 `d_operation_logs`。每次完工、验收均追加历史，主表的最新说明不会覆盖过去的日志。

M3 状态事件为 `CREATE_WORK_ORDER`、`ASSIGN_WORK_ORDER`、`START_REPAIR`、`SUBMIT_COMPLETION`、`ACCEPT_WORK_ORDER`、`REJECT_WORK_ORDER`；公共资源层还记录获得/释放来源。完工及验收日志 `note` 是 JSON 文本，包含批次与当次说明。

### 3.3 派单

```json
{"assignee_id": "6"}
```

只有 `REPORTED` 可以派单；本轮不转派，重复派单返回 409。物业管理员可使用公共 `GET /api/v1/auth/users?role_code=REPAIRER` 查询有效维修人员。

### 3.4 开始维修

`POST /{id}/start` 无需请求体。

指定维修人员操作 `ASSIGNED → REPAIRING`，添加无结束时间的整室 M3 维修限制。已经 `REPAIRING` 时重复开始返回当前结果，不新增来源、历史或资源版本；待验收或已完成时开始返回 409。

### 3.5 提交完工

```json
{"completion_note": "已更换损坏开关并试运行正常。"}
```

完工说明 1～2000 字。指定维修人员操作 `REPAIRING → WAIT_ACCEPTANCE`，每次递增 `completion_round`，记录最近说明、提交时间和当轮历史。此时继续保留维修限制；待验收状态的重复完工返回 409。

### 3.6 验收

通过：

```json
{
  "passed": true,
  "comment": "复测通过",
  "expected_completion_round": 1
}
```

驳回：

```json
{
  "passed": false,
  "comment": "仍有异响，请复查。",
  "expected_completion_round": 1
}
```

- `passed` 必须是 JSON 布尔值；意见最多 500 字，驳回时必须填写非空意见。
- `expected_completion_round` 必须是当前工单的正整数批次；前端从最近详情取值，不能把旧页面的批次用于新一轮验收。
- 通过：`WAIT_ACCEPTANCE → COMPLETED`，只释放本单来源。
- 驳回：`WAIT_ACCEPTANCE → REPAIRING`，保留原来源，维修后再次提交将形成下一批次。
- 先检查批次，再处理完成状态的幂等重放。已完成且批次匹配时重复通过返回原结果，不覆盖原验收人/意见或重复释放；旧批次或完成后的驳回返回 409。

## 4. 事务、并发和资源联动

所有写操作使用公共 `Db` 开启的单请求事务，只 flush，按房间锁 → 工单锁的顺序执行。首次开始、验收通过中的业务状态、来源、资源版本和操作日志共同提交或回滚。

- 通过公共 `start_maintenance`、`release_source` 修改来源，M3 不直接更新公共来源或汇总表。
- 多张工单可以限制同一教室；一张验收通过只撤销自己的来源。
- 完工未验收仍阻止新 M1 预约和 M2 批准。
- 首轮不取消已有 M1/M2 使用记录。已有来源继续保留，新增维修来源阻止后续创建；这不等同于已经完成预约冲突通知或补偿。
- 当前验证直接调用真实 D 公共函数检查 M1/M2 冲突；M1/M2 业务 HTTP 接口和跨模块网络调用尚未实现。

## 5. 错误约定

| HTTP | 说明 |
|---|---|
| 401 | 未登录、令牌失效、用户或身份无效 |
| 403 | 缺少操作权限、不是指定维修人、自验收 |
| 404 | 工单或教室不存在/不可访问 |
| 409 | 状态冲突、旧验收批次、请求键内容不一致、唯一约束竞争 |
| 422 | 字段校验失败、无效维修人员 |

业务错误沿用 `detail.code` 和 `detail.message`；字段校验沿用 FastAPI 的 422 数组格式。未填写的可空输出字段返回 `null`。

## 6. 测试

在 `backend` 目录执行：

```powershell
python -m pytest tests/test_m3_schemas.py -q
python -m pytest --require-mysql -q
python -m ruff check app scripts tests alembic
```

真实 MySQL 测试必须显式设置专用 `TEST_DATABASE_URL`，库名须以 `_test` 结尾；测试会清理测试数据并验证迁移回放，不能指向开发业务库。M3 测试覆盖状态、权限、幂等、多来源、旧批次、并发和故障回滚；迁移测试验证回滚 M3 后 D 表与已有用户数据仍然存在。

### 本次本地验证（2026-10-21）

- Python 3.12.15、MySQL 8.4.13；独立测试库 `campus_resources_m3_test`。
- 后端完整测试 **104 passed**，含 M3 API 30 个案例、M3 输入输出 schema 50 个案例，以及公共层和迁移测试。
- Ruff 与 `git diff --check` 通过。
- 临时移除“开始维修必须是指定人员”的校验后，对应测试因错误地返回 200 而期望 403 失败；随后已恢复源码，并重新通过完整测试。
- 本地开发库升级至 `0002_m3_work_orders`；升级前后公共层八张表的数据行数一致。
- 本机 HTTP 服务通过数据库就绪、七个 M3 OpenAPI 操作、匿名访问拒绝，以及学生/物业管理员/维修人员的登录和列表查询。
- 在本地开发库单事务内跑通驳回后再次完工和通过验收，验证后回滚，工单、来源、历史和教室资源版本均未保留验证改动。
- 依赖自带一条 Starlette TestClient 对 httpx 的弃用提示，未导致测试失败；本次使用仓库锁定依赖，未升级依赖。

这些是本地验证结果，不作为远端 CI 或人工验收结果。

## 7. 后续范围

超时升级、评价、通知、附件、细粒度管理范围、独立事件表、撤销、转派、完成后重开及跨进程可靠补偿仍按设计留待后续。当前实现不能视作课程完整 M3 需求已经全部完成。

## 8. 已记录的原设计限制

2026-10-21，本次实现的用户明确选择继续保持原设计的完工请求格式，仅提交 `completion_note`，暂不增加完工批次字段；这不代表新增团队共识。

已知场景：第一次完工提交进入待验收后被驳回，工单回到 `REPAIRING`。如果第一次完工请求的迟到副本此时到达，服务端无法仅从相同的 `completion_note` 判断它是旧请求还是一次新的完工提交，会再次进入待验收并递增轮次。当前验收接口的轮次校验能阻止旧验收请求处理新批次，但不能解决这个完工请求的辨识问题。

后续改进候选：为提交完工增加客户端携带的预期轮次（首次 0）或独立请求幂等键，经过接口确认后实施。当前前端接入时应避免自动重试提交完工 POST；超时后先刷新详情，再由操作人判断是否重提。
