# Windows 新设备启动

此脚本用于本机开发和课程演示，统一启动 FastAPI 主应用、独立 D 资源服务和 Vue；默认主后端 `8001`、D 资源服务 `8002`、前端 `5173`。需要已安装 Git、原生 Windows Python **3.12**、Node.js **22.12+**、MySQL **8.4**，并启动 MySQL 服务。无需 Docker。

## 1. 下载代码

在 PowerShell 中进入你选择的目录后执行：

```powershell
git clone https://github.com/Z11ZEleven/campus-resources-service-platform.git
cd campus-resources-service-platform
```

本脚本合并前，请使用 PR 对应分支：

```powershell
git switch fix/unified-launcher-m2
```

## 2. 第一次配置

在仓库根目录执行：

```powershell
.\start.cmd --setup
```

没有 `.env` 时，脚本会从示例生成文件、随机 JWT 密钥和独立资源服务密钥，然后退出并提示你编辑。已有数据库密码及有效密钥保留。旧配置中缺失、空或仍是占位值的 `RESOURCE_SERVICE_KEY` 在 `--setup` 时自动补齐到其生效的配置文件；不会覆盖有效的自定义值。密码仅在新设备本地填写，不提交 Git；原设备数据不会自动迁移。

在 DBeaver 中用本机 MySQL 管理员账号执行以下 SQL（密码自行替换）：

```sql
CREATE DATABASE IF NOT EXISTS campus_resources
  CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE USER IF NOT EXISTS 'campus_app'@'localhost'
  IDENTIFIED BY 'REPLACE_WITH_LOCAL_DATABASE_PASSWORD';
GRANT ALL PRIVILEGES ON campus_resources.* TO 'campus_app'@'localhost';
```

如果用户已存在，`CREATE USER IF NOT EXISTS` 保留原密码，请在 `.env` 填其实际密码。脚本不会创建 MySQL 用户、修改已有密码或自动安装系统软件。

编辑仓库根目录的 `.env`：

```dotenv
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_DATABASE=campus_resources
MYSQL_USER=campus_app
MYSQL_PASSWORD=REPLACE_WITH_LOCAL_DATABASE_PASSWORD
SEED_USER_PASSWORD=REPLACE_WITH_LOCAL_DEMO_PASSWORD
```

替换两个密码占位值，演示密码至少 12 位。保留自动生成的 `JWT_SIGNING_KEY` 和 `RESOURCE_SERVICE_KEY`，不得使用相同值。如果是自行复制的示例，须自行替换 JWT 密钥为至少 32 字符的随机值；`--setup` 会补齐资源密钥。数据库密码包含特殊字符时，按 dotenv 格式引用/转义；也可通过进程环境变量传入。优先级为进程环境变量 > `backend/.env` > 根目录 `.env`；无效的资源密钥环境变量需自行修正，启动器不会用文件配置掩盖它。

再次运行：

```powershell
.\start.cmd --setup
```

脚本依次创建 `backend/.venv`、安装锁定的 Python 依赖（含 Windows 时区数据）、补齐独立资源密钥、检查数据库连接、执行 `npm ci`、`alembic upgrade head` 和 `scripts.seed_m2`。后者包含公共层/M3 与 M2 演示账号、教室、座位及审批范围。数据库须已存在且用户具有迁移权限；已有重要数据请先备份。重复执行不会重置账号密码、恢复撤销的角色/范围、启用停用用户或重设已有教室类型。

随后按顺序启动：D 资源服务 → 健康检查 → 使用本机服务密钥进行只读鉴权/数据库查询 → 主后端及数据库就绪检查 → Vue。任一步失败会停止已启动的服务，不会仅凭 `/health` 就认定 M2 可审批。普通用户的 Token 不作为服务密钥使用，密钥不会通过 `VITE_*` 变量公开到浏览器。

只初始化并检查、不启动服务：

```powershell
.\start.cmd --setup --check
```

## 3. 日常启动

```powershell
.\start.cmd
```

无需重新安装依赖。从旧版升级到 M2，或拉取依赖/迁移更新后，再执行一次 `--setup`。

- 页面：<http://127.0.0.1:5173/m3/work-orders>
- M2：<http://127.0.0.1:5173/m2/applications>
- 接口文档：<http://127.0.0.1:8001/docs>
- 独立 D 资源服务：<http://127.0.0.1:8002/docs>，服务端接口需要 `RESOURCE_SERVICE_KEY`。
- 演示账号：`student`、`property_admin`、`repairer`；首次创建时密码取自 `SEED_USER_PASSWORD`。

M2 另包含 `staff`、`dept_admin_cs`、`dept_admin_ee`、`campus_admin`、`m2_room_admin`，首次密码同上。用 `student` 或 `staff` 申请 `M2-CAMPUS`，再用 `campus_admin` 一级通过、`m2_room_admin` 二级通过；最终由主应用经真实 HTTP 访问 D 服务确认占用。时段至少提前两个北京时间自然日、整点、开放时间内 1～4 小时。院系教室一级审批使用对应院系账号。

保持终端打开，按 **Ctrl+C** 停止本次启动的三个服务及其子进程；任一服务异常退出也会回收其他服务。不要直接关闭终端窗口。启动器不结束端口上已有的其他程序，不停止 MySQL。

端口冲突时可指定：

```powershell
.\start.cmd --backend-port 8010 --resource-port 8011 --frontend-port 5180
```

三个端口必须不同且可用。前端代理指向主后端，主后端的 `M2_RESOURCE_BASE_URL` 指向资源服务端口，两者均通过子进程环境变量同步。无需改 `frontend/.env.local`，不改写 `.env` 中旧的资源地址；统一启动时以 `--resource-port` 为准。三个服务仅监听 `127.0.0.1`。

## 4. 诊断与限制

```powershell
.\start.cmd --check
.\start.cmd --help
```

- 缺少 Python 3.12 / Node.js：安装后重新打开终端。PowerShell 入口使用 `py -3.12`，其次查找 `python` / `python3.12`，并校验实际版本和原生 Windows 平台；暂不支持 MSYS2 Python。
- `.venv` 来自另一台机器或 Python 版本不符：停止服务，将其移走后运行 `--setup` 重建，不复制原机器的虚拟环境或 `node_modules`。
- MySQL 认证失败/库不存在：检查 `.env`、本机 MySQL 服务、数据库名、账号和授权。
- 服务密钥缺失或占位：运行 `--setup` 补齐；非占位的短值、环境变量中的无效值、与 JWT 相同的值会被拒绝，须自行修正，不会回显秘密。
- D 鉴权失败：确认两个后端使用同一配置，服务密钥独立且至少 32 字符；检查资源端口。统一启动会自动传递正确的本机调用地址。
- 数据库迁移版本落后：确认目标库后运行 `--setup`；若仓库有多个 Alembic head，脚本停止，迁移分支需由维护者处理。
- 安装依赖失败：检查网络和终端中的 pip/npm 错误；脚本会以非零退出码停止，不继续启动。
- 迁移/种子失败：为避免输出配置或密码，启动器隐藏原始 traceback；确认本机配置后，可在 `backend` 中使用 `.venv` 的 Python 手工运行对应命令定位，勿公开含凭据的输出。
- `--check` 检查依赖入口、密钥、数据库连接、迁移版本和三个端口；此模式不启动服务，网络鉴权在正常联合启动时检查，不代表完整业务验收或生产部署验证。

启动器验证（仓库根目录）：

```powershell
python -m unittest discover -s scripts/tests -v
```

Linux/macOS 可使用 Python 3.12 执行 `python3.12 scripts/dev.py --setup` 或 `python3.12 scripts/dev.py`；Windows 是本脚本的主要验证环境。
