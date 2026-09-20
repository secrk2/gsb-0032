import { useEffect, useRef } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { WebLinksAddon } from "@xterm/addon-web-links";
import "@xterm/xterm/css/xterm.css";

/**
 * 单台机器的实时输出终端：
 * - xterm.js 原生解析 ANSI 颜色/光标控制，UTF-8 宽字符（中文）正常显示；
 * - scrollback 给到 10 万行，输出多了也不截断；
 * - write() 对同一终端实例串行写入，避免大输出时块序错乱。
 */
export default function OutputTerminal({ active, downloadSignal, onCopied, registerApi }) {
  const hostRef = useRef(null);
  const termRef = useRef(null);
  const fitRef = useRef(null);
  const queueRef = useRef(Promise.resolve());
  const writtenRef = useRef("");

  useEffect(() => {
    const term = new Terminal({
      fontFamily:
        'ui-monospace, SFMono-Regular, "JetBrains Mono", Consolas, monospace',
      fontSize: 12.5,
      lineHeight: 1.35,
      scrollback: 100000,
      convertEol: false,
      theme: {
        background: "#1b1f27",
        foreground: "#d7dae0",
        cursor: "#e8eaf0",
      },
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.loadAddon(new WebLinksAddon());
    term.open(hostRef.current);
    try {
      fit.fit();
    } catch {
      /* 容器尚未完成布局时忽略，激活时会再 fit */
    }
    termRef.current = term;
    fitRef.current = fit;

    const onResize = () => {
      try {
        fit.fit();
      } catch {
        /* ignore */
      }
    };
    window.addEventListener("resize", onResize);

    const api = {
      reset() {
        term.reset();
        writtenRef.current = "";
        queueRef.current = Promise.resolve();
      },
      write(text) {
        if (!text) return;
        writtenRef.current += text;
        // 串行化：xterm.write 本身是异步的，保证大块输出顺序
        queueRef.current = queueRef.current.then(
          () =>
            new Promise((resolve) => {
              term.write(text, () => resolve());
            })
        );
      },
      getText() {
        return writtenRef.current;
      },
      fit() {
        onResize();
      },
    };
    registerApi?.(api);

    return () => {
      window.removeEventListener("resize", onResize);
      registerApi?.(null);
      term.dispose();
      termRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 选项卡切到本卡时重新适配尺寸
  useEffect(() => {
    if (active) {
      requestAnimationFrame(() => {
        try {
          fitRef.current?.fit();
        } catch {
          /* ignore */
        }
      });
    }
  }, [active]);

  // 下载完整输出（downloadSignal: {name, nonce}，每次点击都是新对象）
  useEffect(() => {
    if (!downloadSignal?.name) return;
    const blob = new Blob([writtenRef.current], {
      type: "text/plain;charset=utf-8",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = downloadSignal.name || "output.log";
    a.click();
    URL.revokeObjectURL(url);
  }, [downloadSignal]);

  return <div ref={hostRef} className="output-xterm" />;
}
