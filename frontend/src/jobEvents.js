/**
 * 作业实时事件桥：App 顶层 SSE 回调把 job.output / job.finished 发到这里，
 * 快捷命令页按 jobId 订阅，与组件生命周期解耦。
 *
 * 用浏览器原生 EventTarget；detail 通过 CustomEvent 携带。
 */
const target = new EventTarget();

export const jobEvents = {
  output(data) {
    target.dispatchEvent(new CustomEvent("output", { detail: data }));
  },
  finished(data) {
    target.dispatchEvent(new CustomEvent("finished", { detail: data }));
  },
  onOutput(handler) {
    const fn = (e) => handler(e.detail);
    target.addEventListener("output", fn);
    return () => target.removeEventListener("output", fn);
  },
  onFinished(handler) {
    const fn = (e) => handler(e.detail);
    target.addEventListener("finished", fn);
    return () => target.removeEventListener("finished", fn);
  },
};
