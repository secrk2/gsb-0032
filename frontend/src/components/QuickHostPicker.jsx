import { useMemo, useState } from "react";

/**
 * 快捷命令左侧的机器选择树。
 *
 * - 复用「主机与目录」的无限层级树数据；
 * - 勾选目录 = 勾选该目录（含全部子目录）下的所有 Linux 主机，支持半选态；
 * - Windows 机器禁选并说明原因（入口只对 Linux 开放）；
 * - 同一主机挂在多个目录下时按 id 去重，各处勾选状态一致。
 */

function collectHosts(nodes, acc = new Map()) {
  for (const n of nodes) {
    for (const h of n.hosts || []) {
      if (!acc.has(h.id)) acc.set(h.id, h);
    }
    collectHosts(n.children || [], acc);
  }
  return acc;
}

function matchHost(host, keyword) {
  if (!keyword) return true;
  const k = keyword.toLowerCase();
  return (
    host.name.toLowerCase().includes(k) ||
    host.address.toLowerCase().includes(k)
  );
}

/** 目录是否含有可见（匹配搜索）的 Linux 主机 */
function subtreeVisibleLinux(node, keyword) {
  for (const h of node.hosts || []) {
    if (h.os_type !== "windows" && matchHost(h, keyword)) return true;
  }
  return (node.children || []).some((c) => subtreeVisibleLinux(c, keyword));
}

export default function QuickHostPicker({ tree, selected, onToggleHost, onToggleDir }) {
  const [expanded, setExpanded] = useState(() => new Set());
  const [keyword, setKeyword] = useState("");

  const allHosts = useMemo(() => collectHosts(tree), [tree]);

  const toggleExpand = (id) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const selectedLinuxCount = useMemo(() => {
    let n = 0;
    for (const id of selected) {
      const h = allHosts.get(id);
      if (h && h.os_type !== "windows") n += 1;
    }
    return n;
  }, [selected, allHosts]);

  return (
    <div className="panel quick-picker">
      <div className="panel-head">
        选择机器
        <span className="picker-count">已选 {selectedLinuxCount} 台 Linux</span>
      </div>
      <div className="picker-search">
        <input
          type="text"
          placeholder="搜索主机名 / 地址…"
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
        />
      </div>
      <div className="picker-tree">
        {tree.length === 0 && (
          <div className="detail-empty">还没有可选择的机器</div>
        )}
        {tree.map((node) => (
          <PickerNode
            key={node.id}
            node={node}
            depth={0}
            keyword={keyword}
            expanded={expanded}
            toggleExpand={toggleExpand}
            selected={selected}
            onToggleHost={onToggleHost}
            onToggleDir={onToggleDir}
          />
        ))}
        {tree.length > 0 &&
          !tree.some((n) => subtreeVisibleLinux(n, keyword)) && (
            <div className="detail-empty">没有匹配的 Linux 机器</div>
          )}
      </div>
      <div className="picker-foot">
        <span>仅 Linux/SSH 可执行命令</span>
        <span className="win-note">Windows 机器已禁选</span>
      </div>
    </div>
  );
}

function dirCheckState(node, selected) {
  const linuxIds = new Set();
  // 仅按 Linux 主机统计勾选态（Windows 不可选）
  const walk = (n) => {
    for (const h of n.hosts || []) {
      if (h.os_type !== "windows") linuxIds.add(h.id);
    }
    (n.children || []).forEach(walk);
  };
  walk(node);
  if (linuxIds.size === 0) return "disabled";
  let hit = 0;
  for (const id of linuxIds) if (selected.has(id)) hit += 1;
  if (hit === 0) return "none";
  if (hit === linuxIds.size) return "all";
  return "some";
}

function PickerNode({
  node,
  depth,
  keyword,
  expanded,
  toggleExpand,
  selected,
  onToggleHost,
  onToggleDir,
}) {
  const hasVisible = subtreeVisibleLinux(node, keyword);
  // 搜索时自动展开；否则根节点默认展开，其余按 expanded
  const open = keyword ? true : depth === 0 ? true : expanded.has(node.id);
  const state = dirCheckState(node, selected);

  if (keyword && !hasVisible) return null;

  return (
    <div className="picker-node">
      <div className="picker-dir-row" style={{ paddingLeft: 8 + depth * 16 }}>
        <label className="picker-cb">
          <input
            type="checkbox"
            checked={state === "all"}
            ref={(el) => {
              if (el) el.indeterminate = state === "some";
            }}
            disabled={state === "disabled"}
            onChange={() => onToggleDir(node)}
          />
        </label>
        <span
          className={`caret ${open ? "open" : ""}`}
          onClick={() => toggleExpand(node.id)}
        >
          ▶
        </span>
        <span className="node-icon" onClick={() => toggleExpand(node.id)}>
          📁
        </span>
        <span className="node-label" onClick={() => toggleExpand(node.id)}>
          {node.name}
        </span>
      </div>

      {open && (
        <div>
          {(node.children || []).map((child) => (
            <PickerNode
              key={child.id}
              node={child}
              depth={depth + 1}
              keyword={keyword}
              expanded={expanded}
              toggleExpand={toggleExpand}
              selected={selected}
              onToggleHost={onToggleHost}
              onToggleDir={onToggleDir}
            />
          ))}
          {(node.hosts || [])
            .filter((h) => matchHost(h, keyword))
            .map((host) => (
              <PickerHostRow
                key={host.id}
                host={host}
                depth={depth + 1}
                checked={selected.has(host.id)}
                onToggle={() => onToggleHost(host.id)}
              />
            ))}
        </div>
      )}
    </div>
  );
}

function PickerHostRow({ host, depth, checked, onToggle }) {
  const isWindows = host.os_type === "windows";
  const reason = isWindows
    ? `${host.name} 是 Windows 机器（${host.connect_type.toUpperCase()} 通道），快捷命令仅支持 Linux/SSH，无法执行 Shell 命令`
    : "";
  return (
    <div
      className={`picker-host-row ${isWindows ? "windows" : ""}`}
      style={{ paddingLeft: 8 + depth * 16 }}
      title={reason}
    >
      <label className="picker-cb">
        <input
          type="checkbox"
          checked={checked}
          disabled={isWindows}
          onChange={isWindows ? undefined : onToggle}
        />
      </label>
      <span className={`status-dot ${host.status}`} title={`状态: ${host.status}`} />
      <span className={`os-tag ${host.os_type}`}>
        {host.os_type === "windows" ? "WIN" : "LNX"}
      </span>
      <span className="node-label">{host.name}</span>
      <span className="addr">{host.address}</span>
      {isWindows && <span className="win-flag" title={reason}>🚫 仅 Linux</span>}
    </div>
  );
}
