# 数据库统一契约

> **2026-10-22 更新**：T01–T05 新增 0004–0006 迁移；当前 17 张 D 表、5 张 M1 表、3 张 M3 表。下文 §2 为原 0001–0003 字段基线，新结构及兼容变化详见 [M1 基础契约](m1/contracts.md)。

> 本文件是 M1、M2、M3 和 D 共同使用的字段基线。实现以当前 Alembic 迁移和 SQLAlchemy 模型为准；修改公共字段必须先更新本文件并由各模块评审。

## 1. 全局约定

| 项目 | 统一约定 |
|---|---|
| 数据库 | MySQL 8.4、InnoDB、utf8mb4；一个 `campus_resources` 数据库 |
| 表名 | 公共层 `d_`，座位预约 `m1_`，整室申请 `m2_`，维修 `m3_` |
| 主键/外键 | `BIGINT UNSIGNED`；JSON 接口统一序列化为十进制字符串 |
| 时间 | 数据库 `DATETIME(3)` 保存 UTC；页面和业务日期按 `Asia/Shanghai` 解释 |
| 通用字段 | 每张表必须有 `id`、`created_at`；可修改表增加 `updated_at` |
| 删除 | 不级联删除用户、教室、座位和业务单；停用或释放保留历史 |
| 事务 | 资源写操作使用同一 SQLAlchemy Session；先锁 `d_rooms`，再锁业务单；模块不得自行 `commit` |
| 来源 | 所有预约、整室占用、维修限制都写入 `d_resource_blocks`，不得直接写状态汇总 |

## 2. 公共层 D 表（当前实现）

0001–0003 原迁移创建 9 张 D 表。`0001_public_layer` 创建前 8 张；`0003_m3_extensions` 增加 `d_notifications`。

### 2.1 用户、身份与角色

#### `d_users`

| 字段 | 类型 | 可空 | 约束/用途 |
|---|---|---:|---|
| `id` | BIGINT UNSIGNED | 否 | 主键 |
| `username` | VARCHAR(64) | 否 | 唯一，本地登录名 |
| `password_hash` | VARCHAR(255) | 否 | Argon2 哈希，不存明文 |
| `display_name` | VARCHAR(100) | 否 | 展示名称 |
| `is_active` | TINYINT(1) | 否 | 停用后不能登录或执行业务 |
| `created_at` | DATETIME(3) | 否 | UTC |
| `updated_at` | DATETIME(3) | 否 | UTC |

#### `d_user_identities`

| 字段 | 类型 | 可空 | 约束/用途 |
|---|---|---:|---|
| `id` | BIGINT UNSIGNED | 否 | 主键 |
| `user_id` | BIGINT UNSIGNED | 否 | FK → `d_users.id`；0004 起支持一人多身份 |
| `identity_type` | VARCHAR(20) | 否 | `STUDENT` / `TEACHER` / `STAFF` |
| `identity_no` | VARCHAR(64) | 否 | 学号或工号；与类型组合唯一 |
| `department_code` | VARCHAR(32) | 否 | 与 `d_rooms.department_code` 使用同一编码 |
| `is_active` | TINYINT(1) | 否 | 当前有效身份 |
| `created_at` | DATETIME(3) | 否 | UTC |
| `updated_at` | DATETIME(3) | 否 | UTC |

#### `d_user_roles`

| 字段 | 类型 | 可空 | 约束/用途 |
|---|---|---:|---|
| `id` | BIGINT UNSIGNED | 否 | 主键 |
| `user_id` | BIGINT UNSIGNED | 否 | FK → `d_users.id` |
| `role_code` | VARCHAR(32) | 否 | `USER` / `ROOM_ADMIN` / `PROPERTY_ADMIN` / `REPAIRER` / `SYS_ADMIN` |
| `created_at` | DATETIME(3) | 否 | UTC |

0004 后唯一约束：`d_user_identities(identity_type, identity_no)`、`d_user_identities(id, user_id)`、`d_user_roles(user_id, role_code, scope_type, scope_value)`。新增 `scope_type/scope_value`；旧授权保留 GLOBAL/*，不将作用域权限混用。

### 2.2 教室、座位与统一资源来源

#### `d_rooms`

| 字段 | 类型 | 可空 | 约束/用途 |
|---|---|---:|---|
| `id` | BIGINT UNSIGNED | 否 | 主键；模块间只传此 ID |
| `code` | VARCHAR(32) | 否 | 全局唯一，如 `B1-101` |
| `name` | VARCHAR(100) | 否 | 教室名称 |
| `building_name` | VARCHAR(100) | 否 | 建筑显示名称 |
| `capacity` | INT UNSIGNED | 否 | 大于 0 |
| `department_code` | VARCHAR(32) | 是 | 空表示全校；非空表示院系教室 |
| `open_time` | TIME | 否 | 北京时间每日开放起点 |
| `close_time` | TIME | 否 | 北京时间每日关闭时刻 |
| `is_active` | TINYINT(1) | 否 | 行政启停 |
| `resource_version` | BIGINT UNSIGNED | 否 | 资源变化时递增 |
| `created_at` | DATETIME(3) | 否 | UTC |
| `updated_at` | DATETIME(3) | 否 | UTC |

#### `d_seats`

| 字段 | 类型 | 可空 | 约束/用途 |
|---|---|---:|---|
| `id` | BIGINT UNSIGNED | 否 | 主键 |
| `room_id` | BIGINT UNSIGNED | 否 | FK → `d_rooms.id` |
| `seat_no` | VARCHAR(32) | 否 | 教室内唯一编号 |
| `has_fixed_socket` | TINYINT(1) | 否 | 固定插座标记 |
| `has_mobile_socket` | TINYINT(1) | 否 | 移动/导轨插座标记 |
| `is_active` | TINYINT(1) | 否 | 行政启停 |
| `created_at` | DATETIME(3) | 否 | UTC |
| `updated_at` | DATETIME(3) | 否 | UTC |

唯一约束：`d_rooms.code`、`d_seats(room_id, seat_no)`。座位的 `room_id` 不得在模块业务表中重复维护为另一份主数据。

#### `d_resource_blocks`

| 字段 | 类型 | 可空 | 约束/用途 |
|---|---|---:|---|
| `id` | BIGINT UNSIGNED | 否 | 主键 |
| `room_id` | BIGINT UNSIGNED | 否 | FK → `d_rooms.id` |
| `seat_id` | BIGINT UNSIGNED | 是 | M1 必填；M2/M3 为空表示整室 |
| `source_module` | VARCHAR(8) | 否 | `M1` / `M2` / `M3` |
| `source_id` | BIGINT UNSIGNED | 否 | 对应模块业务单 ID |
| `start_at` | DATETIME(3) | 否 | UTC；区间左端 |
| `end_at` | DATETIME(3) | 是 | M1/M2 必填；M3 可为空 |
| `status` | VARCHAR(16) | 否 | `ACTIVE` / `RELEASED` |
| `released_at` | DATETIME(3) | 是 | 释放时间；ACTIVE 必须为空 |
| `created_at` | DATETIME(3) | 否 | UTC |
| `updated_at` | DATETIME(3) | 否 | UTC |

唯一约束：`(source_module, source_id)`。M1/M2/M3 只能通过 `resources.service` 创建或按来源释放，不能互相删除记录。

#### `d_seat_status_summary`

| 字段 | 类型 | 可空 | 约束/用途 |
|---|---|---:|---|
| `id` | BIGINT UNSIGNED | 否 | 主键 |
| `seat_id` | BIGINT UNSIGNED | 否 | FK → `d_seats.id` |
| `slot_start` | DATETIME(3) | 否 | 小时槽起点，UTC；结束为起点后 1 小时 |
| `status` | VARCHAR(24) | 否 | `AVAILABLE` / `RESERVED` / `ROOM_BOOKED` / `MAINTENANCE` / `CLOSED` / `DISABLED` |
| `primary_block_id` | BIGINT UNSIGNED | 是 | 主要展示来源，不能代表唯一来源 |
| `source_count` | INT UNSIGNED | 否 | 覆盖该小时的有效来源数 |
| `resource_version` | BIGINT UNSIGNED | 否 | 计算时的教室版本 |
| `computed_at` | DATETIME(3) | 否 | UTC |
| `created_at` | DATETIME(3) | 否 | UTC |
| `updated_at` | DATETIME(3) | 否 | UTC |

唯一约束：`(seat_id, slot_start)`。这是查询缓存，业务提交时必须重新检查 `d_resource_blocks`。

#### `d_operation_logs`

| 字段 | 类型 | 可空 | 约束/用途 |
|---|---|---:|---|
| `id` | BIGINT UNSIGNED | 否 | 主键 |
| `module` | VARCHAR(8) | 否 | `D` / `M1` / `M2` / `M3` |
| `business_id` | BIGINT UNSIGNED | 否 | 模块业务单 ID，逻辑关联 |
| `action` | VARCHAR(40) | 否 | 固定操作事件名 |
| `actor_id` | BIGINT UNSIGNED | 否 | FK → `d_users.id` |
| `from_status` | VARCHAR(32) | 是 | 原状态 |
| `to_status` | VARCHAR(32) | 是 | 新状态 |
| `note` | TEXT | 是 | 原因或意见，不存密钥/密码 |
| `created_at` | DATETIME(3) | 否 | UTC |

#### `d_notifications`

| 字段 | 类型 | 可空 | 约束/用途 |
|---|---|---:|---|
| `id` | BIGINT UNSIGNED | 否 | 主键 |
| `recipient_id` | BIGINT UNSIGNED | 否 | FK → `d_users.id` |
| `source_module` | VARCHAR(8) | 否 | `D` / `M1` / `M2` / `M3` |
| `business_id` | BIGINT UNSIGNED | 否 | 来源业务单 ID |
| `event_key` | VARCHAR(160) | 否 | 同一收件人事件唯一 |
| `title` | VARCHAR(160) | 否 | 通知标题 |
| `content` | VARCHAR(1000) | 否 | 纯文本正文 |
| `link` | VARCHAR(200) | 否 | 站内路径 |
| `created_at` | DATETIME(3) | 否 | UTC |
| `read_at` | DATETIME(3) | 是 | 已读时间 |

唯一约束：`(recipient_id, event_key)`。通知与触发它的业务变更使用同一事务。

## 3. 模块表的统一引用要求

M1、M2、M3 的业务表由模块负责人维护，但字段必须遵守以下接口：

| 模块表 | 必须字段 | 必须引用 |
|---|---|---|
| `m1_reservations` | `id`、`created_at`、`updated_at`、`user_id`、`room_id`、`seat_id`、`start_at`、`end_at`、`status`、`create_request_key`、`request_hash`、`identity_id` | `user_id → d_users.id`；`room_id → d_rooms.id`；`seat_id → d_seats.id`；来源写 `M1` |
| `m2_applications` | `id`、`created_at`、`updated_at`、`applicant_id`、`room_id`、`start_at`、`end_at`、`status`、`request_key` | `applicant_id → d_users.id`；`room_id → d_rooms.id`；批准后来源写 `M2` |
| `m3_work_orders` | `id`、`created_at`、`updated_at`、`reporter_id`、`room_id`、`status`、`request_key` | `reporter_id → d_users.id`；`room_id → d_rooms.id`；维修中来源写 `M3` |

模块可以增加自己的字段，例如 M2 的用途/人数、M3 的维修人员/验收字段，但不得重新建立 `users`、`rooms`、`seats`、`resource_blocks` 或另一套角色/身份表。

## 4. 资源状态与事务规则

1. 创建预约、批准整室申请、开始维修和验收释放，统一执行“锁教室 → 锁业务单 → 校验来源 → 修改业务单和来源 → 写日志/通知 → 提交”。
2. 时间区间使用 `[start_at, end_at)`；判断重叠使用 `old.start_at < new.end_at AND new.start_at < old.end_at`。
3. 状态显示优先级为 `DISABLED > MAINTENANCE > CLOSED > ROOM_BOOKED > RESERVED > AVAILABLE`；优先级只影响展示，不删除任何来源。
4. `d_seat_status_summary` 不能作为预约成功的唯一依据；提交时必须重新查询真实来源。
5. M3 首轮维修限制允许与已有预约并存，只阻止新的预约/审批；正式使用前必须由团队确认已有预约的通知、取消或改期方案。

## 5. 变更流程

修改公共字段时，按以下顺序提交：

1. 更新本文件和对应迭代设计文档；
2. 更新 SQLAlchemy 模型和 Alembic 迁移；
3. 更新 `docs/public-layer-integration.md` 与受影响模块；
4. 添加迁移回放、外键、事务和接口行为测试；
5. 在 PR 描述中列出受影响模块，由 D、M1、M2、M3 至少各一名成员评审。

M1 基础模型已由 0006 建立，HTTP 业务仍未实现；M2 业务表和接口尚未实现。M1 创建幂等键实际为 `create_request_key`，与旧字段建议 `request_key` 不并存；详见基础契约。

## 6. T01–T05 追加结构

- 0004：d_auth_sessions、d_role_permissions、d_service_principals、d_service_room_scopes；调整身份唯一关系和角色范围。会话/服务凭据仅存摘要，撤销/轮换受控；新增表均有 id 和 created_at，可变服务主体有 updated_at。
- 0005：d_buildings、d_room_opening_rules、d_room_opening_exceptions；教室新增 building_id 与 opening_rules_enabled；历史建筑名回填，保留兼容显示名。规则以替换方式维护，保留 created_at，公共审计保存管理变更。
- 0006：五张 m1 业务表及 d_seat_allocations；来源继续用 d_resource_blocks，新增复合唯一键支持归属外键；完整字段/索引以静态版本迁移及 models 为准，概览见基础契约 §5。

本轮是用户授权的任务分支提案；未确定的人员与组内评审保留待定状态，不代签团队确认。迁移不能依赖 M2 私有表；现有 M3 数据和来源必须保留。
