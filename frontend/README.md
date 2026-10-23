# 公共前端与 M3 报修页面

Vue 3 + TypeScript + Vite + Element Plus + Pinia + Axios。已提供登录、公共布局、当前用户、角色守卫、统一请求客户端、教室/座位小时状态查询示例、M3 报修工单页面，以及超时升级展示/补扫、一次性评价和站内通知中心。

## 运行

Windows 一并启动前后端可在仓库根目录运行 `start.cmd`；首次使用先运行 `start.cmd --setup`，按 [本地启动说明](../docs/local-start.md) 配置 MySQL 和 `.env`。以下为单独启动前端的方式。

先按 backend/README.md 启动 MySQL、迁移、种子和后端，再在 frontend 目录：

```bash
npm ci
npm run dev
```

打开终端给出的本地地址，通常是 http://127.0.0.1:5173。Vite 将 /api 转发至 http://127.0.0.1:8000，无需配置后端宽泛跨域。

后端使用其他端口时，复制 `.env.example` 为 `.env.local`，修改代理地址后重启 Vite。例如本机后端运行在 8001：

```dotenv
VITE_API_PROXY_TARGET=http://127.0.0.1:8001
```

此变量是开发服务器的转发目标，不应填写数据库密码或 Token。`.env.local` 已被 Git 忽略。生产部署仍需由 Web 服务器将 `/api` 转发至后端。

```bash
npm run type-check
npm run build
```

已锁定依赖；成员使用 npm ci，不自行更新版本。Node.js 使用 22.12+。

## 集成入口

- src/api/request.ts：唯一 Axios 实例与错误解析。
- src/stores/auth.ts：当前用户、登录/退出；Token 只放内存，刷新后重新登录。
- src/router/index.ts：将模块路由添加到 catch-all 前。
- src/api/resources.ts：公共资源类型与查询。
- src/views/ResourcesView.vue：真实后端数据示例，没有模拟预约按钮。
- src/api/m3.ts、src/utils/m3.ts：M3 接口类型、请求、权限显示与时间/历史格式化。
- src/views/WorkOrdersView.vue、WorkOrderDetailView.vue：工单列表、详情与处理操作。
- src/components/m3/CreateWorkOrderDialog.vue：故障上报和同一次提交的幂等重试。
- src/components/m3/M3ExtensionsPanel.vue：阶段时限、升级历史、一次性评价与结果核对。
- src/api/notifications.ts、src/stores/notifications.ts：真实通知 API、每 30 秒未读数轮询、会话与旧响应隔离。
- src/views/NotificationsView.vue：本人通知、未读筛选、分页、单条/全部已读与安全工单跳转。
- ID 在接口与 TypeScript 中统一为 string；时间输入带时区，展示固定 Asia/Shanghai。

各模块增加自己的 api/m1.ts、views/m1 等，不重新建登录、Pinia 或请求客户端。菜单与按钮可按角色展示，但不能替代后端鉴权。

资源示例页显示前 100 间教室、08:00–22:00 状态；接口支持分页和完整 24 小时。M1/M2 业务页面尚未实现。

M3 入口为 `/m3/work-orders`，详情为 `/m3/work-orders/:id`，通知中心为 `/notifications`。操作按当前角色、归属及状态显示；仅原报修人可在完成后评分一次。升级保持工单状态和维修限制不变，物业管理员可手动补扫；导航自动查询未读数，通知列表需刷新查看新消息。

使用步骤、错误恢复和已知完工请求限制见 [M3 前端说明](../docs/m3-frontend.md)，规则与完整验证见 [超时/评价/通知扩展](../docs/m3-extensions.md)。新增公共 D 通知为本次用户确认的本地实现提案，待团队评审。站内收件箱不包含邮件、微信或桌面推送；附件、细粒度管理范围、撤销/转派/重开、真实模块间网络集成和跨进程可靠补偿仍未实现。

## M2 两级审批

入口 `/m2/applications`、`/m2/approval-tasks`；通知可跳转 `/m2/applications/{id}`。按完整 M2 文档提供所有有效身份申请、两级进度、本人撤回、按范围审批与资源确认状态。不是旧单级示例。运行须同时启动主应用和独立 D 资源服务，见 [M2 实现与运行](../docs/m2-implementation.md)。
