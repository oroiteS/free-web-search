#!/usr/bin/env node
/**
 * free-web-search — 18 引擎网页搜索 CLI（Node 版，零依赖）
 *
 * 与 scripts/search.py 参数、输出完全一致，二者可互换。
 *
 * 用法：
 *   node search.mjs -q "查询" [-e 引擎] [-n 条数] [-t 时间] [--json]
 *   node search.mjs --list-engines
 *   node search.mjs --test [-q "查询"]
 *   node search.mjs -p github -q "dsh plugin"
 *   node search.mjs --fetch https://example.com   // 抓取网页正文（Markdown，走 DeepSeek Fetch API）
 *
 * 引擎密钥全部从环境变量读取（也可用 --key engine=value 临时传）：
 *   EXA_API_KEY TAVILY_API_KEY KEENABLE_API_KEY FIRECRAWL_API_KEY PARALLEL_API_KEY
 *   PERPLEXITY_API_KEY SERPBASE_API_KEY DEEPSEEK_API_KEY YOUCOM_API_KEY
 *   BAIDU_API_KEY MOONSHOT_API_KEY DASHSCOPE_API_KEY DOUBAO_SEARCH_API_KEY ANYSEARCH_API_KEY
 *
 * 代理：设置 HTTPS_PROXY / HTTP_PROXY 环境变量，或 --proxy http://127.0.0.1:7890。
 * Node 24+ 也可用 NODE_USE_ENV_PROXY=1 让内置 fetch 走代理；本脚本自带的 CONNECT
 * 隧道实现不依赖 Node 版本，显式配置代理时优先使用。
 */

import fs from "node:fs";
import http from "node:http";
import https from "node:https";
import path from "node:path";
import tls from "node:tls";
import { fileURLToPath } from "node:url";

// ───────────────────────────── 常量 ─────────────────────────────

const USER_AGENT =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36";
const ACCEPT_LANG = "zh-CN,zh;q=0.9,en;q=0.8";

const DDG_HTML_URL = "https://html.duckduckgo.com/html/";
const DDG_LITE_URL = "https://lite.duckduckgo.com/lite/";
const BING_URL = "https://www.bing.com/search";
const ANYSEARCH_URL = "https://api.anysearch.com/v1/search";
const EXA_MCP_URL = "https://mcp.exa.ai/mcp";
const EXA_URL = "https://api.exa.ai/search";
const TAVILY_URL = "https://api.tavily.com/search";
const KEENABLE_MCP_URL = "https://api.keenable.ai/mcp";
const KEENABLE_URL = "https://api.keenable.ai/v1/search";
const FIRECRAWL_URL = "https://api.firecrawl.dev/v2/search";
const PARALLEL_MCP_URL = "https://search.parallel.ai/mcp";
const PARALLEL_URL = "https://api.parallel.ai/v1/search";
const PERPLEXITY_URL = "https://api.perplexity.ai/chat/completions";
const DEEPSEEK_SEARCH_URL = "https://api.deepseek.com/v1/tools/search";
const DEEPSEEK_FETCH_URL = "https://api.deepseek.com/v1/tools/fetch";
const SERPBASE_URL = "https://api.serpbase.dev/google/search";
const YOUCOM_URL = "https://ydc-index.io/v1/search";
const BAIDU_URL = "https://qianfan.baidubce.com/v2/ai_search/web_search";
const KIMI_URL = "https://api.moonshot.cn/v1/tools/search";
const ALIYUN_URL = "https://dashscope.aliyuncs.com/api/v1/mcps/EnhancedSearch/mcp";
const DOUBAO_URL = "https://open.feedcoopapi.com/search_api/web_search";

const ALL_ENGINES = [
  "ddg", "ddg-lite", "bing", "searxng", "anysearch",
  "exa", "tavily", "keenable", "firecrawl", "parallel",
  "perplexity", "serpbase", "deepseek", "you",
  "baidu", "kimi", "aliyun", "doubao",
];

// 完全不需要密钥的引擎（开箱即用）
const KEYLESS_ENGINES = ["ddg", "ddg-lite", "bing", "searxng", "anysearch", "exa", "tavily", "keenable", "firecrawl", "parallel"];

// 支持时间过滤的引擎
const TIME_ENGINES = ["tavily", "exa", "keenable", "firecrawl", "parallel", "searxng", "ddg", "ddg-lite", "baidu", "doubao", "deepseek"];

// 支持的单页抓取后端（--fetch-engine / 配置 fetchPriority）
const FETCH_ENGINES = ["deepseek", "tavily", "keenable", "firecrawl", "parallel", "anysearch", "doubao"];

/** 按用户数字重排：有数字的升序在前（同数保持原相对顺序），其余保持原相对顺序垫后。 */
function prioritized(order, priorities) {
  if (!priorities) return order;
  const numbered = order.filter((e) => e in priorities).sort((a, b) => priorities[a] - priorities[b]);
  return [...numbered, ...order.filter((e) => !(e in priorities))];
}

// 默认回退优先级：付费/高质引擎在前，稳定免 key 引擎（bing/anysearch）居中，
// 需要代理且易限流的 ddg/searxng 垫底。这个顺序很关键 —— 若把 ddg 排在 bing 前面，
// 无代理环境下 ddg 每次重试会吃掉 12s+ 预算，导致后面的 bing 根本没机会跑。
const DEFAULT_FALLBACK_ORDER = [
  "exa", "tavily", "keenable", "firecrawl", "parallel",
  "perplexity", "serpbase", "deepseek", "you",
  "baidu", "kimi", "aliyun", "doubao",
  "bing", "anysearch", "ddg", "ddg-lite", "searxng",
];

// 引擎 → 环境变量名
const KEY_ENV = {
  anysearch: "ANYSEARCH_API_KEY",
  exa: "EXA_API_KEY",
  tavily: "TAVILY_API_KEY",
  keenable: "KEENABLE_API_KEY",
  firecrawl: "FIRECRAWL_API_KEY",
  parallel: "PARALLEL_API_KEY",
  perplexity: "PERPLEXITY_API_KEY",
  serpbase: "SERPBASE_API_KEY",
  deepseek: "DEEPSEEK_API_KEY",
  you: "YOUCOM_API_KEY",
  baidu: "BAIDU_API_KEY",
  kimi: "MOONSHOT_API_KEY",
  aliyun: "DASHSCOPE_API_KEY",
  doubao: "DOUBAO_SEARCH_API_KEY",
};

const LANG_PROFILES = {
  zh: { market: "zh-CN", acceptLang: "zh-CN,zh;q=0.9,en;q=0.8" },
  en: { market: "en-US", acceptLang: "en-US,en;q=0.9" },
  ru: { market: "ru-RU", acceptLang: "ru-RU,ru;q=0.9,en;q=0.8" },
  ja: { market: "ja-JP", acceptLang: "ja-JP,ja;q=0.9,en;q=0.8" },
  de: { market: "de-DE", acceptLang: "de-DE,de;q=0.9,en;q=0.8" },
  fr: { market: "fr-FR", acceptLang: "fr-FR,fr;q=0.9,en;q=0.8" },
  es: { market: "es-ES", acceptLang: "es-ES,es;q=0.9,en;q=0.8" },
  ko: { market: "ko-KR", acceptLang: "ko-KR,ko;q=0.9,en;q=0.8" },
};

const MARKET_TO_LANG = {
  "zh-CN": "zh-CN,zh;q=0.9,en;q=0.8",
  "zh-TW": "zh-TW,zh;q=0.9,en;q=0.8",
  "en-US": "en-US,en;q=0.9",
  "en-GB": "en-GB,en;q=0.9",
  "ru-RU": "ru-RU,ru;q=0.9,en;q=0.8",
  "ja-JP": "ja-JP,ja;q=0.9,en;q=0.8",
  "de-DE": "de-DE,de;q=0.9,en;q=0.8",
  "fr-FR": "fr-FR,fr;q=0.9,en;q=0.8",
  "es-ES": "es-ES,es;q=0.9,en;q=0.8",
  "ko-KR": "ko-KR,ko;q=0.9,en;q=0.8",
};

const SERPBASE_LOCALE = {
  zh: { hl: "zh-CN", gl: "cn" },
  en: { hl: "en", gl: "us" },
  ru: { hl: "ru", gl: "ru" },
  ja: { hl: "ja", gl: "jp" },
  de: { hl: "de", gl: "de" },
  fr: { hl: "fr", gl: "fr" },
  es: { hl: "es", gl: "es" },
  ko: { hl: "ko", gl: "kr" },
};

const SEARXNG_INSTANCES = [
  "https://opnxng.com",
  "https://priv.au",
  "https://searx.be",
  "https://searx.tiekoetter.com",
  "https://search.inetol.net",
  "https://paulgo.io",
];

// ───────────────────────── 时间过滤解析 ─────────────────────────

const TIME_RANGES = ["day", "week", "month", "year"];
const DAYS_BY_RANGE = { day: 1, week: 7, month: 30, year: 365 };

function isoDaysAgo(days) {
  return new Date(Date.now() - days * 86_400_000).toISOString().replace(/\.\d{3}Z$/, ".000Z");
}

/**
 * 解析时间过滤：返回 {days} 或 {after}，无效返回 undefined。
 * 支持 day/week/month/year、12h/3d/2mo/1y、2026-07-01。
 */
function parseTimeRange(input) {
  if (input === undefined || input === null) return undefined;
  if (typeof input === "object") {
    if (typeof input.after === "string" && /^\d{4}-\d{2}-\d{2}$/.test(input.after)) return { after: input.after };
    if (typeof input.days === "number" && Number.isFinite(input.days) && input.days > 0) return { days: input.days };
    return undefined;
  }
  const s = String(input).trim().toLowerCase();
  if (s.length === 0) return undefined;
  if (TIME_RANGES.includes(s)) return { days: DAYS_BY_RANGE[s] };
  if (/^\d{4}-\d{2}-\d{2}$/.test(s)) return { after: s };
  const m = s.match(/^(\d+(?:\.\d+)?)\s*(h|hour|hours|d|day|days|w|week|weeks|mo|month|months|y|year|years)$/);
  if (m) {
    const n = parseFloat(m[1]);
    const unit = m[2][0];
    const days = unit === "h" ? n / 24 : unit === "d" ? n : unit === "w" ? n * 7 : unit === "m" ? n * 30 : n * 365;
    return { days };
  }
  return undefined;
}

/** 把任意天数映射到只支持固定档的引擎（Tavily / SearXNG / DDG）的最近似档位 */
function approximateTimeRange(days) {
  if (days <= 2) return "day";
  if (days <= 14) return "week";
  if (days <= 90) return "month";
  return "year";
}

/** 任意天数 → Keenable 的相对时间格式（12h / Nd / Nmo / Ny） */
function formatKeenableRelative(days) {
  if (days <= 0.5) return "12h";
  if (days < 1) return `${Math.round(days * 24)}h`;
  if (days < 30) return `${Math.round(days)}d`;
  if (days < 365) return `${Math.round(days / 30)}mo`;
  return `${Math.round(days / 365)}y`;
}

/** 豆包：OneDay/OneWeek/OneMonth/OneYear 或 "YYYY-MM-DD..YYYY-MM-DD" */
function doubaoTimeRange(timeRange) {
  if (!timeRange) return undefined;
  if (typeof timeRange.after === "string") {
    const end = new Date().toISOString().slice(0, 10);
    return `${timeRange.after}..${end}`;
  }
  const days = Number(timeRange.days);
  if (!Number.isFinite(days) || days <= 0) return undefined;
  if (days <= 1) return "OneDay";
  if (days <= 7) return "OneWeek";
  if (days <= 31) return "OneMonth";
  return "OneYear";
}

// ───────────────────────── 文本清洗 ─────────────────────────

// HTML 实体表：Bing / DDG 的摘要里常见排版实体（&ensp; &middot; &hellip; 等），
// 只覆盖 &amp;/&nbsp; 这几个会让这些字符原样漏进给模型的输出里。
const NAMED_ENTITIES = {
  amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " ",
  ensp: " ", emsp: " ", thinsp: " ", shy: "",
  middot: "·", bull: "•", hellip: "…", prime: "′", Prime: "″",
  mdash: "—", ndash: "–", minus: "−",
  lsquo: "‘", rsquo: "’", ldquo: "“", rdquo: "”", sbquo: "‚", bdquo: "„",
  laquo: "«", raquo: "»", lsaquo: "‹", rsaquo: "›",
  copy: "©", reg: "®", trade: "™", sect: "§", para: "¶", deg: "°",
  times: "×", divide: "÷", plusmn: "±", frac12: "½", frac14: "¼", frac34: "¾",
  euro: "€", pound: "£", yen: "¥", cent: "¢", curren: "¤",
  larr: "←", rarr: "→", uarr: "↑", darr: "↓", harr: "↔",
  le: "≤", ge: "≥", ne: "≠", asymp: "≈", infin: "∞",
  eacute: "é", egrave: "è", agrave: "à", ccedil: "ç", uuml: "ü", ouml: "ö", auml: "ä", szlig: "ß",
};

function decodeEntities(text) {
  return String(text)
    .replace(/&#x([0-9a-f]+);/gi, (_, hex) => String.fromCodePoint(parseInt(hex, 16)))
    .replace(/&#(\d+);/g, (_, n) => String.fromCodePoint(Number(n)))
    .replace(/&([a-z][a-z0-9]*);/gi, (whole, name) =>
      Object.prototype.hasOwnProperty.call(NAMED_ENTITIES, name) ? NAMED_ENTITIES[name] : whole
    );
}

function stripTags(html) {
  return decodeEntities(String(html).replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim());
}

const SNIPPET_NOISE =
  /\b(sign up|sign in|log in|login|subscribe( to| for)?|member[- ]?only|become a member|create (a )?free account|read more|continue reading|story continues|get started|install (the )?app|view on|medium membership|join \w+ for free|get updates from this writer|stories in your inbox|remember me for|unlock this|free to read|become a patron)\b/gi;

function cleanSnippet(text, max = 300) {
  if (!text) return text;
  return String(text)
    .replace(SNIPPET_NOISE, " ")
    .replace(/^\s*(#{1,6}\s*|\[\s*x?\s*\]\s*|-\s*\[\s*x?\s*\]\s*|>\s*)/gm, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, max);
}

const SNIPPET_MAX_BY_ENGINE = { doubao: 2000 };
function snippetCap(engine) {
  return SNIPPET_MAX_BY_ENGINE[engine] ?? 300;
}

function uniqueSources(sources, limit) {
  const seen = new Set();
  const out = [];
  for (const s of sources) {
    if (s.url && !seen.has(s.url)) {
      seen.add(s.url);
      out.push(s);
    }
    if (out.length >= limit) break;
  }
  return out;
}

function extractDdgUrl(rel) {
  if (!rel) return null;
  const m = rel.match(/uddg=([^&]+)/);
  if (m) {
    try {
      return decodeURIComponent(m[1]);
    } catch {
      return m[1];
    }
  }
  if (rel.startsWith("//")) return `https:${rel}`;
  return rel;
}

// ───────────────────────── 配置文件 ─────────────────────────
//
// 目的：让 AI 在「第一次使用」时问用户一次（用 Python 还是 Node、走不走代理、
// 代理端口多少、默认引擎/条数/语言），把答案落到 config.yaml，之后每次直接读文件，
// 不再重复提问。
//
// 判断方式（AI 侧，也是脚本 --config-status 的输出）：
//   文件不存在 或 内容为空/只有注释 → 视为「未配置」，需要提问
//   文件存在且含有效键值          → 视为「已配置」，直接用，不要再问
//
// 支持的格式：极简 YAML（key: value，一层缩进的分组可省略）与 TOML（key = value）。
// 故意不引入 YAML 解析库 —— 这个 skill 的卖点是零依赖，所以只解析本文件用到的子集：
//   runtime: python          # 或 node
//   proxy:
//     enabled: true
//     url: http://127.0.0.1:7890
//   engine: bing
//   maxResults: 5
//   lang: zh
//   timeout: 30
// 值支持 true/false、数字、字符串、带引号字符串；# 之后是注释。

const CONFIG_FILENAME = "config.yaml";
// 凭据单独放一个文件：config.yaml 可能被提交/分享，明文 key 不该混进去
const SECRETS_FILENAME = "secrets.yaml";
// 允许出现在配置文件里的键（避免手写错字静默生效）
const CONFIG_KEYS = new Set([
  "runtime", "proxy", "enabled", "url", "engine", "maxResults", "max_results",
  "lang", "market", "safeSearch", "safe_search", "timeout", "searxng", "platforms",
  "keys", "key", "disabledEngines", "disabled_engines", "engines",
]);

/** 配置文件候选路径：优先 skill 根目录，其次脚本同级，最后 $HOME/.config/free-web-search */
function configCandidates() {
  const here = path.dirname(fileURLToPath(import.meta.url));
  const list = [
    path.join(here, "..", CONFIG_FILENAME),   // <skill>/config.yaml  ← 推荐位置
    path.join(here, CONFIG_FILENAME),         // <skill>/scripts/config.yaml
  ];
  const home = process.env.HOME || process.env.USERPROFILE;
  if (home) list.push(path.join(home, ".config", "free-web-search", CONFIG_FILENAME));
  return list;
}

function findConfigPath() {
  for (const p of configCandidates()) {
    try {
      if (fs.existsSync(p) && fs.statSync(p).isFile()) return p;
    } catch {}
  }
  return null;
}

/** 去掉注释与首尾空白；支持行尾 # 注释（引号内的 # 不算） */
function stripConfigComment(line) {
  let inSingle = false;
  let inDouble = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (ch === "'" && !inDouble) inSingle = !inSingle;
    else if (ch === '"' && !inSingle) inDouble = !inDouble;
    else if (ch === "#" && !inSingle && !inDouble) return line.slice(0, i);
  }
  return line;
}

// 这些键的值就是字面字符串，不能被 YAML 的布尔规则吞掉：
// safeSearch: off 必须是 "off"（Bing 的 adlt=off），不能变成 false。
const STRING_ONLY_KEYS = new Set(["safeSearch", "safe_search", "engine", "lang", "market", "runtime", "url", "searxng", "proxy.url"]);

function coerceConfigValue(raw, key = "") {
  let v = raw.trim();
  if (v.length >= 2 && ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'")))) {
    return v.slice(1, -1);
  }
  if (STRING_ONLY_KEYS.has(key)) return v;
  const lower = v.toLowerCase();
  if (lower === "true" || lower === "yes" || lower === "on") return true;
  if (lower === "false" || lower === "no" || lower === "off") return false;
  if (lower === "null" || lower === "~") return null;
  if (/^-?\d+$/.test(v)) return parseInt(v, 10);
  if (/^-?\d*\.\d+$/.test(v)) return parseFloat(v);
  return v;
}

/** 解析内联列表：`[a, b, "c"]` → ["a","b","c"]；非列表返回 null */
function parseInlineList(raw) {
  const v = String(raw).trim();
  if (!(v.startsWith("[") && v.endsWith("]"))) return null;
  const inner = v.slice(1, -1).trim();
  if (!inner) return [];
  return inner
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean)
    .map((s) => {
      const q = s.match(/^["'](.*)["']$/);
      return q ? q[1] : s;
    });
}

/**
 * 解析极简 YAML / TOML。
 * 只处理 `key: value` 与 `key = value`；`proxy:` 后跟缩进子键会展开成扁平键
 * （proxy.enabled / proxy.url），调用方按扁平键读取即可。
 * 列表支持两种写法：`key: [a, b]` 内联，或 `key:` 后跟缩进的 `- a` / `- b`。
 */
function parseConfigText(text) {
  const out = {};
  let section = null;
  // 分组样式："yaml"（缩进归组，遇到无缩进行即结束）或 "toml"（[section] 持续到下一个段头）
  let sectionStyle = null;
  // 当前正在收集的列表键（`key:` 后跟 `- item`）
  let listKey = null;
  for (const rawLine of String(text).split(/\r?\n/)) {
    const line = stripConfigComment(rawLine);
    if (!line.trim()) continue;
    const indent = line.length - line.trimStart().length;
    const body = line.trim();

    // TOML 段头：[proxy] / [proxy.settings] —— 其后的键归到该段名下
    const sec = body.match(/^\[([A-Za-z_][A-Za-z0-9_.-]*)\]$/);
    if (sec) {
      section = sec[1];
      sectionStyle = "toml";
      continue;
    }

    // 列表项 `- item`：归到当前的列表键名下
    const item = body.match(/^-\s+(.*)$/);
    if (item && listKey) {
      out[listKey].push(coerceConfigValue(item[1], listKey));
      continue;
    }

    // key: value  或  key = value
    const m = body.match(/^([A-Za-z_][A-Za-z0-9_.-]*)\s*[:=]\s*(.*)$/);
    if (!m) continue;
    const key = m[1];
    const valueRaw = m[2];

    // 内联列表 `key: [a, b]`
    const inline = parseInlineList(valueRaw);
    if (inline !== null) {
      out[key] = inline;
      listKey = null;
      if (indent === 0) { section = null; sectionStyle = null; }
      continue;
    }

    if (valueRaw === "") {
      // YAML 风格的分组标题（如 `proxy:`），后续缩进行归到它名下
      section = indent === 0 ? key : section;
      sectionStyle = indent === 0 ? "yaml" : sectionStyle;
      // 可能是列表头（后续 `- item` 归到这里）；先建空数组，若后续没有列表项则删掉
      out[key] = [];
      listKey = key;
      continue;
    }

    if (sectionStyle === "toml" && section) {
      // TOML 段内的键（无缩进），持续到下一个 [section]
      out[`${section}.${key}`] = coerceConfigValue(valueRaw, `${section}.${key}`);
    } else if (sectionStyle === "yaml" && section && indent > 0) {
      // YAML 缩进分组内的键
      out[`${section}.${key}`] = coerceConfigValue(valueRaw, `${section}.${key}`);
    } else {
      // 顶层键：同时结束 YAML 分组
      out[key] = coerceConfigValue(valueRaw, key);
      if (indent === 0) {
        section = null;
        sectionStyle = null;
      }
    }
  }
  // 清理：`key:` 后面没跟任何 `- item` 的，不是列表，删掉占位的空数组
  for (const [k, v] of Object.entries(out)) {
    if (Array.isArray(v) && v.length === 0) delete out[k];
  }
  return out;
}

/**
 * 读取配置。返回：
 *   { configured: false, reason, path }            —— 文件不存在或为空（需要向用户提问）
 *   { configured: true, path, values: {...} }      —— 已配置
 */
function loadConfig() {
  const file = findConfigPath();
  if (!file) {
    return { configured: false, reason: "config file not found", path: configCandidates()[0], searched: configCandidates() };
  }
  let text;
  try {
    text = fs.readFileSync(file, "utf8");
  } catch (error) {
    return { configured: false, reason: `cannot read config: ${error.message}`, path: file };
  }
  const values = parseConfigText(text);
  // 只有注释 / 全空 → 仍视为未配置
  if (Object.keys(values).length === 0) {
    return { configured: false, reason: "config file is empty (no effective keys)", path: file, values };
  }
  // keys.* 是 API key 段（由 loadConfiguredKeys 单独校验），不算未知键
  const unknown = Object.keys(values).filter(
    (k) => !/^keys?\./i.test(k) && !/^(search|fetch)Priority\./i.test(k) && !CONFIG_KEYS.has(k.split(".").pop()) && !CONFIG_KEYS.has(k)
  );
  // 常见手误：把顶层键写在 [proxy] 段之后，TOML 会把它算成 proxy.<key>。
  // 这类键看起来「存在」但不会生效，必须提示，否则用户改了配置却毫无效果。
  const misplaced = Object.keys(values)
    .filter((k) => k.startsWith("proxy."))
    .map((k) => k.slice("proxy.".length))
    .filter((k) => !["enabled", "url"].includes(k));
  const warnings = [];
  if (misplaced.length) {
    warnings.push(
      `这些键位于 [proxy] 段内（或被 proxy: 缩进吞掉），不会生效：${misplaced.join(", ")}。` +
        `把它们移到文件顶部（[proxy] 段之前 / 不缩进）即可。`
    );
  }
  if (unknown.length) warnings.push(`未识别的键（可能拼错）：${unknown.join(", ")}`);
  return { configured: true, path: file, values, unknownKeys: unknown, misplaced, warnings };
}

// ───────────────────────── 凭据（API key） ─────────────────────────
//
// 优先级：secrets.yaml > config.yaml 的 keys: 段 > 环境变量。
// 之所以单列 secrets.yaml：config.yaml 有可能被提交或分享给别人，
// 而 API key 是私密信息，混在一起迟早泄露。
//
// secrets.yaml 的写法（键名就是环境变量名，最直观）：
//   EXA_API_KEY: sk-xxx
//   TAVILY_API_KEY: tvly-xxx
// 也接受小写别名（exa / tavily / baidu …），内部统一映射到 KEY_ENV。

/** secrets.yaml 候选路径（与 config.yaml 同级，外加 ~/.config） */
function secretsCandidates() {
  const here = path.dirname(fileURLToPath(import.meta.url));
  const list = [
    path.join(here, "..", SECRETS_FILENAME),
    path.join(here, SECRETS_FILENAME),
  ];
  const home = process.env.HOME || process.env.USERPROFILE;
  if (home) list.push(path.join(home, ".config", "free-web-search", SECRETS_FILENAME));
  return list;
}

function findSecretsPath() {
  for (const p of secretsCandidates()) {
    try {
      if (fs.existsSync(p) && fs.statSync(p).isFile()) return p;
    } catch {}
  }
  return null;
}

// 引擎 id → 别名集合（允许在配置里用简写，如 `exa:` 或 `EXA_API_KEY:`）
const ENGINE_KEY_ALIASES = (() => {
  const map = new Map();
  for (const [engine, env] of Object.entries(KEY_ENV)) {
    map.set(env.toLowerCase(), env);
    map.set(engine.toLowerCase(), env);
    map.set(`${engine.toLowerCase()}apikey`, env);
    map.set(`${engine.toLowerCase()}_api_key`, env);
  }
  // 豆包还有官方备用名
  map.set("web_search_api_key", "DOUBAO_SEARCH_API_KEY");
  return map;
})();

/** 把配置里写的键名归一成环境变量名；认不出返回 null */
function normalizeKeyName(raw) {
  const lower = String(raw).trim().toLowerCase();
  return ENGINE_KEY_ALIASES.get(lower) ?? null;
}

/** 收集配置里的 key（secrets.yaml 优先，其次 config.yaml 的 keys:/key: 段） */
function loadConfiguredKeys(configValues = {}) {
  const keys = {};      // envName -> value
  const sources = {};   // envName -> 来源描述
  const warnings = [];

  const collect = (flatValues, sourceLabel) => {
    for (const [rawKey, value] of Object.entries(flatValues)) {
      // 支持 `keys.EXA_API_KEY`、`keys.exa`、`key.exa` 三种前缀写法
      const m = rawKey.match(/^(?:keys?|apiKeys?)\.(.+)$/i);
      if (!m) continue;
      const env = normalizeKeyName(m[1]);
      if (!env) {
        warnings.push(`${sourceLabel}: 未知的 key 名 "${m[1]}"（应写成 EXA_API_KEY / tavily / baidu 这类）`);
        continue;
      }
      if (typeof value !== "string" || value.trim() === "") continue;
      if (keys[env] !== undefined) continue; // 先到先得（secrets.yaml 先收集）
      keys[env] = value.trim();
      sources[env] = sourceLabel;
    }
  };

  // 1) secrets.yaml（最优先）
  const secretsFile = findSecretsPath();
  if (secretsFile) {
    try {
      const parsed = parseConfigText(fs.readFileSync(secretsFile, "utf8"));
      // secrets.yaml 允许两种写法：带 keys: 前缀，或直接把 EXA_API_KEY 写在顶层
      const flat = {};
      for (const [k, v] of Object.entries(parsed)) flat[`keys.${k.replace(/^keys?\./, "")}`] = v;
      collect(flat, secretsFile);
    } catch (error) {
      warnings.push(`无法读取 ${secretsFile}: ${error.message}`);
    }
  }

  // 2) config.yaml 里的 keys: 段（不推荐，但支持）
  collect(configValues, "config.yaml");

  return { keys, sources, secretsFile, warnings };
}

/** 从配置推导出运行时设置（代理、默认引擎、条数等） */
function configToSettings(values) {
  const s = {};
  if (typeof values.runtime === "string") s.runtime = values.runtime.toLowerCase();
  if (typeof values.engine === "string") s.engine = values.engine;
  const maxResults = values.maxResults ?? values.max_results;
  if (typeof maxResults === "number") s.maxResults = maxResults;
  if (typeof values.lang === "string") s.lang = values.lang;
  if (typeof values.market === "string") s.market = values.market;
  const safe = values.safeSearch ?? values.safe_search;
  if (typeof safe === "string") s.safeSearch = safe;
  if (typeof values.timeout === "number") s.timeout = values.timeout;
  if (typeof values.searxng === "string") s.searxng = values.searxng;

  // 手动禁用部分引擎：disabledEngines: [ddg, searxng]
  // 也接受逗号分隔字符串 `disabledEngines: ddg,searxng`（YAML/TOML 都好写）
  const disabledRaw = values.disabledEngines ?? values.disabled_engines;
  if (disabledRaw !== undefined) {
    const list = Array.isArray(disabledRaw)
      ? disabledRaw
      : String(disabledRaw).split(",").map((x) => x.trim());
    const known = [];
    const unknownEngines = [];
    for (const item of list) {
      const name = String(item).trim().toLowerCase();
      if (!name) continue;
      if (ALL_ENGINES.includes(name)) {
        if (!known.includes(name)) known.push(name);
      } else {
        unknownEngines.push(String(item).trim());
      }
    }
    s.disabledEngines = known;
    if (unknownEngines.length) s.unknownDisabled = unknownEngines;
  }

  // 代理：proxy.enabled / proxy.url 两种写法都认
  const proxyEnabled = values["proxy.enabled"] ?? values.proxyEnabled;
  const proxyUrl = values["proxy.url"] ?? values.proxyUrl ?? (typeof values.proxy === "string" ? values.proxy : undefined);
  if (proxyEnabled === false) {
    s.proxy = null;                       // 显式关闭：忽略环境变量
    s.proxyDisabled = true;
  } else if (typeof proxyUrl === "string" && proxyUrl.trim()) {
    s.proxy = proxyUrl.trim();
  } else if (proxyEnabled === true) {
    s.proxy = "http://127.0.0.1:7890";    // 开了但没给地址：用常见默认端口
  }

  // 搜索 / 抓取优先级：数字越小越靠前。
  // 支持数字映射（searchPriority.deepseek = 1）或列表/逗号串（顺序即优先级，数字按 1,2,3… 编）；
  // 映射与列表同时出现时映射优先，列表只补缺。未知引擎或非数字给出警告并忽略。
  const parsePriorities = (name, validSet) => {
    const priorities = {};
    const warns = [];
    const prefix = `${name}.`;
    for (const [key, value] of Object.entries(values)) {
      if (!key.startsWith(prefix)) continue;
      const rawName = key.slice(prefix.length).trim();
      const eng = rawName.toLowerCase();
      const num = Number(value);
      if (!validSet.includes(eng)) warns.push(`${name} 里有未知引擎 "${rawName}"，已忽略`);
      else if (!Number.isFinite(num)) warns.push(`${name} 里 "${eng}" 的值 "${value}" 不是数字，已忽略`);
      else priorities[eng] = num;
    }
    const raw = values[name];
    const items = Array.isArray(raw)
      ? raw
      : typeof raw === "string" && raw.trim()
        ? raw.split(",").map((x) => x.trim()).filter(Boolean)
        : [];
    for (const [idx, item] of items.entries()) {
      const rawName = String(item).trim();
      const eng = rawName.toLowerCase();
      if (!eng) continue;
      if (!validSet.includes(eng)) warns.push(`${name} 里有未知引擎 "${rawName}"，已忽略`);
      else if (!(eng in priorities)) priorities[eng] = idx + 1;
    }
    return [priorities, warns];
  };
  const [sp, spWarns] = parsePriorities("searchPriority", ALL_ENGINES);
  const [fp, fpWarns] = parsePriorities("fetchPriority", FETCH_ENGINES);
  if (Object.keys(sp).length) s.searchPriority = sp;
  if (Object.keys(fp).length) s.fetchPriority = fp;
  if (spWarns.length || fpWarns.length) s.priorityWarnings = [...spWarns, ...fpWarns];
  return s;
}

// --init-config 写出的模板：带注释、默认值可直接用
const CONFIG_TEMPLATE = `# free-web-search 配置文件
#
# 这个文件一旦存在且包含有效键值，AI 就视为「已配置」，后续不再向你提问，
# 直接按这里的选择工作。想重新配置就改这里（或删掉文件让它重新问一遍）。
#
# 格式：极简 YAML（key: value）。也可以写成 TOML（key = value），解析器两种都认。

# 用哪个运行时执行搜索：python | node
# 影响 AI 调用哪个脚本（search.py 还是 search.mjs）
runtime: python

# 代理设置：国内访问 DuckDuckGo / SearXNG 通常需要开
proxy:
  enabled: false
  url: http://127.0.0.1:7890

# 默认搜索引擎。
#   auto  = 智能路由（推荐）：按查询语言选引擎，并自动跳过「需要 key 但没配」的引擎
#   其余  = 指定某个引擎 id，失败仍会自动回退到其它引擎
# 免 key 可用：bing ddg ddg-lite searxng anysearch exa tavily keenable firecrawl parallel
# 需 key：perplexity serpbase deepseek you baidu kimi aliyun doubao
engine: auto

# 手动禁用不想用的引擎（auto / 回退链 / multi 都不会再碰它们）
# 两种写法都行：列表，或逗号分隔字符串
disabledEngines: []
# disabledEngines:
#   - ddg
#   - ddg-lite
#   - searxng

# 默认返回条数（1-20）
maxResults: 5

# 结果本地化语言：zh en ru ja de fr es ko
lang: zh

# 安全搜索（仅 Bing / DuckDuckGo 生效）：off | moderate | strict
safeSearch: off

# 整条回退链的总超时预算（秒）
timeout: 30

# ── 搜索 / 抓取优先级（可选）──────────────────────────────────
# 用简单数字给引擎排序：数字越小越靠前（1 = 最优先）。
# 未列出的引擎按内置默认顺序排在所有已列出引擎之后，不会插队。
#
# searchPriority —— 影响 auto 智能路由、回退链顺序和 multi 的选源。
#   设置后 auto 不再按查询语言加权，完全按这里的数字走；
#   带 -t 时间过滤时，你列出的引擎里「支持时间过滤的」仍会前置。
# fetchPriority —— 决定 --fetch 不指定 --fetch-engine 时的默认抓取后端
#   （自动跳过没配 key 的；抓取不做跨后端自动回退）。
#
# 数字映射写法：
# searchPriority:
#   deepseek: 1
#   bing: 2
# fetchPriority:
#   firecrawl: 1
#   deepseek: 2
#
# 列表写法（顺序即优先级，等价于 1,2,3…）：
# searchPriority: [deepseek, bing, exa]
#
# 写错的引擎名 / 非数字会在 --config-status 的 warnings 里提示。

# ── API key 放在哪？────────────────────────────────────────────
# 推荐单独放 secrets.yaml（与这个文件同级），写法见 secrets.example.yaml：
#   EXA_API_KEY: "sk-..."
#   TAVILY_API_KEY: "tvly-..."
# 用 --init-secrets 可生成模板（自动设为 600 权限）。
#
# 也可以直接写在本文件下面（不推荐：本文件可能被提交或分享）：
# keys:
#   exa: "sk-..."
#   baidu: "..."
#
# 优先级：secrets.yaml > 本文件的 keys: 段 > 环境变量
`;

// --init-secrets 写出的模板：只放 key，别放其他配置
const SECRETS_TEMPLATE = `# free-web-search 凭据文件
#
# ⚠️ 这个文件含明文密钥：
#   - 不要提交到 git（本目录 .gitignore 已忽略 secrets.yaml）
#   - 不要分享给别人
#   本文件由 --init-secrets 生成时已设为 600 权限（仅本人可读写）
#
# 写法：键名用环境变量名（推荐），或直接用引擎 id 简写。
# 留空或注释掉表示不使用该引擎。免 key 引擎不需要在这里配置。

# ── 免 key 也能用，配了只是提额 ──────────────────────────────
# EXA_API_KEY: ""
# TAVILY_API_KEY: ""
# KEENABLE_API_KEY: ""
# FIRECRAWL_API_KEY: ""
# PARALLEL_API_KEY: ""
# ANYSEARCH_API_KEY: ""

# ── 必须配 key 才能用 ────────────────────────────────────────
# PERPLEXITY_API_KEY: ""        # https://www.perplexity.ai/settings/api
# SERPBASE_API_KEY: ""          # https://serpbase.dev
# DEEPSEEK_API_KEY: ""          # https://platform.deepseek.com/api_keys （deepseek / --fetch 共用）
# YOUCOM_API_KEY: ""            # https://you.com/platform/api-keys
# BAIDU_API_KEY: ""             # 百度千帆控制台
# MOONSHOT_API_KEY: ""          # https://platform.moonshot.cn  （Kimi）
# DASHSCOPE_API_KEY: ""         # https://bailian.console.aliyun.com （阿里云百炼）
# DOUBAO_SEARCH_API_KEY: ""     # https://console.volcengine.com/search-infinity/web-search
`;

// ───────────────────────── HTTP 层 ─────────────────────────
let PROXY_URL = process.env.HTTPS_PROXY || process.env.https_proxy || process.env.HTTP_PROXY || process.env.http_proxy || "";

// 整条回退链的总预算（毫秒）。>0 时，单次请求的超时会被裁剪到剩余预算，
// 防止「前几个引擎各超时 12-25s」把总时长拖到分钟级。0 = 不限制。
let REQUEST_DEADLINE = 0;

function setProxy(value) {
  PROXY_URL = value || "";
}

function setDeadline(budgetMs) {
  REQUEST_DEADLINE = budgetMs > 0 ? Date.now() + budgetMs : 0;
  return REQUEST_DEADLINE;
}

/** 按总预算裁剪单次请求超时；预算已耗尽则立即失败 */
function effectiveTimeout(timeoutMs) {
  if (!REQUEST_DEADLINE) return timeoutMs;
  const remaining = REQUEST_DEADLINE - Date.now();
  if (remaining <= 0) throw new Error("search time budget exhausted");
  return Math.min(timeoutMs, remaining);
}

/** 用 CONNECT 隧道发起一次 HTTPS 请求（不依赖 Node 版本，也不依赖 undici） */
function proxyRequest(urlObj, { method = "GET", headers = {}, body } = {}, timeoutMs) {
  return new Promise((resolve, reject) => {
    const proxy = new URL(PROXY_URL);
    const targetPort = Number(urlObj.port) || 443;
    const authority = `${urlObj.hostname}:${targetPort}`;
    let settled = false;
    const fail = (err) => {
      if (settled) return;
      settled = true;
      reject(err);
    };

    const connectHeaders = { host: authority };
    if (proxy.username) {
      const auth = `${decodeURIComponent(proxy.username)}:${decodeURIComponent(proxy.password)}`;
      connectHeaders["proxy-authorization"] = `Basic ${Buffer.from(auth).toString("base64")}`;
    }

    const connectReq = http.request({
      host: proxy.hostname,
      port: Number(proxy.port) || 80,
      method: "CONNECT",
      path: authority,
      headers: connectHeaders,
    });

    connectReq.setTimeout(timeoutMs, () => {
      connectReq.destroy();
      fail(new Error(`proxy connect timeout after ${timeoutMs}ms`));
    });
    connectReq.on("error", (err) => fail(new Error(`proxy connection error: ${err.message}`)));
    connectReq.on("connect", (res, socket) => {
      if (res.statusCode !== 200) {
        socket.destroy();
        fail(new Error(`proxy CONNECT failed with HTTP ${res.statusCode}`));
        return;
      }
      const tlsSocket = tls.connect({ socket, servername: urlObj.hostname }, () => {
        const req = https.request(
          {
            host: urlObj.hostname,
            port: targetPort,
            path: urlObj.pathname + urlObj.search,
            method,
            headers: { host: urlObj.hostname, ...headers },
            createConnection: () => tlsSocket,
          },
          (res2) => {
            const chunks = [];
            res2.on("data", (c) => chunks.push(c));
            res2.on("end", () => {
              if (settled) return;
              settled = true;
              const text = Buffer.concat(chunks).toString("utf8");
              resolve({ ok: res2.statusCode >= 200 && res2.statusCode < 300, status: res2.statusCode, text });
            });
            res2.on("error", fail);
          }
        );
        req.setTimeout(timeoutMs, () => {
          req.destroy();
          fail(new Error(`request timeout after ${timeoutMs}ms`));
        });
        req.on("error", fail);
        if (body) req.write(body);
        req.end();
      });
      tlsSocket.on("error", (err) => fail(new Error(`tls error: ${err.message}`)));
    });
    connectReq.end();
  });
}

/**
 * 统一请求出口。返回 { ok, status, text, json() }。
 * 无代理时走内置 fetch（Node 18+）；有代理时走 CONNECT 隧道。
 */
async function request(url, { method = "GET", headers = {}, body, timeoutMs = 15000 } = {}) {
  timeoutMs = effectiveTimeout(timeoutMs);
  const urlObj = new URL(url);
  if (PROXY_URL) {
    const res = await proxyRequest(urlObj, { method, headers, body }, timeoutMs);
    return { ...res, json: () => JSON.parse(res.text) };
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(url, { method, headers, body, signal: controller.signal, redirect: "follow" });
    const text = await res.text();
    return { ok: res.ok, status: res.status, text, json: () => JSON.parse(text) };
  } catch (error) {
    if (controller.signal.aborted) throw new Error(`request timeout after ${timeoutMs}ms`);
    throw new Error(`connection error: ${error?.message ?? String(error)}`);
  } finally {
    clearTimeout(timer);
  }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** 带重试的 HTML 抓取：网络错误/空结果时重试，间隔 1.5s，最多 3 次 */
async function fetchHtmlWithRetry(url, acceptLang) {
  let lastError;
  for (let attempt = 1; attempt <= 3; attempt++) {
    try {
      const res = await request(url, {
        headers: { "user-agent": USER_AGENT, "accept-language": acceptLang ?? ACCEPT_LANG },
        timeoutMs: 12000,
      });
      if (!res.ok) throw new Error(`HTTP ${res.status} from ${url.split("?")[0]}`);
      const html = res.text;
      // DuckDuckGo 反爬验证页检测（HTTP 202 或验证关键字）
      if (res.status === 202 || /anomaly|captcha|unusual traffic|robot check/i.test(html.slice(0, 4000))) {
        throw new Error("rate-limited (anti-bot challenge, usually temporary)");
      }
      if (html.length > 500) return html;
      lastError = new Error(`empty response (${html.length} bytes)`);
    } catch (error) {
      lastError = error;
    }
    if (attempt < 3) await sleep(1500);
  }
  throw lastError ?? new Error("fetch failed");
}

// ───────────────────────── 免费引擎 ─────────────────────────

async function searchDdg(query, maxResults, opts) {
  const params = new URLSearchParams({ q: query });
  if (opts.region) params.set("kl", opts.region);
  const adlt = opts.safeSearch ?? "off";
  params.set("adlt", adlt === "strict" ? "1" : adlt === "moderate" ? "0" : "-1");
  if (opts.timeRange) {
    const df = { day: "d", week: "w", month: "m", year: "y" }[approximateTimeRange(opts.timeRange.days ?? 7)];
    if (df) params.set("df", df);
  }
  const html = await fetchHtmlWithRetry(`${DDG_HTML_URL}?${params}`);
  const blocks = html.match(/<div class="result results_links[\s\S]*?<\/div>\s*<\/div>\s*<\/div>/g) ?? [];
  const sources = [];
  for (const block of blocks) {
    const urlMatch = block.match(/<a[^>]*class="result__a"[^>]*href="([^"]*)"/);
    const titleMatch = block.match(/<a[^>]*class="result__a"[^>]*>(.*?)<\/a>/);
    const snippetMatch = block.match(/<a[^>]*class="result__snippet"[^>]*>(.*?)<\/a>/);
    const url = extractDdgUrl(urlMatch?.[1]);
    if (!url) continue;
    sources.push({
      url,
      ...(titleMatch ? { title: stripTags(titleMatch[1]) } : {}),
      ...(snippetMatch ? { snippet: stripTags(snippetMatch[1]) } : {}),
    });
  }
  return { sources: uniqueSources(sources, maxResults) };
}

async function searchDdgLite(query, maxResults, opts) {
  const params = new URLSearchParams({ q: query });
  const adlt = opts.safeSearch ?? "off";
  params.set("adlt", adlt === "strict" ? "1" : adlt === "moderate" ? "0" : "-1");
  if (opts.timeRange) {
    const df = { day: "d", week: "w", month: "m", year: "y" }[approximateTimeRange(opts.timeRange.days ?? 7)];
    if (df) params.set("df", df);
  }
  const html = await fetchHtmlWithRetry(`${DDG_LITE_URL}?${params}`);
  const linkMatches = html.match(/<a[^>]*class=['"]result-link['"][^>]*>[\s\S]*?<\/a>/g) ?? [];
  const snippetMatches = html.match(/class=['"]result-snippet['"][^>]*>([\s\S]*?)<\/td>/g) ?? [];
  const sources = [];
  for (let i = 0; i < linkMatches.length; i++) {
    const tag = linkMatches[i];
    const hrefMatch = tag.match(/href="([^"]*)"/);
    const titleMatch = tag.match(/class=['"]result-link['"][^>]*>(.*?)<\/a>/);
    if (!hrefMatch) continue;
    const url = extractDdgUrl(hrefMatch[1]);
    if (!url) continue;
    const snippet = snippetMatches[i]?.match(/class=['"]result-snippet['"][^>]*>([\s\S]*?)<\/td>/)?.[1];
    sources.push({
      url,
      ...(titleMatch ? { title: stripTags(titleMatch[1]) } : {}),
      ...(snippet ? { snippet: stripTags(snippet) } : {}),
    });
  }
  return { sources: uniqueSources(sources, maxResults) };
}

// Bing 无结果时会返回一张完全无关的缓存 SERP，用「查询 token 与结果文本是否重叠」识别。
function queryOverlapTokens(query) {
  const tokens = new Set();
  for (const run of String(query).match(/[\u4e00-\u9fff]+/g) ?? []) {
    if (run.length <= 2) tokens.add(run);
    for (let i = 0; i + 1 < run.length; i++) tokens.add(run.slice(i, i + 2));
  }
  for (const word of String(query).toLowerCase().split(/[^a-z0-9]+/)) {
    if (word.length >= 2) tokens.add(word);
  }
  return [...tokens];
}

function looksRelevant(query, sources) {
  const tokens = queryOverlapTokens(query);
  if (tokens.length === 0) return true;
  return sources.some((s) => {
    const hay = `${s.title ?? ""} ${s.snippet ?? ""} ${s.url ?? ""}`.toLowerCase();
    return tokens.some((t) => hay.includes(t.toLowerCase()));
  });
}

async function searchBing(query, maxResults, opts) {
  const profile = LANG_PROFILES[opts.lang] ?? LANG_PROFILES.zh;
  const market = opts.bingMarket ?? profile.market;
  const params = new URLSearchParams({ q: query, mkt: market });
  const acceptLang = opts.bingMarket ? (MARKET_TO_LANG[market] ?? ACCEPT_LANG) : (profile.acceptLang ?? ACCEPT_LANG);
  const adlt = opts.safeSearch ?? "off";
  if (["off", "moderate", "strict"].includes(adlt)) params.set("adlt", adlt);
  const html = await fetchHtmlWithRetry(`${BING_URL}?${params}`, acceptLang);
  const blocks = html.match(/<li class="b_algo"[\s\S]*?<\/li>/g) ?? [];
  const sources = [];
  for (const block of blocks) {
    const hrefMatch = block.match(/<a[^>]*href="(https?:\/\/[^"]+)"/);
    const titleMatch = block.match(/<h2[^>]*>[\s\S]*?<a[^>]*>(.*?)<\/a>[\s\S]*?<\/h2>/);
    const snippetMatch = block.match(/<p[^>]*>([\s\S]*?)<\/p>/);
    if (!hrefMatch) continue;
    sources.push({
      url: hrefMatch[1],
      ...(titleMatch ? { title: stripTags(titleMatch[1]) } : {}),
      ...(snippetMatch ? { snippet: stripTags(snippetMatch[1]) } : {}),
    });
  }
  if (sources.length > 0 && !looksRelevant(query, sources)) return { sources: [] };
  return { sources: uniqueSources(sources, maxResults) };
}

async function searchSearxng(query, maxResults, opts) {
  const instances = opts.searxngInstances?.length ? opts.searxngInstances : SEARXNG_INSTANCES;
  const errors = [];
  for (const base of instances) {
    try {
      const params = new URLSearchParams({ q: query, format: "json" });
      if (opts.timeRange) {
        const tr = { day: "day", week: "week", month: "month", year: "year" }[
          approximateTimeRange(opts.timeRange.days ?? 7)
        ];
        if (tr) params.set("time_range", tr);
      }
      const res = await request(`${base}/search?${params}`, {
        headers: { "user-agent": USER_AGENT, accept: "application/json" },
        timeoutMs: 8000,
      });
      if (!res.ok) {
        errors.push(`${base}: HTTP ${res.status}`);
        continue;
      }
      let data;
      try {
        data = res.json();
      } catch {
        errors.push(`${base}: invalid JSON`);
        continue;
      }
      if (!data || !Array.isArray(data.results)) {
        errors.push(`${base}: invalid JSON`);
        continue;
      }
      const sources = data.results
        .filter((r) => r.url)
        .map((r) => ({
          url: r.url,
          ...(r.title ? { title: String(r.title) } : {}),
          ...(r.content ? { snippet: String(r.content) } : {}),
        }));
      if (sources.length > 0) return { sources: uniqueSources(sources, maxResults) };
      errors.push(`${base}: 0 results`);
    } catch (error) {
      errors.push(`${base}: ${error.message}`);
    }
  }
  const detail = errors.length > 0 ? errors.join(", ") : "no instances configured";
  throw new Error(`all SearXNG instances failed: ${detail.slice(0, 300)}`);
}

async function searchAnysearch(query, maxResults, apiKey) {
  const headers = { "content-type": "application/json", "user-agent": USER_AGENT };
  if (apiKey) headers.authorization = `Bearer ${apiKey}`;
  let res;
  try {
    res = await request(ANYSEARCH_URL, {
      method: "POST",
      headers,
      body: JSON.stringify({ query, max_results: maxResults }),
      timeoutMs: 12000,
    });
  } catch (error) {
    throw new Error(`AnySearch request failed: ${error.message}`);
  }
  if (res.status === 401 || res.status === 403) {
    // key 无效：退回匿名额度再试一次
    const retry = await request(ANYSEARCH_URL, {
      method: "POST",
      headers: { "content-type": "application/json", "user-agent": USER_AGENT },
      body: JSON.stringify({ query, max_results: maxResults }),
      timeoutMs: 12000,
    });
    if (!retry.ok) throw new Error(`AnySearch API error (HTTP ${retry.status})`);
    const data = retry.json();
    if (data.code !== 0) throw new Error(`AnySearch API error: ${data.message ?? data.code}`);
    return { sources: mapAnysearch(data, maxResults), content: "AnySearch key 无效，本次已忽略该 key，改回免费匿名" };
  }
  if (!res.ok) throw new Error(`AnySearch API error (HTTP ${res.status})`);
  const data = res.json();
  if (data.code !== 0) throw new Error(`AnySearch API error: ${data.message ?? data.code}`);
  return { sources: mapAnysearch(data, maxResults) };
}

function mapAnysearch(data, maxResults) {
  return uniqueSources(
    (data.data?.results ?? [])
      .filter((r) => r.url)
      .map((r) => ({
        url: r.url,
        ...(r.title ? { title: String(r.title) } : {}),
        ...(r.snippet ? { snippet: String(r.snippet).slice(0, 300) } : {}),
      })),
    maxResults
  );
}

/** 解析 MCP 响应：兼容 JSON 与 SSE（event: message\ndata: {...}） */
function parseMcpBody(text) {
  try {
    return JSON.parse(text);
  } catch {
    for (const line of text.split("\n")) {
      if (line.startsWith("data: ")) {
        try {
          return JSON.parse(line.slice(6));
        } catch {}
      }
    }
  }
  return null;
}

/** 把 "Title: X\nURL: Y\nPublished: Z\nHighlights:\n..." 格式的文本块解析成 sources */
function parseTitleUrlBlocks(text, maxResults, snippetHeader) {
  const sources = [];
  const blocks = String(text).split(/\n(?=Title:)/);
  for (const block of blocks) {
    const title = block.match(/^Title: (.+)$/m)?.[1];
    const url = block.match(/^URL: (\S+)$/m)?.[1];
    const published = block.match(/^Published: (.+)$/m)?.[1] ?? block.match(/^Acquired: (.+)$/m)?.[1];
    const body = block
      .split(new RegExp(`^${snippetHeader}:$`, "m"))[1]
      ?.split("\n")
      .filter((l) => l.trim() && !l.trim().startsWith("..."))
      .slice(0, 3)
      .join(" ");
    if (!url) continue;
    sources.push({
      url,
      ...(title ? { title } : {}),
      ...(body ? { snippet: body.slice(0, 300) } : {}),
      ...(published && /^\d{4}-\d{2}-\d{2}/.test(published) ? { publishedAt: published } : {}),
    });
  }
  return uniqueSources(sources, maxResults);
}

async function searchExaMCP(query, maxResults) {
  const res = await request(EXA_MCP_URL, {
    method: "POST",
    headers: { "content-type": "application/json", accept: "application/json, text/event-stream", "user-agent": USER_AGENT },
    body: JSON.stringify({
      jsonrpc: "2.0",
      id: Date.now(),
      method: "tools/call",
      params: { name: "web_search_exa", arguments: { query, numResults: maxResults } },
    }),
    timeoutMs: 20000,
  });
  if (!res.ok) throw new Error(`Exa MCP error (HTTP ${res.status})`);
  const json = parseMcpBody(res.text);
  if (!json || json.error) throw new Error(`Exa MCP error: ${json?.error?.message ?? "no data"}`);
  const text = (json.result?.content ?? []).filter((b) => b.type === "text").map((b) => b.text).join("\n");
  if (json.result?.isError) throw new Error(`Exa MCP error: ${text.slice(0, 200)}`);
  return { sources: parseTitleUrlBlocks(text, maxResults, "Highlights") };
}

async function searchTavily(query, maxResults, apiKey, timeRange) {
  const body = { query, max_results: Math.min(maxResults, 20), search_depth: "basic" };
  if (timeRange) {
    const tr = approximateTimeRange(timeRange.days ?? 7);
    if (tr) body.time_range = tr;
  }
  const res = await request(TAVILY_URL, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      accept: "application/json",
      "user-agent": USER_AGENT,
      // 无 key 时走 keyless 匿名额度（这是 Tavily 官方支持的免 key 模式）
      ...(apiKey ? { authorization: `Bearer ${apiKey}` } : { "x-tavily-access-mode": "keyless" }),
    },
    body: JSON.stringify(body),
    timeoutMs: 15000,
  });
  if (!res.ok) {
    if (res.status === 401) throw new Error("Tavily API key is invalid (HTTP 401)");
    throw new Error(`Tavily API error (HTTP ${res.status}): ${res.text.slice(0, 200)}`);
  }
  const data = res.json();
  return {
    sources: uniqueSources(
      (data.results ?? [])
        .filter((r) => r.url)
        .map((r) => ({
          url: r.url,
          ...(r.title ? { title: String(r.title) } : {}),
          ...(r.content ? { snippet: String(r.content).slice(0, 300) } : {}),
        })),
      maxResults
    ),
  };
}

async function searchKeenable(query, maxResults, apiKey, timeRange) {
  return apiKey
    ? searchKeenableREST(query, maxResults, apiKey, timeRange)
    : searchKeenableMCP(query, maxResults, timeRange);
}

async function searchKeenableREST(query, maxResults, apiKey, timeRange) {
  const body = { query, mode: "realtime" };
  if (timeRange) {
    if (timeRange.after) body.published_after = timeRange.after;
    else if (timeRange.days !== undefined) body.published_after = formatKeenableRelative(timeRange.days);
  }
  const res = await request(KEENABLE_URL, {
    method: "POST",
    headers: { "x-api-key": apiKey, "content-type": "application/json", accept: "application/json", "user-agent": USER_AGENT },
    body: JSON.stringify(body),
    timeoutMs: 20000,
  });
  if (!res.ok) {
    if (res.status === 401) throw new Error("Keenable API key is invalid (HTTP 401)");
    throw new Error(`Keenable API error (HTTP ${res.status}): ${res.text.slice(0, 200)}`);
  }
  const data = res.json();
  return {
    sources: uniqueSources(
      (data.results ?? [])
        .filter((r) => r.url)
        .map((r) => ({
          url: r.url,
          ...(r.title ? { title: String(r.title) } : {}),
          ...(r.snippet ?? r.description ? { snippet: String(r.snippet ?? r.description).slice(0, 300) } : {}),
          ...(r.published_at ? { publishedAt: String(r.published_at) } : {}),
        })),
      maxResults
    ),
  };
}

async function searchKeenableMCP(query, maxResults, timeRange) {
  const args = { query };
  if (timeRange) {
    if (timeRange.after) args.published_after = timeRange.after;
    else if (timeRange.days !== undefined) args.published_after = formatKeenableRelative(timeRange.days);
  }
  const res = await request(KEENABLE_MCP_URL, {
    method: "POST",
    headers: { "content-type": "application/json", accept: "application/json, text/event-stream", "user-agent": USER_AGENT },
    body: JSON.stringify({
      jsonrpc: "2.0",
      id: Date.now(),
      method: "tools/call",
      params: { name: "search_web_pages", arguments: args },
    }),
    timeoutMs: 25000,
  });
  if (!res.ok) throw new Error(`Keenable MCP error (HTTP ${res.status})`);
  const data = parseMcpBody(res.text);
  if (!data || data.error) throw new Error(`Keenable MCP error: ${data?.error?.message ?? "no data"}`);
  const text = (data.result?.content ?? []).filter((b) => b.type === "text").map((b) => b.text).join("\n");
  if (data.result?.isError) throw new Error(`Keenable MCP error: ${text.slice(0, 200)}`);
  return { sources: parseTitleUrlBlocks(text, maxResults, "Snippets") };
}

const FIRECRAWL_TBS = { day: "qdr:d", week: "qdr:w", month: "qdr:m", year: "qdr:y" };

function formatFirecrawlDate(date) {
  const [y, m, d] = String(date).split("-").map((n) => parseInt(n, 10));
  return `${m}/${d}/${y}`;
}

async function searchFirecrawl(query, maxResults, apiKey, timeRange) {
  const body = { query, limit: Math.min(Math.max(maxResults, 1), 10) };
  if (timeRange) {
    if (timeRange.after) body.tbs = `cdr:1,cd_min:${formatFirecrawlDate(timeRange.after)}`;
    else if (timeRange.days !== undefined) {
      const tr = approximateTimeRange(timeRange.days);
      if (FIRECRAWL_TBS[tr]) body.tbs = FIRECRAWL_TBS[tr];
    }
  }
  const res = await request(FIRECRAWL_URL, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      accept: "application/json",
      "user-agent": USER_AGENT,
      ...(apiKey ? { authorization: `Bearer ${apiKey}` } : {}),
    },
    body: JSON.stringify(body),
    timeoutMs: 20000,
  });
  if (!res.ok) {
    if (res.status === 401) throw new Error("Firecrawl API key is invalid (HTTP 401)");
    if (res.status === 429) throw new Error("Firecrawl rate limit exceeded (HTTP 429) - configure FIRECRAWL_API_KEY for higher limits");
    throw new Error(`Firecrawl API error (HTTP ${res.status}): ${res.text.slice(0, 200)}`);
  }
  const data = res.json();
  return {
    sources: uniqueSources(
      (data.data?.web ?? [])
        .filter((r) => r.url)
        .map((r) => ({
          url: r.url,
          ...(r.title ? { title: String(r.title) } : {}),
          ...(r.description ? { snippet: String(r.description).slice(0, 300) } : {}),
        })),
      maxResults
    ),
  };
}

const PARALLEL_MCP_SESSION = (() => {
  let id = "";
  for (let i = 0; i < 32; i++) id += Math.floor(Math.random() * 16).toString(16);
  return id;
})();

async function searchParallel(query, maxResults, apiKey, timeRange) {
  if (!apiKey) return searchParallelMCP(query, maxResults, timeRange);
  const body = {
    objective: query,
    search_queries: [query],
    mode: "fast",
    advanced_settings: { max_results: Math.min(Math.max(maxResults, 1), 20) },
  };
  if (timeRange) {
    const after = timeRange.after ?? (timeRange.days !== undefined ? isoDaysAgo(timeRange.days).slice(0, 10) : undefined);
    if (after) body.advanced_settings.source_policy = { after_date: after };
  }
  const res = await request(PARALLEL_URL, {
    method: "POST",
    headers: { "x-api-key": apiKey, "content-type": "application/json", accept: "application/json", "user-agent": USER_AGENT },
    body: JSON.stringify(body),
    timeoutMs: 25000,
  });
  if (!res.ok) {
    if (res.status === 401 || res.status === 403) throw new Error(`Parallel API key is invalid (HTTP ${res.status})`);
    if (res.status === 402) throw new Error(`Parallel quota/billing error (HTTP 402). ${res.text.slice(0, 200)}`);
    throw new Error(`Parallel API error (HTTP ${res.status}): ${res.text.slice(0, 200)}`);
  }
  return { sources: mapParallel(res.json().results ?? [], maxResults) };
}

function mapParallel(results, maxResults) {
  return uniqueSources(
    results
      .filter((r) => r.url)
      .map((r) => {
        const excerpt = (r.excerpts ?? []).find((e) => String(e).trim().length > 0);
        return {
          url: r.url,
          ...(r.title ? { title: String(r.title) } : {}),
          ...(excerpt ? { snippet: String(excerpt).slice(0, 300) } : {}),
          ...(r.publish_date ? { publishedAt: String(r.publish_date) } : {}),
        };
      }),
    maxResults
  );
}

async function searchParallelMCP(query, maxResults, timeRange) {
  const after = timeRange
    ? timeRange.after ?? (timeRange.days !== undefined ? isoDaysAgo(timeRange.days).slice(0, 10) : undefined)
    : undefined;
  const objective = after ? `${query} (prefer results published after ${after})` : query;
  const res = await request(PARALLEL_MCP_URL, {
    method: "POST",
    headers: { "content-type": "application/json", accept: "application/json, text/event-stream", "user-agent": USER_AGENT },
    body: JSON.stringify({
      jsonrpc: "2.0",
      id: Date.now(),
      method: "tools/call",
      params: { name: "web_search", arguments: { objective, search_queries: [query], session_id: PARALLEL_MCP_SESSION } },
    }),
    timeoutMs: 25000,
  });
  if (!res.ok) throw new Error(`Parallel MCP error (HTTP ${res.status})`);
  const data = parseMcpBody(res.text);
  if (!data || data.error) throw new Error(`Parallel MCP error: ${data?.error?.message ?? "no data"}`);
  const blocks = (data.result?.content ?? []).filter((b) => b.type === "text").map((b) => b.text);
  if (data.result?.isError) throw new Error(`Parallel MCP error: ${blocks.join(" ").slice(0, 200) || "tool call failed"}`);
  let payload = null;
  for (const block of blocks) {
    try {
      payload = JSON.parse(block);
      break;
    } catch {}
  }
  return { sources: mapParallel(payload?.results ?? [], maxResults) };
}

// ───────────────────────── 付费/带 key 引擎 ─────────────────────────

async function searchExa(query, maxResults, apiKey, timeRange) {
  if (!apiKey) throw new Error("Exa search requires EXA_API_KEY");
  const body = {
    query,
    type: "auto",
    contents: { highlights: { highlightsPerUrl: 1 } },
    numResults: maxResults,
  };
  if (timeRange) {
    if (timeRange.after) body.startPublishedDate = timeRange.after;
    else if (timeRange.days !== undefined) body.startPublishedDate = isoDaysAgo(timeRange.days);
  }
  const res = await request(EXA_URL, {
    method: "POST",
    headers: { authorization: `Bearer ${apiKey}`, "content-type": "application/json", accept: "application/json", "user-agent": USER_AGENT },
    body: JSON.stringify(body),
    timeoutMs: 20000,
  });
  if (!res.ok) {
    if (res.status === 401) throw new Error("Exa API key is invalid (HTTP 401)");
    if (res.status === 402) throw new Error(`Exa quota/billing error (HTTP 402). ${res.text.slice(0, 200)}`);
    throw new Error(`Exa API error (HTTP ${res.status}): ${res.text.slice(0, 200)}`);
  }
  const sources = (res.json().results ?? [])
    .map((r) => {
      const snippet = r.highlights?.find((h) => h.trim().length > 0);
      if (!snippet) return null;
      return {
        url: r.url,
        ...(r.title ? { title: r.title } : {}),
        snippet,
        ...(r.publishedDate ? { publishedAt: r.publishedDate } : {}),
      };
    })
    .filter(Boolean);
  return { sources: uniqueSources(sources, maxResults) };
}

async function searchPerplexity(query, maxResults, apiKey) {
  if (!apiKey) throw new Error("Perplexity search requires PERPLEXITY_API_KEY");
  const res = await request(PERPLEXITY_URL, {
    method: "POST",
    headers: { authorization: `Bearer ${apiKey}`, "content-type": "application/json", accept: "application/json", "user-agent": USER_AGENT },
    body: JSON.stringify({ model: "sonar", max_tokens: 1024, messages: [{ role: "user", content: query }] }),
    timeoutMs: 20000,
  });
  if (!res.ok) {
    if (res.status === 401) throw new Error("Perplexity API key is invalid (HTTP 401)");
    throw new Error(`Perplexity API error (HTTP ${res.status}): ${res.text.slice(0, 200)}`);
  }
  const data = res.json();
  const answer = data.choices?.[0]?.message?.content ?? "";
  const sources = (data.citations ?? []).map((url) => ({ url, ...(answer ? { snippet: answer.slice(0, 200) } : {}) }));
  return { content: answer, sources: uniqueSources(sources, maxResults) };
}

// DeepSeek 搜索 API 的 query 上限是 128 个 Unicode 字符
const DS_QUERY_MAX = 128;

/** 按码点截断到 n 个字符（JS 的 string.length 按 UTF-16 计，中文 emoji 会偏差） */
function clampChars(s, n) {
  const chars = Array.from(s);
  return chars.length > n ? chars.slice(0, n).join("") : s;
}

/**
 * DS Search 的 query 自带搜索语法，时间过滤直接拼 after:（精确到日）。
 * 用户 query 里已写了 after: 就不再追加。总长截断到 128 字符（时间语法优先保留）。
 */
function deepseekQueryWithTime(query, timeRange) {
  const q = String(query ?? "").trim();
  if (!timeRange || /\bafter:/.test(q)) return clampChars(q, DS_QUERY_MAX);
  let date;
  if (typeof timeRange.after === "string") {
    date = timeRange.after;
  } else {
    const days = Number(timeRange.days ?? 0);
    date = new Date(Date.now() - days * 86400000).toISOString().slice(0, 10);
  }
  const suffix = ` after:${date}`;
  const room = DS_QUERY_MAX - Array.from(suffix).length;
  if (room < 1) return clampChars(q, DS_QUERY_MAX); // 极端情况：宁丢时间过滤不丢查询词
  return clampChars(clampChars(q, room).replace(/\s+$/, "") + suffix, DS_QUERY_MAX);
}

/** DeepSeek 自研搜索引擎（/v1/tools/search）：结构化结果，带摘要/日期/权威分 */
async function searchDeepSeek(query, maxResults, apiKey, timeRange) {
  if (!apiKey) throw new Error("DeepSeek search requires DEEPSEEK_API_KEY");
  const res = await request(DEEPSEEK_SEARCH_URL, {
    method: "POST",
    headers: {
      authorization: `Bearer ${apiKey}`,
      "content-type": "application/json",
      accept: "application/json",
      "user-agent": USER_AGENT,
    },
    body: JSON.stringify({
      query: deepseekQueryWithTime(query, timeRange),
      count: Math.min(Math.max(Number(maxResults ?? 5) || 5, 1), 50),
    }),
    timeoutMs: 20000,
  });
  if (!res.ok) {
    if (res.status === 401) throw new Error("DeepSeek API key is invalid (HTTP 401)");
    if (res.status === 402) throw new Error(`DeepSeek balance insufficient (HTTP 402). ${res.text.slice(0, 200)}`);
    if (res.status === 429) throw new Error("DeepSeek search rate limited (HTTP 429), retry later");
    throw new Error(`DeepSeek search API error (HTTP ${res.status}): ${res.text.slice(0, 200)}`);
  }
  const sources = (res.json().results ?? [])
    .filter((r) => r.url)
    .map((r) => ({
      url: r.url,
      ...(r.title ? { title: String(r.title) } : {}),
      ...(r.snippet || r.summary ? { snippet: String(r.snippet || r.summary) } : {}),
      ...(r.date ? { publishedAt: String(r.date) } : {}),
    }));
  return { sources: uniqueSources(sources, maxResults) };
}

/** DeepSeek Fetch API（/v1/tools/fetch）：抓取网页，返回 Markdown 正文 */
async function deepseekFetch(url, apiKey) {
  if (!apiKey) throw new Error("DeepSeek fetch requires DEEPSEEK_API_KEY");
  const u = String(url ?? "").trim();
  if (!/^https?:\/\//.test(u)) throw new Error("DeepSeek fetch requires an http(s) URL");
  const res = await request(DEEPSEEK_FETCH_URL, {
    method: "POST",
    headers: {
      authorization: `Bearer ${apiKey}`,
      "content-type": "application/json",
      accept: "application/json",
      "user-agent": USER_AGENT,
    },
    body: JSON.stringify({ url: u }),
    timeoutMs: 30000,
  });
  if (!res.ok) {
    if (res.status === 401) throw new Error("DeepSeek API key is invalid (HTTP 401)");
    if (res.status === 402) throw new Error("DeepSeek balance insufficient (HTTP 402)");
    if (res.status === 403) throw new Error("DeepSeek fetch blocked by safety policy (HTTP 403)");
    if (res.status === 404) throw new Error("DeepSeek fetch: no extractable content (HTTP 404) — 页面不可用、被站点拦截或没有可提取的正文");
    if (res.status === 429) throw new Error("DeepSeek fetch rate limited (HTTP 429), retry later");
    throw new Error(`DeepSeek fetch API error (HTTP ${res.status})`);
  }
  let data;
  try { data = res.json(); } catch { throw new Error("DeepSeek fetch returned invalid JSON"); }
  return { title: String(data.title ?? ""), content: typeof data.content === "string" ? data.content : "" };
}

// 单页提取只发一次请求，不走搜索引擎的回退链或重试。
async function fetchPage(engine, url, apiKey) {
  if (engine === "deepseek") return deepseekFetch(url, apiKey);
  const headers = { accept: "application/json", "user-agent": USER_AGENT };
  let endpoint, method = "POST", body;
  if (engine === "keenable") {
    const params = new URLSearchParams({ url, live: "true" });
    endpoint = `https://api.keenable.ai/v1/fetch${apiKey ? "" : "/public"}?${params}`;
    method = "GET";
    if (apiKey) headers["X-API-Key"] = apiKey;
    else headers["X-Keenable-Title"] = "free-web-search";
  } else {
    headers["content-type"] = "application/json";
    if (engine === "tavily") {
      endpoint = "https://api.tavily.com/extract";
      body = { urls: [url] };
      headers.authorization = `Bearer ${apiKey}`;
    } else if (engine === "firecrawl") {
      endpoint = "https://api.firecrawl.dev/v2/scrape";
      body = { url, formats: ["markdown"] };
      headers.authorization = `Bearer ${apiKey}`;
    } else if (engine === "parallel") {
      endpoint = "https://api.parallel.ai/v1/extract";
      body = { urls: [url], advanced_settings: { full_content: true } };
      headers["x-api-key"] = apiKey;
    } else if (engine === "doubao") {
      // 豆包 Fetch：字段名是大写 Url；与豆包搜索共用 key 和每月免费额度
      endpoint = "https://open.feedcoopapi.com/search_api/fetch";
      body = { Url: url };
      headers.authorization = `Bearer ${apiKey}`;
    } else {
      endpoint = "https://api.anysearch.com/v1/extract";
      body = { url };
      if (apiKey) headers.authorization = `Bearer ${apiKey}`;
    }
  }
  const res = await request(endpoint, { method, headers, body: body && JSON.stringify(body), timeoutMs: 30000 });
  // 不输出供应商错误正文：其中可能包含密钥或匿名凭据。
  if (!res.ok) throw new Error(`${engine} fetch API error (HTTP ${res.status})`);
  let data;
  try { data = res.json(); } catch { throw new Error(`${engine} fetch returned invalid JSON`); }
  let page;
  if (engine === "tavily") {
    page = data.results?.[0];
    if (!page && data.failed_results?.length) throw new Error("tavily fetch could not extract the URL (failed_results)");
    page = { title: "", content: page?.raw_content };
  } else if (engine === "parallel") {
    page = data.results?.[0];
    if (!page && data.errors?.length) throw new Error("parallel fetch could not extract the URL (errors)");
    page = { title: page?.title, content: page?.full_content };
  } else if (engine === "firecrawl") {
    if (data.success === false) throw new Error("firecrawl fetch failed");
    const status = data.data?.metadata?.statusCode;
    if (Number.isInteger(status) && status >= 400) throw new Error(`firecrawl fetch target error (HTTP ${status})`);
    page = { title: data.data?.metadata?.title, content: data.data?.markdown };
  } else if (engine === "anysearch") {
    if (data.code !== 0) throw new Error("anysearch fetch failed");
    page = data.data;
  } else if (engine === "doubao") {
    // 先查接口级错误（额度/权限/robots 合规），再查目标网页状态码
    const metaError = data.ResponseMetadata?.Error;
    if (metaError) throw new Error(`doubao fetch error ${metaError.Code ?? ""}: ${String(metaError.Message ?? "").slice(0, 160)}`);
    const item = data.Result?.Data ?? {};
    if (Number.isInteger(item.StatusCode) && item.StatusCode >= 400) throw new Error(`doubao fetch target error (HTTP ${item.StatusCode})`);
    page = { title: item.Title, content: item.ContentText };
  } else page = data;
  return { title: typeof page?.title === "string" ? page.title : "", content: typeof page?.content === "string" ? page.content : "" };
}

async function searchSerpbase(query, maxResults, apiKey, opts) {
  if (!apiKey) throw new Error("SerpBase search requires SERPBASE_API_KEY");
  const locale = SERPBASE_LOCALE[opts.lang] ?? SERPBASE_LOCALE.en;
  const res = await request(SERPBASE_URL, {
    method: "POST",
    headers: { "X-API-Key": apiKey, "content-type": "application/json", accept: "application/json", "user-agent": USER_AGENT },
    body: JSON.stringify({ q: query, hl: locale.hl, gl: locale.gl, page: 1 }),
    timeoutMs: 15000,
  });
  if (!res.ok) throw new Error(`SerpBase API error (HTTP ${res.status}): ${res.text.slice(0, 200)}`);
  // SerpBase 始终返回 HTTP 200，业务状态在 status 字段（0 成功 / 1001 key 无效 / 1000 请求非法）
  const data = res.json();
  if (data.status !== 0) {
    if (data.status === 1001) throw new Error("SerpBase API key is invalid or missing (status 1001)");
    throw new Error(`SerpBase API error (status ${data.status}): ${String(data.error ?? "").slice(0, 200)}`);
  }
  return {
    sources: uniqueSources(
      (data.organic ?? [])
        .filter((r) => r.link)
        .map((r) => ({
          url: r.link,
          ...(r.title ? { title: String(r.title) } : {}),
          ...(r.snippet ? { snippet: String(r.snippet) } : {}),
          ...((r.published_at ?? r.date) ? { publishedAt: String(r.published_at ?? r.date) } : {}),
        })),
      maxResults
    ),
  };
}

async function searchYoucom(query, maxResults, apiKey) {
  if (!apiKey) throw new Error("You.com search requires YOUCOM_API_KEY");
  const params = new URLSearchParams({ query, count: String(Math.min(maxResults, 20)) });
  const res = await request(`${YOUCOM_URL}?${params}`, {
    method: "GET",
    headers: { "X-API-Key": apiKey, accept: "application/json", "user-agent": USER_AGENT },
    timeoutMs: 15000,
  });
  if (!res.ok) {
    if (res.status === 401 || res.status === 403) throw new Error(`You.com API key is invalid (HTTP ${res.status})`);
    if (res.status === 402) throw new Error(`You.com quota/billing error (HTTP 402). ${res.text.slice(0, 200)}`);
    if (res.status === 429) throw new Error("You.com rate limit exceeded (HTTP 429)");
    throw new Error(`You.com API error (HTTP ${res.status}): ${res.text.slice(0, 200)}`);
  }
  const hits = res.json().results?.web ?? [];
  return {
    sources: uniqueSources(
      hits
        .filter((r) => r.url)
        .map((r) => {
          const snippet = Array.isArray(r.snippets)
            ? r.snippets.filter((s) => typeof s === "string" && s).join(" ")
            : (r.description ?? "");
          return {
            url: r.url,
            ...(r.title ? { title: String(r.title) } : {}),
            ...(snippet ? { snippet: String(snippet).slice(0, 300) } : {}),
          };
        }),
      maxResults
    ),
  };
}

async function searchBaidu(query, maxResults, apiKey, timeRange) {
  if (!apiKey) throw new Error("Baidu search requires BAIDU_API_KEY");
  const count = Math.min(Math.max(maxResults, 1), 50);
  const searchFilter = {};
  if (timeRange) {
    const now = new Date();
    const end = new Date(now.getTime() + 86400000).toISOString().slice(0, 10);
    let start;
    if (timeRange.after) start = timeRange.after;
    else if (timeRange.days !== undefined) start = new Date(now.getTime() - timeRange.days * 86400000).toISOString().slice(0, 10);
    if (start) searchFilter.range = { page_time: { gte: start, lt: end } };
  }
  const res = await request(BAIDU_URL, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      authorization: `Bearer ${apiKey}`,
      "X-Appbuilder-From": "openclaw",
      "user-agent": USER_AGENT,
    },
    body: JSON.stringify({
      messages: [{ content: query, role: "user" }],
      search_source: "baidu_search_v2",
      resource_type_filter: [{ type: "web", top_k: count }],
      search_filter: searchFilter,
    }),
    timeoutMs: 15000,
  });
  if (!res.ok) {
    if (res.status === 401 || res.status === 403) throw new Error(`Baidu API key is invalid (HTTP ${res.status})`);
    throw new Error(`Baidu API error (HTTP ${res.status}): ${res.text.slice(0, 200)}`);
  }
  const data = res.json();
  if (data.code) throw new Error(`Baidu API error: ${data.message ?? JSON.stringify(data).slice(0, 200)}`);
  return {
    sources: uniqueSources(
      (data.references ?? [])
        .filter((r) => r.url)
        .map((r) => ({
          url: r.url,
          ...(r.title ? { title: String(r.title) } : {}),
          ...(r.content ? { snippet: String(r.content).slice(0, 300) } : {}),
          ...(r.date ? { publishedAt: String(r.date) } : {}),
        })),
      maxResults
    ),
  };
}

async function searchKimi(query, maxResults, apiKey, timeRange) {
  if (!apiKey) throw new Error("Kimi search requires MOONSHOT_API_KEY");
  const limit = Math.min(Math.max(maxResults, 1), 20);
  const res = await request(KIMI_URL, {
    method: "POST",
    headers: { "content-type": "application/json", authorization: `Bearer ${apiKey}`, "user-agent": USER_AGENT },
    body: JSON.stringify({ text_query: query, limit, timeout_seconds: 30 }),
    timeoutMs: 35000,
  });
  if (!res.ok) {
    if (res.status === 401 || res.status === 403) throw new Error(`Kimi API key is invalid (HTTP ${res.status})`);
    throw new Error(`Kimi API error (HTTP ${res.status}): ${res.text.slice(0, 200)}`);
  }
  const data = res.json();
  return {
    sources: uniqueSources(
      (data.search_results ?? [])
        .filter((r) => r.url)
        .map((r) => {
          let snippet = "";
          if (Array.isArray(r.chunks) && r.chunks.length > 0) {
            // chunks 是 [{ text }]，不是字符串数组
            snippet = r.chunks
              .map((c) => (typeof c === "string" ? c : c && typeof c.text === "string" ? c.text : ""))
              .filter(Boolean)
              .join(" ");
          } else if (r.snippet) {
            snippet = String(r.snippet);
          }
          return {
            url: r.url,
            ...(r.title ? { title: String(r.title) } : {}),
            ...(snippet ? { snippet: snippet.slice(0, 300) } : {}),
            ...(r.publish_date ? { publishedAt: String(r.publish_date) } : {}),
          };
        }),
      maxResults
    ),
  };
}

async function searchAliyun(query, maxResults, apiKey, timeRange) {
  if (!apiKey) throw new Error("Aliyun search requires DASHSCOPE_API_KEY");
  const trimmed = String(query).trim();
  const q = trimmed.length < 2 ? `${trimmed} ` : trimmed.slice(0, 500);
  const res = await request(ALIYUN_URL, {
    method: "POST",
    headers: {
      authorization: `Bearer ${apiKey}`,
      "content-type": "application/json",
      accept: "application/json, text/event-stream",
      "user-agent": USER_AGENT,
    },
    body: JSON.stringify({
      jsonrpc: "2.0",
      id: 1,
      method: "tools/call",
      params: { name: "search_pro", arguments: { query: q } },
    }),
    timeoutMs: 35000,
  });
  if (!res.ok) {
    if (res.status === 401 || res.status === 403) throw new Error(`Aliyun API key is invalid (HTTP ${res.status})`);
    throw new Error(`Aliyun API error (HTTP ${res.status}): ${res.text.slice(0, 200)}`);
  }
  const envelope = parseMcpBody(res.text);
  if (!envelope) throw new Error(`Aliyun MCP response is not valid JSON: ${res.text.slice(0, 200)}`);
  if (envelope.error) throw new Error(`Aliyun MCP error ${envelope.error.code}: ${envelope.error.message}`);
  const callResult = envelope.result;
  if (!callResult || typeof callResult !== "object") throw new Error(`Aliyun MCP result missing`);
  if (callResult.isError) throw new Error(`Aliyun tool error: ${JSON.stringify(callResult).slice(0, 200)}`);
  const textItem = (callResult.content ?? []).find((c) => c && c.type === "text" && typeof c.text === "string");
  if (!textItem) return { sources: [] };
  let inner;
  try {
    inner = JSON.parse(textItem.text);
  } catch {
    throw new Error(`Aliyun inner content JSON parse error: ${textItem.text.slice(0, 200)}`);
  }
  return {
    sources: uniqueSources(
      (inner.pages ?? [])
        .filter((p) => p && p.url)
        .map((p) => ({
          url: p.url,
          ...(p.title ? { title: String(p.title) } : {}),
          ...(p.snippet ? { snippet: String(p.snippet).slice(0, 300) } : {}),
          ...(p.published_at ? { publishedAt: String(p.published_at) } : {}),
        })),
      maxResults
    ),
  };
}

// 豆包：错误也返回 HTTP 200，必须解析 ResponseMetadata.Error
const DOUBAO_ERROR_HINTS = {
  10400: "invalid request parameters (check Query/Count/TimeRange)",
  10402: "invalid SearchType (only web|image)",
  10403: "account or permission problem - make sure the key is from the Web Search console",
  10406: "free quota exhausted (500 requests/month on the free tier)",
  10407: "no available free-tier policy - check the account status",
  10500: "Web Search service internal error - retry later",
  700429: "free-tier rate limit hit - slow down and retry",
  100013: "sub-account is not granted TorchlightApiFullAccess",
  700901: "invalid api key",
};

async function searchDoubao(query, maxResults, apiKey, timeRange) {
  if (!apiKey) throw new Error("Doubao search requires DOUBAO_SEARCH_API_KEY");
  const body = {
    Query: String(query).slice(0, 100),
    SearchType: "web",
    Count: Math.min(Math.max(maxResults, 1), 50),
    NeedSummary: true,
    Filter: { NeedUrl: true },
  };
  const range = doubaoTimeRange(timeRange);
  if (range !== undefined) body.TimeRange = range;
  const res = await request(DOUBAO_URL, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      authorization: `Bearer ${apiKey}`,
      "X-Traffic-Tag": "free-web-search-skill",
      "user-agent": USER_AGENT,
    },
    body: JSON.stringify(body),
    timeoutMs: 20000,
  });
  if (!res.ok) {
    if (res.status === 401 || res.status === 403) {
      throw new Error(`Doubao search auth failed (HTTP ${res.status}) - check DOUBAO_SEARCH_API_KEY (Web Search console)`);
    }
    throw new Error(`Doubao search API error (HTTP ${res.status}): ${res.text.slice(0, 200)}`);
  }
  const data = res.json();
  const err = data?.ResponseMetadata?.Error;
  if (err && (err.Code || err.CodeN || err.Message)) {
    const code = err.CodeN ?? err.Code;
    const hint = DOUBAO_ERROR_HINTS[Number(code)] ?? DOUBAO_ERROR_HINTS[String(code)];
    throw new Error(
      `Doubao search API error (${code ?? "unknown"}): ${err.Message ?? "request failed"}${hint ? ` - ${hint}` : ""}`
    );
  }
  return {
    sources: uniqueSources(
      (data?.Result?.WebResults ?? [])
        .filter((item) => item && item.Url)
        .map((item) => {
          const raw = item.Summary || item.Content || item.Snippet || "";
          const snippet = stripTags(String(raw)).replace(/\s+/g, " ").trim();
          return {
            url: String(item.Url),
            ...(item.Title ? { title: stripTags(String(item.Title)).trim() } : {}),
            ...(snippet ? { snippet: snippet.slice(0, snippetCap("doubao")) } : {}),
            ...(item.PublishTime ? { publishedAt: String(item.PublishTime) } : {}),
          };
        }),
      maxResults
    ),
  };
}

// ───────────────────────── 平台搜索 ─────────────────────────

const PLATFORMS = ["github", "v2ex", "bilibili", "reddit", "hn", "stackoverflow", "wikipedia", "npm"];

async function searchPlatform(platform, query, maxResults, lang) {
  const n = maxResults;
  switch (platform) {
    case "github": {
      const res = await request(
        `https://api.github.com/search/repositories?q=${encodeURIComponent(query)}&per_page=${n}`,
        { headers: { "user-agent": USER_AGENT, accept: "application/vnd.github+json" }, timeoutMs: 15000 }
      );
      if (!res.ok) throw new Error(`GitHub API error (HTTP ${res.status})`);
      return {
        sources: (res.json().items ?? []).map((item) => ({
          url: item.html_url,
          title: item.full_name ?? item.name,
          snippet: `${item.description ?? ""}${item.stargazers_count ? ` ⭐${item.stargazers_count}` : ""}`.trim(),
        })),
      };
    }
    case "v2ex": {
      const res = await request("https://www.v2ex.com/api/topics/hot.json", {
        headers: { "user-agent": USER_AGENT },
        timeoutMs: 15000,
      });
      if (!res.ok) throw new Error(`V2EX API error (HTTP ${res.status})`);
      const topics = res.json();
      const q = query.toLowerCase();
      const matched = Array.isArray(topics)
        ? topics.filter((t) => (t.title ?? "").toLowerCase().includes(q) || (t.content ?? "").toLowerCase().includes(q))
        : [];
      return {
        sources: matched.slice(0, n).map((t) => ({
          url: `https://www.v2ex.com/t/${t.id}`,
          title: t.title,
          ...(t.content ? { snippet: String(t.content).slice(0, 200) } : {}),
        })),
      };
    }
    case "bilibili": {
      const res = await request(
        `https://api.bilibili.com/x/web-interface/search/all/v2?keyword=${encodeURIComponent(query)}`,
        { headers: { "user-agent": USER_AGENT, referer: "https://www.bilibili.com" }, timeoutMs: 15000 }
      );
      if (!res.ok) throw new Error(`Bilibili API error (HTTP ${res.status})`);
      const data = res.json();
      if (data.code !== 0) throw new Error(`Bilibili API error: ${data.message ?? data.code}`);
      const sources = [];
      for (const section of data.data?.result ?? []) {
        for (const item of section.data ?? []) {
          if (!item.arcurl) continue;
          sources.push({
            url: item.arcurl,
            title: item.title ? String(item.title).replace(/<[^>]+>/g, "") : item.bvid,
            ...(item.desc ? { snippet: String(item.desc).slice(0, 200) } : {}),
          });
          if (sources.length >= n) break;
        }
        if (sources.length >= n) break;
      }
      return { sources };
    }
    case "reddit": {
      const res = await request(
        `https://old.reddit.com/search.json?q=${encodeURIComponent(query)}&limit=${n}&sort=relevance`,
        {
          headers: { "user-agent": `${USER_AGENT} (free-web-search-skill)`, accept: "application/json" },
          timeoutMs: 15000,
        }
      );
      if (!res.ok) throw new Error(`Reddit API error (HTTP ${res.status})`);
      return {
        sources: (res.json().data?.children ?? [])
          .map((c) => c.data)
          .filter((p) => p && p.url)
          .map((p) => ({
            url: p.url,
            title: p.title ?? "",
            ...(p.selftext ? { snippet: String(p.selftext).slice(0, 200) } : {}),
          })),
      };
    }
    case "hn": {
      const res = await request(
        `https://hn.algolia.com/api/v1/search?query=${encodeURIComponent(query)}&hitsPerPage=${n}`,
        { headers: { "user-agent": USER_AGENT, accept: "application/json" }, timeoutMs: 15000 }
      );
      if (!res.ok) throw new Error(`Hacker News API error (HTTP ${res.status})`);
      return {
        sources: (res.json().hits ?? [])
          .filter((h) => h.title || h.story_title)
          .map((h) => ({
            url: h.url ?? `https://news.ycombinator.com/item?id=${h.objectID}`,
            title: h.title ?? h.story_title,
            snippet: `HN discussion · ${h.points ?? 0} points · ${h.num_comments ?? 0} comments`,
          })),
      };
    }
    case "stackoverflow": {
      const res = await request(
        `https://api.stackexchange.com/2.3/search/advanced?order=desc&sort=relevance&q=${encodeURIComponent(
          query
        )}&site=stackoverflow&pagesize=${n}&filter=!nNPvSNVZJS`,
        { headers: { "user-agent": USER_AGENT, accept: "application/json" }, timeoutMs: 15000 }
      );
      if (!res.ok) throw new Error(`Stack Exchange API error (HTTP ${res.status})`);
      const data = res.json();
      if (data.error_message) throw new Error(`Stack Exchange API error: ${data.error_message}`);
      return {
        sources: (data.items ?? []).map((it) => ({
          url: it.link,
          title: it.title,
          snippet: `${it.is_answered ? "✓ answered" : "unanswered"} · score ${it.score ?? 0} · ${it.answer_count ?? 0} answers`,
        })),
      };
    }
    case "wikipedia": {
      const host = lang === "en" ? "en.wikipedia.org" : "zh.wikipedia.org";
      const res = await request(
        `https://${host}/w/api.php?action=query&list=search&srsearch=${encodeURIComponent(query)}&format=json&srlimit=${n}`,
        { headers: { "user-agent": USER_AGENT, accept: "application/json" }, timeoutMs: 15000 }
      );
      if (!res.ok) throw new Error(`Wikipedia API error (HTTP ${res.status})`);
      return {
        sources: (res.json().query?.search ?? []).map((s) => ({
          url: `https://${host}/wiki/${encodeURIComponent(String(s.title).replace(/ /g, "_"))}`,
          title: s.title,
          ...(s.snippet ? { snippet: stripTags(s.snippet).slice(0, 200) } : {}),
        })),
      };
    }
    case "npm": {
      const res = await request(
        `https://registry.npmjs.com/-/v1/search?text=${encodeURIComponent(query)}&size=${n}`,
        { headers: { "user-agent": USER_AGENT, accept: "application/json" }, timeoutMs: 15000 }
      );
      if (!res.ok) throw new Error(`npm registry API error (HTTP ${res.status})`);
      return {
        sources: (res.json().objects ?? [])
          .map((o) => o.package)
          .filter((p) => p && p.name)
          .map((p) => ({
            url: p.links?.npm ?? `https://www.npmjs.com/package/${p.name}`,
            title: p.name,
            ...(p.description || p.version
              ? { snippet: `v${p.version ?? "?"}${p.description ? ` — ${String(p.description).slice(0, 160)}` : ""}` }
              : {}),
          })),
      };
    }
    default:
      throw new Error(`unknown platform: ${platform}`);
  }
}

// ───────────────────────── 路由与调度 ─────────────────────────

// 已配置的 key（由 main 在启动时注入）：envName -> value
let CONFIGURED_KEYS = {};

/** 取某个引擎的 key：secrets.yaml / config.yaml 优先，其次环境变量 */
function resolveKey(engine) {
  const env = KEY_ENV[engine];
  if (!env) return "";
  if (CONFIGURED_KEYS[env]) return CONFIGURED_KEYS[env];
  if (engine === "doubao") return process.env.DOUBAO_SEARCH_API_KEY || process.env.WEB_SEARCH_API_KEY || "";
  return process.env[env] ?? "";
}

function hasKey(engine) {
  if (!KEY_ENV[engine]) return true; // 免 key 引擎
  return Boolean(resolveKey(engine));
}

/** 智能路由：按查询语言/时间条件把最可能成功的引擎排在前面 */
/**
 * auto 模式下可用的引擎。
 * 不能一律用 hasKey 判断，要区分三种情况：
 *   1. 完全免 key（bing/ddg/searxng）        → 恒可用
 *   2. key 可选（exa/tavily/keenable/...）    → 没 key 走匿名额度，也恒可用
 *   3. key 必需（perplexity/baidu/kimi/...）  → 没 key 必然失败，必须剔除
 * KEYLESS_ENGINES 正好覆盖 1+2。
 */
function usableEngines(engines) {
  return engines.filter((e) => KEYLESS_ENGINES.includes(e) || hasKey(e));
}

/**
 * 智能路由（auto 模式）：
 *   1. 先剔除「需要 key 但没配」的引擎 —— 它们必然失败，只会污染 attempts
 *   2. 有 searchPriority 时：完全按用户数字排序（不再按语言加权），未列出的按默认回退顺序垫后
 *   3. 无 searchPriority 时：按查询语言分组（中文 bing/baidu/aliyun/deepseek/anysearch，英文 bing/exa/tavily）
 *   4. 带时间过滤时，支持时间过滤的引擎整体前置
 *   5. 其余引擎按默认优先级兜底
 */
function routeEngines(query, { timeRange, engines = ALL_ENGINES, priorities = null } = {}) {
  const usable = usableEngines(engines);
  let ordered;
  if (priorities) {
    // 数字排序生效时，-t 只在「用户列出的引擎」内做时间过滤前置：
    // 能力需要优先于偏好，但未列出的引擎绝不插队。
    ordered = prioritized(usable.filter((e) => e in priorities), priorities);
    if (timeRange) {
      ordered = [
        ...ordered.filter((e) => TIME_ENGINES.includes(e)),
        ...ordered.filter((e) => !TIME_ENGINES.includes(e)),
      ];
    }
  } else {
    const isCjk = /[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]/.test(String(query ?? ""));
    const routeHead = isCjk ? ["bing", "baidu", "aliyun", "deepseek", "anysearch"] : ["bing", "exa", "tavily"];
    ordered = usable.filter((e) => routeHead.includes(e));
    if (timeRange) {
      // 时间过滤优先：能用时间过滤的排前面
      ordered = [
        ...ordered.filter((e) => TIME_ENGINES.includes(e)),
        ...usable.filter((e) => TIME_ENGINES.includes(e) && !ordered.includes(e)),
        ...ordered.filter((e) => !TIME_ENGINES.includes(e)),
      ];
    }
  }
  // 其余按默认回退优先级（而非 ALL_ENGINES 定义顺序）
  const remaining = DEFAULT_FALLBACK_ORDER.filter((e) => usable.includes(e) && !ordered.includes(e));
  return [...ordered, ...remaining];
}

/** 单引擎调度：把 (engine, query, n, timeRange, opts) 映射到具体实现 */
async function runEngine(engine, query, maxResults, timeRange, opts) {
  const key = resolveKey(engine);
  switch (engine) {
    case "ddg":
      return searchDdg(query, maxResults, { ...opts, timeRange });
    case "ddg-lite":
      return searchDdgLite(query, maxResults, { ...opts, timeRange });
    case "bing":
      return searchBing(query, maxResults, opts);
    case "searxng":
      return searchSearxng(query, maxResults, { ...opts, timeRange });
    case "anysearch":
      return searchAnysearch(query, maxResults, key);
    case "exa":
      return key ? searchExa(query, maxResults, key, timeRange) : searchExaMCP(query, maxResults);
    case "tavily":
      return searchTavily(query, maxResults, key, timeRange);
    case "keenable":
      return searchKeenable(query, maxResults, key, timeRange);
    case "firecrawl":
      return searchFirecrawl(query, maxResults, key, timeRange);
    case "parallel":
      return searchParallel(query, maxResults, key, timeRange);
    case "perplexity":
      if (!key) throw new Error("PERPLEXITY_API_KEY is not configured");
      return searchPerplexity(query, maxResults, key);
    case "deepseek":
      if (!key) throw new Error("DEEPSEEK_API_KEY is not configured");
      return searchDeepSeek(query, maxResults, key, timeRange);
    case "serpbase":
      if (!key) throw new Error("SERPBASE_API_KEY is not configured");
      return searchSerpbase(query, maxResults, key, opts);
    case "you":
      if (!key) throw new Error("YOUCOM_API_KEY is not configured");
      return searchYoucom(query, maxResults, key);
    case "baidu":
      if (!key) throw new Error("BAIDU_API_KEY is not configured");
      return searchBaidu(query, maxResults, key, timeRange);
    case "kimi":
      if (!key) throw new Error("MOONSHOT_API_KEY is not configured");
      return searchKimi(query, maxResults, key, timeRange);
    case "aliyun":
      if (!key) throw new Error("DASHSCOPE_API_KEY is not configured");
      return searchAliyun(query, maxResults, key, timeRange);
    case "doubao": {
      // 豆包主 key 名 DOUBAO_SEARCH_API_KEY，官方示例也用 WEB_SEARCH_API_KEY（已在 resolveKey 里回退）
      if (!key) throw new Error("DOUBAO_SEARCH_API_KEY is not configured");
      return searchDoubao(query, maxResults, key, timeRange);
    }
    default:
      throw new Error(`unknown engine: ${engine}`);
  }
}

/** 单引擎执行（含空结果重试一次），返回 {ok, sources, content} 或 {ok:false, error} */
async function runEngineSafe(engine, query, maxResults, timeRange, opts) {
  const attempt = () => runEngine(engine, query, maxResults, timeRange, opts);
  try {
    const result = await attempt();
    if ((result.sources ?? []).length === 0) {
      await sleep(1500);
      const retry = await attempt();
      return { ok: true, ...retry, sources: cleanSources(retry.sources, engine) };
    }
    return { ok: true, ...result, sources: cleanSources(result.sources, engine) };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}

function cleanSources(sources, engine) {
  return (sources ?? []).map((s) => (s.snippet ? { ...s, snippet: cleanSnippet(s.snippet, snippetCap(engine)) } : s));
}

/** 多源并发合并：按规范化 URL 去重，跨源命中的排前面，附 seenIn */
async function multiSearch(query, maxResults, timeRange, opts, explicitEngines) {
  const enabled = opts.engines ?? ALL_ENGINES;
  const targets = Array.isArray(explicitEngines) && explicitEngines.length > 0
    ? [...new Set(explicitEngines.filter((e) => ALL_ENGINES.includes(e) && enabled.includes(e)))]
    : routeEngines(query, { timeRange: Boolean(timeRange), engines: enabled, priorities: opts.searchPriority ?? null }).slice(0, 3);
  if (targets.length === 0) throw new Error("no enabled engines selected");

  const settled = await Promise.all(
    targets.map(async (engine) => ({ engine, res: await runEngineSafe(engine, query, maxResults, timeRange, opts) }))
  );

  const enginesUsed = [];
  const failed = [];
  const skipped = [];
  const urlMap = new Map();
  let seq = 0;

  for (const { engine, res } of settled) {
    if (!res.ok) {
      if (/not configured|requires/i.test(res.error)) skipped.push(engine);
      else failed.push(`${engine} (${res.error})`);
      continue;
    }
    enginesUsed.push(engine);
    for (const source of res.sources ?? []) {
      if (!source?.url) continue;
      let norm;
      try {
        const u = new URL(String(source.url).trim());
        norm = `${u.protocol}//${u.host.toLowerCase()}${u.pathname.replace(/\/+$/, "")}${u.search}${u.hash}`;
      } catch {
        norm = String(source.url).toLowerCase().replace(/\/+$/, "");
      }
      if (urlMap.has(norm)) {
        const entry = urlMap.get(norm);
        entry.seenIn.add(engine);
        if (!entry.source.snippet && source.snippet) entry.source.snippet = source.snippet;
        if (!entry.source.title && source.title) entry.source.title = source.title;
        if (!entry.source.publishedAt && source.publishedAt) entry.source.publishedAt = source.publishedAt;
      } else {
        urlMap.set(norm, { source: { ...source }, seenIn: new Set([engine]), order: seq++ });
      }
    }
  }

  if (enginesUsed.length === 0) {
    const details = [...failed, ...skipped.map((e) => `${e} (no key)`)];
    throw new Error(`multi search failed: ${details.join("; ") || "no engine returned results"}`);
  }

  const merged = [...urlMap.values()]
    .sort((a, b) => (b.seenIn.size !== a.seenIn.size ? b.seenIn.size - a.seenIn.size : a.order - b.order))
    .slice(0, maxResults)
    .map(({ source, seenIn }) => {
      const out = { url: source.url };
      if (source.title) out.title = String(source.title);
      if (source.snippet) out.snippet = String(source.snippet);
      if (source.publishedAt) out.publishedAt = String(source.publishedAt);
      out.seenIn = [...seenIn];
      return out;
    });

  const notes = [`enginesUsed: [${enginesUsed.join(", ")}]`];
  if (failed.length) notes.push(`failed: [${failed.join(", ")}]`);
  if (skipped.length) notes.push(`skipped (no key): [${skipped.join(", ")}]`);

  return { content: `Note: ${notes.join("; ")}`, sources: merged, enginesUsed, failed, skipped };
}

/**
 * 主搜索：按 preferred 选择引擎，失败自动沿回退链换下一个，直到成功或全部失败。
 * preferred 可为 auto / multi / 具体引擎 id。
 */
async function search(query, { engine: preferred = "auto", maxResults = 5, timeRange, opts = {}, fallback = true } = {}) {
  const started = Date.now();
  const n = Math.min(Math.max(Number(maxResults) || 5, 1), 20);
  const enabled = opts.engines ?? ALL_ENGINES;

  if (preferred === "multi") {
    const result = await multiSearch(query, Math.min(Math.max(n, 1), 20), timeRange, opts, opts.explicitEngines);
    return { ok: true, query, requested: preferred, engine: "multi", ...result, elapsedMs: Date.now() - started };
  }

  // 回退链：显式指定的引擎永远排第一（尊重用户选择），其后按默认优先级补齐。
  // 用 DEFAULT_FALLBACK_ORDER 而非 ALL_ENGINES 定义顺序 —— 后者把 ddg 排得很前，
  // 无代理时 ddg 的超时会吃掉全部预算，后面的 bing 根本没机会跑。
  const priorities = opts.searchPriority ?? null;
  const fallbackRest = prioritized(DEFAULT_FALLBACK_ORDER.filter((e) => enabled.includes(e)), priorities);
  const disabledSet = new Set(opts.disabledEngines ?? []);
  // 显式指定的引擎被配置禁用时，要明确告知，而不是静默换一个引擎
  let disabledNote = null;
  if (preferred !== "auto" && preferred !== "multi" && disabledSet.has(preferred)) {
    disabledNote = `${preferred} is disabled in config (disabledEngines), using another engine.`;
  }
  let chain;
  if (preferred === "auto") {
    chain = routeEngines(query, { timeRange: Boolean(timeRange), engines: enabled, priorities });
  } else if (ALL_ENGINES.includes(preferred)) {
    chain = fallback ? [preferred, ...fallbackRest.filter((e) => e !== preferred)] : [preferred];
  } else {
    throw new Error(`unknown engine "${preferred}" — valid: auto, multi, ${ALL_ENGINES.join(", ")}`);
  }
  chain = [...new Set(chain)].filter((e) => enabled.includes(e));
  // --no-fallback 且唯一引擎被禁用 → 直接报错，别静默返回空
  if (chain.length === 0) {
    throw new Error(
      `engine "${preferred}" is disabled in config (disabledEngines) and fallback is off`
    );
  }

  const attempts = [];
  // 总超时预算：串行回退时限制整条引擎链的总时长，防止各引擎超时累加达分钟级。
  // 剩余预算不足 2s 时停止继续尝试，避免「每个引擎都报超时」的噪音。
  // 预算可由配置文件 timeout（秒）覆盖。
  const BUDGET_MS = Number(opts.budgetMs) > 0 ? Number(opts.budgetMs) : 30000;
  const deadline = setDeadline(BUDGET_MS);
  try {
  for (const engine of chain) {
    if (deadline && deadline - Date.now() < 2000) {
      attempts.push({ engine, error: "skipped: time budget exhausted" });
      continue;
    }
    const res = await runEngineSafe(engine, query, n, timeRange, opts);
    if (res.ok && (res.sources ?? []).length > 0) {
      const out = {
        ok: true,
        query,
        requested: preferred,
        engine,
        sources: res.sources,
        elapsedMs: Date.now() - started,
      };
      if (res.content) out.content = res.content;
      // 用了非首选引擎时给出准确提示
      if (disabledNote) {
        out.content = `Note: ${disabledNote}`;
      } else if (engine !== preferred && preferred !== "auto") {
        const prev = attempts[attempts.length - 1];
        out.content = prev && /not configured/i.test(prev.error ?? "")
          ? `Note: ${preferred} unavailable (${prev.error}), using ${engine}.`
          : `Note: ${preferred} unavailable or failed, using ${engine}.`;
      }
      if (disabledNote) out.requestedDisabled = preferred;
      if (attempts.length) out.attempts = attempts;
      return out;
    }
    attempts.push({ engine, error: res.ok ? "returned 0 results" : res.error });
  }

  return {
    ok: false,
    query,
    requested: preferred,
    error: attempts.at(-1)?.error ?? "all search engines failed",
    attempts,
    elapsedMs: Date.now() - started,
  };
  } finally {
    // 退出回退链后解除预算，避免影响同一进程内的后续调用（如 --test）
    setDeadline(0);
  }
}

// ───────────────────────── CLI ─────────────────────────

function parseArgs(argv) {
  const args = { _: [] };
  const takesValue = new Set([
    "-q", "--query", "-e", "--engine", "-n", "--max", "--max-results", "-t", "--time", "--time-range",
    "-p", "--platform", "--lang", "--market", "--safe-search", "--proxy", "--key", "--searxng", "--engines",
    "--fetch", "--fetch-engine",
  ]);
  for (let i = 0; i < argv.length; i++) {
    let a = argv[i];
    if (!a.startsWith("-")) {
      args._.push(a);
      continue;
    }
    let value;
    const eq = a.indexOf("=");
    if (eq > 0) {
      value = a.slice(eq + 1);
      a = a.slice(0, eq);
    }
    if (takesValue.has(a) && value === undefined) {
      value = argv[++i];
    }
    const key = a.replace(/^-+/, "");
    if (value === undefined) args[key] = true;
    else if (args[key] === undefined) args[key] = value;
    else args[key] = [].concat(args[key], value);
  }
  return args;
}

function printHelp() {
  console.log(`free-web-search — 18 引擎网页搜索（Node 版，零依赖）

用法:
  node search.mjs -q "查询" [选项]

选项:
  -q, --query <文本>        搜索词（必填，除非用 --list-engines/--test）
  -e, --engine <引擎>       引擎 id / auto（智能路由，默认）/ multi（多源并发合并）
  -n, --max <数量>          结果条数，默认 5，上限 20
  -t, --time <范围>         day|week|month|year，或 12h/3d/2mo/1y，或 2026-07-01
  -p, --platform <平台>     ${PLATFORMS.join(" | ")}
      --lang <语言>         zh|en|ru|ja|de|fr|es|ko（影响 Bing/SerpBase 本地化）
      --market <市场>       Bing mkt，如 zh-CN / en-US（覆盖 --lang）
      --safe-search <档>    off|moderate|strict（仅 Bing/DDG 生效）
      --searxng <实例>      自定义 SearXNG 实例，逗号分隔
      --fetch <URL>         抓取单页正文（默认 DeepSeek Fetch API）
      --fetch-engine <id>   deepseek|tavily|keenable|firecrawl|parallel|anysearch|doubao（不回退）
      --proxy <URL>         代理，如 http://127.0.0.1:7890
      --key <engine=value>  临时指定密钥（也可用环境变量）
      --no-fallback         只用指定引擎，失败不回退
      --json                输出原始 JSON（默认就是 JSON；此参数为显式兼容）
      --text                输出人类可读文本
      --list-engines        列出引擎及密钥配置状态
      --config-status       输出配置状态（AI 用它判断是否需要向用户提问）
      --init-config         生成 config.yaml 模板（已存在不覆盖，加 --force 覆盖）
      --test                测试引擎可用性
  -h, --help                显示帮助

环境变量（密钥）:
  ${Object.entries(KEY_ENV).map(([e, v]) => `${e}=${v}`).join("\n  ")}
  HTTPS_PROXY / HTTP_PROXY  代理
`);
}

function formatText(result) {
  if (!result.ok) return `搜索失败: ${result.error}`;
  const lines = [`引擎: ${result.engine}${result.requested !== result.engine ? `（请求 ${result.requested}）` : ""}`];
  if (result.content) lines.push(result.content);
  lines.push("");
  for (const s of result.sources) {
    const seen = s.seenIn ? ` [${s.seenIn.join(",")}]` : "";
    lines.push(`- ${s.title ?? s.url}${seen}`);
    lines.push(`  ${s.url}`);
    if (s.snippet) lines.push(`  ${s.snippet}`);
    if (s.publishedAt) lines.push(`  (${s.publishedAt})`);
  }
  if (result.sources.length === 0) lines.push("（无结果）");
  return lines.join("\n");
}

async function listEngines() {
  const rows = ALL_ENGINES.map((e) => ({
    engine: e,
    keyless: KEYLESS_ENGINES.includes(e),
    keyConfigured: hasKey(e),
    timeFilter: TIME_ENGINES.includes(e),
    keyEnv: KEY_ENV[e] ?? null,
  }));
  return { ok: true, engines: rows, proxy: PROXY_URL || null };
}

async function main() {
  const args = parseArgs(process.argv.slice(2));

  if (args.help || args.h) {
    printHelp();
    return;
  }

  // ── 配置：先读文件，CLI 参数随后覆盖 ───────────────────────────────
  const cfg = loadConfig();
  const cfgSettings = cfg.configured ? configToSettings(cfg.values) : {};

  // ── 凭据：secrets.yaml > config.yaml 的 keys: 段 > 环境变量 ─────────
  const keyInfo = loadConfiguredKeys(cfg.values ?? {});
  CONFIGURED_KEYS = keyInfo.keys;
  // 把配置里的 key 同时写进 process.env，方便各引擎实现里已有的读取路径直接生效
  for (const [env, value] of Object.entries(CONFIGURED_KEYS)) {
    if (!process.env[env]) process.env[env] = value;
  }

  // 代理生效顺序：CLI --proxy > 配置显式关闭 > 配置指定 > 环境变量（已在模块初始化时读入）
  const applyProxy = () => {
    if (args.proxy) setProxy(args.proxy);
    else if (cfgSettings.proxyDisabled) setProxy("");
    else if (cfgSettings.proxy) setProxy(cfgSettings.proxy);
  };
  applyProxy();

  // 汇总所有配置类警告（配置解析 / 凭据 / 禁用引擎拼错）
  const statusWarnings = () => {
    const list = [...(cfg.warnings ?? []), ...(keyInfo.warnings ?? []), ...(cfgSettings.priorityWarnings ?? [])];
    if (cfgSettings.unknownDisabled?.length) {
      list.push(`disabledEngines 里有未知引擎，已忽略：${cfgSettings.unknownDisabled.join(", ")}`);
    }
    return list;
  };

  // --config-status：给 AI 判断「是否已配置、要不要向用户提问」用
  if (args["config-status"]) {
    // 哪些引擎现在真的能用（有 key 或本身免 key）
    const keyStatus = {};
    for (const engine of ALL_ENGINES) {
      if (!KEY_ENV[engine]) continue; // 免 key 引擎不列
      const env = KEY_ENV[engine];
      const from = keyInfo.sources[env] ? "config" : process.env[env] ? "env" : null;
      keyStatus[engine] = { configured: hasKey(engine), env, source: from };
    }
    console.log(
      JSON.stringify(
        {
          configured: cfg.configured,
          path: cfg.path,
          reason: cfg.configured ? undefined : cfg.reason,
          settings: cfgSettings,
          raw: cfg.values ?? undefined,
          unknownKeys: cfg.unknownKeys?.length ? cfg.unknownKeys : undefined,
          warnings: statusWarnings().length ? statusWarnings() : undefined,
          proxyInEffect: PROXY_URL || null,
          secretsFile: keyInfo.secretsFile ?? null,
          keys: keyStatus,
          keysConfiguredCount: Object.values(keyStatus).filter((k) => k.configured).length,
          // auto 模式实际会用到哪些引擎（已排除禁用的和无 key 的）
          disabledEngines: cfgSettings.disabledEngines ?? [],
          usableEngines: usableEngines(ALL_ENGINES.filter((e) => !(cfgSettings.disabledEngines ?? []).includes(e))),
        },
        null,
        2
      )
    );
    return;
  }

  // --init-secrets：生成 secrets.yaml 模板（含各引擎 key 的占位注释）
  if (args["init-secrets"]) {
    const target = keyInfo.secretsFile ?? secretsCandidates()[0];
    if (fs.existsSync(target) && !args.force) {
      console.error(`secrets file already exists: ${target} (use --force to overwrite)`);
      return;
    }
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.writeFileSync(target, SECRETS_TEMPLATE, "utf8");
    try {
      fs.chmodSync(target, 0o600); // 仅本人可读写
    } catch {}
    console.log(JSON.stringify({ ok: true, written: target, note: "已设为 600 权限；该文件应在 .gitignore 中" }, null, 2));
    return;
  }

  // --init-config：生成一份带注释的模板（已存在则不覆盖，除非 --force）
  if (args["init-config"]) {
    const target = cfg.path && cfg.configured ? cfg.path : configCandidates()[0];
    if (fs.existsSync(target) && !args.force) {
      console.error(`config already exists: ${target} (use --force to overwrite)`);
      return;
    }
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.writeFileSync(target, CONFIG_TEMPLATE, "utf8");
    console.log(JSON.stringify({ ok: true, written: target }, null, 2));
    return;
  }

  // --key engine=value 临时注入密钥
  if (args.key) {
    for (const pair of [].concat(args.key)) {
      const [engine, ...rest] = String(pair).split("=");
      const env = KEY_ENV[engine];
      if (!env) {
        console.error(`warning: 未知引擎 "${engine}"，忽略 --key`);
        continue;
      }
      process.env[env] = rest.join("=");
    }
  }
  if (args.searxng) {
    args.searxngInstances = String(args.searxng).split(",").map((s) => s.trim()).filter(Boolean);
  } else if (cfgSettings.searxng) {
    args.searxngInstances = String(cfgSettings.searxng).split(",").map((s) => s.trim()).filter(Boolean);
  }

  if (args["list-engines"]) {
    const out = await listEngines();
    console.log(JSON.stringify(out, null, 2));
    return;
  }

  if (args.test) {
    const q = args.query ?? args.q ?? "DeepSeek Harness";
    const results = [];
    for (const engine of ALL_ENGINES) {
      const r = await runEngineSafe(engine, q, 2, undefined, { lang: "zh" });
      results.push(
        r.ok
          ? {
              engine,
              status: r.sources.length > 0 ? "ok" : "empty",
              results: r.sources.length,
              sampleTitle: r.sources[0]?.title,
              sampleUrl: r.sources[0]?.url,
            }
          : { engine, status: "fail", error: r.error }
      );
    }
    const out = { ok: true, query: q, proxy: PROXY_URL || null, results };
    console.log(JSON.stringify(out, null, 2));
    return;
  }

  // --fetch URL：抓取网页正文。默认后端：--fetch-engine > 配置 fetchPriority（跳过没配 key 的）> deepseek。
  if (args.fetch) {
    let engine = args["fetch-engine"];
    if (!engine) {
      const fp = cfgSettings.fetchPriority ?? {};
      const ordered = Object.keys(fp).sort((a, b) => fp[a] - fp[b]);
      engine = ordered.find((e) => FETCH_ENGINES.includes(e) && (["keenable", "anysearch"].includes(e) || resolveKey(e))) ?? "deepseek";
    }
    if (!FETCH_ENGINES.includes(engine)) throw new Error(`unknown fetch engine; use: ${FETCH_ENGINES.join(", ")}`);
    const inputUrl = typeof args.fetch === "string" ? args.fetch.trim() : "";
    let validUrl = false;
    try {
      const authority = /^https?:\/\/([^/?#]+)/i.exec(inputUrl)?.[1];
      const parsed = new URL(inputUrl);
      validUrl = !!authority && !authority.includes("@") && !authority.includes("\\") &&
        !/\s/.test(inputUrl) && ["http:", "https:"].includes(parsed.protocol) &&
        !!parsed.hostname && !parsed.username && !parsed.password;
    } catch { /* 无效 URL 或端口 */ }
    if (!validUrl) throw new Error("--fetch requires an http(s) URL without credentials");
    const key = resolveKey(engine);
    if (!key && !["keenable", "anysearch"].includes(engine)) throw new Error(`${KEY_ENV[engine]} is not configured（写入 secrets.yaml 或设置环境变量）`);
    const started = Date.now();
    const page = await fetchPage(engine, args.fetch.trim(), key);
    if (!page.content.trim()) throw new Error(`${engine} fetch returned empty content`);
    const out = {
      ok: true,
      fetch: true,
      engine: `${engine}-fetch`,
      url: args.fetch,
      title: page.title,
      content: page.content,
      elapsedMs: Date.now() - started,
    };
    if (args.text) console.log(`${out.title ? out.title + "\n\n" : ""}${out.content}`);
    else console.log(JSON.stringify(out, null, 2));
    return;
  }

  if (args.platform || args.p) {
    const platform = args.platform ?? args.p;
    const query = args.query ?? args.q;
    if (!query) throw new Error("--query is required");
    if (!PLATFORMS.includes(platform)) throw new Error(`unknown platform "${platform}" — use: ${PLATFORMS.join(", ")}`);
    const n = Math.min(Number(args.max ?? args.n ?? 5) || 5, 10);
    const started = Date.now();
    const result = await searchPlatform(platform, query, n, args.lang ?? "zh");
    const out = {
      ok: true,
      query,
      platform,
      engine: platform,
      sources: result.sources,
      elapsedMs: Date.now() - started,
    };
    console.log(args.text ? formatText(out) : JSON.stringify(out, null, 2));
    return;
  }

  const query = args.query ?? args.q ?? (args._.length ? args._.join(" ") : "");
  if (!query || !String(query).trim()) {
    printHelp();
    process.exitCode = 2;
    return;
  }

  const timeRangeRaw = args.time ?? args.t ?? args["time-range"];
  const timeRange = parseTimeRange(timeRangeRaw);
  if (timeRangeRaw && !timeRange) {
    console.error(`warning: 无法解析时间范围 "${timeRangeRaw}"，已忽略`);
  }

  // --engines a,b,c：限定候选引擎集（同时作用于 auto 路由、回退链和 multi 并发）
  let engineList = args.engines
    ? String(args.engines).split(",").map((s) => s.trim()).filter((e) => ALL_ENGINES.includes(e))
    : null;
  if (args.engines && engineList.length === 0) {
    console.error(`warning: --engines 中没有有效引擎，已忽略`);
    engineList = null;
  }

  // 配置文件里手动禁用的引擎（disabledEngines）。--engines 显式指定时以 CLI 为准。
  const disabledEngines = cfgSettings.disabledEngines ?? [];
  if (cfgSettings.unknownDisabled?.length) {
    console.error(`warning: disabledEngines 里有未知引擎，已忽略: ${cfgSettings.unknownDisabled.join(", ")}`);
  }

  // 候选引擎集 = CLI --engines（若有）或全量，再剔除被禁用的
  const candidateEngines = (engineList ?? ALL_ENGINES).filter((e) => !disabledEngines.includes(e));
  if (candidateEngines.length === 0) {
    throw new Error(
      "所有引擎都被禁用了 —— 检查 config.yaml 的 disabledEngines，或至少保留一个"
    );
  }

  // 参数优先级：CLI 显式传参 > 配置文件 > 内置默认
  const opts = {
    lang: args.lang ?? cfgSettings.lang ?? "zh",
    bingMarket: args.market ?? cfgSettings.market,
    safeSearch: args["safe-search"] ?? cfgSettings.safeSearch ?? "off",
    searxngInstances: args.searxngInstances,
    engines: candidateEngines,
    explicitEngines: engineList ? engineList.filter((e) => !disabledEngines.includes(e)) : undefined,
    disabledEngines,
    searchPriority: cfgSettings.searchPriority,
    budgetMs: typeof cfgSettings.timeout === "number" ? cfgSettings.timeout * 1000 : 30000,
  };

  const result = await search(query, {
    engine: args.engine ?? args.e ?? cfgSettings.engine ?? "auto",
    maxResults: args.max ?? args.n ?? cfgSettings.maxResults ?? 5,
    timeRange,
    opts,
    fallback: !args["no-fallback"],
  });

  console.log(args.text ? formatText(result) : JSON.stringify(result, null, 2));
  if (!result.ok) process.exitCode = 1;
}

main().catch((error) => {
  console.log(JSON.stringify({ ok: false, error: error?.message ?? String(error) }, null, 2));
  process.exitCode = 1;
});
