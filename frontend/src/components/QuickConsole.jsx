import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api.js";
import { onSse } from "../events.js";
import QuickHostPicker from "./QuickHostPicker.jsx";
import OutputTerminal from "./OutputTerminal.jsx";
import HistoryPanel, { ResultBadge } from "./HistoryPanel.jsx";

/**
 * 快捷命令：左选机器、上输命令、右看实时输出。
 *
 * 数据流：
 * - POST /quick/run 立即返回 batch + 每台机器一个 job；
 * - quick.output SSE 按块推送（带单调序号 seq），先 GET 一次全量输出
 *   建立基线（块数 chunks），之后只追加 seq 更大的块，不重不漏；
 * - quick.finished SSE 更新每台卡片的成功/失败/异常类别。
 */
export default function QuickConsole({ tree, hosts, pushToast }) {
  const [selected, setSelected] = useState(() => new Set());
  const [command, setCommand] = useState("");
  const [jobs, setJobs] = useState([]); // 当前批次的执行卡片
  const [batchId, setBatchId] = useState(null);
  const [activeJob, setActiveJob] = useState(null);
  const [confirm, setConfirm] = useState(null); // {reasons, hostNames}
  const [showHistory, setShowHistory] = useState(false);
  const [viewing, setViewing] = useState(null); // 历史回看 {record, output}
  const [downloadSig, setDownloadSig] = useState({}); // jobId -> 文件名信号

  const termApis = useRef(new Map()); // jobId -> terminal api
  const hydrated = useRef(new Set());
  const pendingChunks = useRef(new Map()); // 水合完成前到达的块
  const lastSeq = useRef(new Map());
  const jobsRef = useRef([]);
  jobsRef.current = jobs;

  const linuxHosts = useMemo(
    () => hosts.filter((h) => h.os_type !== "windows"),
    [hosts]
  );
  const hostById = useMemo(() => new Map(hosts.map((h) => [h.id, h])), [hosts]);

  const anyRunning = jobs.some((j) => j.status === "running");

  // ---------- 选择 ----------
  const toggleHost = useCallback((hostId) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(hostId)) next.delete(hostId);
      else next.add(hostId);
      return next;
    });
  }, []);

  const toggleDir = useCallback(
    (node) => {
      const ids = collectSubtreeLinuxIds(node);
      setSelected((prev) => {
        const next = new Set(prev);
        const allOn = ids.every((id) => next.has(id));
        if (allOn) ids.forEach((id) => next.delete(id));
        else ids.forEach((id) => next.add(id));
        return next;
      });
    },
    []
  );

  // ---------- 实时输出 / 结束事件 ----------
  useEffect(() => {
    const off1 = onSse("quick.output", (data) => {
      const jobId = data.id;
      const card = jobsRef.current.find((j) => j.id === jobId);
      if (!card) return;
      if (!hydrated.current.has(jobId)) {
        const arr = pendingChunks.current.get(jobId) || [];
        arr.push(data);
        pendingChunks.current.set(jobId, arr);
        return;
      }
      const seq = data.seq || 0;
      if (seq && seq <= (lastSeq.current.get(jobId) || 0)) return;
      if (seq) lastSeq.current.set(jobId, seq);
      termApis.current.get(jobId)?.write(data.chunk || "");
    });

    const off2 = onSse("quick.finished", (data) => {
      const jobId = data.id;
      setJobs((prev) =>
        prev.map((j) =>
          j.id === jobId
            ? {
                ...j,
                status: data.status,
                exit_code: data.exit_code,
                error_category: data.error_category || "",
                error_message: data.error_message || "",
                interrupted: !!data.interrupted,
              }
            : j
        )
      );
    });
    return () => {
      off1();
      off2();
    };
  }, []);

  const hydrateJob = useCallback(async (jobId) => {
    try {
      const res = await api.quickJobOutput(jobId);
      const term = termApis.current.get(jobId);
      if (term) {
        term.reset();
        term.write(res.output || "");
      }
      // res.seq = Redis 缓冲里最大块序号；之后只追加更大的 SSE 块
      // 归档输出已包含全部历史块，水合前缓存的 SSE 块直接丢弃，避免重复
      lastSeq.current.set(jobId, res.archived ? Number.MAX_SAFE_INTEGER : (res.seq || 0));
      hydrated.current.add(jobId);
      if (!res.archived) {
        const pending = pendingChunks.current.get(jobId) || [];
        const base = res.seq || 0;
        pending
          .filter((c) => (c.seq || 0) > base)
          .sort((a, b) => (a.seq || 0) - (b.seq || 0))
          .forEach((c) => {
            if ((c.seq || 0) > (lastSeq.current.get(jobId) || 0)) {
              lastSeq.current.set(jobId, c.seq || 0);
              termApis.current.get(jobId)?.write(c.chunk || "");
            }
          });
      }
      pendingChunks.current.delete(jobId);
    } catch {
      // 拉取失败稍后靠 SSE 继续；不打断使用
      hydrated.current.add(jobId);
    }
  }, []);

  // ---------- 执行 ----------
  const doRun = useCallback(
    async (confirmReason = "") => {
      const hostIds = [...selected].filter((id) => {
        const h = hostById.get(id);
        return h && h.os_type !== "windows";
      });
      if (hostIds.length === 0) {
        pushToast("请至少选择一台 Linux 机器");
        return;
      }
      if (!command.trim()) {
        pushToast("请输入要执行的命令");
        return;
      }
      try {
        const res = await api.quickRun({
          command,
          host_ids: hostIds,
          actor: "admin",
          confirm_reason: confirmReason,
        });
        setConfirm(null);
        startBatch(res, hostIds);
      } catch (e) {
        if (e.status === 400 && e.data?.code === "dangerous_confirm_required") {
          setConfirm({
            reasons: e.data.reasons || [],
            hostNames: hostIds.map((id) => hostById.get(id)?.name).filter(Boolean),
          });
          return;
        }
        if (e.status === 400 && e.data?.code === "windows_blocked") {
          const names = (e.data.blocked_hosts || []).map((b) => b.name).join("、");
          pushToast(`Windows 机器已被拦截（${names}）：该入口仅支持 Linux/SSH`);
          return;
        }
        pushToast(e.message);
      }
    },
    [selected, command, hostById, pushToast]
  );

  const startBatch = (res, hostIds) => {
    // 清理上一批终端引用状态
    hydrated.current = new Set();
    pendingChunks.current = new Map();
    lastSeq.current = new Map();
    setBatchId(res.batch_id);
    const cards = res.jobs.map((j) => ({ ...j }));
    setJobs(cards);
    setActiveJob(cards[0]?.id ?? null);
    pushToast(
      `已向 ${cards.length} 台机器下发命令${res.dangerous ? "（危险命令已确认）" : ""}`,
      "success"
    );
    // 等终端挂载后逐台拉基线
    setTimeout(() => {
      cards.forEach((j) => hydrateJob(j.id));
    }, 60);
  };

  const cancelJob = async (job) => {
    if (!window.confirm(`确定中断「${job.host_name}」上正在执行的命令吗？`)) return;
    try {
      await api.quickCancelJob(job.id);
      pushToast(`已向「${job.host_name}」发送中断信号`, "success");
    } catch (e) {
      pushToast(e.message);
    }
  };

  const cancelAll = async () => {
    if (!batchId || !anyRunning) return;
    if (!window.confirm("确定中断全部仍在执行的机器吗？")) return;
    try {
      const res = await api.quickCancelBatch(batchId);
      pushToast(res.detail, "success");
    } catch (e) {
      pushToast(e.message);
    }
  };

  const copyOutput = async (job) => {
    const text = termApis.current.get(job.id)?.getText() || "";
    try {
      await navigator.clipboard.writeText(text);
      pushToast("完整输出已复制", "success");
    } catch {
      pushToast("复制失败：浏览器未授权剪贴板");
    }
  };

  const downloadOutput = (job) => {
    const stamp = new Date()
      .toISOString()
      .replace(/[:T]/g, "-")
      .slice(0, 19);
    setDownloadSig((s) => ({
      ...s,
      [job.id]: { name: `${job.host_name}-${job.id}-${stamp}.log`, nonce: Date.now() },
    }));
  };

  const viewHistoryOutput = async (record) => {
    try {
      const res = await api.quickJobOutput(record.id);
      setViewing({ record, output: res.output || "" });
    } catch (e) {
      pushToast(e.message);
    }
  };

  const successCount = jobs.filter((j) => j.status === "success").length;
  const failedCount = jobs.filter((j) => j.status === "failed").length;
  const runningCount = jobs.filter((j) => j.status === "running").length;

  return (
    <div className="quick-page">
      <div className="page-head">
        <div>
          <h1>快捷命令</h1>
          <div className="sub">
            左侧勾选 Linux 机器（可按目录批量选），顶部输入命令，右侧实时查看每台输出
          </div>
        </div>
        <button className="btn" onClick={() => setShowHistory((v) => !v)}>
          {showHistory ? "收起执行记录" : "📜 执行记录"}
        </button>
      </div>

      <div className="quick-layout">
        <QuickHostPicker
          tree={tree}
          selected={selected}
          onToggleHost={toggleHost}
          onToggleDir={toggleDir}
        />

        <div className="quick-main">
          <div className="panel quick-command-bar">
            <span className="shell-prompt">$</span>
            <textarea
              className="command-input"
              rows={2}
              placeholder="输入要在所选机器上执行的 Shell 命令，例如：df -h && uptime"
              value={command}
              onChange={(e) => setCommand(e.target.value)}
              onKeyDown={(e) => {
                if ((e.ctrlKey || e.metaKey) && e.key === "Enter") doRun("");
              }}
            />
            <div className="command-actions">
              <span className="selected-hint">
                {selected.size > 0
                  ? `将发往 ${selected.size} 台 Linux 机器`
                  : "尚未选择机器"}
              </span>
              {anyRunning && (
                <button className="btn danger" onClick={cancelAll}>
                  ⏹ 中断全部（{runningCount}）
                </button>
              )}
              <button
                className="btn primary"
                onClick={() => doRun("")}
                disabled={selected.size === 0 || !command.trim() || anyRunning}
                title={anyRunning ? "当前批次仍在执行，可中断后再下发" : "Ctrl+Enter 快捷执行"}
              >
                ▶ 执行
              </button>
            </div>
          </div>

          {jobs.length > 0 && (
            <div className="panel quick-result">
              <div className="result-summary">
                <span className="sum total">共 {jobs.length} 台</span>
                <span className="sum running">
                  {runningCount > 0 && (
                    <>
                      <span className="spin" />执行中 {runningCount}
                    </>
                  )}
                </span>
                <span className="sum ok">成功 {successCount}</span>
                <span className="sum fail">失败 {failedCount}</span>
                <span className="sum-cmd mono" title={command}>
                  {command}
                </span>
              </div>

              <div className="result-tabs">
                {jobs.map((job) => (
                  <button
                    key={job.id}
                    className={`result-tab ${activeJob === job.id ? "active" : ""} ${job.status}`}
                    onClick={() => setActiveJob(job.id)}
                  >
                    <TabStatusIcon job={job} />
                    {job.host_name}
                  </button>
                ))}
              </div>

              {jobs.map((job) => (
                <div
                  key={job.id}
                  className="output-card"
                  style={{ display: activeJob === job.id ? "flex" : "none" }}
                >
                  <div className={`output-card-head status-${job.status}`}>
                    <ResultBadge record={job} />
                    <span className="output-host mono">
                      {job.host_name}
                      {job.exit_code != null && (
                        <span className="exit-code"> · 退出码 {job.exit_code}</span>
                      )}
                    </span>
                    {job.error_message && (
                      <span className="error-msg" title={job.error_message}>
                        {job.error_message}
                      </span>
                    )}
                    <span className="output-tools">
                      {job.status === "running" && (
                        <button
                          className="btn small danger"
                          onClick={() => cancelJob(job)}
                        >
                          ⏹ 中断
                        </button>
                      )}
                      <button className="btn small" onClick={() => copyOutput(job)}>
                        复制
                      </button>
                      <button className="btn small" onClick={() => downloadOutput(job)}>
                        下载
                      </button>
                    </span>
                  </div>
                  <OutputTerminal
                    active={activeJob === job.id}
                    downloadSignal={downloadSig[job.id]}
                    registerApi={(api) => {
                      if (api) termApis.current.set(job.id, api);
                      else termApis.current.delete(job.id);
                    }}
                  />
                </div>
              ))}
            </div>
          )}

          {jobs.length === 0 && (
            <div className="panel quick-empty">
              <div className="detail-empty">
                选择机器并输入命令后点「执行」；
                <br />
                多台机器会各自独立显示输出，成功 / 失败 / 超时 / 认证失败一眼可分。
              </div>
            </div>
          )}
        </div>
      </div>

      {showHistory && (
        <HistoryPanel hosts={linuxHosts} onViewOutput={viewHistoryOutput} />
      )}

      {confirm && (
        <DangerConfirm
          reasons={confirm.reasons}
          hostNames={confirm.hostNames}
          command={command}
          onCancel={() => setConfirm(null)}
          onConfirm={(reason) => doRun(reason)}
        />
      )}

      {viewing && (
        <OutputViewer
          record={viewing.record}
          output={viewing.output}
          onClose={() => setViewing(null)}
        />
      )}
    </div>
  );
}

function TabStatusIcon({ job }) {
  if (job.status === "running") return <span className="spin dark" />;
  if (job.status === "success") return <span className="tab-ico ok">●</span>;
  return <span className="tab-ico fail">●</span>;
}

/** 危险命令二次确认弹层：列原因 + 必填确认原因（留痕） */
function DangerConfirm({ reasons, hostNames, command, onCancel, onConfirm }) {
  const [reason, setReason] = useState("");
  const valid = reason.trim().length >= 2;
  return (
    <div className="modal-mask" onClick={onCancel}>
      <div className="modal danger-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head danger">
          ⚠️ 危险命令二次确认
        </div>
        <div className="modal-body">
          <p className="modal-section-label">即将执行的命令：</p>
          <pre className="danger-cmd mono">{command}</pre>

          <p className="modal-section-label">
            命中 {reasons.length} 条危险规则：
          </p>
          <ul className="danger-reasons">
            {reasons.map((r, i) => (
              <li key={i}>· {r}</li>
            ))}
          </ul>

          <p className="modal-section-label">
            将下发到 {hostNames.length} 台机器：
            <span className="danger-hosts">{hostNames.join("、")}</span>
          </p>

          <label className="modal-section-label" htmlFor="confirm-reason">
            确认原因（必填，会随本次执行一起记录留痕）：
          </label>
          <textarea
            id="confirm-reason"
            className="confirm-reason-input"
            rows={3}
            placeholder="例如：已在预发验证，业务低峰期清理该节点临时日志"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            autoFocus
          />
        </div>
        <div className="modal-foot">
          <button className="btn" onClick={onCancel}>
            取消
          </button>
          <button
            className="btn danger primary-danger"
            disabled={!valid}
            onClick={() => valid && onConfirm(reason.trim())}
            title={valid ? "" : "请先填写确认原因"}
          >
            我已知晓风险，确认执行
          </button>
        </div>
      </div>
    </div>
  );
}

/** 历史输出回看弹层（只读终端） */
function OutputViewer({ record, output, onClose }) {
  const apiHolder = useRef(null);
  useEffect(() => {
    const t = setTimeout(() => apiHolder.current?.write(output), 50);
    return () => clearTimeout(t);
  }, [output]);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(output);
    } catch {
      /* ignore */
    }
  };

  return (
    <div className="modal-mask" onClick={onClose}>
      <div className="modal viewer-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <span>
            输出回看 · {record.host_name} · {new Date(record.created_at).toLocaleString()}
          </span>
          <span className="viewer-head-right">
            <ResultBadge record={record} />
            {record.exit_code != null && (
              <span className="exit-code">退出码 {record.exit_code}</span>
            )}
          </span>
        </div>
        <div className="modal-cmd mono" title={record.command}>
          $ {record.command}
          {record.dangerous && record.confirm_reason && (
            <span className="viewer-confirm">
              ｜危险确认原因：{record.confirm_reason}
            </span>
          )}
        </div>
        <div className="viewer-term">
          <OutputTerminal
            active
            registerApi={(a) => {
              apiHolder.current = a;
            }}
          />
        </div>
        <div className="modal-foot">
          <button className="btn" onClick={copy}>
            复制完整输出
          </button>
          <button className="btn primary" onClick={onClose}>
            关闭
          </button>
        </div>
      </div>
    </div>
  );
}

/** 收集目录子树（含更深层）里的全部 Linux 主机 id */
function collectSubtreeLinuxIds(node) {
  const ids = [];
  const walk = (n) => {
    for (const h of n.hosts || []) {
      if (h.os_type !== "windows") ids.push(h.id);
    }
    (n.children || []).forEach(walk);
  };
  walk(node);
  return ids;
}
