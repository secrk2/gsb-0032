# 执钥 · ZhiYue 运维平台

公司散落在各机房的 Linux / Windows 服务器统一纳管平台。

- **后端**：Python · Django + DRF · Linux 经 **asyncssh** 真实登录、Windows 经 **RDP** X.224 握手探测
- **实时**：SSE（Redis pub/sub）推送主机状态、树变更、作业输出、操作记录
- **前端**：React 18 + Vite，仪表盘 + 可拖拽的无限层级主机目录树
- **存储**：MySQL 8（业务数据）、Redis 7（事件 / 作业输出缓冲）
- **编排**：docker compose，含 2 台真实 sshd 的 Linux 客户端和 1 台真实 RDP（xrdp）客户端

## 一键启动

```bash
docker compose up -d --build
```

首次构建约几分钟（xrdp 镜像较大）。启动完成后打开：

> **http://localhost:8129**

打开即是「仪表盘」，无需任何额外操作即可验证。

## 打开后能验证什么

### 1. 仪表盘

四张 KPI 卡：**在线主机数 / 主机总数 / 今日作业数 / 今日失败数**，下方是**最近操作记录**（随操作实时刷新）。

种子数据：11 台主机、4 个今日作业（其中 1 个失败）、多级目录树。
`checker` 进程每 20s 轮询一轮，约 20~40 秒后：

- `web-prod-01`、`web-prod-02`（连真实 sshd）变为 **在线** 🟢
- `win-rdp-01`（连真实 xrdp）变为 **在线** 🟢
- 其余占位地址的主机显示 **离线** 🔴
- 状态变化通过 SSE **实时推送**，无需刷新页面；顶栏右上角显示「实时连接」

### 2. 主机与目录树

- 目录**无限层级**（种子：生产环境/核心业务/数据库…），目录下既能挂子目录也能挂主机
- **一台主机挂多个目录**：`web-prod-01` 同时挂在「应用集群」和「跳板与巡检」
- **拖拽**：
  - 拖目录到另一个目录 = 移动；拖到顶部「拖目录到此处 = 移到根层」
  - 把目录拖到**它自己的子目录**下 → 前端直接禁止放置（红色提示），后端 `/move` 也会二次校验返回 400，树绝不会断
  - 拖主机到任意目录 = 追加挂接（原有挂接保留）
- 目录行悬浮显示「新建子目录 / 改名 / 删除」；删除目录时子树级联删除、**主机保留**
- 点主机打开右侧编辑面板：地址、端口、操作系统、连接方式、账号；**口令/私钥只写不读**——接口只返回 `has_password` 布尔位，密文（Fernet/AES）绝不离开服务端
- 「立即探测」可对单台主机实时验证在线状态

## 直接连模拟客户端（证明不是假目标）

| 客户端 | 宿主机入口 | 账号 / 口令 |
| --- | --- | --- |
| linux-client-1（真实 sshd） | `ssh ops@127.0.0.1 -p 2221` | `ops` / `Linux@2026` |
| linux-client-2（真实 sshd） | `ssh ops@127.0.0.1 -p 2222` | `ops` / `Linux@2026` |
| windows-client（真实 RDP） | 远程桌面连 `127.0.0.1:3390` | `admin` / `Windows@2026` |

Windows 客户端是 Debian + xrdp + Xfce，用 mstsc / Remmina / FreeRDP 连上后能看到完整图形桌面：

```bash
# FreeRDP 示例
xfreerdp /v:127.0.0.1:3390 /u:admin /p:Windows@2026 /size:1280x800
```

在平台「主机与目录」里对 `win-rdp-01` 点「立即探测」，状态即变为在线（探测发送标准 RDP X.224 Connection Request 并校验 TPKT 响应）。

也可以在平台对在线的 Linux 主机跑真实作业（asyncssh）：

```bash
curl -s http://localhost:8129/api/jobs/run \
  -H 'Content-Type: application/json' \
  -d '{"name":"磁盘巡检","host_id":1,"command":"df -h && hostname"}'
```

### 3. 快捷命令（多机批量执行）

左侧导航进入「⚡ 快捷命令」：

- **左边选机器**：直接在目录树勾选；勾目录 = 选中其下全部 Linux 主机（支持半选态、搜索、多目录去重）。Windows 机器禁选并悬浮说明原因——该入口只对 Linux/SSH 开放；若经接口直接提交带 Windows 主机的请求，后端返回 400 并列明被拦机器，**不会默默跳过任何机器**。
- **上面输命令**：`Ctrl+Enter` 或点执行；同一条命令并发下发给所选机器。
- **右边看输出**：每台机器一个标签页/终端（xterm.js，原生解析 ANSI 颜色、UTF-8 中文），顶部汇总「执行中 / 成功 / 失败」台数；标签颜色和徽标一眼区分成功、失败（带退出码）、连接超时、认证失败、无法连接、凭据未配置、已中断。输出完整不截断（10 万行回滚 + 数据库完整归档），可复制 / 下载 `.log`。
- **执行中可中断**：单机「中断」或「中断全部」；后端向远端发 Ctrl-C，宽限期不退出再 terminate，中断结果、退出码、中断前输出全部保留。
- **危险命令二次确认**：命中 `rm -rf /`、`mkfs`、`dd` 写盘、fork 炸弹、`shutdown/reboot`、`iptables -F`、递归改坏系统目录权限、杀 sshd 等规则时弹窗列出命中原因，必须填写**确认原因**才能执行；确认原因随批次落库，执行记录里可回看。
- **执行记录**：页面下方「执行记录」可按机器、时间区间、状态过滤，点「回看」查看该机当时的完整输出（含危险确认原因）。
- **错误分得清**：连接超时与认证失败分别归类展示，不再是一句笼统的「连不上」。

## 主要接口

| 方法 & 路径 | 说明 |
| --- | --- |
| `GET /api/dashboard` | 仪表盘统计 + 最近操作 |
| `GET /api/tree` | 整棵目录树（嵌套目录 + 每级主机，一次查询拼装） |
| `POST /api/directories` | 新建目录 `{name, parent_id?}` |
| `PATCH /api/directories/{id}` | 改名 |
| `POST /api/directories/{id}/move` | 拖拽移动 `{target_id}`（null=根层，服务端防环） |
| `DELETE /api/directories/{id}` | 删除子树（主机保留） |
| `GET/POST /api/hosts` · `PATCH/DELETE /api/hosts/{id}` | 主机 CRUD（凭据只写不读） |
| `POST /api/hosts/{id}/attach` / `detach` | 多目录挂接 / 摘除 |
| `POST /api/hosts/{id}/check` | 立即在线探测 |
| `POST /api/jobs/run` · `GET /api/jobs/{id}/output` | asyncssh 执行作业 / Redis 输出缓冲 |
| `POST /api/quick/check` | 快捷命令预检（危险原因 + Windows 拦截清单） |
| `POST /api/quick/run` | 多机执行 `{command, host_ids, confirm_reason?}`，202 异步 |
| `POST /api/quick/jobs/{id}/cancel` · `POST /api/quick/batches/{id}/cancel` | 中断单机 / 整批 |
| `GET /api/quick/history` | 执行记录（`host_id`/`start`/`end`/`status` 过滤） |
| `GET /api/quick/jobs/{id}/output` | 单机完整输出（执行中读实时流，结束读归档） |
| `GET /api/events` | SSE 实时事件流（新增 `quick.output` / `quick.finished`） |

## 目录结构

```
.
├── docker-compose.yml         # 8 个服务：前端/后端/checker/mysql/redis/2 Linux/1 Windows
├── .env.example
├── backend/                   # Django（zhiyue 项目 + hosts / ops 两个 app）
│   ├── hosts/                 # 目录树模型、树不变量服务、凭据加密、asyncssh/RDP 探测、作业执行
│   ├── ops/                   # 作业、操作记录、仪表盘、seed_demo 种子命令
│   └── entrypoint.sh          # 等待依赖 → migrate → seed → uvicorn
├── frontend/                  # React：Dashboard / DirectoryTree（拖拽）/ HostEditor
└── clients/
    ├── linux/                 # debian + openssh-server（真实 sshd）
    └── windows/               # debian + xrdp + tigervnc + xfce（真实 RDP）
```

## 关键设计说明

- **树不被写断**：目录自关联 `parent`；所有移动/改名只走 `hosts/services.py` 一个入口。
  移动时用「新父节点是否落在自己的子树 id 集合内」判环，拖到自己或任意深度的子孙下都会被拒；
  `(parent, name)` 上有唯一约束兜底同级重名；删除走外键级联，主机经多对多挂接天然保留。
- **多目录挂接**：`Host ↔ Directory` 是多对多（`DirectoryHost` 带目录内排序），拖拽主机即新增一条关系。
- **凭据安全**：`password` / `private_key` 经 Fernet（AES-128-CBC + HMAC）加密入库，
  序列化器中为 `write_only`，输出仅有 `has_password` / `has_private_key`。密钥由 `CREDENTIAL_KEY` 注入。
- **在线探测**：Linux 有凭据时做真实 asyncssh 登录并执行 `true`；无凭据退化为 SSH 端口探测；
  Windows 向 3389 发送标准 X.224 CR PDU，读到 TPKT 响应才判在线（比裸 TCP 连通更可靠）。
- **实时性**：写操作 / 状态变化 `publish` 到 Redis 频道，uvicorn 上的 SSE 视图订阅广播；
  另有 30s 轮询兜底。
- **快捷命令执行**：
  - 命令在常驻后台事件循环线程上**按机并发**执行（HTTP 接口立即返回 202，结果靠 SSE 回推）；多 uvicorn worker 下取消靠 Redis 标志键跨进程生效。
  - 申请 `xterm-256color` PTY 保留 ANSI 颜色，字节流经增量 UTF-8 解码器，跨包的多字节中文不会乱码；输出 Redis 不截断、结束后完整归档进 `ops.Job.output`，历史回看不依赖 Redis。
  - 连接异常细分 `timeout / auth / connection / config / interrupted / remote`，前端分别呈现。
  - 危险规则（`ops/safety.py`）启发式匹配 shell 子命令，确认原因随 `CommandBatch` 落库并写操作记录。

## 重置数据

```bash
docker compose down -v   # -v 同时清掉 mysql/redis 数据卷
docker compose up -d --build
```
