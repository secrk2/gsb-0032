import { useEffect, useRef } from "react";

/**
 * 订阅后端 SSE：/api/events
 * @param {(eventName: string, data: any) => void} onEvent
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
    ];
    const listeners = names.map((name) => {
      const fn = (e) => {
        try {
          handlerRef.current(name, e.data ? JSON.parse(e.data) : {});
        } catch {
          /* ignore malformed frame */
        }
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
