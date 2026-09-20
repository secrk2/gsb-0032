const ACTION_LABELS = {
  login: "登录",
  create: "新建",
  update: "编辑",
  delete: "删除",
  rename: "改名",
  move: "移动",
  attach: "挂接",
  detach: "摘除",
  run: "执行作业",
};

const TARGET_LABELS = {
  host: "主机",
  directory: "目录",
  job: "作业",
  user: "用户",
};

function formatTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(
    d.getMinutes()
  )}:${pad(d.getSeconds())}`;
}

/**
 * 仪表盘：四张 KPI 统计卡 + 最近操作记录。
 * 状态色始终与文字/图标同时出现，不仅靠颜色传达。
 */
export default function Dashboard({ data }) {
  if (!data) {
    return <div className="detail-empty">正在加载仪表盘…</div>;
  }

  const offline = Math.max(0, data.total_hosts - data.online_hosts);

  const cards = [
    {
      key: "online",
      icon: "🟢",
      label: "在线主机",
      value: data.online_hosts,
      extra: `共 ${data.total_hosts} 台 · 离线 ${offline} 台`,
    },
    {
      key: "hosts",
      icon: "🖥️",
      label: "纳管主机总数",
      value: data.total_hosts,
      extra: "Linux + Windows",
    },
    {
      key: "jobs",
      icon: "⚙️",
      label: "今日作业数",
      value: data.jobs_today,
      extra: "按服务器时区统计",
    },
    {
      key: "failed",
      icon: "⚠️",
      label: "今日失败数",
      value: data.jobs_failed_today,
      extra: data.jobs_failed_today > 0 ? "需要关注失败作业" : "今日无失败",
    },
  ];

  return (
    <>
      <div className="page-head">
        <div>
          <h1>仪表盘</h1>
          <div className="sub">
            平台总览 · 数据更新于 {formatTime(data.generated_at)}
          </div>
        </div>
      </div>

      <div className="kpi-grid">
        {cards.map((c) => (
          <div key={c.key} className={`kpi ${c.key}`}>
            <div className="kpi-icon" aria-hidden="true">
              {c.icon}
            </div>
            <div style={{ minWidth: 0 }}>
              <div className="kpi-value">{c.value}</div>
              <div className="kpi-label">{c.label}</div>
              <div className="kpi-extra">{c.extra}</div>
            </div>
          </div>
        ))}
      </div>

      <div className="panel">
        <div className="panel-head">
          最近操作记录
          <span className="section-hint">实时推送，最新 15 条</span>
        </div>
        <div className="panel-body">
          {(data.recent_audits || []).length === 0 && (
            <div className="detail-empty">暂无操作记录</div>
          )}
          {data.recent_audits?.map((a) => (
            <div className="audit-row" key={a.id}>
              <span className="audit-time">{formatTime(a.created_at)}</span>
              <span className="audit-action">
                {ACTION_LABELS[a.action] || a.action} ·{" "}
                {TARGET_LABELS[a.target_type] || a.target_type}
              </span>
              <span className="audit-detail">
                <strong>{a.target_name}</strong>
                {a.detail ? ` — ${a.detail}` : ""}
              </span>
              <span className={`badge ${a.result}`}>
                {a.result === "success" ? "✓ 成功" : a.result === "failed" ? "✕ 失败" : "信息"}
              </span>
            </div>
          ))}
        </div>
      </div>
    </>
  );
}
