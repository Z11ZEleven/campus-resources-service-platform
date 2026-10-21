# M2 状态机、数据与接口设计草案

状态：提案，等待 M2 需求基线和项目技术栈评审后实施。

本文基于 [M2 需求基线](m2-requirements.md) 给出技术无关的设计。表名、接口路径和状态码是候选方案，不代表团队已完成架构决策。

## 1. 设计目标

- 服务端实施身份、角色、数据范围和禁止自审规则；
- 状态转换可追溯、可幂等，并能抵抗并发审批、撤回和超时任务；
- 不在数据库事务中等待远程资源服务；
- 只有资源占用得到确定成功结果后才将申请标记为已批准；
- 通过公开网络接口完成 M2 到资源维护方的真实集成；
- 定时任务重启可恢复、重复运行安全。

## 2. 申请状态

候选状态枚举：

| 状态 | 含义 | 是否终态 |
| --- | --- | --- |
| `PENDING_FIRST_APPROVAL` | 等待院系或校级管理员一级审批 | 否 |
| `PENDING_ROOM_APPROVAL` | 等待教室管理员二级审批 | 否 |
| `RESOURCE_CONFIRMING` | 二级已同意，正在确认整室资源占用 | 否 |
| `APPROVED` | 资源占用已经确定成功 | 是 |
| `REJECTED` | 人工驳回或确定的资源冲突导致驳回 | 是 |
| `WITHDRAWN` | 申请人在待审批阶段撤回 | 是 |
| `AUTO_CANCELLED` | 审批步骤超时或使用时间已到 | 是 |

`RESOURCE_CONFIRMING` 是必要的内部中间状态：远程调用超时并不能证明资源服务没有处理请求，因此不能直接把技术超时当作资源冲突，也不能直接标记批准。

## 3. 状态转换约束

| 原状态 | 操作 | 目标状态 | 关键条件 |
| --- | --- | --- | --- |
| `PENDING_FIRST_APPROVAL` | 一级通过 | `PENDING_ROOM_APPROVAL` | 正确角色和范围、非本人、未过期 |
| `PENDING_FIRST_APPROVAL` | 一级驳回 | `REJECTED` | 驳回理由非空 |
| `PENDING_ROOM_APPROVAL` | 二级通过 | `RESOURCE_CONFIRMING` | 对应教室管理员、非本人、未过期 |
| `PENDING_ROOM_APPROVAL` | 二级驳回 | `REJECTED` | 驳回理由非空 |
| 任一待审批状态 | 本人撤回 | `WITHDRAWN` | 操作者为申请人 |
| 任一待审批状态 | 超时扫描 | `AUTO_CANCELLED` | 步骤截止或使用开始时间已经到达 |
| `RESOURCE_CONFIRMING` | 资源确认成功 | `APPROVED` | 返回来源化占用 ID，响应与请求一致 |
| `RESOURCE_CONFIRMING` | 确定资源冲突 | `REJECTED` | 记录结构化冲突原因 |

所有转换使用“申请 ID + 预期原状态 + version”条件更新。受影响行数为零表示状态已被其他操作改变，调用方重新读取并返回当前结果，不覆盖新状态。

## 4. 数据模型

### 4.1 `m2.applications`

| 字段 | 候选类型 | 说明 |
| --- | --- | --- |
| `id` | UUID | 主键 |
| `applicant_user_id` | UUID | 申请人，来自认证上下文 |
| `applicant_identity_id` | UUID | 本次使用的有效身份 |
| `room_id` | UUID | 目标教室 |
| `purpose` | TEXT | 申请理由，非空 |
| `start_at` | TIMESTAMPTZ | 使用开始时间 |
| `end_at` | TIMESTAMPTZ | 使用结束时间 |
| `status` | VARCHAR(32) | 申请状态 |
| `current_level` | SMALLINT | 待审批时为 1 或 2，终态可空 |
| `step_deadline_at` | TIMESTAMPTZ | 当前步骤自然日截止时刻 |
| `resource_sync_status` | VARCHAR(20) | `NONE/PENDING/SUCCEEDED/FAILED` |
| `resource_hold_id` | UUID，可空 | 资源维护方返回的限制 ID |
| `final_reason` | TEXT，可空 | 驳回或自动撤销原因快照 |
| `submitted_at` | TIMESTAMPTZ | 提交时间 |
| `finalized_at` | TIMESTAMPTZ，可空 | 进入终态时间 |
| `version` | INTEGER | 乐观锁版本 |
| `created_at` | TIMESTAMPTZ | 创建时间 |
| `updated_at` | TIMESTAMPTZ | 更新时间 |

最低约束：`start_at < end_at`、使用时长 1 至 4 小时、`version > 0`。整点、同一自然日、提前两个自然日和 08:00 至 22:00 涉及时区，应由服务层统一校验并用测试覆盖边界；如数据库约束能清晰表达，可增加第二层保护。

### 4.2 `m2.approval_steps`

| 字段 | 候选类型 | 说明 |
| --- | --- | --- |
| `id` | UUID | 主键 |
| `application_id` | UUID | 所属申请 |
| `level` | SMALLINT | 1 或 2 |
| `required_role` | VARCHAR(64) | 该步骤所需角色快照 |
| `scope_type` | VARCHAR(32) | 院系、校级或教室范围 |
| `scope_id` | UUID，可空 | 具体范围对象 |
| `status` | VARCHAR(20) | `PENDING/APPROVED/REJECTED/CANCELLED/EXPIRED` |
| `deadline_at` | TIMESTAMPTZ | 该步骤截止时刻 |
| `decided_by` | UUID，可空 | 实际审批人 |
| `decision_reason` | TEXT，可空 | 审批意见或驳回理由 |
| `decided_at` | TIMESTAMPTZ，可空 | 决策时间 |
| `version` | INTEGER | 乐观锁版本 |
| `created_at` | TIMESTAMPTZ | 创建时间 |
| `updated_at` | TIMESTAMPTZ | 更新时间 |

约束：`UNIQUE(application_id, level)`。第二级步骤在一级通过时创建，避免未进入该步骤的申请提前出现在二级待办中。

### 4.3 `m2.application_events`

追加式业务历史表，候选字段包括：`application_id`、`event_type`、`actor_type`、`actor_id`、`from_status`、`to_status`、`reason`、`request_id`、`created_at`。

该表服务于申请详情和业务追踪；公共审计表服务于安全与治理，两者不能互相替代。

### 4.4 可靠外部任务

创建通知、调用资源服务和补偿任务时，业务状态变更与 outbox 事件应在同一本地事务中提交。Worker 通过租约、重试次数和幂等键领取任务，服务重启后继续处理。

## 5. 权限判定

### 5.1 提交申请

1. 从认证上下文取得当前用户，不采用请求体中的申请人 ID。
2. 验证账号及所选身份有效，且身份属于当前用户。
3. 验证教室有效、开放模式为仅整教室预约。
4. 执行时间规则校验。

### 5.2 一级审批

1. 根据教室归属选择院系管理员或校级管理员角色。
2. 检查用户当前有效角色授权及数据范围覆盖目标教室。
3. 检查审批人不是申请人。
4. 检查申请仍处于一级待审批且未过期。

### 5.3 二级审批

1. 检查教室管理员角色的数据范围覆盖目标教室。
2. 检查审批人不是申请人。
3. 检查申请仍处于二级待审批且未过期。
4. 通过操作进入资源确认流程，不能绕过资源接口直接批准。

## 6. M2 公开接口草案

接口前缀暂记为 `/api/v1/m2`，最终由项目 API 规范统一。

| 方法与路径 | 用途 | 关键权限或约束 |
| --- | --- | --- |
| `POST /applications` | 提交申请 | 有效登录用户；申请人取自认证上下文 |
| `GET /applications/me` | 查询本人申请 | 仅返回本人数据 |
| `GET /applications/{id}` | 查询详情和审批进度 | 本人或管理范围内审批人 |
| `POST /applications/{id}/withdraw` | 撤回申请 | 本人且处于待审批状态 |
| `GET /approval-tasks` | 查询当前用户待办 | 服务端按角色和数据范围过滤 |
| `POST /approval-tasks/{stepId}/approve` | 通过当前步骤 | 正确层级、角色、范围、非本人、未过期 |
| `POST /approval-tasks/{stepId}/reject` | 驳回当前步骤 | 同上，且理由非空 |
| `GET /applications/{id}/events` | 查询业务历史 | 与详情相同的数据权限 |

写接口建议要求 `Idempotency-Key`，并可携带客户端最后读取的 `version`。重复请求返回第一次已确定的结果；同一幂等键对应不同请求内容时返回冲突错误。

列表接口统一支持分页，不能把未授权数据取回后再由前端过滤。

## 7. 资源服务接口契约草案

M2 需要资源维护方提供类似以下网络接口：

```http
POST /api/v1/resource-holds
Idempotency-Key: m2-application:{application_id}:revision:{revision}
```

请求语义包含：

- `source_type = M2`；
- `business_id = application_id`；
- `scope_type = ROOM`；
- `room_id`；
- `start_at`、`end_at`；
- `revision`。

结果至少区分：

- 创建成功或相同请求已经成功；
- 确定的业务冲突；
- 请求参数或资源状态不合法；
- 暂时性技术失败；
- 结果未知，例如服务端可能成功但客户端超时。

确定的业务冲突使申请自动驳回。暂时性失败或结果未知时，M2 使用相同幂等键重试或按来源查询最终结果；只有得到确定失败结果后才自动驳回并写明原因，避免资源已经创建但申请被错误驳回。

## 8. 二级通过时序

```text
教室管理员
  → M2：通过二级审批
  → M2 数据库：条件更新为 RESOURCE_CONFIRMING，写入事件和 outbox
  → Worker：使用稳定幂等键调用资源服务
  → 资源服务：原子检查重叠并创建 M2 整室限制
  → M2 数据库：确认成功后更新为 APPROVED
  → M2：使同教室重叠时段的其余待审批申请失效并自动驳回
  → 通知服务：通知申请人及相关审批人
```

若另一个重叠申请已抢先成功，资源服务返回确定冲突，当前申请进入 `REJECTED` 并保存“教室对应时段已被其他申请占用”。

## 9. 超时与临期任务

定时 Worker 查询：

- `status` 为任一待审批状态；并且
- `step_deadline_at <= now` 或 `start_at <= now`。

对每条记录执行带原状态和版本条件的更新，成功后：

- 标记为 `AUTO_CANCELLED`；
- 取消尚未完成的审批步骤；
- 追加业务事件和公共审计；
- 产生通知 outbox 事件。

临期提醒使用独立事件键，例如“申请 ID + 当前审批级别 + 提醒类型”，防止定时扫描产生重复通知。默认提前量尚待评审，不能硬编码在业务逻辑中。

## 10. 错误语义草案

| HTTP 状态 | 场景示例 |
| --- | --- |
| `400` | 时间格式错误、理由为空 |
| `401` | 未登录或认证失效 |
| `403` | 无审批角色、范围不匹配、自审、查看未授权申请 |
| `404` | 申请或审批步骤不存在；必要时也用于避免泄露对象存在性 |
| `409` | 状态已变化、版本冲突、幂等键内容冲突、资源时段冲突 |
| `422` | 违反整点、提前日期、时长、开放时间等业务规则 |
| `503` | 依赖服务暂不可用且本次不能给出确定业务结果 |

错误响应应包含稳定错误码、用户可读消息和 `request_id`，不能依赖中文消息文本判断业务分支。

## 11. 验证计划

### 单元和服务测试

- 时间规则：整点、自然日、月末、年末、08:00/22:00 边界、1/4 小时边界；
- 状态机：每条允许和禁止的转换；
- 权限：身份与角色分离、多角色、数据范围、自审禁止；
- 驳回理由、撤回权限和终态不可变。

### 数据库和并发测试

- 两个审批人并发处理同一步骤，最多一个成功；
- 审批、撤回和超时并发，最多一个终态生效；
- 同教室重叠资源请求最多一个成功；
- 重复幂等请求不产生重复申请、审批事件或资源限制。

### 集成测试

- M2 通过真实 HTTP 调用资源服务；
- 审批成功后可从资源查询接口看到来源为 M2 的整室限制；
- 冲突、超时、断连、重试和结果未知场景；
- 通知与审计事件随状态变化产生且不重复。

关键测试完成后，应临时破坏状态条件更新或资源唯一约束，确认测试确实失败，再恢复实现。

## 12. 实施顺序

1. 全组评审需求基线、技术栈和 M2/D 接口归属。
2. 确认状态枚举、表结构和资源接口 OpenAPI 契约。
3. 创建版本化迁移与幂等角色/权限种子数据。
4. 实现申请提交、本人列表和详情。
5. 实现一级审批、二级审批和服务端授权。
6. 实现撤回、超时扫描、临期提醒和通知审计。
7. 实现资源确认 Worker 及 M2 到资源服务的真实网络调用。
8. 完成前端申请、进度、待办和审批页面。
9. 补齐并发、故障注入、集成和端到端测试。
10. 接入构建、迁移、测试环境部署及发布回退流程。
