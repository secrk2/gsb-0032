import { useEffect, useState } from "react";
import { api } from "../api.js";

const STATUS_LABELS = {
  running: "执行中",
  success: "成功",
  failed: "失败",
};

/**
 * 快捷命令执行记录：可按机器、时间区间、状态过滤；
 * 点任意一条可回看该机当时的完整输出。
 */
export default function HistoryPanel({ hosts, onViewOutput }) {
  const [hostId, setHostId] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [status, setStatus] = useState("");
  const [records, setRecords] = useState([]);
  const [loading, setLoading] = useState(false);

  const query = async () => {
    setLoading(true);
    try {
      const params = {
        host_id: hostId,
        status,
        limit: 100,
      };
      if (start) params.start = toIso(start);
      if (end) params.end = toIso(end);
      const res = await api.quickHistory(params);
      setRecords(res.records || []);
    } catch (e) {
      setRecords([]);
    } finally {
      setLoading(false);
    }
  };

  // 首次展开 / 主机变化时自动查一次
  useEffect(() => {
    query();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="panel history-panel">
      <div className="panel-head">执行记录（按机器 / 时间追溯）</div>
      <div className="history-filters">
        <select value={hostId} onChange={(e) => setHostId(e.target.value)}>
          <option value="">全部机器</option>
          {hosts.map((h) => (
            <option key={h.id} value={h.id}>
              {h.name}（{h.address}）
            </option>
          ))}
        </select>
        <label>
          起
          <input
            type="datetime-local"
            value={start}
            onChange={(e) => setStart(e.target.value)}
          />
        </label>
        <label>
          止
          <input
            type="datetime-local"
            value={end}
            onChange={(e) => setEnd(e.target.value)}
          />
        </label>
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">全部状态</option>
          <option value="running">执行中</option>
          <option value="success">成功</option>
          <option value="failed">失败</option>
        </select>
        <button className="btn small primary" onClick={query} disabled={loading}>
          {loading ? "查询中…" : "查询"}
        </button>
        <span className="history-count">共 {records.length} 条</span>
      </div>

      <div className="history-table-wrap">
        <table className="history-table">
          <thead>
            <tr>
              <th style={{ width: 150 }}>时间</th>
              <th style={{ width: 90 }}>操作人</th>
              <th style={{ width: 140 }}>机器</th>
              <th>命令</th>
              <th style={{ width: 90 }}>结果</th>
              <th style={{ width: 80 }}>退出码</th>
              <th style={{ width: 64 }}>输出</th>
            </tr>
          </thead>
          <tbody>
            {records.length === 0 && (
              <tr>
                <td colSpan={7} className="detail-empty">
                  没有符合条件的执行记录
                </td>
              </tr>
            )}
            {records.map((r) => (
              <tr key={r.id} className="history-row">
                <td className="mono">{formatTime(r.created_at)}</td>
                <td>{r.actor}</td>
                <td title={`host_id=${r.host_id}`}>{r.host_name}</td>
                <td className="cmd-cell" title={r.command}>
                  {r.dangerous && <span className="danger-flag" title="命中危险规则，已二次确认">危</span>}
                  <span className="mono">{r.command}</span>
                </td>
                <td>
                  <ResultBadge record={r} />
                </td>
                <td className="mono">
                  {r.exit_code == null ? "—" : r.exit_code}
                </td>
                <td>
                  <button
                    className="btn small"
                    onClick={() => onViewOutput(r)}
                  >
                    回看
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function ResultBadge({ record }) {
  const map = {
    timeout: ["连接超时", "warn"],
    auth: ["认证失败", "danger"],
    config: ["凭据未配置", "warn"],
    connection: ["无法连接", "danger"],
    privilege: ["权限不足", "danger"],
    interrupted: ["已中断", "warn"],
    unknown: ["异常失败", "danger"],
    remote: [`失败·退出码 ${record.exit_code}`, "danger"],
    "": ["失败", "danger"],
  };
  if (record.status === "running")
    return <span className="res-badge running"><span className="spin" />执行中</span>;
  if (record.status === "success")
    return <span className="res-badge ok">成功</span>;
  const [label, cls] = map[record.error_category] || ["失败", "danger"];
  return <span className={`res-badge ${cls}`}>{label}</span>;
}

function toIso(localValue) {
  // datetime-local 的值没有时区，补成本地时间 ISO 字符串给后端
  const d = new Date(localValue);
  const pad = (n) => String(n).padStart(2, "0");
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
    `T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
  );
}

function formatTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const pad = (n) => String(n).padStart(2, "0");
  return (
    `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
  );
}
