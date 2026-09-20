import { useEffect, useMemo, useState } from "react";

/**
 * 主机与目录树。
 *
 * 拖拽语义：
 * - 拖「目录」到另一个目录上  -> 移动目录（拖到自己或自己的子树里会被拦下）
 * - 拖「目录」到根层放置区    -> 移到根层
 * - 拖「主机」到任意目录上    -> 挂接到该目录（原目录挂接保留，实现多目录挂载）
 *
 * 前端先做防环判断并给出视觉反馈，后端 /move 接口会再次校验兜底。
 */

// 构造：目录 id -> 其全部后代 id 集合（含自身）
function buildDescendantMap(nodes, acc = new Map()) {
  for (const node of nodes) {
    const ids = new Set([node.id]);
    buildDescendantMap(node.children || [], acc);
    for (const child of node.children || []) {
      for (const id of acc.get(child.id) || []) ids.add(id);
    }
    acc.set(node.id, ids);
  }
  return acc;
}

function collectDirIds(nodes, acc = []) {
  for (const n of nodes) {
    acc.push(n.id);
    collectDirIds(n.children || [], acc);
  }
  return acc;
}

export default function DirectoryTree({
  tree,
  onAddRoot,
  onAddSubdir,
  onRename,
  onDelete,
  onMoveDirectory,
  onAttachHost,
  onHostClick,
}) {
  const [expanded, setExpanded] = useState(() => new Set(collectDirIds(tree)));
  const [dragPayload, setDragPayload] = useState(null); // {kind,id}
  const [hoverDirId, setHoverDirId] = useState(null);
  const [hoverRoot, setHoverRoot] = useState(false);

  const descendantMap = useMemo(() => buildDescendantMap(tree), [tree]);

  // 数据刷新（新增目录）后，把新目录默认展开
  useEffect(() => {
    const all = collectDirIds(tree);
    setExpanded((prev) => {
      const next = new Set(prev);
      all.forEach((id) => next.add(id));
      return next;
    });
  }, [tree]);

  const toggle = (id) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  /** 目录能否作为拖放目标：拖的是目录时不允许落到自己或后代身上 */
  const canDropOnDir = (dirId) => {
    if (!dragPayload) return false;
    if (dragPayload.kind === "host") {
      return true;
    }
    return !descendantMap.get(dragPayload.id)?.has(dirId);
  };

  const handleDirDragStart = (e, dirId) => {
    const payload = { kind: "directory", id: dirId };
    setDragPayload(payload);
    e.dataTransfer.setData("application/json", JSON.stringify(payload));
    e.dataTransfer.effectAllowed = "move";
  };

  const handleHostDragStart = (e, hostId) => {
    const payload = { kind: "host", id: hostId };
    setDragPayload(payload);
    e.dataTransfer.setData("application/json", JSON.stringify(payload));
    e.dataTransfer.effectAllowed = "link";
  };

  const handleDragEnd = () => {
    setDragPayload(null);
    setHoverDirId(null);
    setHoverRoot(false);
  };

  const handleDropOnDir = async (e, dirId) => {
    e.preventDefault();
    e.stopPropagation();
    let payload = dragPayload;
    if (!payload) {
      try {
        payload = JSON.parse(e.dataTransfer.getData("application/json"));
      } catch {
        payload = null;
      }
    }
    const ok = canDropOnDir(dirId);
    setHoverDirId(null);
    setHoverRoot(false);
    setDragPayload(null);
    if (!payload || !ok) return;

    if (payload.kind === "directory") {
      await onMoveDirectory(payload.id, dirId);
    } else {
      await onAttachHost(payload.id, dirId);
    }
  };

  const handleDropOnRoot = async (e) => {
    e.preventDefault();
    let payload = dragPayload;
    if (!payload) {
      try {
        payload = JSON.parse(e.dataTransfer.getData("application/json"));
      } catch {
        payload = null;
      }
    }
    setHoverRoot(false);
    setDragPayload(null);
    if (payload?.kind === "directory") {
      await onMoveDirectory(payload.id, null);
    }
    // 主机不能挂在“根层”，忽略
  };

  return (
    <div className="panel tree-panel">
      <div className="panel-head">
        目录树
        <button className="btn small" onClick={onAddRoot}>
          ➕ 新建根目录
        </button>
      </div>
      <div className="panel-body">
        <div
          className={`drop-root-hint ${hoverRoot ? "active" : ""}`}
          onDragOver={(e) => {
            if (dragPayload?.kind === "directory") {
              e.preventDefault();
              e.dataTransfer.dropEffect = "move";
              setHoverRoot(true);
            }
          }}
          onDragLeave={() => setHoverRoot(false)}
          onDrop={handleDropOnRoot}
        >
          拖目录到此处 = 移到根层
        </div>

        {tree.length === 0 && (
          <div className="detail-empty">还没有目录，点击右上角新建一个吧</div>
        )}

        {tree.map((node) => (
          <TreeNode
            key={node.id}
            node={node}
            depth={0}
            expanded={expanded}
            toggle={toggle}
            dragPayload={dragPayload}
            hoverDirId={hoverDirId}
            setHoverDirId={setHoverDirId}
            canDropOnDir={canDropOnDir}
            onDirDragStart={handleDirDragStart}
            onHostDragStart={handleHostDragStart}
            onDragEnd={handleDragEnd}
            onDropOnDir={handleDropOnDir}
            onAddSubdir={onAddSubdir}
            onRename={onRename}
            onDelete={onDelete}
            onHostClick={onHostClick}
          />
        ))}
      </div>
    </div>
  );
}

function TreeNode(props) {
  const {
    node,
    expanded,
    toggle,
    dragPayload,
    hoverDirId,
    setHoverDirId,
    canDropOnDir,
    onDirDragStart,
    onHostDragStart,
    onDragEnd,
    onDropOnDir,
    onAddSubdir,
    onRename,
    onDelete,
    onHostClick,
  } = props;

  const isOpen = expanded.has(node.id);
  const isDragging =
    dragPayload?.kind === "directory" && dragPayload.id === node.id;
  const dropAllowed = canDropOnDir(node.id);
  const showHover = hoverDirId === node.id;

  const askRename = () => {
    const name = window.prompt("修改目录名称：", node.name);
    if (name && name.trim() && name.trim() !== node.name) {
      onRename(node.id, name.trim());
    }
  };

  return (
    <div className="tree-node">
      <div
        className={`dir-row ${isOpen ? "open" : ""} ${
          showHover && dropAllowed ? "drop-hover" : ""
        } ${isDragging ? "dragging" : ""}`}
        draggable
        onDragStart={(e) => onDirDragStart(e, node.id)}
        onDragEnd={onDragEnd}
        onDragOver={(e) => {
          if (canDropOnDir(node.id)) {
            e.preventDefault();
            e.dataTransfer.dropEffect =
              dragPayload?.kind === "host" ? "link" : "move";
            setHoverDirId(node.id);
          }
        }}
        onDragLeave={() => setHoverDirId((id) => (id === node.id ? null : id))}
        onDrop={(e) => onDropOnDir(e, node.id)}
        title={
          dragPayload?.kind === "directory" && !dropAllowed
            ? "不能移动到自己或自己的子目录下面"
            : node.name
        }
      >
        <span className={`caret ${isOpen ? "open" : ""}`} onClick={() => toggle(node.id)}>
          ▶
        </span>
        <span className="node-icon">📁</span>
        <span className="node-label">{node.name}</span>
        <span className="node-count">
          {node.hosts?.length ? `${node.hosts.length} 台` : ""}
        </span>
        <span className="row-actions">
          <button title="新建子目录" onClick={() => onAddSubdir(node.id)}>
            ➕
          </button>
          <button title="改名" onClick={askRename}>
            ✏️
          </button>
          <button title="删除" onClick={() => onDelete(node.id, node.name)}>
            🗑️
          </button>
        </span>
      </div>

      {isOpen && (
        <div className="children">
          {(node.children || []).map((child) => (
            <TreeNode key={child.id} {...props} node={child} />
          ))}
          <div className="host-children">
            {(node.hosts || []).map((host) => (
              <HostRow
                key={host.id}
                host={host}
                dragPayload={dragPayload}
                onHostDragStart={onHostDragStart}
                onDragEnd={onDragEnd}
                onHostClick={onHostClick}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function HostRow({ host, dragPayload, onHostDragStart, onDragEnd, onHostClick }) {
  const isDragging = dragPayload?.kind === "host" && dragPayload.id === host.id;
  return (
    <div
      className={`host-row ${isDragging ? "dragging" : ""}`}
      draggable
      onDragStart={(e) => onHostDragStart(e, host.id)}
      onDragEnd={onDragEnd}
      onClick={() => onHostClick(host.id)}
      style={{ cursor: "pointer" }}
      title={`${host.name} · ${host.address}:${host.port}（点击编辑）`}
    >
      <span className={`status-dot ${host.status}`} title={`状态: ${host.status}`} />
      <span className={`os-tag ${host.os_type}`}>
        {host.os_type === "windows" ? "WIN" : "LNX"}
      </span>
      <span className="node-label">{host.name}</span>
      <span className="addr">
        {host.address}:{host.port}
      </span>
    </div>
  );
}
