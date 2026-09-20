import { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import TerminalPane, { ERROR_KIND_LABELS } from "./TerminalPane.jsx";

/**
 * 执行历史：按机器 + 时间区间查询每条命令，点开可回放完整输出、退出码、
 * 错误分类与（危险命令的）确认原因。
 */
function toDateInput(d) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function formatTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(
    d.getMinutes()
  )}:${pad(d.getSeconds())}`;
}

const STATUS_LABELS = { running: "执行中", success: "成功", failed: "失败", aborted: "已中断" };

export default function HistoryPanel({ hosts, onClose, pushToast }) {
  const flatHosts = useMemo(() => {
    const map = new Map();
    const walk = (list) =>
      list.forEach((n) => {
        (n.hosts || []).forEach((h) => {
          if (!map.has(h.id)) map.set(h.id, h);
        });
        walk(n.children || []);
      });
    walk(hosts);
    return [...map.values()].sort((a, b) => a.name.localeCompare(b.name, "zh"));
  }, [hosts]);

  const today = toDateInput(new Date());
  const [hostFilter, setHostFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [from, setFrom] = useState(today);
  const [to, setTo] = useState(today);
  const [jobs, setJobs] = useState([]);
  const [loading, setLoading] = useState(false);
  const [openJob, setOpenJob] = useState(null);

  const load = async () => {
    setLoading(true);
    try {
      const params = { limit: 200 };
      if (hostFilter) params.host_id = hostFilter;
      if (statusFilter) params.status = statusFilter;
      if (from) params.from = `${from}T00:00:00`;
      if (to) params.to = `${to}T23:59:59`;
      setJobs(await api.jobs(params));
    } catch (e) {
      pushToast(`查询历史失败：${e.message}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="history-mask" onClick={onClose}>
      <div className="history-panel" onClick={(e) => e.stopPropagation()}>
        <div className="panel-head">
          执行历史
          <button className="btn small" onClick={onClose}>
            ✕ 关闭
          </button>
        </div>

        <div className="history-filters">
          <label>
            机器
            <select value={hostFilter} onChange={(e) => setHostFilter(e.target.value)}>
              <option value="">全部机器</option>
              {flatHosts.map((h) => (
                <option key={h.id} value={h.id}>
                  {h.name}（{h.os_type === "windows" ? "Windows" : "Linux"}）
                </option>
              ))}
            </select>
          </label>
          <label>
            状态
            <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
              <option value="">全部</option>
              <option value="success">成功</option>
              <option value="failed">失败</option>
              <option value="aborted">已中断</option>
              <option value="running">执行中</option>
            </select>
          </label>
          <label>
            起
            <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
          </label>
          <label>
            止
            <input type="date" value={to} onChange={(e) => setTo(e.target.value)} />
          </label>
          <button className="btn primary small" onClick={load} disabled={loading}>
            {loading ? "查询中…" : "查询"}
          </button>
        </div>

        <div className="history-list">
          {jobs.length === 0 && !loading && (
            <div className="detail-empty">该条件下没有执行记录</div>
          )}
          {jobs.map((j) => (
            <div
              key={j.id}
              className={`history-item ${j.status}`}
              onClick={() => setOpenJob(j)}
            >
              <span className={`hist-badge ${j.status}`}>
                {STATUS_LABELS[j.status] || j.status}
              </span>
              <span className="hist-time">{formatTime(j.created_at)}</span>
              <span className="hist-host">{j.host_name || `#${j.host_id}`}</span>
              <code className="hist-cmd" title={j.command}>
                {j.command}
              </code>
              {j.exit_code !== null && j.exit_code !== undefined && (
                <span className={`hist-exit ${j.exit_code === 0 ? "ok" : "bad"}`}>
                  exit {j.exit_code}
                </span>
              )}
              {j.error_kind && (
                <span className="hist-errkind">{ERROR_KIND_LABELS[j.error_kind] || j.error_kind}</span>
              )}
              {j.dangerous && <span className="hist-danger" title={`确认原因：${j.confirm_reason}`}>⚠ 危险</span>}
            </div>
          ))}
        </div>

        {openJob && (
          <div className="history-replay-mask" onClick={() => setOpenJob(null)}>
            <div className="history-replay" onClick={(e) => e.stopPropagation()}>
              <div className="panel-head">
                <span>
                  {openJob.host_name} · {formatTime(openJob.created_at)} · 作业 #{openJob.id}
                </span>
                <button className="btn small" onClick={() => setOpenJob(null)}>
                  ✕
                </button>
              </div>
              {openJob.dangerous && (
                <div className="replay-danger-note">
                  ⚠ 危险命令，确认原因：{openJob.confirm_reason || "（未记录）"}
                </div>
              )}
              <div className="replay-cmd">
                <span className="section-hint">命令：</span>
                <code>{openJob.command}</code>
              </div>
              <TerminalPane job={openJob} onAbort={() => {}} pushToast={pushToast} />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
