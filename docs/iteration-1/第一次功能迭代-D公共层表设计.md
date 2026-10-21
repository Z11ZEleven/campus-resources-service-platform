# 第一次功能迭代：D 公共层表设计

> **版本：第一次功能迭代（Iteration 1）**  
> 日期：2026-10-21。数据库统一为 **MySQL 8.4 + InnoDB + utf8mb4**。  
> 本文件是待团队确认的最小实现设计，不是最终需求完成声明；仅提供设计，不执行建表。

## 1. 本轮范围

| 本轮实现 | 后续迭代补齐 |
|---|---|
| 种子账号登录、固定角色、单一有效身份 | 校园 SSO、动态角色权限、管理区域配置 |
| 教室和座位列表、每日固定开放时间 | 独立建筑/院系主表、周期与节假日开放规则、完整维护页面 |
| M1/M2/M3 统一资源限制、座位小时汇总 | 资源分配明细、跨服务消息与补偿 |
| 最小操作记录 | 完整公共审计、通知、附件与统计 |

当前迁移共 **9 张公共表**：`0001_public_layer` 的 8 张基础表，加上 `0003_m3_extensions` 中的 `d_notifications`。第一轮用脚本提供账号、角色、教室和座位，不同时开发复杂后台配置页面。完整冻结字段以[数据库统一契约](../database-contract.md)为准。

## 通用约定

| 项目 | 统一约定 |
|---|---|
| 数据库 | 一个数据库 `campus_resources`，使用表名前缀 `d_`、`m1_`、`m2_`、`m3_`；不再使用 PostgreSQL schema |
| ID | 所有主键及外键使用 BIGINT UNSIGNED；接口中的 ID 统一序列化为十进制字符串 |
| 通用字段 | 所有表含 id、created_at；可修改表含 updated_at，本稿逐表列出 |
| 时间 | DATETIME(3) 统一存 UTC；请求带时区，后端转换；页面展示北京时间。TIME 开放时刻按北京时间解释 |
| 状态 | VARCHAR + 后端枚举校验；不使用可任意填写的中文状态 |
| 外键 | 同一数据库内指向统一主表；不级联删除用户、教室和业务单 |
| 并发 | 同一教室资源变更先在事务中 SELECT d_rooms 对应行 FOR UPDATE；所有模块遵守同一顺序 |
| 事实来源 | d_resource_blocks 为占用/维修事实，d_seat_status_summary 为可重算结果 |
| 身份信任 | 操作人从登录凭据获取；不信任前端传入的用户身份或角色 |
| 原方案关系 | 本方案作为首次迭代的独立简化稿，不覆盖此前完整设计文件；表名、ID、时间类型需全组一起切换 |

## 2. 表清单

| 表 | 用途 | 建议主责 |
|---|---|---|
| d_users | 本地账号、密码哈希、用户名称 | 身份负责人 |
| d_user_identities | 学生/教师/员工身份及院系编码 | 身份负责人 |
| d_user_roles | 固定角色授权 | 身份负责人 |
| d_rooms | 教室、归属、开放时间 | 资源负责人 |
| d_seats | 座位、插座标记 | 资源负责人 |
| d_resource_blocks | 个人占用、整室占用、维修限制 | 资源负责人统一维护 |
| d_seat_status_summary | 按座位和小时查询状态 | 资源负责人统一维护 |
| d_operation_logs | 最小操作记录 | D 提供统一函数，各模块写事件 |
| d_notifications | 站内通知收件箱 | D 提供事务内通知服务 |

## 3. 字段设计

### `d_users`：用户及本地账号

| 字段 | MySQL 类型 | 可空 | 说明／约束 |
|---|---|---|---|
| `id` | BIGINT UNSIGNED | 否 | 自增主键 |
| `created_at` | DATETIME(3) | 否 | 创建时间，UTC |
| `username` | VARCHAR(64) | 否 | 唯一；种子脚本统一小写，登录按同一规则归一化 |
| `password_hash` | VARCHAR(255) | 否 | Argon2 密码哈希，不存明文 |
| `display_name` | VARCHAR(100) | 否 | 展示名称 |
| `is_active` | TINYINT(1) | 否 | 默认 1；禁用后不能登录或执行业务 |
| `updated_at` | DATETIME(3) | 否 | 更新时间，UTC |

- 将原 users 与本地 auth_accounts 合并；本轮不实现多认证提供方。
- 停用保留历史。种子账号使用虚构数据，密码从初始化环境配置读取。

### `d_user_identities`：用户身份

| 字段 | MySQL 类型 | 可空 | 说明／约束 |
|---|---|---|---|
| `id` | BIGINT UNSIGNED | 否 | 自增主键 |
| `created_at` | DATETIME(3) | 否 | 创建时间，UTC |
| `user_id` | BIGINT UNSIGNED | 否 | FK → d_users.id；唯一，一人本轮一个身份 |
| `identity_type` | VARCHAR(20) | 否 | STUDENT / TEACHER / STAFF |
| `identity_no` | VARCHAR(64) | 否 | 学号/工号；与 identity_type 组合唯一 |
| `department_code` | VARCHAR(32) | 否 | 院系编码，与教室字段使用同一受控编码清单 |
| `is_active` | TINYINT(1) | 否 | 默认 1 |
| `updated_at` | DATETIME(3) | 否 | 更新时间，UTC |

- UNIQUE(user_id)，UNIQUE(identity_type, identity_no)。
- 暂不建 departments 表；编码放统一种子数据清单，禁止各组自造同义编码。后续可迁移为院系外键。
- 身份类型不是操作角色；是否有审批/派单权限看 d_user_roles。

### `d_user_roles`：固定角色授权

| 字段 | MySQL 类型 | 可空 | 说明／约束 |
|---|---|---|---|
| `id` | BIGINT UNSIGNED | 否 | 自增主键 |
| `created_at` | DATETIME(3) | 否 | 创建时间，UTC |
| `user_id` | BIGINT UNSIGNED | 否 | FK → d_users.id |
| `role_code` | VARCHAR(32) | 否 | USER / ROOM_ADMIN / PROPERTY_ADMIN / REPAIRER / SYS_ADMIN |

- UNIQUE(user_id, role_code)。
- 角色权限映射统一放后端 auth/permissions.py；本轮不建 roles、permissions、role_permissions。
- 一个人可有多个角色；SYS_ADMIN 是否可执行业务要在映射中显式配置，不隐式绕过约束。

### `d_rooms`：教室

| 字段 | MySQL 类型 | 可空 | 说明／约束 |
|---|---|---|---|
| `id` | BIGINT UNSIGNED | 否 | 自增主键 |
| `created_at` | DATETIME(3) | 否 | 创建时间，UTC |
| `code` | VARCHAR(32) | 否 | 全局唯一，如 B1-101 |
| `name` | VARCHAR(100) | 否 | 名称 |
| `building_name` | VARCHAR(100) | 否 | 建筑名称，暂不建建筑表 |
| `capacity` | INT UNSIGNED | 否 | 容量，大于 0 |
| `department_code` | VARCHAR(32) | 是 | 为空表示全校开放；非空表示仅本院系使用 |
| `open_time` | TIME | 否 | 每日固定开放起点，北京时间，如 08:00 |
| `close_time` | TIME | 否 | 每日固定关闭时刻，北京时间，如 22:00 |
| `is_active` | TINYINT(1) | 否 | 行政启停，不代表预约或维修状态 |
| `resource_version` | BIGINT UNSIGNED | 否 | 默认 0；影响可用性的变更时递增 |
| `updated_at` | DATETIME(3) | 否 | 更新时间，UTC |

- UNIQUE(code)。本轮 open_time < close_time、整点且同日，不支持跨午夜和节假日例外。
- 建筑名称只用于显示，跨模块一律使用 room_id。
- 本轮管理员管理本组全部测试教室；细粒度管理范围后续补齐。
- 所有占用、释放、维修限制及教室/座位启停变更，在房间锁保护下递增 resource_version。

### `d_seats`：座位

| 字段 | MySQL 类型 | 可空 | 说明／约束 |
|---|---|---|---|
| `id` | BIGINT UNSIGNED | 否 | 自增主键 |
| `created_at` | DATETIME(3) | 否 | 创建时间，UTC |
| `room_id` | BIGINT UNSIGNED | 否 | FK → d_rooms.id |
| `seat_no` | VARCHAR(32) | 否 | 室内座位编号 |
| `has_fixed_socket` | TINYINT(1) | 否 | 默认 0，固定插座 |
| `has_mobile_socket` | TINYINT(1) | 否 | 默认 0，移动导轨插座 |
| `is_active` | TINYINT(1) | 否 | 默认 1，行政启停 |
| `updated_at` | DATETIME(3) | 否 | 更新时间，UTC |

- UNIQUE(room_id, seat_no)。可增加 UNIQUE(id, room_id)，用于组合外键校验座位归属。
- 本轮用列表选择座位，不做平面图；资源数据初始化后不开放随意迁移座位。

### `d_resource_blocks`：统一资源占用与限制

| 字段 | MySQL 类型 | 可空 | 说明／约束 |
|---|---|---|---|
| `id` | BIGINT UNSIGNED | 否 | 自增主键 |
| `created_at` | DATETIME(3) | 否 | 创建时间，UTC |
| `room_id` | BIGINT UNSIGNED | 否 | FK → d_rooms.id |
| `seat_id` | BIGINT UNSIGNED | 是 | FK → d_seats.id；M1 必填，M2/M3 为空表示整室 |
| `source_module` | VARCHAR(8) | 否 | M1 / M2 / M3 |
| `source_id` | BIGINT UNSIGNED | 否 | 来源业务单 ID；随模块解释，逻辑关联 |
| `start_at` | DATETIME(3) | 否 | 生效起点，UTC |
| `end_at` | DATETIME(3) | 是 | M1/M2 必填；M3 未验收时为空 |
| `status` | VARCHAR(16) | 否 | ACTIVE / RELEASED |
| `released_at` | DATETIME(3) | 是 | 释放时间；ACTIVE 时为空 |
| `updated_at` | DATETIME(3) | 否 | 更新时间，UTC |

- UNIQUE(source_module, source_id)；索引 (room_id, status, start_at)。
- M1 来源 m1_reservations，M2 来源 m2_applications，M3 来源 m3_work_orders；D 由受控调用验证来源，不给任意用户开放写表接口。
- M1 seat_id 必须属于 room_id；M2/M3 seat_id 为空；end_at 非空时 start_at < end_at。
- 本轮不建 seat_allocations：查询统一检查座位级和整室级来源。整室来源本身即可阻止全室座位被个人预约。
- 释放只定位本模块、本单号的来源；不能将某教室所有来源一起删除或释放。
- 所有资源变更和对应业务主单、日志在同一数据库事务中提交；本轮禁止模块私自 commit。
- 重复创建同一业务来源不再新建；已释放来源不允许由旧请求重新激活。冲突时比较目标与当前业务状态。

### `d_seat_status_summary`：座位状态汇总

| 字段 | MySQL 类型 | 可空 | 说明／约束 |
|---|---|---|---|
| `id` | BIGINT UNSIGNED | 否 | 自增主键 |
| `created_at` | DATETIME(3) | 否 | 创建时间，UTC |
| `seat_id` | BIGINT UNSIGNED | 否 | FK → d_seats.id |
| `slot_start` | DATETIME(3) | 否 | 整点时段开始，UTC；与 seat_id 组合唯一 |
| `status` | VARCHAR(24) | 否 | AVAILABLE / RESERVED / ROOM_BOOKED / MAINTENANCE / CLOSED / DISABLED |
| `primary_block_id` | BIGINT UNSIGNED | 是 | FK → d_resource_blocks.id，主要展示来源 |
| `source_count` | INT UNSIGNED | 否 | 该时段重叠有效来源数，默认 0 |
| `resource_version` | BIGINT UNSIGNED | 否 | 计算时 d_rooms.resource_version |
| `computed_at` | DATETIME(3) | 否 | 计算时间，UTC |
| `updated_at` | DATETIME(3) | 否 | 更新时间，UTC |

- UNIQUE(seat_id, slot_start)；时段结束固定为 slot_start + 1 小时，省略 slot_end。room_id 从座位表获取，不重复保存。
- 本轮查询某天时按需生成；只支持配置窗口内日期。初次查询或房间资源版本变化时，持有同一房间锁重算该天汇总后提交，防止旧结果覆盖新结果。
- 建议优先级：DISABLED > MAINTENANCE > CLOSED > ROOM_BOOKED > RESERVED > AVAILABLE。多来源保留在 d_resource_blocks。
- AVAILABLE 的 primary_block_id 为空、source_count 为 0；用户院系资格另行判断。
- 不存在汇总行不能当作 AVAILABLE；必须计算或回到真实来源判断。提交预约永远再查真实来源。

### `d_operation_logs`：最小操作及状态记录

| 字段 | MySQL 类型 | 可空 | 说明／约束 |
|---|---|---|---|
| `id` | BIGINT UNSIGNED | 否 | 自增主键 |
| `created_at` | DATETIME(3) | 否 | 创建时间，UTC |
| `module` | VARCHAR(8) | 否 | D / M1 / M2 / M3 |
| `business_id` | BIGINT UNSIGNED | 否 | 按 module 和 action 解释的对象 ID，逻辑关联 |
| `action` | VARCHAR(40) | 否 | CREATE_RESERVATION / APPROVE / START_REPAIR 等固定事件 |
| `actor_id` | BIGINT UNSIGNED | 否 | FK → d_users.id；本轮业务均为用户操作 |
| `from_status` | VARCHAR(32) | 是 | 原业务状态 |
| `to_status` | VARCHAR(32) | 是 | 新业务状态 |
| `note` | TEXT | 是 | 操作原因、验收意见或必要结果，不存敏感凭据 |

- 索引 (module, business_id, created_at)。按相同时间再以 id 排序。
- 追加写入，不提供普通修改或删除接口；与对应业务变更同事务写入。
- 本轮用统一最小记录支撑模块时间线；后续再拆分领域事件与更完整的安全审计。

## 4. D 对外公共函数与接口

| 能力 | 首轮形式 | 约束 |
|---|---|---|
| 登录、当前用户 | /api/v1/auth/login、/api/v1/auth/me | 服务端读取有效用户和角色 |
| 教室、座位列表 | /api/v1/resources/rooms、/rooms/{id}/seats | 与前端统一路由前缀 |
| 小时状态查询 | /api/v1/resources/rooms/{id}/availability?date=YYYY-MM-DD | 返回摘要并执行院系过滤 |
| 锁定并检查资源 | resources/service.py 函数 | 使用调用方同一 SQLAlchemy Session 和事务 |
| 创建/释放来源 | resources/service.py 函数 | 模块、单号和归属经验证；不独立提交 |
| 操作历史 | 由对应模块详情/日志接口读取 | 必须先检查业务记录查看权限 |

本轮为同进程模块集成，不能冒充已满足课程最终的跨模块网络调用演示；后续需单独完成一条真实网络链路。

## 5. 并发规则与冲突策略

1. 所有资源变更由业务 service 开启事务，先按主键锁 d_rooms 行，再锁/复查业务单。
2. 在同一事务里检查真实 d_resource_blocks，使用 READ COMMITTED 或锁定读取取得最新已提交状态，不先用旧快照判断。
3. 相交条件：existing.start_at < requested.end_at，且 existing.end_at 为空或 existing.end_at > requested.start_at。
4. M1 检查该座位的 M1 来源及同室的 M2/M3 来源；同室其他座位的 M1 不阻塞本座位。
5. M2 审批检查同室所有重叠 M1/M2/M3 来源，有冲突则返回 409，申请保持 PENDING，不擅自取消学生预约。
6. M3 开始维修可添加与已有占用重叠的限制：阻止新预约，但第一轮不自动取消已有预约；界面提示并记录此限制。
7. 业务单、来源、日志及房间版本一起成功或一起回滚。
8. 表的唯一键不能保证时间区间不重叠；MySQL 下必须由统一房间锁协议保证，不能只写“先查询再插入”。

## 6. 初始化与验收

- 种子用户：学生、教室管理员、物业管理员、维修人员；角色授权来自 d_user_roles。
- 种子资源：至少 2 间教室、每间至少 3 个座位；含同院系/不同院系、带插座/无插座样例。
- 新环境通过 Alembic + 幂等种子脚本初始化，不手工建表补数据。
- 验证同座位并发预约仅一成功；同室不同座位可分别预约。
- 验证 M2 占用、M3 维修能阻止新的 M1 预约；多来源恢复不误释放。

## 7. 后续扩展映射

| 首轮简化 | 后续扩展 |
|---|---|
| 用户表内登录字段 | 独立认证提供方、会话及 SSO |
| 固定角色码 | roles、permissions、角色权限、数据范围 |
| 身份中的院系编码 | departments 主表及多身份 |
| 教室内建筑名与每日时段 | buildings、周期与特殊开放表 |
| resource_blocks | 完整来源版本、细化分配及网络同步 |
| operation_logs | 领域事件、公共审计 |
| 暂无通知/任务/附件 | 通知投递、可靠任务、附件表 |

## 配套文档

- [D 公共层表设计](第一次功能迭代-D公共层表设计.md)
- [M1 座位预约表设计](第一次功能迭代-M1座位预约表设计.md)
- [M2 教室审批表设计](第一次功能迭代-M2教室审批表设计.md)
- [M3 报修工单表设计](第一次功能迭代-M3报修工单表设计.md)
- [技术栈与编写指南](第一次功能迭代-技术栈与编写指南.md)
