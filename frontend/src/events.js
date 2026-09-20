// 全局 SSE 事件总线：App 里只有一条 /api/events 连接，
// 各页面（仪表盘、快捷命令…）从这里订阅自己关心的事件。
const listeners = new Map(); // eventName -> Set<fn>

export function emitSse(name, data) {
  listeners.get(name)?.forEach((fn) => {
    try {
      fn(data);
    } catch (e) {
      console.error("SSE listener error", name, e);
    }
  });
  listeners.get("*")?.forEach((fn) => fn(name, data));
}

export function onSse(name, fn) {
  if (!listeners.has(name)) listeners.set(name, new Set());
  listeners.get(name).add(fn);
  return () => listeners.get(name)?.delete(fn);
}
