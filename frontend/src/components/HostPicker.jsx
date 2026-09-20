import { useEffect, useMemo, useState } from "react";

/**
 * 快捷命令页左侧主机选择树。
 *
 * - 主机行带复选框，可跨目录一次选多台（同一主机挂多个目录时按 id 去重）；
 * - 目录行的复选框 = 全选/取消该目录子树下全部 **Linux** 主机（半选态表示部分选中）；
 * - Windows 主机不允许勾选：点击时回调 onBlockedWindows，由页面弹出明确原因，
 *   后端也会再次硬拦截，前后端都不会“默默什么都不做”。
 */

function collectHosts(node, acc = new Map()) {
  // 同一主机挂多个目录时按 id 去重，避免全选/半选计数重复
  (node.hosts || []).forEach((h) => acc.set(h.id, h));
  (node.children || []).forEach((c) => collectHosts(c, acc));
  return [...acc.values()];
}

function collectDirIds(nodes, acc = []) {
  for (const n of nodes) {
    acc.push(n.id);
    collectDirIds(n.children || [], acc);
  }
  return acc;
}

export default function HostPicker({ tree, selectedIds, onToggleHost, onToggleSubtree }) {
  const [expanded, setExpanded] = useState(() => new Set(collectDirIds(tree)));

  useEffect(() => {
    // 数据刷新后默认展开新目录
    setExpanded((prev) => {
      const next = new Set(prev);
      collectDirIds(tree).forEach((id) => next.add(id));
      return next;
    });
  }, [tree]);

  const toggleDir = (id) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  return (
    <div className="panel picker-panel">
      <div className="panel-head">
        选择主机
        <span className="section-hint">仅 Linux 可执行命令</span>
      </div>
      <div className="panel-body picker-body">
        {tree.length === 0 && <div className="detail-empty">还没有目录和主机</div>}
        {tree.map((node) => (
          <PickerNode
            key={node.id}
            node={node}
            depth={0}
            expanded={expanded}
            toggleDir={toggleDir}
            selectedIds={selectedIds}
            onToggleHost={onToggleHost}
            onToggleSubtree={onToggleSubtree}
          />
        ))}
      </div>
    </div>
  );
}

function PickerNode({
  node, depth, expanded, toggleDir, selectedIds, onToggleHost, onToggleSubtree,
}) {
  const isOpen = expanded.has(node.id);
  const allHosts = useMemo(() => collectHosts(node), [node]);
  const linuxHosts = useMemo(() => allHosts.filter((h) => h.os_type !== "windows"),
                             [allHosts]);
  const selectedInSubtree = linuxHosts.filter((h) => selectedIds.has(h.id));
  const allChecked = linuxHosts.length > 0 && selectedInSubtree.length === linuxHosts.length;
  const someChecked = selectedInSubtree.length > 0 && !allChecked;

  return (
    <div className="picker-node">
      <div className="picker-dir-row" style={{ paddingLeft: 8 + depth * 16 }}>
        <span className={`caret ${isOpen ? "open" : ""}`} onClick={() => toggleDir(node.id)}>
          ▶
        </span>
        <input
          type="checkbox"
          className="picker-cb dir"
          checked={allChecked}
          ref={(el) => {
            if (el) el.indeterminate = someChecked;
          }}
          disabled={linuxHosts.length === 0}
          onChange={() => onToggleSubtree(linuxHosts.map((h) => h.id), !allChecked)}
          title={
            linuxHosts.length === 0
              ? "该目录下没有 Linux 主机"
              : allChecked
                ? "取消选择该目录下全部 Linux 主机"
                : "选择该目录下全部 Linux 主机（Windows 自动排除）"
          }
        />
        <span className="node-icon">📁</span>
        <span className="node-label">{node.name}</span>
        <span className="node-count">
          {linuxHosts.length > 0 ? `${linuxHosts.length} 台 Linux` : ""}
        </span>
      </div>

      {isOpen && (
        <div>
          {(node.children || []).map((child) => (
            <PickerNode
              key={child.id}
              node={child}
              depth={depth + 1}
              expanded={expanded}
              toggleDir={toggleDir}
              selectedIds={selectedIds}
              onToggleHost={onToggleHost}
              onToggleSubtree={onToggleSubtree}
            />
          ))}
          {(node.hosts || []).map((host) => (
            <HostCheckRow
              key={host.id}
              host={host}
              depth={depth + 1}
              checked={selectedIds.has(host.id)}
              onToggleHost={onToggleHost}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function HostCheckRow({ host, depth, checked, onToggleHost }) {
  const isWindows = host.os_type === "windows";
  return (
    <label
      className={`picker-host-row ${isWindows ? "windows" : ""}`}
      style={{ paddingLeft: 8 + depth * 16 + 18 }}
      title={
        isWindows
          ? `「${host.name}」是 Windows 主机，快捷命令仅支持 Linux/SSH，不可选择`
          : `${host.name} · ${host.address}:${host.port}`
      }
    >
      <input
        type="checkbox"
        className="picker-cb"
        checked={checked}
        disabled={isWindows}
        onChange={() => {
          if (!isWindows) onToggleHost(host.id);
        }}
        onClick={(e) => {
          // label 默认行为在 disabled 时不会触发 change，这里补一个明确拦截提示
          if (isWindows) {
            e.preventDefault();
            onToggleHost(host);
          }
        }}
      />
      <span className={`status-dot ${host.status}`} />
      <span className={`os-tag ${host.os_type}`}>
        {host.os_type === "windows" ? "WIN" : "LNX"}
      </span>
      <span className="node-label">{host.name}</span>
      {isWindows && <span className="win-lock" title="仅支持 RDP 探测">🔒 仅 RDP</span>}
      <span className="addr">{host.address}</span>
    </label>
  );
}
