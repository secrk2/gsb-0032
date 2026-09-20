import { useMemo, useRef, useState } from "react";
import { api } from "../api.js";
import HostPicker from "./HostPicker.jsx";
import TerminalPane from "./TerminalPane.jsx";
import DangerConfirmModal from "./DangerConfirmModal.jsx";
import HistoryPanel from "./HistoryPanel.jsx";

/**
 * 快捷命令台：
 *   ┌────────────┬───────────────────────────────┐
 *   │ 左侧主机树  │ 顶部：命令输入 / 执行 / 全选统计 │
 *   │（多选 Linux）│ 右侧：每台主机一个实时输出窗     │
 *   └────────────┴───────────────────────────────┘
 */
const QUICK_PRESETS = [
  "df -h",
  "free -m",
  "uptime",
  "ss -ltnp | head -30",
  "tail -n 50 /var/log/syslog",
  "systemctl status nginx --no-pager",
];

function flattenHosts(nodes, acc = new Map()) {
  for (const n of nodes) {
    (n.hosts || []).forEach((h) => {
      if (!acc.has(h.id)) acc.set(h.id, h);
    });
    flattenHosts(n.children || [], acc);
  }
  return acc;
}

export default function CommandConsole({ tree, pushToast }) {
  const hostIndex = useMemo(() => flattenHosts(tree), [tree]);
  const [selectedIds, setSelectedIds] = useState(() => new Set());
  const [command, setCommand] = useState("");
  const [panes, setPanes] = useState([]); // 最近一次批次的 jobs
  const [submitting, setSubmitting] = useState(false);
  const [danger, setDanger] = useState(null); // {hits,count}
  const [blocked, setBlocked] = useState(null); // Windows 拦截明细
  const [showHistory, setShowHistory] = useState(false);
  const commandRef = useRef(null);

  const selectedHosts = useMemo(
    () => [...selectedIds].map((id) => hostIndex.get(id)).filter(Boolean),
    [selectedIds, hostIndex]
  );
  const runningCount = panes.filter((p) => p.status === "running").length;

  const toggleHost = (hostOrId) => {
    // Windows：参数是 host 对象时给出明确拦截原因（绝不静默）
    if (typeof hostOrId === "object" && hostOrId !== null) {
      const h = hostOrId;
      if (h.os_type === "windows") {
        pushToast(
          `「${h.name}」是 Windows 主机（${h.connect_type.toUpperCase()} 纳管），` +
            "快捷命令只对 Linux/SSH 开放：平台对 Windows 仅做 RDP 在线探测，没有 Shell 通道，无法选择。",
          "error"
        );
        return;
      }
      hostOrId = h.id;
    }
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(hostOrId)) next.delete(hostOrId);
      else next.add(hostOrId);
      return next;
    });
  };

  const toggleSubtree = (linuxIds, checked) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      linuxIds.forEach((id) => (checked ? next.add(id) : next.delete(id)));
      return next;
    });
  };

  const dispatch = async (confirm = false, reason = "") => {
    if (!command.trim()) {
      pushToast("请输入要执行的命令");
      commandRef.current?.focus();
      return;
    }
    if (selectedIds.size === 0) {
      pushToast("请先在左侧至少勾选一台 Linux 主机");
      return;
    }

    setSubmitting(true);
    try {
      const payload = {
        command,
        host_ids: [...selectedIds],
        name: "快捷命令",
      };
      if (confirm) {
        payload.confirm = true;
        payload.confirm_reason = reason;
      }
      const data = await api.runCommand(payload);
      setPanes(data.jobs);
      setDanger(null);
      pushToast(
        `已派发到 ${data.jobs.length} 台主机${data.dangerous ? "（危险命令已确认留痕）" : ""}`,
        "success"
      );
    } catch (e) {
      if (e.status === 409 && e.data?.dangerous) {
        // 危险命令：弹确认框，原因必填
        setDanger({ hits: e.data.hits || [], count: selectedIds.size });
        return;
      }
      if (e.status === 400 && Array.isArray(e.data?.blocked_windows)) {
        setBlocked(e.data.blocked_windows);
        return;
      }
      pushToast(e.message);
    } finally {
      setSubmitting(false);
    }
  };

  const confirmDanger = async (reason) => {
    setSubmitting(true);
    try {
      const data = await api.runCommand({
        command,
        host_ids: [...selectedIds],
        name: "快捷命令",
        confirm: true,
        confirm_reason: reason,
      });
      setPanes(data.jobs);
      setDanger(null);
      pushToast(`危险命令已在 ${data.jobs.length} 台主机执行，确认原因已留痕`, "success");
    } catch (e) {
      if (e.status === 400 && Array.isArray(e.data?.blocked_windows)) {
        setBlocked(e.data.blocked_windows);
        setDanger(null);
        return;
      }
      pushToast(e.message);
    } finally {
      setSubmitting(false);
    }
  };

  const abortJob = async (job) => {
    try {
      await api.abortJob(job.id);
      pushToast(`已向「${job.host_name}」发送中断请求`, "success");
    } catch (e) {
      pushToast(e.message);
    }
  };

  const abortAll = async () => {
    const running = panes.filter((p) => p.status === "running");
    if (running.length === 0) return;
    if (!window.confirm(`确定中断全部 ${running.length} 台主机上正在执行的命令吗？`)) return;
    let failed = 0;
    for (const j of running) {
      try {
        await api.abortJob(j.id);
      } catch {
        failed += 1;
      }
    }
    pushToast(
      failed === 0
        ? `已向 ${running.length} 台主机发送中断请求`
        : `${running.length - failed} 台已请求中断，${failed} 台失败`,
      failed === 0 ? "success" : "error"
    );
  };

  const successCount = panes.filter((p) => p.status === "success").length;
  const failedCount = panes.filter((p) => p.status === "failed").length;
  const abortedCount = panes.filter((p) => p.status === "aborted").length;

  return (
    <div className="console-page">
      <div className="page-head">
        <div>
          <h1>快捷命令</h1>
          <div className="sub">
            左侧勾选一台或多台 Linux 主机，顶部输入命令，右侧实时查看每台机器各自的输出
            · Windows 仅 RDP 纳管，已被禁止选择
          </div>
        </div>
        <button className="btn" onClick={() => setShowHistory(true)}>
          🕘 执行历史
        </button>
      </div>

      <div className="console-layout">
        <HostPicker
          tree={tree}
          selectedIds={selectedIds}
          onToggleHost={toggleHost}
          onToggleSubtree={toggleSubtree}
        />

        <div className="console-main">
          <div className="panel command-panel">
            <div className="panel-body">
              <textarea
                ref={commandRef}
                className="command-input"
                rows={2}
                placeholder="输入要在所选 Linux 主机上执行的 Shell 命令，例如：df -h && uptime（Ctrl+Enter 快速执行）"
                value={command}
                onChange={(e) => setCommand(e.target.value)}
                onKeyDown={(e) => {
                  if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
                    e.preventDefault();
                    dispatch();
                  }
                }}
                spellCheck={false}
              />
              <div className="command-bar">
                <div className="presets">
                  {QUICK_PRESETS.map((p) => (
                    <button
                      key={p}
                      className="preset-chip"
                      onClick={() =>
                        setCommand((c) => (c.trim() ? `${c.replace(/\s+$/, "")}\n${p}` : p))
                      }
                      title="追加到命令框"
                    >
                      {p}
                    </button>
                  ))}
                </div>
                <div className="command-actions">
                  <span className="selected-hint">
                    已选 <strong>{selectedHosts.length}</strong> 台
                    {selectedHosts.length > 0 && (
                      <span className="selected-names">
                        ：{selectedHosts.map((h) => h.name).join("、")}
                      </span>
                    )}
                  </span>
                  {runningCount > 0 && (
                    <button className="btn danger" onClick={abortAll}>
                      ■ 全部中断（{runningCount}）
                    </button>
                  )}
                  <button
                    className="btn primary"
                    onClick={() => dispatch()}
                    disabled={submitting || selectedHosts.length === 0}
                  >
                    {submitting ? "派发中…" : `▶ 在 ${selectedHosts.length || 0} 台主机执行`}
                  </button>
                </div>
              </div>
            </div>
          </div>

          {panes.length === 0 ? (
            <div className="panel console-empty">
              <div className="detail-empty">
                勾选主机、输入命令并执行后，这里会为每台主机各开一个实时输出窗；
                <br />
                成功 / 失败 / 中断状态一眼可辨，失败会保留退出码与错误输出，历史可完整回放。
              </div>
            </div>
          ) : (
            <>
              <div className="batch-summary">
                <span className="sum-item total">共 {panes.length} 台</span>
                <span className="sum-item running">执行中 {runningCount}</span>
                <span className="sum-item success">成功 {successCount}</span>
                <span className="sum-item failed">失败 {failedCount}</span>
                <span className="sum-item aborted">中断 {abortedCount}</span>
              </div>
              <div className={`term-grid ${panes.length === 1 ? "single" : ""}`}>
                {panes.map((job) => (
                  <TerminalPane
                    key={job.id}
                    job={job}
                    onAbort={abortJob}
                    pushToast={pushToast}
                  />
                ))}
              </div>
            </>
          )}
        </div>
      </div>

      {danger && (
        <DangerConfirmModal
          hits={danger.hits}
          hostCount={danger.count}
          command={command}
          onCancel={() => setDanger(null)}
          onConfirm={confirmDanger}
        />
      )}

      {blocked && (
        <div className="modal-mask" onClick={() => setBlocked(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-head danger">🚫 选中的主机包含 Windows，已整单拦下</div>
            <div className="modal-body">
              <div className="danger-intro">
                快捷命令执行<strong>仅支持 Linux/SSH</strong>。以下主机未执行任何命令，
                请取消勾选它们后重新执行：
              </div>
              <div className="danger-hits">
                {blocked.map((b) => (
                  <div className="danger-hit" key={b.host_id}>
                    <div className="danger-hit-title">🖥️ {b.name}</div>
                    <div className="danger-hit-advice">{b.reason}</div>
                  </div>
                ))}
              </div>
            </div>
            <div className="modal-actions">
              <button className="btn primary" onClick={() => setBlocked(null)}>
                我知道了
              </button>
            </div>
          </div>
        </div>
      )}

      {showHistory && (
        <HistoryPanel
          hosts={tree}
          onClose={() => setShowHistory(false)}
          pushToast={pushToast}
        />
      )}
    </div>
  );
}
