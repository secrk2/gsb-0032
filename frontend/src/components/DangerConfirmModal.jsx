import { useState } from "react";

/**
 * 危险命令二次确认弹窗。
 * 必须填写确认原因（≥2 个字）才允许执行；原因会随批次落库并写操作记录。
 */
export default function DangerConfirmModal({ hits, hostCount, command, onCancel, onConfirm }) {
  const [reason, setReason] = useState("");
  const canConfirm = reason.trim().length >= 2;

  return (
    <div className="modal-mask" onClick={onCancel}>
      <div className="modal danger-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head danger">
          ⚠️ 危险命令确认
        </div>
        <div className="modal-body">
          <div className="danger-intro">
            即将在 <strong>{hostCount}</strong> 台 Linux 主机上执行以下命令，
            服务端规则判定它具有破坏性。请确认你清楚后果，
            并填写执行原因（将写入操作记录，事后可审计）。
          </div>

          <div className="danger-hits">
            {hits.map((h) => (
              <div className="danger-hit" key={h.key}>
                <div className="danger-hit-title">
                  🛑 {h.label}
                  <code className="danger-matched">{h.matched}</code>
                </div>
                <div className="danger-hit-advice">{h.advice}</div>
              </div>
            ))}
          </div>

          <pre className="danger-cmd-preview">{command}</pre>

          <label className="danger-reason-label">
            确认执行原因（必填，至少 2 个字）：
            <textarea
              className="danger-reason-input"
              rows={3}
              placeholder="例如：已在预发验证，业务变更窗口 22:00-23:00，值班人张三，回滚方案见变更单 CHG-123"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              autoFocus
            />
          </label>
        </div>
        <div className="modal-actions">
          <button className="btn" onClick={onCancel}>
            取消执行
          </button>
          <button
            className="btn danger"
            disabled={!canConfirm}
            onClick={() => onConfirm(reason.trim())}
            title={canConfirm ? "" : "必须填写确认原因"}
          >
            我已了解风险，确认在 {hostCount} 台主机执行
          </button>
        </div>
      </div>
    </div>
  );
}
