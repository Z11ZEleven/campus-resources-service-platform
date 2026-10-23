# Windows 新设备启动

此脚本用于本机开发和课程演示，启动仓库当前代码中的 FastAPI 和 Vue；默认后端端口 `8001`、前端端口 `5173`。需要已安装 Git、Python **3.12**、Node.js **22.12+**、MySQL **8.4**，并启动 MySQL 服务。无需 Docker。

## 1. 下载代码

在 PowerShell 中进入你选择的目录后执行：

```powershell
git clone https://github.com/Z11ZEleven/campus-resources-service-platform.git
cd campus-resources-service-platform
```

本脚本合并前，请使用 PR 对应分支：

```powershell
git switch feat/windows-dev-launcher
```

## 2. 第一次配置

在仓库根目录执行：

```powershell
.\start.cmd --setup
```

没有 `.env` 时，脚本会从示例生成文件和随机 JWT 密钥，然后暂停并提示你编辑。已有 `.env` 会原样保留。密码仅在新设备本地填写；不要把 `.env` 提交到 Git。新设备需要单独配置数据库，原设备的数据不会自动迁移。

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

替换两个密码占位值，演示密码至少 12 位。保留脚本生成的 `JWT_SIGNING_KEY`；如果是自行复制的示例，须自行生成至少 32 字符的随机密钥。数据库密码包含特殊字符时，按 dotenv 格式引用/转义；也可通过进程环境变量传入，环境变量优先。

再次运行：

```powershell
.\start.cmd --setup
```

脚本依次创建 `backend/.venv`、安装锁定的 Python 依赖（含 Windows 时区数据）和 `npm ci`、检查数据库连接、执行 `alembic upgrade head`、执行幂等演示种子，随后启动前后端。数据库必须存在且用户具有该库的迁移权限。迁移会升级配置中指定的数据库；已有重要数据请先自行备份。重复执行种子不会重置账号密码、恢复撤销的角色或启用停用用户。

只初始化并检查、不启动服务：

```powershell
.\start.cmd --setup --check
```

## 3. 日常启动

```powershell
.\start.cmd
```

无需重新安装依赖。拉取依赖或迁移更新后，再执行一次 `--setup`。

- 页面：<http://127.0.0.1:5173/m3/work-orders>
- 接口文档：<http://127.0.0.1:8001/docs>
- 演示账号：`student`、`property_admin`、`repairer`；首次创建时密码取自 `SEED_USER_PASSWORD`。

保持终端打开，按 **Ctrl+C** 停止本次启动的前后端进程及其子进程。不要直接关闭终端窗口。启动器不会结束端口上已有的其他程序，也不会停止 MySQL 服务。

端口冲突时可指定：

```powershell
.\start.cmd --backend-port 8010 --frontend-port 5180
```

前端代理地址会通过当前子进程的环境变量同步，无需改 `frontend/.env.local`，不会覆盖成员已有配置。仅监听 `127.0.0.1`，用于在新设备本机查看。

## 4. 诊断与限制

```powershell
.\start.cmd --check
.\start.cmd --help
```

- 缺少 Python 3.12 / Node.js：安装后重新打开终端。PowerShell 入口使用 `py -3.12`，其次查找 `python` / `python3.12`，并校验实际版本和原生 Windows 平台；暂不支持 MSYS2 Python。
- `.venv` 来自另一台机器或 Python 版本不符：停止服务，将其移走后运行 `--setup` 重建，不复制原机器的虚拟环境或 `node_modules`。
- MySQL 认证失败/库不存在：检查 `.env`、本机 MySQL 服务、数据库名、账号和授权。
- 数据库迁移版本落后：确认目标库后运行 `--setup`；若仓库有多个 Alembic head，脚本停止，迁移分支需由维护者处理。
- 安装依赖失败：检查网络和终端中的 pip/npm 错误；脚本会以非零退出码停止，不继续启动。
- 迁移/种子失败：为避免输出配置或密码，启动器隐藏原始 traceback；确认本机配置后，可在 `backend` 中使用 `.venv` 的 Python 手工运行对应命令定位，勿公开含凭据的输出。
- `--check` 检查依赖入口、数据库连接、迁移版本和端口可用性；不代表完整业务验收或生产部署验证。

启动器验证（仓库根目录）：

```powershell
python -m unittest discover -s scripts/tests -v
```

Linux/macOS 可使用 Python 3.12 执行 `python3.12 scripts/dev.py --setup` 或 `python3.12 scripts/dev.py`；Windows 是本脚本的主要验证环境。
