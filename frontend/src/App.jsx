import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api.js";
import { useEventSource } from "./useEventSource.js";
import Dashboard from "./components/Dashboard.jsx";
import DirectoryTree from "./components/DirectoryTree.jsx";
import HostEditor from "./components/HostEditor.jsx";
import QuickConsole from "./components/QuickConsole.jsx";

/** 递归更新树里某个主机的状态（SSE 推送时就地打补丁，避免整树闪烁） */
function patchHostStatus(nodes, hostId, status, checkedAt) {
  return nodes.map((node) => ({
    ...node,
    children: patchHostStatus(node.children || [], hostId, status, checkedAt),
    hosts: (node.hosts || []).map((h) =>
      h.id === hostId ? { ...h, status, status_checked_at: checkedAt } : h
    ),
  }));
}

/** 计算目录 id -> 节点 的索引，供主机编辑器的目录多选使用 */
function indexDirectories(nodes, acc = new Map()) {
  for (const n of nodes) {
    acc.set(n.id, n);
    indexDirectories(n.children || [], acc);
  }
  return acc;
}

/** 拍平树里的全部主机（同一主机多目录挂接时按 id 去重） */
function flattenHosts(nodes, acc = new Map()) {
  for (const node of nodes) {
    for (const h of node.hosts || []) {
      if (!acc.has(h.id)) acc.set(h.id, h);
    }
    flattenHosts(node.children || [], acc);
  }
  return [...acc.values()];
}

export default function App() {
  const [view, setView] = useState("dashboard");
  const [tree, setTree] = useState([]);
  const [dashboard, setDashboard] = useState(null);
  const [toasts, setToasts] = useState([]);
  const [connected, setConnected] = useState(false);

  // 主机编辑器：null 关闭；{mode:'create'} 或 {mode:'edit', hostId}
  const [editor, setEditor] = useState(null);

  const toastSeq = useRef(0);
  const pushToast = useCallback((message, kind = "error") => {
    const id = ++toastSeq.current;
    setToasts((ts) => [...ts, { id, message, kind }]);
    setTimeout(() => {
      setToasts((ts) => ts.filter((t) => t.id !== id));
    }, 3800);
  }, []);

  const refreshTree = useCallback(async () => {
    try {
      setTree(await api.tree());
    } catch (e) {
      pushToast(`加载目录树失败：${e.message}`);
    }
  }, [pushToast]);

  const refreshDashboard = useCallback(async () => {
    try {
      setDashboard(await api.dashboard());
    } catch {
      /* 仪表盘静默失败，下次轮询自动恢复 */
    }
  }, []);

  useEffect(() => {
    refreshTree();
    refreshDashboard();
    // 兜底轮询：即便 SSE 断开也能看到最新状态
    const timer = setInterval(() => {
      refreshDashboard();
      refreshTree();
    }, 30000);
    return () => clearInterval(timer);
  }, [refreshTree, refreshDashboard]);

  // 实时事件
  useEventSource(
    useCallback(
      (name, data) => {
        if (name === "hello") {
          setConnected(true);
          return;
        }
        if (name === "host.status") {
          setTree((t) =>
            patchHostStatus(t, data.id, data.status, data.status_checked_at)
          );
          refreshDashboard();
          return;
        }
        if (name === "tree.changed") {
          refreshTree();
          refreshDashboard();
          return;
        }
        if (name === "audit") {
          setDashboard((d) =>
            d
              ? {
                  ...d,
                  recent_audits: [
                    {
                      id: data.id,
                      actor: data.actor,
                      action: data.action,
                      target_type: data.target_type,
                      target_name: data.target_name,
                      detail: data.detail,
                      result: data.result,
                      created_at: data.created_at,
                    },
                    ...(d.recent_audits || []),
                  ].slice(0, 15),
                }
              : d
          );
          refreshDashboard();
          return;
        }
        if (name === "job.finished") {
          refreshDashboard();
        }
      },
      [refreshTree, refreshDashboard]
    )
  );

  const dirIndex = useMemo(() => indexDirectories(tree), [tree]);
  const editingHost =
    editor?.mode === "edit"
      ? findHost(tree, editor.hostId)
      : null;

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="logo">🔑</span>
          执钥
          <small>ZhiYue Ops Platform</small>
        </div>
        <div className="sse-state" title="服务端实时推送连接状态">
          <span className={`sse-dot ${connected ? "" : "offline"}`} />
          {connected ? "实时连接" : "连接中…"}
        </div>
      </header>

      <nav className="sidebar">
        <button
          className={`nav-item ${view === "dashboard" ? "active" : ""}`}
          onClick={() => setView("dashboard")}
        >
          📊 仪表盘
        </button>
        <button
          className={`nav-item ${view === "hosts" ? "active" : ""}`}
          onClick={() => setView("hosts")}
        >
          🗂️ 主机与目录
        </button>
        <button
          className={`nav-item ${view === "quick" ? "active" : ""}`}
          onClick={() => setView("quick")}
        >
          ⚡ 快捷命令
        </button>
      </nav>

      <main className="main">
        {view === "dashboard" && <Dashboard data={dashboard} />}
        {view === "quick" && (
          <QuickConsole
            tree={tree}
            hosts={flattenHosts(tree)}
            pushToast={pushToast}
          />
        )}
        {view === "hosts" && (
          <HostsPage
            tree={tree}
            dirIndex={dirIndex}
            editor={editor}
            editingHost={editingHost}
            setEditor={setEditor}
            refreshTree={refreshTree}
            pushToast={pushToast}
          />
        )}
      </main>

      <div className="toast-stack">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.kind}`}>
            {t.message}
          </div>
        ))}
      </div>
    </div>
  );
}

function findHost(nodes, hostId) {
  for (const node of nodes) {
    const hit = (node.hosts || []).find((h) => h.id === hostId);
    if (hit) return hit;
    const deeper = findHost(node.children || [], hostId);
    if (deeper) return deeper;
  }
  return null;
}

function HostsPage({
  tree,
  dirIndex,
  editor,
  editingHost,
  setEditor,
  refreshTree,
  pushToast,
}) {
  return (
    <>
      <div className="page-head">
        <div>
          <h1>主机与目录</h1>
          <div className="sub">
            无限层级目录 · 一台主机可挂多个目录 · 拖拽移动（拖到自己的子目录会被拦截）
          </div>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="btn" onClick={() => setEditor({ mode: "create" })}>
            ➕ 新增主机
          </button>
        </div>
      </div>

      <div className="tree-layout">
        <DirectoryTree
          tree={tree}
          onAddRoot={() =>
            quickCreateDirectory(null, refreshTree, pushToast)
          }
          onAddSubdir={(dirId) =>
            quickCreateDirectory(dirId, refreshTree, pushToast)
          }
          onRename={(dirId, name) =>
            api
              .renameDirectory(dirId, name)
              .then(refreshTree)
              .catch((e) => pushToast(e.message))
          }
          onDelete={(dirId, name) => {
            if (
              window.confirm(
                `确定删除目录「${name}」吗？\n其所有子目录将一并删除；目录内的主机不会被删除，只摘除挂接关系。`
              )
            ) {
              api
                .deleteDirectory(dirId)
                .then(refreshTree)
                .catch((e) => pushToast(e.message));
            }
          }}
          onMoveDirectory={async (dirId, targetId) => {
            try {
              await api.moveDirectory(dirId, targetId);
              await refreshTree();
              return true;
            } catch (e) {
              pushToast(e.message);
              return false;
            }
          }}
          onAttachHost={async (hostId, dirId) => {
            try {
              await api.attachHost(hostId, dirId);
              pushToast("主机已挂到目标目录（保留原有挂接）", "success");
              await refreshTree();
              return true;
            } catch (e) {
              pushToast(e.message);
              return false;
            }
          }}
          onHostClick={(hostId) => setEditor({ mode: "edit", hostId })}
        />

        {editor ? (
          <HostEditor
            key={editor.mode === "edit" ? `h-${editor.hostId}` : "new"}
            host={editingHost}
            dirIndex={dirIndex}
            onClose={() => setEditor(null)}
            onSaved={async () => {
              await refreshTree();
              setEditor(null);
            }}
            pushToast={pushToast}
          />
        ) : (
          <div className="panel detail-panel">
            <div className="panel-head">主机详情</div>
            <div className="detail-empty">
              点击左侧任意主机查看/编辑连接信息；
              <br />
              也可以把主机拖到其它目录实现多目录挂接。
            </div>
          </div>
        )}
      </div>
    </>
  );
}

async function quickCreateDirectory(parentId, refreshTree, pushToast) {
  const name = window.prompt(
    parentId ? "请输入子目录名称：" : "请输入根目录名称："
  );
  if (name === null) return;
  if (!name.trim()) {
    pushToast("目录名称不能为空");
    return;
  }
  try {
    await api.createDirectory(name.trim(), parentId);
    await refreshTree();
  } catch (e) {
    pushToast(e.message);
  }
}
