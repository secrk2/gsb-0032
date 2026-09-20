// 后端接口封装
const BASE = "/api";

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  if (res.status === 204) return null;
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const err = new Error(data?.detail || `请求失败（${res.status}）`);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}

function qs(params = {}) {
  const usp = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") usp.append(k, v);
  });
  const s = usp.toString();
  return s ? `?${s}` : "";
}

export const api = {
  tree: () => request("/tree"),
  hosts: () => request("/hosts"),
  dashboard: () => request("/dashboard"),

  createDirectory: (name, parentId) =>
    request("/directories", {
      method: "POST",
      body: { name, parent_id: parentId ?? null },
    }),
  renameDirectory: (id, name) =>
    request(`/directories/${id}`, { method: "PATCH", body: { name } }),
  moveDirectory: (id, targetId) =>
    request(`/directories/${id}/move`, {
      method: "POST",
      body: { target_id: targetId ?? null },
    }),
  deleteDirectory: (id) => request(`/directories/${id}`, { method: "DELETE" }),

  createHost: (payload) => request("/hosts", { method: "POST", body: payload }),
  updateHost: (id, payload) =>
    request(`/hosts/${id}`, { method: "PATCH", body: payload }),
  deleteHost: (id) => request(`/hosts/${id}`, { method: "DELETE" }),
  attachHost: (hostId, directoryId) =>
    request(`/hosts/${hostId}/attach`, {
      method: "POST",
      body: { directory_id: directoryId },
    }),
  detachHost: (hostId, directoryId) =>
    request(`/hosts/${hostId}/detach`, {
      method: "POST",
      body: { directory_id: directoryId },
    }),
  checkHost: (hostId) =>
    request(`/hosts/${hostId}/check`, { method: "POST" }),

  // ---- 快捷命令 ----
  inspectCommand: (command) =>
    request("/commands/inspect", { method: "POST", body: { command } }),
  runCommand: (payload) =>
    request("/commands/run", { method: "POST", body: payload }),

  jobs: (params = {}) => request(`/jobs${qs(params)}`),
  jobOutput: (id) => request(`/jobs/${id}/output`),
  abortJob: (id) => request(`/jobs/${id}/abort`, { method: "POST" }),

  batches: (params = {}) => request(`/batches${qs(params)}`),
  abortBatch: (id) =>
    request(`/batches/${id}/abort`, { method: "POST" }),
};
