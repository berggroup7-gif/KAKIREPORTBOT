// Helpers that reproduce Python 3 str/re semantics in JavaScript so stats and
// reply decisions match the original Python bot exactly.

// Characters for which Python str.isspace() is true (used by str.split()/strip()).
export const PYWS_CLASS = "\\t\\n\\x0b\\x0c\\r\\x1c-\\x1f \\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000";
const WCLS = "\\p{L}\\p{N}_"; // Python Unicode \w
const SPLIT_RE = new RegExp(`[${PYWS_CLASS}]+`, "u");
const LSTRIP_RE = new RegExp(`^[${PYWS_CLASS}]+`, "u");
const RSTRIP_RE = new RegExp(`[${PYWS_CLASS}]+$`, "u");

export const pySplit = (s) => String(s).split(SPLIT_RE).filter((x) => x !== "");
export const pyStrip = (s) => String(s).replace(LSTRIP_RE, "").replace(RSTRIP_RE, "");
export const cpLen = (s) => [...String(s)].length;
// Python s[a:b] on code points (b may be undefined, a may be negative).
export function cpSlice(s, a, b) {
  const arr = [...String(s)];
  return arr.slice(a, b).join("");
}

// Convert a Python regex source to an equivalent Unicode JS RegExp.
// Handles \w \W \d \s \b and Python's "$" (also matches before a final newline).
export function pyRe(src, flags = "") {
  let out = "";
  let inCls = false;
  for (let i = 0; i < src.length; i++) {
    const ch = src[i];
    if (ch === "\\") {
      const n = src[++i];
      if (n === "w") out += inCls ? WCLS : `[${WCLS}]`;
      else if (n === "W") {
        if (inCls) throw new Error("\\W inside class unsupported");
        out += `[^${WCLS}]`;
      } else if (n === "d") out += inCls ? "\\p{Nd}" : "\\p{Nd}";
      else if (n === "s") out += inCls ? PYWS_CLASS : `[${PYWS_CLASS}]`;
      else if (n === "b") {
        if (inCls) out += "\\x08";
        else out += `(?:(?<=[${WCLS}])(?![${WCLS}])|(?<![${WCLS}])(?=[${WCLS}]))`;
      } else out += "\\" + n;
      continue;
    }
    if (!inCls && ch === "[") inCls = true;
    else if (inCls && ch === "]") inCls = false;
    if (!inCls && ch === "$") { out += "(?=\\n?$)"; continue; }
    out += ch;
  }
  return new RegExp(out, "u" + flags);
}
