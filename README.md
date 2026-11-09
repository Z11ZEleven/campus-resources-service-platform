# campus-resources-service-platform

校园资源服务平台：座位预约、教室审批与物业报修。

本项目归属 2026 年秋季软件工程与过程伦理课程项目第九组。

## 当前状态

第一次功能迭代：已加入公共层 D 的 MySQL 后端、迁移与种子数据、认证与资源 API、模块接入函数、最小 Vue 示例和 CI 检查配置；M3 已提供工单主表与上报、查询、派单、维修、完工和验收后端。M2 已按两份 M2 需求/设计文档接入完整两级审批与独立 D 资源网络服务，见 [M2 实现与运行](docs/m2-implementation.md)。M1、自动部署和生产发布尚未实现。

开始运行请看 [后端启动说明](backend/README.md)、[公共前端](frontend/README.md) 和 [模块接入指南](docs/public-layer-integration.md)。

Windows 新设备可直接按照下方的“Windows 启动教程”运行，更多诊断见 [本地启动说明](docs/local-start.md)。

M3 的接口、权限、状态与测试见 [M3 后端接入说明](docs/m3-backend.md)。

## Windows 启动教程（无需 Docker）

### 1. 准备环境并下载代码

先安装 **Git、原生 Windows Python 3.12、Node.js 22.12 或以上版本、MySQL 8.4**，并启动 MySQL 服务。安装完成后重新打开 PowerShell；暂不支持 MSYS2 Python。

在希望存放项目的目录执行：

```powershell
git clone https://github.com/Z11ZEleven/campus-resources-service-platform.git
cd campus-resources-service-platform
```

本次 M2 统一启动适配合并前，可切换到以下分支体验；合并后直接使用最新 `main`：

```powershell
git switch fix/unified-launcher-m2
```

### 2. 生成本机配置

在仓库根目录执行：

```powershell
.\start.cmd --setup
```

首次没有 `.env` 时，脚本会从示例分别生成随机 JWT 密钥和独立的 `RESOURCE_SERVICE_KEY`，然后退出，提示填写本机信息。完成下一步后再次执行即可。已有数据库密码和有效密钥保留；旧配置中缺失、空或仍是示例占位值的资源服务密钥会在 `--setup` 时自动补齐。

### 3. 创建 MySQL 数据库并填写密码

在 DBeaver 中连接本机 MySQL，使用有建库和用户管理权限的账号执行以下 SQL。先将密码占位值换成自己设置的数据库密码：

```sql
CREATE DATABASE IF NOT EXISTS campus_resources
  CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE USER IF NOT EXISTS 'campus_app'@'localhost'
  IDENTIFIED BY 'REPLACE_WITH_LOCAL_DATABASE_PASSWORD';
GRANT ALL PRIVILEGES ON campus_resources.* TO 'campus_app'@'localhost';
```

如果 `campus_app` 已存在，以上 SQL 保留其原密码；配置时填写实际密码。脚本需要数据库和账号已经存在，后续会自动创建和升级项目表。

打开仓库根目录的 `.env`，填写下列配置：

```dotenv
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_DATABASE=campus_resources
MYSQL_USER=campus_app
MYSQL_PASSWORD=REPLACE_WITH_LOCAL_DATABASE_PASSWORD
SEED_USER_PASSWORD=REPLACE_WITH_LOCAL_DEMO_PASSWORD
```

替换两个密码占位值：`MYSQL_PASSWORD` 必须与数据库账号密码一致，`SEED_USER_PASSWORD` 是首次创建演示账号时使用的密码，至少 12 位。保留脚本生成的 `JWT_SIGNING_KEY` 和 `RESOURCE_SERVICE_KEY`，两者必须不同；资源服务密钥不会发送到浏览器。

如果 `.env` 是自行复制的示例，须另行替换 JWT 密钥占位值，可用以下命令生成随机密钥：

```powershell
py -3.12 -c "import secrets; print(secrets.token_urlsafe(48))"
```

密码仅填写在新设备本地，不要把 `.env` 提交到 Git。新设备使用独立数据库，原设备中的工单和数据不会随代码自动迁移。

### 4. 首次安装、建表并启动

配置完成后，再在仓库根目录执行：

```powershell
.\start.cmd --setup
```

脚本会创建 `backend/.venv`，安装锁定依赖，补齐独立服务密钥，执行 `alembic upgrade head` 和 `scripts.seed_m2`（包含公共层/M3 及 M2 演示数据），再启动独立 D 资源服务、FastAPI 主应用和 Vue。D 服务通过健康、服务密钥鉴权及只读数据库检查后，才启动主应用和前端。迁移会升级 `.env` 指定的数据库；已有重要数据请先备份。重复初始化不会重置已有账号密码、启用停用用户或恢复已撤销的角色/审批范围。

启动成功后访问：

- M3 页面：<http://127.0.0.1:5173/m3/work-orders>
- M2 页面：<http://127.0.0.1:5173/m2/applications>
- 后端接口文档：<http://127.0.0.1:8001/docs>
- 独立 D 资源服务：<http://127.0.0.1:8002/docs>（服务接口需要独立密钥，普通登录 Token 无权调用）

| 演示角色 | 用户名 | 首次创建时的密码 |
| --- | --- | --- |
| 报修人 | `student` | `.env` 中的 `SEED_USER_PASSWORD` |
| 物业管理员 | `property_admin` | 同上 |
| 维修人员 | `repairer` | 同上 |
| 校级一级审批 | `campus_admin` | 同上 |
| 院系一级审批 | `dept_admin_cs` / `dept_admin_ee` | 同上 |
| M2 教室二级审批 | `m2_room_admin` | 同上 |
| 工作人员申请 | `staff` | 同上 |

M2 演示：`student` 或 `staff` 申请 `M2-CAMPUS` → `campus_admin` 一级通过 → `m2_room_admin` 二级通过 → 主应用通过真实 HTTP 请求 D 服务，最终显示“已批准”。申请须至少提前两个北京时间自然日，在开放时段内整点开始、持续 1～4 小时。院系教室 `M2-CS` / `M2-EE` 则由对应院系管理员完成一级审批。

### 5. 日常启动与停止

以后确认 MySQL 服务运行，在仓库根目录执行一条命令即可：

```powershell
.\start.cmd
```

保持终端打开，按 **Ctrl+C** 停止本次启动的主后端、D 资源服务、前端及其子进程，MySQL 服务会继续运行。任一服务启动失败或退出，启动器也会回收其他服务。拉取到依赖或迁移更新后，再执行一次 `--setup`；从旧版启动器升级到 M2 时也需要执行一次。

### 6. 端口和常见检查命令

三个端口必须不同，默认如下：

| 服务 | 默认端口 | 参数 |
| --- | --- | --- |
| 主后端（M2/M3 与公共 HTTP API） | `8001` | `--backend-port` |
| D 资源命令服务 | `8002` | `--resource-port` |
| Vue 前端 | `5173` | `--frontend-port` |

端口被占用时，可分别指定，脚本会同步前端代理和 M2 资源调用地址：

```powershell
.\start.cmd --backend-port 8010 --resource-port 8011 --frontend-port 5180
```

此时页面地址为 `http://127.0.0.1:5180/m3/work-orders`，主接口文档为 `http://127.0.0.1:8010/docs`，D 服务为 `http://127.0.0.1:8011/docs`。统一启动期间，`--resource-port` 对应地址会覆盖子进程中的 `M2_RESOURCE_BASE_URL`，不会改写成员已有 `.env` 或前端代理配置。

```powershell
# 仅检查本地配置、独立密钥、数据库迁移版本和三个端口，不启动服务
.\start.cmd --check

# 安装依赖、迁移和初始化后，仅检查，不启动服务
.\start.cmd --setup --check

# 查看参数说明
.\start.cmd --help
```

连接失败时检查 MySQL 服务、`.env` 中的地址、账号密码和授权；缺少依赖、资源密钥或迁移落后时，确认目标库后重新运行 `--setup`。环境变量优先于配置文件；无效的 `RESOURCE_SERVICE_KEY` 环境变量或与 JWT 相同的密钥需自行修正，启动器不会擅自覆盖。详细故障排查见 [本地启动说明](docs/local-start.md)。

## 业务模块

| 模块 | 范围 |
| --- | --- |
| M1 | 自习座位搜索、预约、签到、提醒、违约记录、历史查询与复用 |
| M2 | 整教室申请、权限门槛、多级审批与超时撤销 |
| M3 | 故障上报、派单、维修、超时升级、验收与评价 |
| D | 身份与权限、空间与座位主数据、通知、操作审计 |

公共层子域负责人及模块分工尚待团队确认，见 [团队分工](docs/team.md)。

## 技术方向

- 团队倾向后端使用 Python。
- 首次迭代实施方案：Vue 3 + TypeScript；FastAPI + SQLAlchemy + Alembic；**MySQL 8.4**；Docker Compose。
- 上述框架、版本及部署拓扑尚待团队评审，见 [架构草案](docs/architecture.md) 和 [技术选型记录](docs/decisions/0001-technology-stack.md)。

## 目录导航

| 路径 | 内容 |
| --- | --- |
| frontend/ | 公共登录、布局与资源查询 Vue 示例 |
| backend/ | 公共层 FastAPI、MySQL 迁移、初始化和测试 |
| deploy/ | 本地 MySQL Compose；自动部署待实现 |
| docs/public-layer-integration.md | 各模块调用公共层的接口与事务约定 |
| docs/database-contract.md | D、M1、M2、M3 统一数据库字段与变更流程 |
| docs/requirements.md | 需求草案、追踪表及待定规则 |
| docs/team.md | 分工与公共层责任表 |
| docs/first-review.md | 第一次讨论课准备清单 |
| docs/risks.md | 风险与伦理问题记录 |
| docs/decisions/ | 架构及公共层归属决策记录 |
| CONTRIBUTING.md | 人员协作与 PR 流程 |
| AGENTS.md | AI 编程代理规则 |
| .github/ | Issue 和 PR 模板 |

## 新成员开始协作

1. 接受仓库协作邀请，使用个人 GitHub 账号，不共用账号。
2. 克隆仓库：

   ```bash
   git clone https://github.com/Z11ZEleven/campus-resources-service-platform.git
   cd campus-resources-service-platform
   ```

3. 阅读 CONTRIBUTING.md、AGENTS.md、需求和分工文档。
4. 认领 Issue，从最新 main 创建短期任务分支。
5. 在 PR 中说明实现、验证结果和已知问题，请另一位成员评审。

环境、启动、迁移、种子及测试命令见 backend/README.md；前端命令见 frontend/README.md。历史完整设计中的 PostgreSQL 内容不作为首次迭代实施规范。

## 第一轮工程目标

先通过团队评审确认规则与技术方案，再建立“前端调用后端并显示数据库中的教室列表”的最小链路，随后完成登录、座位查询和预约。同步建立自动构建、真实行为测试与测试环境部署。

## 发布与安全

- main 只通过人工评审后的 PR 更新；发布版本用 tag 关联提交。
- 分支保护、成员邀请和部署权限需要在 GitHub 设置中另行配置，本文档不代表已启用。
- 不提交真实密码、Token、私钥、学生信息、生产数据库或真实 AI 对话中的敏感内容。
- 若需更改仓库可见性，由仓库所有者确认课程和团队要求后操作。

## 补充规则

### M1：个人预约自习座位
- 预约时段限制在当天8:00-22:00；

### M2：整间教室预约
- 限制一部分教室只供团体预约（暂定，需要根据老师指导改进）

### M3：物业报修工单

### D：公共层
