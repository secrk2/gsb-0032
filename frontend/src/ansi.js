/**
 * 极简终端模拟器：解析 ANSI/VT 序列，输出按行分组的样式片段。
 *
 * 支持：
 * - SGR：常规/加粗/暗淡/斜体/下划线/反色/删除线、16 色 + 亮体前景背景、39/49 复位；
 * - OSC（窗口标题等，含平台自注入的 tty 标记）整段丢弃，不显示给用户；
 * - \r 回车覆盖（进度条类输出不会刷出大量重复行）、\b、\t、BEL 忽略；
 * - UTF-8 在 JS 层已是字符串，中文按整字符占一格写入，不会乱码或截断。
 *
 * 设计为增量喂入（feed），适合实时流；历史回放一次性 feed 整段即可。
 */

// xterm 标准 16 色调色板
const COLORS = {
  black: "#2e3436",
  red: "#cc0000",
  green: "#4e9a06",
  yellow: "#c4a000",
  blue: "#3465a4",
  magenta: "#75507b",
  cyan: "#06989a",
  white: "#d3d7cf",
  brightBlack: "#555753",
  brightRed: "#ef2929",
  brightGreen: "#8ae234",
  brightYellow: "#fce94f",
  brightBlue: "#729fcf",
  brightMagenta: "#ad7fa8",
  brightCyan: "#34e2e2",
  brightWhite: "#eeeeec",
};

const FG = [
  COLORS.black, COLORS.red, COLORS.green, COLORS.yellow,
  COLORS.blue, COLORS.magenta, COLORS.cyan, COLORS.white,
];
const BG = FG.slice();
const BRIGHT_FG = [
  COLORS.brightBlack, COLORS.brightRed, COLORS.brightGreen, COLORS.brightYellow,
  COLORS.brightBlue, COLORS.brightMagenta, COLORS.brightCyan, COLORS.brightWhite,
];
const BRIGHT_BG = BRIGHT_FG.slice();

const DEFAULT_FG = "#d6d9df";

function makeStyle(s) {
  // s: {fg,bg,bold,dim,italic,underline,strike,inverse}
  let fg = s.fg || DEFAULT_FG;
  let bg = s.bg || "transparent";
  // 终端惯例：加粗的标准 30-37 色显示为亮色变体（如 ls 目录的亮蓝）
  if (s.bold) {
    const stdIdx = FG.indexOf(fg);
    if (stdIdx >= 0) fg = BRIGHT_FG[stdIdx];
  }
  if (s.dim && !s.fg) fg = "#9aa0aa";
  if (s.inverse) {
    const tmp = fg;
    fg = bg === "transparent" ? "#1e1f24" : bg;
    bg = tmp === DEFAULT_FG ? "#d6d9df" : tmp;
  }
  const css = {
    color: fg,
    background: bg,
    fontWeight: s.bold ? 700 : 400,
    fontStyle: s.italic ? "italic" : "normal",
    textDecoration: [
      s.underline ? "underline" : "",
      s.strike ? "line-through" : "",
    ]
      .filter(Boolean)
      .join(" ") || "none",
  };
  return css;
}

export class AnsiLineBuffer {
  constructor() {
    // 每行一个字符数组：{ c: 字符, k: 样式 key }
    this.lines = [[]];
    this._x = 0;
    this._style = {
      fg: null, bg: null, bold: false, dim: false, italic: false,
      underline: false, strike: false, inverse: false,
    };
    this._styleKey = "";
    this._styleCache = new Map();
    this._escHold = ""; // 帧尾未闭合的转义序列，拼到下一帧开头
  }

  _curLine() {
    return this.lines[this.lines.length - 1];
  }

  _newline() {
    this.lines.push([]);
    this._x = 0;
  }

  _put(ch) {
    const line = this._curLine();
    if (this._x < line.length) {
      line[this._x] = { c: ch, k: this._styleKey };
    } else {
      line.push({ c: ch, k: this._styleKey });
    }
    this._x += 1;
  }

  _applySgr(params) {
    const codes = params.length && params[0] !== "" ? params.map(Number) : [0];
    for (let i = 0; i < codes.length; i += 1) {
      const code = codes[i];
      switch (code) {
        case 0:
          this._style = {
            fg: null, bg: null, bold: false, dim: false, italic: false,
            underline: false, strike: false, inverse: false,
          };
          break;
        case 1: this._style.bold = true; break;
        case 2: this._style.dim = true; break;
        case 3: this._style.italic = true; break;
        case 4: this._style.underline = true; break;
        case 7: this._style.inverse = true; break;
        case 9: this._style.strike = true; break;
        case 22: this._style.bold = false; this._style.dim = false; break;
        case 23: this._style.italic = false; break;
        case 24: this._style.underline = false; break;
        case 27: this._style.inverse = false; break;
        case 29: this._style.strike = false; break;
        case 39: this._style.fg = null; break;
        case 49: this._style.bg = null; break;
        default:
          if (code >= 30 && code <= 37) this._style.fg = FG[code - 30];
          else if (code >= 40 && code <= 47) this._style.bg = BG[code - 40];
          else if (code >= 90 && code <= 97) this._style.fg = BRIGHT_FG[code - 90];
          else if (code >= 100 && code <= 107) this._style.bg = BRIGHT_BG[code - 100];
          else if (code === 38 || code === 48) {
            // 38;5;n（256 色）/ 38;2;r;g;b（真彩色）
            const mode = codes[i + 1];
            if (mode === 5 && codes[i + 2] !== undefined) {
              const color = ANSI256(codes[i + 2]);
              if (code === 38) this._style.fg = color;
              else this._style.bg = color;
              i += 2;
            } else if (mode === 2 && codes[i + 4] !== undefined) {
              const color = `rgb(${codes[i + 2]},${codes[i + 3]},${codes[i + 4]})`;
              if (code === 38) this._style.fg = color;
              else this._style.bg = color;
              i += 4;
            }
          }
          // 其余私有/未知码忽略
      }
    }
    this._styleKey = [
      this._style.fg || "", this._style.bg || "",
      this._style.bold ? 1 : 0, this._style.dim ? 1 : 0,
      this._style.italic ? 1 : 0, this._style.underline ? 1 : 0,
      this._style.strike ? 1 : 0, this._style.inverse ? 1 : 0,
    ].join("|");
  }

  _styleForKey(key) {
    if (!this._styleCache.has(key)) {
      const [fg, bg, bold, dim, italic, underline, strike, inverse] =
        key.split("|");
      // 注意：JS 中字符串 "0" 为真值，必须显式与 "1" 比较
      const on = (v) => v === "1";
      this._styleCache.set(key, makeStyle({
        fg: fg || null, bg: bg || null, bold: on(bold), dim: on(dim),
        italic: on(italic), underline: on(underline), strike: on(strike),
        inverse: on(inverse),
      }));
    }
    return this._styleCache.get(key);
  }

  feed(text) {
    if (!text) return;
    // 上一帧尾部未结束的转义序列与本帧拼接
    const s = this._escHold + text;
    this._escHold = "";
    let i = 0;
    while (i < s.length) {
      const ch = s[i];
      if (ch === "\x1b") {
        const next = s[i + 1];
        if (next === undefined) {
          // 帧尾孤零零的 ESC，等下一帧
          this._escHold = s.slice(i);
          break;
        }
        if (next === "[") {
          // CSI：读到最终字节（0x40-0x7E）
          let j = i + 2;
          while (j < s.length && !(s.charCodeAt(j) >= 0x40 && s.charCodeAt(j) <= 0x7e)) {
            j += 1;
          }
          if (j >= s.length) {
            // 序列跨帧：缓存，与下一帧拼接
            this._escHold = s.slice(i);
            break;
          }
          const final = s[j];
          const body = s.slice(i + 2, j);
          if (final === "m") {
            this._applySgr(body.split(";"));
          }
          i = j + 1;
          continue;
        }
        if (next === "]") {
          // OSC：ESC ] ... BEL 或 ESC \（可能跨帧）
          let j = i + 2;
          let end = -1;
          while (j < s.length) {
            if (s[j] === "\x07") { end = j; break; }
            if (s[j] === "\x1b" && s[j + 1] === "\\") { end = j + 1; break; }
            j += 1;
          }
          if (end >= 0) {
            i = end + 1;
            continue;
          }
          // 未闭合：缓存残余，等下一帧拼齐
          this._escHold = s.slice(i);
          break;
        }
        if (next === "(" || next === ")") {
          if (i + 2 >= s.length) {
            this._escHold = s.slice(i);
            break;
          }
          // 字符集选择 ESC ( B，跳过 3 字节
          i += 3;
          continue;
        }
        // 其它两字节转义（如 ESC=、ESC>）跳过
        i += 2;
        continue;
      }
      if (ch === "\n") {
        this._newline();
      } else if (ch === "\r") {
        this._x = 0;
      } else if (ch === "\b") {
        this._x = Math.max(0, this._x - 1);
      } else if (ch === "\t") {
        const spaces = 8 - (this._x % 8);
        for (let k = 0; k < spaces; k += 1) this._put(" ");
      } else if (ch === "\x07" || ch === "\x00" || ch === "\x0b" || ch === "\x0c") {
        // BEL / NUL / VT / FF：不产生可见内容
      } else if (ch.charCodeAt(0) < 0x20) {
        // 其它 C0 控制字符忽略
      } else {
        this._put(ch);
      }
      i += 1;
    }
  }

  /** 渲染成分组片段：[{text, style}] 数组（每行一个子数组）。 */
  toSegments() {
    return this.lines.map((chars) => {
      const segs = [];
      let cur = null;
      for (const { c, k } of chars) {
        if (cur && cur.k === k) {
          cur.text += c;
        } else {
          cur = { text: c, k, style: this._styleForKey(k) };
          segs.push(cur);
        }
      }
      if (segs.length === 0) return [{ text: "", style: null }];
      return segs;
    });
  }

  /** 纯文本（下载/复制用），把 \r 覆盖语义拍平成最终行内容。 */
  toPlainText() {
    return this.lines.map((chars) => chars.map((x) => x.c).join("")).join("\n");
  }
}

function ANSI256(n) {
  if (n < 16) {
    return n < 8 ? FG[n] : BRIGHT_FG[n - 8];
  }
  if (n >= 232) {
    const v = Math.round(((n - 232) / 23) * 255);
    return `rgb(${v},${v},${v})`;
  }
  let rem = n - 16;
  const r6 = Math.floor(rem / 36); rem %= 36;
  const g6 = Math.floor(rem / 6);
  const b6 = rem % 6;
  const conv = (v) => (v === 0 ? 0 : 55 + v * 40);
  return `rgb(${conv(r6)},${conv(g6)},${conv(b6)})`;
}
