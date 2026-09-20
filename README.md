# 执钥 · ZhiYue 运维平台

公司散落在各机房的 Linux / Windows 服务器统一纳管平台。

- **后端**：Python · Django + DRF · Linux 经 **asyncssh** 真实登录（PTY 彩色输出）、Windows 经 **RDP** X.224 握手探测
- **实时**：SSE（Redis pub/sub）推送主机状态、树变更、作业输出、操作记录
- **前端**：React 18 + Vite，仪表盘 + 可拖拽的无限层级主机目录树 + 多机快捷命令台
- **存储**：MySQL 8（业务数据/完整作业输出）、Redis 7（事件 / 作业队列 / 中断标记）
- **编排**：docker compose，含 2 台真实 sshd 的 Linux 客户端、1 台真实 RDP（xrdp）客户端和 1 个快捷命令执行进程

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

### 3. 快捷命令（仅 Linux）

左侧导航「⌨️ 快捷命令」进入：

- **左边选机器**：直接在目录树上勾选，支持跨目录多选；目录行复选框可整目录全选 Linux 主机（半选态表示部分选中）。**Windows 主机关联禁用**，点击会明确提示「平台对 Windows 只做 RDP 探测、没有 Shell 通道」；即使绕过前端，后端也会整单拒绝（400）并逐台说明原因，绝不静默执行。
- **上面输命令**：支持多行与常用命令快填，Ctrl+Enter 执行。
- **右边实时输出**：每台主机一个独立终端窗，各自实时滚动；顶部汇总条一眼看清成功/失败/中断各几台，失败保留**退出码**与错误输出，输出可复制/下载，**完整落库不截断**。
- **彩色与中文**：经 PTY（xterm-256color）执行，ANSI 颜色原样保留，前端终端模拟器渲染（含 `\r` 进度条覆盖），UTF-8 中文不乱码。
- **运行状态与中断**：执行中窗格有动效，可单台「中断」或「全部中断」（先向 PTY 发 Ctrl-C/SIGINT，再按回传的 tty 强杀，最后断链兜底）；排队中的作业显示「排队中」。
- **连接错误分得清**：超时、认证失败、连接被拒绝、主机不可达、连接被重置、DNS 解析失败、未配置凭据等分别给出中文原因，而不是一句“连不上”。
- **危险命令二次确认**：`rm -rf /`、`mkfs`、`dd of=/dev/…`、fork 炸弹、关机重启、清防火墙等规则命中后必须确认，且**确认原因必填**（随批次/作业落库并写操作记录）。
- **执行历史**：右上角「执行历史」可按**机器 + 时间区间 + 状态**查询每一条命令（谁、何时、哪些机器、命令、输出、退出码），点开完整回放，危险命令能看到当时的确认原因。

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

也可以在平台对在线的 Linux 主机跑真实作业（多机快捷执行，输出经 SSE 实时推送）：

```bash
# 预检是否命中危险规则
curl -s http://localhost:8129/api/commands/inspect \
  -H 'Content-Type: application/json' \
  -d '{"command":"df -h && uptime"}'

# 派发到一台或多台 Linux 主机（host_ids 可传多台，每台一条独立作业）
curl -s http://localhost:8129/api/commands/run \
  -H 'Content-Type: application/json' \
  -d '{"command":"df -h && hostname","host_ids":[1,2]}'
```

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
| `POST /api/commands/inspect` | 危险命令预检（返回命中规则与原因说明） |
| `POST /api/commands/run` | 多机快捷执行（Windows 整单拦截 400；危险命令 409 待确认，确认原因必填留痕） |
| `GET /api/jobs?host_id=&status=&from=&to=` | 作业历史：按机器/状态/时间区间查询 |
| `POST /api/jobs/{id}/abort` | 中断单机作业（Redis 中断标记，Ctrl-C→强杀→断链） |
| `GET /api/jobs/{id}/output` | 作业完整输出回放（落库，不截断） |
| `GET /api/batches` · `POST /api/batches/{id}/abort` | 命令批次列表 / 整批中断 |
| `POST /api/jobs/run` | 旧版单机同步执行（保留兼容） |
| `GET /api/events` | SSE 实时事件流 |

## 目录结构

```
.
├── docker-compose.yml         # 9 个服务：前端/后端/checker/runner/mysql/redis/2 Linux/1 Windows
├── .env.example
├── backend/                   # Django（zhiyue 项目 + hosts / ops 两个 app）
│   ├── hosts/                 # 目录树模型、树不变量服务、凭据加密、asyncssh/RDP 探测、PTY 作业执行
│   ├── ops/                   # 作业/命令批次、危险命令规则、操作记录、仪表盘、seed_demo 种子命令
│   └── entrypoint.sh          # 等待依赖 → migrate → seed → uvicorn（checker/runner 为独立角色）
├── frontend/                  # React：Dashboard / DirectoryTree / HostEditor / CommandConsole
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
- **快捷命令执行模型**：`POST /api/commands/run` 一次创建一个 `CommandBatch` 与每台主机一条 `Job`，
  作业 id `RPUSH` 到 Redis 队列后立即返回；独立的 **runner** 进程 `BLPOP` 消费、asyncio 并发执行，
  web 进程不被长命令阻塞。输出经 PTY（`xterm-256color`，保留颜色、合并 stderr）按 100ms 泵
  同时推 SSE 与追加落库（`Job.output`，完整不截断；不再使用只留 1000 行的 Redis 环形缓冲）。
  中断靠 `zhiyue:job:{id}:abort` 标记：执行器轮询命中后先向 PTY 发 `Ctrl-C`（SIGINT），
  2s 宽限后用开跑时回传的 tty 名 `pkill -KILL -t` 清整个终端会话，再关闭连接兜底。
  runner 重启会把残留的“执行中”作业标记为「执行服务重启」失败，不会永远挂起。
- **连接错误分类**：`hosts/runner.classify_connect_error` 把 asyncssh/OSError 映射为
  timeout / auth_failed / connection_refused / host_unreachable / connection_reset /
  dns_error / no_credential 等，前端按分类展示明确中文原因。
- **危险命令闸门**：规则集中在 `ops/danger.py`（服务端检测，前端提示不作安全边界），
  命中后未确认返 409；确认必须填原因，原因写入 `CommandBatch/Jobs.confirm_reason` 与审计日志。
- **Linux 专属入口**：Windows 机器在前端复选框禁用 + 后端硬拦截双重把关，
  并把“为什么不能执行”逐台返回给用户。

## 重置数据

```bash
docker compose down -v   # -v 同时清掉 mysql/redis 数据卷
docker compose up -d --build
```
