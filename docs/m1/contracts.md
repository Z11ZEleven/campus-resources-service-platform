# M1 基础契约：T01–T06

日期：2026-10-22；T06 更新：2026-10-31。关联 [实施 Issue #15](https://github.com/Z11ZEleven/campus-resources-service-platform/issues/15)、[规划 Issue #8](https://github.com/Z11ZEleven/campus-resources-service-platform/issues/8)。本轮为用户授权的任务分支提案，人员责任与组内评审仍待确认，见 [责任登记](../team.md)。

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
- 多态 source_id 不能无条件外键到 M1。新表提供可选 typed `m1_reservation_id`，填写时须与来源 ID 及预约房间/座位一致。历史逻辑来源保留空 typed 关联，不凭空创建预约；T09 的 M1 预约写入已使用该绑定。

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

历史范围（截至 2026-10-31）：交付基础工程、D 接入、主数据、M1 数据结构，以及 T06 的公共 Outbox、独立 Worker、站内通知和受控审计接收。2026-11-11 更新：T07 资源裁决内核及 T09–T10 预约、签到后端提案已实现；T08 搜索及独立管理员时段限制来源已实现，搜索与创建共享可预约判定。T12 的 HTTP 操作记录与核对、真实网络入口和维修免责联动仍待完成。T11–T17、真实跨模块网络和人工验收未完成。人员分工、组内评审、人工验收及生产审批状态均由人补充。

## 8. T06 公共 Outbox 与受控通知/审计适配

### 8.1 公共任务表

`d_outbox_events` 由 D 维护，业务模块只通过公开函数登记，不直接写表。主要字段：来源模块、模块内唯一事件键、处理器类型、业务 ID/版本、JSON 载荷、可执行时间、状态、尝试次数、最大尝试次数、租约持有者/令牌/到期时间、错误分类/最后错误、乐观版本和完成时间。业务键唯一约束为 `(source_module, event_key)`，状态为 `PENDING`、`PROCESSING`、`SUCCEEDED`、`MANUAL`。

`register_event(session, *, source_module, event_key, handler, payload, business_id=0, business_version=1, available_at=None, max_attempts=None)` 在调用方事务内插入。相同处理器的同载荷登记是幂等读取；同键换处理器、业务版本或载荷返回 409，不静默覆盖，也不重置已完成状态。函数只 flush，不 commit，因此业务回滚会同时撤销任务。

### 8.2 领取、完成和恢复

`claim_events(session, *, worker_id, limit, lease_seconds, now=None)` 在事务内以 `FOR UPDATE SKIP LOCKED` 分别领取到期的 `PENDING` 和租约过期的 `PROCESSING`，避免多个 Worker 抢同一行。领取增加尝试次数和乐观版本，写入随机租约令牌及到期时间。

`complete_event`、`retry_event`、`mark_manual` 都必须匹配事件 ID、`PROCESSING` 状态、Worker、租约令牌和期望版本；任一不匹配抛出 `LeaseLost`，旧 Worker 不能覆盖新 Worker 的结果。进程崩溃未完成时，租约到期后由其他 Worker 重新领取；接收方副作用与完成标记在同一事务提交。接收方已提交但响应丢失时，重试由通知/审计的稳定事件键去重。

### 8.3 重试、失败和查询

`retry_event` 写回 `PENDING`、下次可执行时间和 `TRANSIENT` 错误；默认指数退避从 5 秒起、上限 300 秒。达到 `max_attempts` 或收到不可重试错误时写 `MANUAL`，错误分类为 `RETRY_EXHAUSTED` 或 `PERMANENT`，并保留最后错误供人工处理。`list_events` 可按状态/处理器查询，`list_failures` 查询 `MANUAL`；当前是服务层运维入口，不新增公共 HTTP 管理界面。

M1 的 `notify_reservation` 和 `audit_reservation` 默认 `max_attempts=6`，与计划中的六次投递策略一致。必须无限重试的 M1 业务定时/释放任务可显式传 `max_attempts=None`，不与外部投递策略混用；T06 未实现这些业务定时任务本身。

### 8.4 独立 Worker

入口为 `python -m app.worker`，支持常驻轮询和 `--once` 单批模式。默认每批 10 条、租约 30 秒、退避 5–300 秒，可由 `OUTBOX_*` 配置或命令行覆盖。Worker 独立于 HTTP 进程启动，不依赖 M3 同进程超时循环；收到 SIGTERM/SIGINT 后停止领取新批次。启动前须执行迁移和种子。

### 8.5 通知与审计接收

`deliver_notification` 调用现有 `publish_notifications`，写入真实 `d_notifications` 站内收件箱；收件人/模块事件键唯一，重复投递保留首次通知及已读时间。站内收件箱是本轮的实际通知渠道；邮件、微信等外部渠道未接入，开发日志不作为通知渠道。

`receive_audit` 调用扩展后的 `record_operation`。审计行必须有 `actor_type`：用户为 `USER` 且带 `actor_id`，服务主体为 `SERVICE` 且带 `service_principal_id`，Worker/系统动作为 `SYSTEM` 且两者为空；不得把服务主体 ID 冒充用户 ID，也不得为系统动作编造学生操作者。`(module, event_key)` 唯一使重试幂等，异载荷冲突返回 409。既有用户审计调用默认 `USER/SUCCESS`，保持兼容。

### 8.6 边界与未完成

T06 只建立公共任务与接收基础设施，不实现预约创建/取消/签到/违约的具体触发点；T09 已登记预约任务，T11 的业务处理仍待实现。故障查询目前无管理 HTTP 页面；通知没有外部通道；审计迁移降级在存在 `SYSTEM`/`SERVICE` 行时会因 `actor_id` 恢复非空而拒绝，生产优先前向修复。Worker 运行指标、告警和真实跨模块网络投递仍待 T16/T17。

## 9. T08 座位搜索提案（待组内评审）

`GET /api/v1/m1/seats` 接受 `identity_id`、带时区的 `start_at`/`end_at`，可选 `room_id`、`has_fixed_socket`、`has_mobile_socket`、`page`（默认 1）和 `page_size`（默认 20，最大 100）。布尔筛选为精确匹配；结果按教室 ID、座位 ID 排序，返回 `total`、`page`、`page_size` 和 `items`，每项含字符串 `seat_id`、`room_id`、`resource_version`、座位号、教室名、楼名和插座属性，不返回预约人。结果仅为查询观察，不构成保留或承诺；并发变化可能使结果立即过期，分页各请求也不保证一致快照。

会话身份必须为提交的当前有效学生身份；同时要求 `m1:seat:search` 与 `m1:reservation:create`，每项授权分别受范围限制。院系和权限范围、资源有效性、完整开放以及全部有效来源重叠检查均在计数和分页之前执行。来源为 D 的公开资源事实，活动来源按 UTC 半开区间 `[start_at, end_at)` 匹配，整室来源作用于全部座位，释放的来源不阻挡。当前来源包括 M1/M2/M3 和 D 管理员时段限制，资源停用也会排除座位。

用户本轮选择 README 所述校园时间当天 08:00–22:00，替代先前建议的七日窗口和跨午夜；保留整点、单次 1–4 小时、开始不早于当前时刻及全程开放。该选择是实现提案，仍待组内评审，不代表团队共识。时间按 Asia/Shanghai 判断，存储和重叠计算为 UTC。T09 在房间锁内重检时段、有效性、开放、来源、会话、身份、权限及院系；单独调用资源判断不涵盖授权或时段政策。旧资源 `reserve_seat` 的时间校验与该提案并不完全一致，不能视作相同的预约入口。

搜索使用 SQL 筛选来源冲突，批量读取开放规则，并在 READ COMMITTED 下复核候选教室资源版本；版本改变则最多重试三次，仍持续变化时返回 409。该机制依赖所有资源变更在同一事务内递增房间资源版本；搜索仍会在应用内为符合来源条件的候选座位计算开放及分页，大数据量下的内存与延迟仍须评估。查询不构成资源承诺。管理员限制使用 0011 迁移；真实跨模块网络链路尚未完成。

## 10. T09 预约创建、本人查询与取消提案（待组内评审）

`POST /api/v1/m1/reservations` 接受 `identity_id`、`seat_id`、带时区的 `start_at` 和 `end_at`，必须提供 1–64 字符 `Idempotency-Key`；所有者来自有效会话。用户与请求键唯一，规范化至 UTC 的请求摘要一致时返回首次创建的 201 响应（即使预约后来取消），不同请求返回 409。重放仍须通过当前选中学生身份、创建权限、房间范围和院系校验，但不重新判断时间或资源可用性。键比较遵循数据库列的排序规则。房间行锁后以用户行锁串行化不同房间的同用户请求；锁内刷新会话、身份和角色，并重新验证选择的学生身份、房间授权、院系、当日时段、开放及全部活动来源。预约、资源来源及其类型化占用、创建事件、审计任务和设置/资源快照在一个事务内写入。

`GET /api/v1/m1/reservations` 返回本人且在本人读取授权范围内的记录，可按 `status`、校园日期 `day` 筛选并用 `page`、`page_size` 分页；`GET /api/v1/m1/reservations/{id}` 返回本人详情。`POST /api/v1/m1/reservations/{id}/cancel` 仅允许本人在开始前取消 RESERVED 预约；重复取消返回当前结果。取消只释放对应 M1 来源和明细，状态、版本、事件和审计任务同事务写入。历史快照保留，用户使用历史座位重新提交创建时执行所有现行检查。

创建登记开始前 15 分钟、开始后 10 分钟、15 分钟及结束时的四条持久 Outbox 任务。T11 处理器尚未实现：Worker 领取时跳过未注册的 `m1_timer`，任务保持 PENDING 且不占用批次或增加重试；T11 注册该处理器后可重新领取。这不代表通知、违约、自动结束已实现；任务积压监控和 T11 接管策略仍待评审。D 新增供 M1 使用的来源获取与精确释放服务接口，公共契约变更待组内评审；真实跨模块网络验收仍未完成。

0009 引入 D 的 M1 来源号高水位序列，迁移以现有 M1 来源与预约的最大 ID 初始化，不修改或关联历史来源；新预约分配 ID 与旧来源入口共用序列锁；两个人员路径统一按房间、用户、序列的顺序加锁，服务主体不映射为用户。部署迁移时须暂停旧版本写入，所有写入方升级后才能依赖此互斥；版本回退前应协调写入方，重新升级会从现有数据重建高水位。公共 schema 和接口待组内评审。

## 11. T10 签到码与签到（待组内评审）

`GET /api/v1/m1/admin/rooms/{room_id}/check-in-code` 与 `POST /api/v1/m1/admin/rooms/{room_id}/check-in-code/rotate` 使用当前会话和房间授权范围；读取需 `m1:checkin_code:read`，轮换需独立的 `m1:checkin_code:rotate`（轮换响应也展示新码）；返回 `code` 和校园时区的 `expires_at`，响应 `Cache-Control: no-store`。展示与轮换均记录房间、操作者和校园日期的审计，不记录签到码或密钥；后者轮换当日 nonce，轮换与签到按房间锁串行化。展示码须仅向有权人员提供，代理及客户端不得缓存、记录或传播。跨节点须配置相同、独立的 `M1_CHECKIN_SECRET`，至少 32 字符，禁止使用示例占位符；密钥不进入数据库或日志。丢失或更换密钥将使现有码立即失效，密钥保管、轮换和受控展示方式待团队评审。

`POST /api/v1/m1/reservations/{id}/check-in` 请求为 `{"code":"八位数字"}`；必须为预约本人、当前有效学生身份和该房间的 `m1:reservation:checkin` 授权。签到窗口为 `[start-15分钟,start+15分钟)`，使用提交时 Asia/Shanghai 自然日的房间码；已签到重试返回当前状态，不重复事件。错误码返回 400，每用户每房间滚动十分钟内五次失败后返回 429，窗口起点不包含；失败记录只存关联标识、时间和结果，不存码，受房间锁约束。超出窗口或身份权限失败不计入错误码尝试。过期记录清理期限、审计保留和展示端交互待评审。预约、房间码与失败尝试均在请求事务中提交；错误码响应不会回滚失败记录。T11 的超时、违约与通知业务处理尚未实现，后续须沿用房间锁并验证竞争。

权限迁移 0010 不修改既有角色权限，历史仅有读取权限的账户不能轮换；团队应审查并显式授予需要轮换的角色，新安装迁移及种子也不授予该权限。已初始化的种子不会复活已撤销权限。回退旧版会恢复读取权限即可轮换的旧行为，须先评审授权及停用相关接口；不要将旧版回退视为安全修复。

授权由受信任部署操作员在 `backend/` 运行 `.venv/bin/python -m scripts.manage_m1_rotation --actor-id <全局权限管理员ID> grant`；撤销时将 `grant` 替换为 `revoke`。脚本验证该用户当前具有全局 `auth:manage` 并审计变更；它依赖操作系统及数据库部署访问控制，`--actor-id` 本身不是操作员身份认证。授权影响全部 ROOM_ADMIN 用户，但其房间访问仍受各自角色范围约束；执行前须由团队确认影响范围，重新执行种子不恢复撤销。

已知限制：维修开始后既有预约目前仍可能签到；维修免责取消或中断及签到联动待 T12/T13 实现和验证，不能视为已完成跨模块约束。


## 10. D 管理员时段限制提案（待组内评审）

`POST /api/v1/resources/rooms/{room_id}/restrictions` 接受 `seat_id`（可空，空表示整室）、带时区的 `start_at`、`end_at`，以及必填 `Idempotency-Key`（1–64 字符）；返回限制 ID、房间、座位、UTC 时段及状态。时段是有限的正长度半开区间。`DELETE /api/v1/resources/rooms/{room_id}/restrictions/{restriction_id}` 只释放指定 D 来源；重复释放幂等。两者均须当前用户持有该房间范围内的 `resources:manage`，服务端取得房间锁后重新校验会话与当前权限，事务内审计并递增资源版本。同一用户同键同内容且仍活动时重放返回原限制；内容不一致或已释放时返回 409。限制按键摘要去重，不储存明文请求键。

限制创建时若与相关座位的活动 M1 预约或整室 M2 占用重叠则返回 409，既有业务状态不变；允许与 M3 及其他 D 限制叠加。释放只改变指定来源，不会移除维修、预约或其他限制。M1 搜索和创建锁内复核都读取同一 D 来源事实；旧座位可用性视图将 D 限制显示为 `DISABLED` 并附 D 来源，不可预约。已有预约与维修后续处置不由本接口决定。

迁移 `0011_admin_restrictions` 增加 D 请求标识登记表并扩展资源来源约束；不修改历史来源、不创建新的座位分配明细。升级支持请求表已建立、旧约束／约束缺失／新约束三种中断状态重跑，约束替换使用单条原子 DDL；存在 D 来源历史时迁移拒绝降级，避免丢失审计和限制记录；正式发布按前向修复处理。新增公共接口和数据库结构、冲突政策均须组内评审，当前没有管理前端。
