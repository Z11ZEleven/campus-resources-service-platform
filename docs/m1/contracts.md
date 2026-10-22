# M1 基础契约：T01–T05

日期：2026-10-22。关联 [实施 Issue #15](https://github.com/Z11ZEleven/campus-resources-service-platform/issues/15)、[规划 Issue #8](https://github.com/Z11ZEleven/campus-resources-service-platform/issues/8)。本轮为用户授权的任务分支提案，人员责任与组内评审仍待确认，见 [责任登记](../team.md)。

## 1. 固定技术与模块边界

Vue 3/TypeScript、FastAPI、同步 SQLAlchemy、MySQL 8.4、Alembic；主外键 BIGINT UNSIGNED，HTTP ID 十进制字符串，DATETIME(3) 存 UTC，业务日期 Asia/Shanghai。区间统一 `[start_at, end_at)`。主数据和来源统一使用 D resources，业务状态分别属于 M1/M2/M3。HTTP Db 负责事务，服务仅 flush。

原规划中的 JavaScript、UUID 主键、DATETIME(6)、`modules/d` 和 `d_resource_holds` 不作为本轮实施标准；不改造其他模块的历史迁移。

## 2. 身份、会话与授权

| 方法与路径 | 已实现行为 |
| --- | --- |
| POST `/api/v1/auth/login` | 用户名密码校验；建立可撤销数据库会话；返回 `{authenticated:true}` 并设置 Cookie，不在 JSON 返回会话凭据 |
| GET `/api/v1/auth/me` | 当前有效用户、所选身份、角色与权限；实时读取 D 数据 |
| POST `/api/v1/auth/logout` | 撤销当前会话并清除 Cookie |
| GET `/api/v1/auth/identities` | 仅当前用户的有效身份 |
| POST `/api/v1/auth/identity` | `identity_id` 必须属于当前用户且有效；更新当前会话选择 |
| PUT `/api/v1/auth/users/{user_id}/roles` | 全局 `auth:manage` 赋予指定角色与范围，重复同授权幂等并审计 |
| DELETE `/api/v1/auth/users/{user_id}/roles/{grant_id}` | 全局 `auth:manage` 撤销指定授权，实时生效并审计 |

浏览器使用 `campus_session` HttpOnly Cookie；`campus_csrf` 可读 Cookie 的值通过 `X-CSRF-Token` 提交，服务端核验会话绑定的摘要。Cookie 写请求校验 Origin（存在时）及 CSRF；Cookie 优先于 Authorization，不能附加 Bearer 绕过 CSRF。生产启用 `SESSION_COOKIE_SECURE=true`，SameSite 支持 lax/strict；生产反向代理须保留原始 Host、采用同源 `/api`。

数据库只保存会话和服务凭据 SHA256 摘要；用户停用、身份停用、角色撤销、会话撤销或过期均默认拒绝。旧 JWT 不再被 HTTP 接口接受，升级后所有旧客户端重新登录。没有过渡性的 JWT 绕过通道。

角色动作来自 `d_role_permissions`，用户授权范围来自 `d_user_roles`；支持 GLOBAL、ROOM、DEPARTMENT、BUILDING。单条授权必须同时满足动作与范围，SQL 查询在分页/计数前限制范围。既有授权迁移为原有 GLOBAL，保留原权限边界；新权限最小授权。`Principal.has/require` 仅接受 GLOBAL，房间授权使用 `can_for_room/require_for_room`，避免未适配的旧模块把局部授权扩大为全局。既有 M3 继续支持原全局角色；M3 局部业务角色的完整适配不在 T01–T05 中，当前保守拒绝，不宣称已支持。

服务主体通过 `authenticate_service(session, token, module, room_id)` 或 `require_service(module)` 校验，绑定所属模块与明确房间范围。用户会话不能替代服务凭据，M2 不能冒充 M3 来源。凭据通过环境变量和受审计的配置脚本设置，不能手工改库；实际 M2/M3 来源 HTTP 写回在 T12，真实网络集成在 T16，本轮不冒充完成。

## 3. 资源与开放规则

资源 HTTP 前缀为 `/api/v1/resources`。新增 buildings 的 GET/POST/PUT，rooms 的 POST/PUT，`rooms/{room_id}/seats` 的 POST、座位详情 PUT，以及 `rooms/{room_id}/opening` 的 GET/PUT；列表、详情和可用性沿用现有 GET。

管理请求使用 `resources:manage` 的同条有效范围授权；创建建筑需全局授权，创建教室需全局或目标建筑授权。教室/座位唯一标识、合法归属、容量与时段在服务端和数据库验证。座位更新不接受 room_id，不能原地跨室迁移。启停采用软状态，历史来源及审计保留；有有效来源时，停用、缩短开放或其他破坏性维护返回 409，免责取消/中断由后续 T13 处理。

建筑名保留旧接口字段 `building_name`，新建筑关系以 `building_id` 为准；建筑改名在房间锁内同步兼容字段与版本。迁移根据历史名称建立建筑；新种子使用明确建筑关联。历史可空关联兼容旧资料，管理保存必须选择有效建筑。

周期规则使用校园星期 0–6 和本地起止时间，结束早于开始代表跨午夜；起止相等禁止。特殊开启先并入周期区间，再扣除特殊关闭；相邻开启可合并，任何空隙都导致完整区间检查失败。`opening_rules_enabled=false` 回退现有每日 open_time/close_time；PUT 开放规则启用显式日程，空规则表示关闭，特殊开启仍可补入。时段必须带时区，存 UTC。

只扩充开放事实与维护，不提前开放原计划中未定的跨日预约政策，也不修改 M3 业务状态机。

## 4. 来源与占用

`d_resource_blocks` 是唯一来源事实表，唯一键 `(source_module, source_id)`，记录影响房间/座位、时间和释放状态。`d_seat_allocations` 是占用明细，不作为第二套预约成功判断；来源与组合归属由复合外键校验。

- M1 来源只生成对应座位明细，M2 为有效座位生成明细，M3 以整室来源限制，不生成无限未来明细。
- 同来源同载荷重试不重复生成，异资源/时间或已释放来源复活返回 409。
- 释放只更新对应来源和明细，其他工单/整室/个人来源继续生效；不会直接将座位置为 AVAILABLE。
- M2 来源仍对全室权威生效，因此后续新增座位也不能绕过整室占用；新座位对应明细补算属于后续资源裁决内核完善。
- 多态 source_id 不能无条件外键到 M1。新表提供可选 typed `m1_reservation_id`，填写时须与来源 ID 及预约房间/座位一致。历史逻辑来源保留空 typed 关联，不凭空创建预约；未来 M1 写入入口在 T07/T09 补齐该绑定。

## 5. M1 私有模型与迁移

| 表 | 主要字段与约束 |
| --- | --- |
| `m1_reservations` | user_id/identity_id 组合归属，room_id/seat_id 组合归属，start/end、六种合法状态，签到/实际结束/取消时间与原因，设置/资源 JSON 快照，create_request_key/request_hash、版本；用户与请求键唯一；用户历史、房间状态起点、状态起止索引 |
| `m1_reservation_events` | 预约、事件、前后状态、操作者类型与 ID、correlation_id、原因及 JSON 详情；只提供读取 Repository，不开放更改历史接口 |
| `m1_violations` | 预约与用户组合关联，NO_SHOW、发生时间与原因，预约唯一 |
| `m1_room_checkin_codes` | 房间、校园日期、nonce、版本；房间日期唯一，不存明文编码或环境密钥 |
| `m1_checkin_attempts` | 用户/房间及可选预约组合关联、尝试时间、结果；用户/房间/时间索引，不存提交的编码 |

迁移链：0001–0003 保持不变 → 0004 身份会话与授权 → 0005 主数据与开放 → 0006 M1 与占用。0006 为旧 M1/M2 来源补历史明细，保留释放状态与 M3 数据，不要求 M2 私有表存在。重复 `alembic upgrade head` 不重复结构；种子幂等，不恢复撤销角色或停用用户。

降级仅在专用测试库验证；含多身份或局部授权的数据不能降回单身份/全局授权，0004 主动拒绝有损转换。MySQL DDL 不保证整体事务回滚，正式发布优先前向修复，破坏性降级人工处理。

## 6. 错误及请求 ID

所有 HTTP 请求生成服务器 UUID 请求 ID，返回 `X-Request-ID`；不信任客户端指定的关联 ID。错误体为 `detail.code/message/request_id`，字段校验附 `fields`（位置、类型和安全提示），不回显原输入、密码、令牌或 SQL 参数。404/405 同样规范化并保留 Allow 等头。未知异常记录关联 ID 和异常类别，返回通用 500；数据库约束冲突为 409。

`/health` 只检查进程，`/ready` 真实执行数据库探测，不可用时 503；readiness 不是业务验收。

## 7. 本轮与后续工作的界线

本轮只交付基础工程、D 接入、主数据和 M1 数据结构。预约查询/创建/取消、签到编码、违约、通知任务、M2/M3 写回及真实网络验收仍在 T06–T17，不因表已存在就标记业务完成。人员分工、组内评审、人工验收及生产审批状态均由人补充。
