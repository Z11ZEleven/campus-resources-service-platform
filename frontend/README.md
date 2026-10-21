# 公共前端：第一次功能迭代

Vue 3 + TypeScript + Vite + Element Plus + Pinia + Axios。已提供登录、公共布局、当前用户、角色守卫、统一请求客户端、教室/座位小时状态查询示例。

## 运行

先按 backend/README.md 启动 MySQL、迁移、种子和后端，再在 frontend 目录：

```bash
npm ci
npm run dev
```

打开终端给出的本地地址，通常是 http://127.0.0.1:5173。Vite 将 /api 转发至 http://127.0.0.1:8000，无需配置后端宽泛跨域。

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
- ID 在接口与 TypeScript 中统一为 string；时间输入带时区，展示固定 Asia/Shanghai。

各模块增加自己的 api/m1.ts、views/m1 等，不重新建登录、Pinia 或请求客户端。菜单与按钮可按角色展示，但不能替代后端鉴权。

示例页显示前 100 间教室、08:00–22:00 状态；接口支持分页和完整 24 小时。M2 申请/审批入口为 `/m2/applications`，见 [M2 运行与验收](../docs/iteration-1/M2运行与验收.md)；M1/M3 业务页面尚未实现。
