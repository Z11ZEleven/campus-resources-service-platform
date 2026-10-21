# M3 报修工单 Implementation Plan

> **For Claude:** Use `${SUPERPOWERS_SKILLS_ROOT}/skills/collaboration/executing-plans/SKILL.md` to implement this plan task-by-task.

**Goal:** 按远端 `dbb7883` 的第一次迭代 M3 设计实现一张工单主表、七个 HTTP 接口及维修资源联动。

**Architecture:** 复用公共身份、权限、单请求事务、资源服务和操作历史。所有写操作按房间锁、工单锁的顺序串行化；模块只 flush，事务提交由公共 Db 依赖负责。通过独立 MySQL `campus_resources_m3_test` 验证行为，再升级本地开发库并重启后端。

**Tech Stack:** Python 3.12、FastAPI、Pydantic、同步 SQLAlchemy 2.0、Alembic、MySQL 8.4、pytest。

---

## 基线与范围

- 依据：`docs/iteration-1/第一次功能迭代-M3报修工单表设计.md`、`docs/public-layer-integration.md`、`AGENTS.md`。
- 本地分支：`feat/m3-work-orders`；基线：`dbb7883`。
- 本轮：文字上报、按身份查询、派单、开始维修、提交完工、通过/驳回验收、历史与幂等。
- 后续：超时升级、评价、通知、附件、转派、撤销、重新打开及跨进程补偿。
- 用户明确保留原完工请求格式；驳回后迟到的旧完工请求可能被当作新提交，该设计限制记录于 `docs/m3-backend.md`，本次不增加完工批次字段。

## Task 1：先建立行为测试与专用数据库

**Files:**
- Create: `backend/tests/test_m3.py`
- Modify: `backend/tests/test_migrations.py`

1. 写 `test_create_work_order_returns_string_ids`，提交有效上报应返回 201 及字符串 ID。
2. 在专用 `_test` 数据库运行，确认现有代码返回 404，使测试因缺少功能失败。
3. 补充完整状态流转、权限、同请求键重放、旧验收轮次、并发和故障回滚测试。
4. 测试用连接仅从本机配置在内存构造；日志隐藏密码。

## Task 2：实现主表、输入输出契约与增量迁移

**Files:**
- Create: `backend/app/modules/m3/__init__.py`
- Create: `backend/app/modules/m3/models.py`
- Create: `backend/app/modules/m3/schemas.py`
- Create: `backend/alembic/versions/0002_m3_work_orders.py`
- Create: `backend/tests/test_m3_schemas.py`
- Modify: `backend/app/models.py`

1. 写非法 ID、文本、UUID、验收批次和驳回原因的输入测试。
2. 实现 `m3_work_orders`，保持设计的字段、五个外键、唯一键、索引与 UTC 毫秒精度。
3. 输入使用禁止额外字段的模型，身份由登录上下文取得；所有输出 ID 为字符串。
4. 运行 schema 测试及 Alembic metadata check；专用库验证 upgrade/downgrade 保留公共层数据。

## Task 3：实现服务和七个 HTTP 接口

**Files:**
- Create: `backend/app/modules/m3/service.py`
- Create: `backend/app/modules/m3/router.py`
- Modify: `backend/app/main.py`

1. 创建按用户与请求 UUID 幂等；相同键不同内容返回 409。跨房间并发由唯一约束兜底，不能生成两条工单。
2. 列表和详情复用同一可见性规则：本人上报、本人分配；物业管理员全量。详情仅在授权后读取 M3 操作历史。
3. 派单检查有效维修人员；开始/完工仅限被分配者；验收拒绝自验收。
4. 开始维修调用公共 `start_maintenance`；完工和驳回保留来源；验收通过调用 `release_source`，只释放当前工单。
5. 每次完工递增轮次，验收先比较预期轮次；状态矛盾请求 409。重复开始/通过按设计返回当前结果，不重复追加来源或日志。
6. 完工说明和每次验收意见写入公共操作历史，保留轮次供追溯。
7. 在专用数据库运行 M3 测试，再运行已有后端测试与 ruff。

## Task 4：审查、文档与本机运行

**Files:**
- Create: `docs/m3-backend.md`
- Modify: `backend/README.md`
- Modify: `README.md`

1. 由独立代理对照设计审查权限、并发、幂等、锁顺序、迁移与测试。
2. 根据具体发现修复，并补充能失败的回归测试。
3. 在专用库临时移除指定维修人员检查，确认关键测试失败，随后恢复并重新通过。
4. 只对本地开发库执行 `python -m alembic upgrade head`；记录 `0002_m3_work_orders (head)`，保留既有用户与资源数据。
5. 重启本机 8001 后端，验证 `/ready`、OpenAPI 的七个接口，以及在开发库事务回滚内的完整流程。
6. 将接口请求/响应、权限、幂等语义、错误码、测试结果与未完成范围写清，变更交给用户查看。

## 验证命令

以下命令在 `backend` 目录、Python 3.12 环境中执行。集成测试须由本机安全运行器设置专用 `TEST_DATABASE_URL`。

```powershell
python -m pytest tests/test_m3_schemas.py -q
python -m pytest --require-mysql -q
python -m ruff check app scripts tests alembic
python -m alembic upgrade head
python -m alembic current
```

测试数据库不可替换为 `campus_resources`。现有 `campus_resources` 的业务数据不用于测试清理。
