import { useEffect, useMemo, useRef, useState } from "react";
import { AnsiLineBuffer } from "../ansi.js";
import { api } from "../api.js";
import { jobEvents } from "../jobEvents.js";

/**
 * 单台主机的实时输出窗：
 * - SSE 增量喂入 ANSI 解析器，保留颜色；\r 进度条覆盖正确呈现；
 * - 自动滚动贴底（用户上翻时不强制拉回）；
 * - 运行中显示动效与「中断」按钮；成功/失败/中断状态色区分，
 *   失败保留退出码与错误说明；输出完整保留，可下载。
 */
const STATUS_UI = {
  running: { label: "执行中", cls: "running", dot: "●" },
  success: { label: "成功", cls: "success", dot: "✓" },
  failed: { label: "失败", cls: "failed", dot: "✕" },
  aborted: { label: "已中断", cls: "aborted", dot: "■" },
};

export default function TerminalPane({ job, onAbort, pushToast }) {
  const [jobState, setJobState] = useState(job);
  const [version, setVersion] = useState(0); // 触发重渲染
  const [autoscroll, setAutoscroll] = useState(true);
  const bufferRef = useRef(new AnsiLineBuffer());
  const loadedRef = useRef(false);
  const fullTextRef = useRef(""); // 已纳入显示的原始输出，用于与 SSE 增量做去重
  const syncedRef = useRef(false); // 全量与实时流是否已对齐；对齐后原样追加
  const preRef = useRef([]); // 全量 fetch 在途期间到达的 SSE 帧，稍后回放
  const bodyRef = useRef(null);

  /**
   * 全量回放与实时增量可能重叠（fetch 在途时已有 SSE 帧到达）。
   * 找新块前缀与已收文本后缀的最长重叠，只喂真正新增的部分；
   * flip=true 时处理完本块即进入“原样追加”模式（实时流已越过全量快照边界）。
   */
  const reconcile = (chunk, flip) => {
    if (!chunk) {
      if (flip) syncedRef.current = true;
      return;
    }
    if (!syncedRef.current) {
      const full = fullTextRef.current;
      if (!full.endsWith(chunk)) {
        let overlap = 0;
        const max = Math.min(full.length, chunk.length, 65536);
        for (let n = max; n > 0; n -= 1) {
          if (full.endsWith(chunk.slice(0, n))) {
            overlap = n;
            break;
          }
        }
        const delta = chunk.slice(overlap);
        if (delta) {
          fullTextRef.current = full + delta;
          bufferRef.current.feed(delta);
          setVersion((v) => v + 1);
        }
      }
    } else {
      fullTextRef.current += chunk;
      bufferRef.current.feed(chunk);
      setVersion((v) => v + 1);
    }
    if (flip) syncedRef.current = true;
  };

  const resetBuffer = (text) => {
    const fresh = new AnsiLineBuffer();
    fresh.feed(text || "");
    bufferRef.current = fresh;
    fullTextRef.current = text || "";
    syncedRef.current = false;
    setVersion((v) => v + 1);
  };

  // 同一个窗格会被历史作业复用：job.id 变化时重置
  useEffect(() => {
    setJobState(job);
    bufferRef.current = new AnsiLineBuffer();
    fullTextRef.current = "";
    syncedRef.current = false;
    preRef.current = [];
    loadedRef.current = false;
    setVersion((v) => v + 1);
  }, [job.id]); // eslint-disable-line react-hooks/exhaustive-deps

  // 先拉一次完整输出（页面刷新后也能恢复现场），再叠加实时增量
  useEffect(() => {
    let cancelled = false;
    api
      .jobOutput(job.id)
      .then((data) => {
        if (cancelled) return;
        resetBuffer(data.output || "");
        setJobState((prev) => ({
          ...prev,
          status: data.status,
          exit_code: data.exit_code,
          error_kind: data.error_kind,
          error_message: data.error_message,
        }));
        // 回放 fetch 在途期间积压的帧（按发布顺序连续对齐，不切到原样模式）
        const buffered = preRef.current;
        preRef.current = [];
        buffered.forEach((chunk) => reconcile(chunk, false));
        loadedRef.current = true;
      })
      .catch(() => {
        loadedRef.current = true; // 拉取失败也允许实时增量
      });

    const offOut = jobEvents.onOutput((data) => {
      if (data.id !== job.id) return;
      if (!loadedRef.current) {
        preRef.current.push(data.chunk || ""); // 全量在途：先缓存
        return;
      }
      reconcile(data.chunk || "", true); // 首块对齐，其后原样追加
    });
    const offFin = jobEvents.onFinished((data) => {
      if (data.id !== job.id) return;
      setJobState((prev) => ({
        ...prev,
        status: data.status,
        exit_code: data.exit_code,
        error_kind: data.error_kind || prev.error_kind,
        error_message: data.error_message || prev.error_message,
      }));
      // 收尾帧（落库与 SSE 略有先后）：稍后再拉一次完整输出补齐
      setTimeout(() => {
        api.jobOutput(job.id).then((full) => {
          if (!cancelled) resetBuffer(full.output || "");
        }).catch(() => {});
      }, 400);
    });
    return () => {
      cancelled = true;
      offOut();
      offFin();
    };
  }, [job.id]); // eslint-disable-line react-hooks/exhaustive-deps

  // 自动滚动
  useEffect(() => {
    if (autoscroll && bodyRef.current) {
      bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
    }
  }, [version, autoscroll]);

  const segments = useMemo(() => bufferRef.current.toSegments(), [version]); // eslint-disable-line react-hooks/exhaustive-deps
  const plainText = useMemo(() => bufferRef.current.toPlainText(), [version]); // eslint-disable-line react-hooks/exhaustive-deps

  const onScroll = () => {
    const el = bodyRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    setAutoscroll(atBottom);
  };

  const download = () => {
    const blob = new Blob([plainText], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${jobState.host_name || `host-${jobState.host_id}`}-job${jobState.id}.log`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(plainText);
      pushToast("输出已复制", "success");
    } catch {
      pushToast("复制失败：浏览器未授权剪贴板");
    }
  };

  const ui = STATUS_UI[jobState.status] || STATUS_UI.running;
  const running = jobState.status === "running";
  const queued = running && !jobState.started_at;
  const statusLabel = queued ? "排队中" : ui.label;

  return (
    <div className={`term-pane ${ui.cls}`}>
      <div className="term-head">
        <span className={`term-status-dot ${ui.cls}`}>
          {running ? <span className="term-spinner" /> : ui.dot}
        </span>
        <span className="term-host" title={`${jobState.host_name} (#${jobState.host_id})`}>
          {jobState.host_name}
        </span>
        <span className={`term-badge ${ui.cls}`}>{statusLabel}</span>
        {!running && jobState.exit_code !== null && jobState.exit_code !== undefined && (
          <span
            className={`term-exit ${jobState.exit_code === 0 ? "ok" : "bad"}`}
            title="远端进程退出码"
          >
            exit {jobState.exit_code}
          </span>
        )}
        <span className="term-actions">
          <button className="btn small" onClick={copy} title="复制完整输出">
            📋
          </button>
          <button className="btn small" onClick={download} title="下载完整输出（不截断）">
            ⬇
          </button>
          {running && (
            <button
              className="btn small danger"
              onClick={() => onAbort(jobState)}
              title="中断该主机上的命令（先 SIGINT，再强制结束）"
            >
              ■ 中断
            </button>
          )}
        </span>
      </div>

      <div className="term-body" ref={bodyRef} onScroll={onScroll}>
        {segments.map((segs, idx) => (
          <div className="term-line" key={idx}>
            {segs.map((seg, sIdx) =>
              seg.style ? (
                <span key={sIdx} style={seg.style}>
                  {seg.text}
                </span>
              ) : (
                <span key={sIdx}>{seg.text || " "}</span>
              )
            )}
          </div>
        ))}
      </div>

      {!running && jobState.status === "failed" && jobState.error_message && (
        <div className="term-error">
          <strong>连接/执行失败</strong>
          {jobState.error_kind ? `（${ERROR_KIND_LABELS[jobState.error_kind] || jobState.error_kind}）` : ""}
          ：{jobState.error_message}
        </div>
      )}
      {!running && jobState.status === "aborted" && (
        <div className="term-error aborted-note">{jobState.error_message || "命令已被手动中断。"}</div>
      )}
    </div>
  );
}

export const ERROR_KIND_LABELS = {
  timeout: "连接超时",
  auth_failed: "认证失败",
  connection_refused: "连接被拒绝",
  host_unreachable: "主机不可达",
  connection_reset: "连接被重置",
  dns_error: "地址解析失败",
  no_credential: "未配置凭据",
  aborted: "用户中断",
  ssh_error: "SSH 协议错误",
  worker_restart: "执行服务重启",
  error: "其他错误",
};
