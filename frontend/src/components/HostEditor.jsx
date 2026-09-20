import { useMemo, useState } from "react";
import { api } from "../api.js";

/** 生成目录 id -> 完整路径名（用缩进展示层级） */
function useDirectoryPaths(dirIndex) {
  return useMemo(() => {
    const pathOf = (id, seen = new Set()) => {
      const node = dirIndex.get(id);
      if (!node || seen.has(id)) return "";
      seen.add(id);
      const parentPath = node.parent_id ? pathOf(node.parent_id, seen) : "";
      return parentPath ? `${parentPath} / ${node.name}` : node.name;
    };
    return new Map(
      [...dirIndex.keys()].map((id) => [id, pathOf(id)])
    );
  }, [dirIndex]);
}

export default function HostEditor({ host, dirIndex, onClose, onSaved, pushToast }) {
  const isEdit = Boolean(host);
  const paths = useDirectoryPaths(dirIndex);

  const [form, setForm] = useState(() => ({
    name: host?.name || "",
    address: host?.address || "",
    port: host?.port || 22,
    os_type: host?.os_type || "linux",
    connect_type: host?.connect_type || "ssh",
    username: host?.username || "root",
    password: "",
    private_key: "",
    directory_ids: host?.directory_ids ? [...host.directory_ids] : [],
  }));
  const [saving, setSaving] = useState(false);

  const set = (key) => (e) =>
    setForm((f) => ({ ...f, [key]: e.target.value }));

  const changeOs = (osType) =>
    setForm((f) => ({
      ...f,
      os_type: osType,
      connect_type: osType === "windows" ? "rdp" : "ssh",
      port: f.port === 22 || f.port === 3389
        ? osType === "windows" ? 3389 : 22
        : f.port,
    }));

  const toggleDirectory = (id) =>
    setForm((f) => ({
      ...f,
      directory_ids: f.directory_ids.includes(id)
        ? f.directory_ids.filter((x) => x !== id)
        : [...f.directory_ids, id],
    }));

  const submit = async () => {
    if (!form.name.trim()) return pushToast("主机名不能为空");
    if (!form.address.trim()) return pushToast("地址不能为空");
    const payload = {
      ...form,
      port: Number(form.port),
      // 编辑时留空表示不修改；新建时空串即可
      password: form.password,
      private_key: form.private_key,
    };
    setSaving(true);
    try {
      if (isEdit) {
        await api.updateHost(host.id, payload);
        pushToast("主机已更新", "success");
      } else {
        await api.createHost(payload);
        pushToast("主机已纳管", "success");
      }
      await onSaved();
    } catch (e) {
      pushToast(e.message);
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    if (!window.confirm(`确定删除主机「${host.name}」吗？所有目录挂接将一并解除。`)) {
      return;
    }
    try {
      await api.deleteHost(host.id);
      pushToast("主机已删除", "success");
      await onSaved();
    } catch (e) {
      pushToast(e.message);
    }
  };

  const checkNow = async () => {
    try {
      const result = await api.checkHost(host.id);
      pushToast(
        `探测完成：${result.name} 当前 ${
          result.status === "online" ? "在线" : "离线"
        }${result.latency_ms != null ? `（${result.latency_ms} ms）` : ""}`,
        result.status === "online" ? "success" : "error"
      );
    } catch (e) {
      pushToast(e.message);
    }
  };

  return (
    <div className="panel detail-panel">
      <div className="panel-head">
        {isEdit ? `编辑主机 · ${host.name}` : "新增主机"}
        <button className="btn small" onClick={onClose}>
          ✕ 关闭
        </button>
      </div>
      <div className="panel-body">
        <div className="form-grid">
          <div className="form-field">
            <label>主机名 *</label>
            <input value={form.name} onChange={set("name")} placeholder="web-prod-01" />
          </div>
          <div className="form-field">
            <label>登录账号</label>
            <input value={form.username} onChange={set("username")} />
          </div>
          <div className="form-field">
            <label>地址 *</label>
            <input value={form.address} onChange={set("address")} placeholder="IP 或主机名" />
          </div>
          <div className="form-field">
            <label>端口</label>
            <input type="number" min="1" max="65535" value={form.port}
              onChange={set("port")} />
          </div>
          <div className="form-field">
            <label>操作系统</label>
            <select value={form.os_type} onChange={(e) => changeOs(e.target.value)}>
              <option value="linux">Linux（SSH）</option>
              <option value="windows">Windows（RDP）</option>
            </select>
          </div>
          <div className="form-field">
            <label>连接方式</label>
            <select value={form.connect_type} onChange={set("connect_type")}>
              {form.os_type === "linux" ? (
                <option value="ssh">SSH</option>
              ) : (
                <option value="rdp">RDP</option>
              )}
            </select>
          </div>

          <div className="form-field full">
            <label>
              登录口令（AES/Fernet 加密存储）
              {isEdit && host.has_password ? " · 已配置" : ""}
            </label>
            <input
              type="password"
              value={form.password}
              onChange={set("password")}
              autoComplete="new-password"
              placeholder={
                isEdit
                  ? host.has_password
                    ? "已保存口令，留空则保持不变"
                    : "尚未配置，输入后保存"
                  : "可选；Linux 也可改用私钥"
              }
            />
            <div className="cred-note">
              🔐 凭据在服务端加密入库，接口只回传“是否已配置”，任何情况下都不会把明文返回浏览器。
            </div>
          </div>

          {form.os_type === "linux" && (
            <div className="form-field full">
              <label>
                SSH 私钥（可选，PEM 文本）
                {isEdit && host.has_private_key ? " · 已配置" : ""}
              </label>
              <textarea
                rows={3}
                style={{
                  fontFamily: "var(--mono)",
                  fontSize: 12,
                  padding: "7px 10px",
                  border: "1px solid var(--border-strong)",
                  borderRadius: "var(--radius-sm)",
                }}
                value={form.private_key}
                onChange={set("private_key")}
                placeholder="-----BEGIN OPENSSH PRIVATE KEY-----&#10;..."
              />
            </div>
          )}

          <div className="form-field full">
            <label>挂载目录（可多选 —— 一台主机允许同时挂在多个目录下）</label>
            <div
              style={{
                border: "1px solid var(--border-strong)",
                borderRadius: "var(--radius-sm)",
                maxHeight: 168,
                overflow: "auto",
                padding: "6px 8px",
                background: "#fff",
              }}
            >
              {[...dirIndex.keys()].length === 0 && (
                <span style={{ color: "var(--text-muted)", fontSize: 12 }}>
                  暂无目录，请先在左侧创建
                </span>
              )}
              {[...dirIndex.keys()].map((id) => (
                <label
                  key={id}
                  style={{
                    display: "flex",
                    gap: 7,
                    alignItems: "center",
                    padding: "3px 0",
                    fontSize: 13,
                    cursor: "pointer",
                  }}
                >
                  <input
                    type="checkbox"
                    checked={form.directory_ids.includes(id)}
                    onChange={() => toggleDirectory(id)}
                  />
                  <span>{paths.get(id)}</span>
                </label>
              ))}
            </div>
          </div>
        </div>

        <div className="form-actions">
          {isEdit && (
            <>
              <button className="btn" onClick={checkNow}>
                🔌 立即探测
              </button>
              <button className="btn danger" onClick={remove}>
                删除主机
              </button>
            </>
          )}
          <button className="btn" onClick={onClose}>
            取消
          </button>
          <button className="btn primary" disabled={saving} onClick={submit}>
            {saving ? "保存中…" : isEdit ? "保存修改" : "纳管主机"}
          </button>
        </div>
      </div>
    </div>
  );
}
