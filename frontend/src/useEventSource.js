import { useEffect, useRef } from "react";
import { emitSse } from "./events.js";

/**
 * 订阅后端 SSE：/api/events（全应用唯一连接）。
 * 收到的事件转发到全局事件总线 events.js，同时交给 onEvent 回调。
 * @param {(eventName: string, data: any) => void} [onEvent]
 */
export function useEventSource(onEvent) {
  const handlerRef = useRef(onEvent);
  handlerRef.current = onEvent;

  useEffect(() => {
    const es = new EventSource("/api/events");
    const names = [
      "hello",
      "tree.changed",
      "host.status",
      "audit",
      "job.output",
      "job.finished",
      "quick.output",
      "quick.finished",
    ];
    const listeners = names.map((name) => {
      const fn = (e) => {
        let data = {};
        try {
          data = e.data ? JSON.parse(e.data) : {};
        } catch {
          return; // 忽略畸形帧
        }
        emitSse(name, data);
        handlerRef.current?.(name, data);
      };
      es.addEventListener(name, fn);
      return [name, fn];
    });
    return () => {
      listeners.forEach(([name, fn]) => es.removeEventListener(name, fn));
      es.close();
    };
  }, []);
}
