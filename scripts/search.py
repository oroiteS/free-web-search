#!/usr/bin/env python3
"""
free-web-search — 18 引擎网页搜索 CLI（Python 版，零第三方依赖）

与 scripts/search.mjs 参数、输出完全一致，二者可互换。
只依赖 Python 3.8+ 标准库（urllib / json / re / ssl），不需要 pip install 任何东西。

用法：
    python3 search.py -q "查询" [-e 引擎] [-n 条数] [-t 时间]
    python3 search.py --list-engines
    python3 search.py --test [-q "查询"]
    python3 search.py -p github -q "dsh plugin"
    python3 search.py --fetch https://example.com   # 抓取网页正文（Markdown，走 DeepSeek Fetch API）

引擎密钥全部从环境变量读取（也可用 --key engine=value 临时传）：
    EXA_API_KEY TAVILY_API_KEY KEENABLE_API_KEY FIRECRAWL_API_KEY PARALLEL_API_KEY
    PERPLEXITY_API_KEY SERPBASE_API_KEY DEEPSEEK_API_KEY YOUCOM_API_KEY
    BAIDU_API_KEY MOONSHOT_API_KEY DASHSCOPE_API_KEY DOUBAO_SEARCH_API_KEY ANYSEARCH_API_KEY

代理：设置 HTTPS_PROXY / HTTP_PROXY 环境变量，或 --proxy http://127.0.0.1:7890。
"""

import json
import os
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# ───────────────────────────── 常量 ─────────────────────────────

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
ACCEPT_LANG = "zh-CN,zh;q=0.9,en;q=0.8"

DDG_HTML_URL = "https://html.duckduckgo.com/html/"
DDG_LITE_URL = "https://lite.duckduckgo.com/lite/"
BING_URL = "https://www.bing.com/search"
ANYSEARCH_URL = "https://api.anysearch.com/v1/search"
EXA_MCP_URL = "https://mcp.exa.ai/mcp"
EXA_URL = "https://api.exa.ai/search"
TAVILY_URL = "https://api.tavily.com/search"
KEENABLE_MCP_URL = "https://api.keenable.ai/mcp"
KEENABLE_URL = "https://api.keenable.ai/v1/search"
FIRECRAWL_URL = "https://api.firecrawl.dev/v2/search"
PARALLEL_MCP_URL = "https://search.parallel.ai/mcp"
PARALLEL_URL = "https://api.parallel.ai/v1/search"
PERPLEXITY_URL = "https://api.perplexity.ai/chat/completions"
DEEPSEEK_SEARCH_URL = "https://api.deepseek.com/v1/tools/search"
DEEPSEEK_FETCH_URL = "https://api.deepseek.com/v1/tools/fetch"
SERPBASE_URL = "https://api.serpbase.dev/google/search"
YOUCOM_URL = "https://ydc-index.io/v1/search"
BAIDU_URL = "https://qianfan.baidubce.com/v2/ai_search/web_search"
KIMI_URL = "https://api.moonshot.cn/v1/tools/search"
ALIYUN_URL = "https://dashscope.aliyuncs.com/api/v1/mcps/EnhancedSearch/mcp"
DOUBAO_URL = "https://open.feedcoopapi.com/search_api/web_search"

ALL_ENGINES = [
    "ddg", "ddg-lite", "bing", "searxng", "anysearch",
    "exa", "tavily", "keenable", "firecrawl", "parallel",
    "perplexity", "serpbase", "deepseek", "you",
    "baidu", "kimi", "aliyun", "doubao",
]

# 完全不需要密钥的引擎（开箱即用）
KEYLESS_ENGINES = [
    "ddg", "ddg-lite", "bing", "searxng", "anysearch",
    "exa", "tavily", "keenable", "firecrawl", "parallel",
]

# 支持时间过滤的引擎
TIME_ENGINES = [
    "tavily", "exa", "keenable", "firecrawl", "parallel",
    "searxng", "ddg", "ddg-lite", "baidu", "doubao",
    "deepseek",  # 通过 query 语法 after:/before: 精确过滤
]

# 默认回退优先级：付费/高质引擎在前，稳定免 key 引擎（bing/anysearch）居中，
# 需要代理且易限流的 ddg/searxng 垫底。这个顺序很关键 —— 若把 ddg 排在 bing 前面，
# 无代理环境下 ddg 每次重试会吃掉 12s+ 预算，导致后面的 bing 根本没机会跑。
DEFAULT_FALLBACK_ORDER = [
    "exa", "tavily", "keenable", "firecrawl", "parallel",
    "perplexity", "serpbase", "deepseek", "you",
    "baidu", "kimi", "aliyun", "doubao",
    "bing", "anysearch", "ddg", "ddg-lite", "searxng",
]

# 引擎 → 环境变量名
KEY_ENV = {
    "anysearch": "ANYSEARCH_API_KEY",
    "exa": "EXA_API_KEY",
    "tavily": "TAVILY_API_KEY",
    "keenable": "KEENABLE_API_KEY",
    "firecrawl": "FIRECRAWL_API_KEY",
    "parallel": "PARALLEL_API_KEY",
    "perplexity": "PERPLEXITY_API_KEY",
    "serpbase": "SERPBASE_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "you": "YOUCOM_API_KEY",
    "baidu": "BAIDU_API_KEY",
    "kimi": "MOONSHOT_API_KEY",
    "aliyun": "DASHSCOPE_API_KEY",
    "doubao": "DOUBAO_SEARCH_API_KEY",
}

LANG_PROFILES = {
    "zh": {"market": "zh-CN", "acceptLang": "zh-CN,zh;q=0.9,en;q=0.8"},
    "en": {"market": "en-US", "acceptLang": "en-US,en;q=0.9"},
    "ru": {"market": "ru-RU", "acceptLang": "ru-RU,ru;q=0.9,en;q=0.8"},
    "ja": {"market": "ja-JP", "acceptLang": "ja-JP,ja;q=0.9,en;q=0.8"},
    "de": {"market": "de-DE", "acceptLang": "de-DE,de;q=0.9,en;q=0.8"},
    "fr": {"market": "fr-FR", "acceptLang": "fr-FR,fr;q=0.9,en;q=0.8"},
    "es": {"market": "es-ES", "acceptLang": "es-ES,es;q=0.9,en;q=0.8"},
    "ko": {"market": "ko-KR", "acceptLang": "ko-KR,ko;q=0.9,en;q=0.8"},
}

MARKET_TO_LANG = {
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
}

SERPBASE_LOCALE = {
    "zh": {"hl": "zh-CN", "gl": "cn"},
    "en": {"hl": "en", "gl": "us"},
    "ru": {"hl": "ru", "gl": "ru"},
    "ja": {"hl": "ja", "gl": "jp"},
    "de": {"hl": "de", "gl": "de"},
    "fr": {"hl": "fr", "gl": "fr"},
    "es": {"hl": "es", "gl": "es"},
    "ko": {"hl": "ko", "gl": "kr"},
}

SEARXNG_INSTANCES = [
    "https://opnxng.com",
    "https://priv.au",
    "https://searx.be",
    "https://searx.tiekoetter.com",
    "https://search.inetol.net",
    "https://paulgo.io",
]

PLATFORMS = ["github", "v2ex", "bilibili", "reddit", "hn", "stackoverflow", "wikipedia", "npm"]

TIME_RANGES = ["day", "week", "month", "year"]
DAYS_BY_RANGE = {"day": 1, "week": 7, "month": 30, "year": 365}

# ───────────────────────── 时间过滤解析 ─────────────────────────


def iso_days_ago(days):
    """返回 N 天前的 ISO 时间戳（毫秒精度，与 Node 版一致）"""
    import datetime

    dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def parse_time_range(value):
    """
    解析时间过滤：返回 {"days": n} 或 {"after": "YYYY-MM-DD"}，无效返回 None。
    支持 day/week/month/year、12h/3d/2mo/1y、2026-07-01。
    """
    if value is None:
        return None
    if isinstance(value, dict):
        after = value.get("after")
        if isinstance(after, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", after):
            return {"after": after}
        days = value.get("days")
        if isinstance(days, (int, float)) and days > 0:
            return {"days": days}
        return None
    s = str(value).strip().lower()
    if not s:
        return None
    if s in TIME_RANGES:
        return {"days": DAYS_BY_RANGE[s]}
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return {"after": s}
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(h|hour|hours|d|day|days|w|week|weeks|mo|month|months|y|year|years)", s)
    if m:
        n = float(m.group(1))
        unit = m.group(2)[0]
        if unit == "h":
            days = n / 24
        elif unit == "d":
            days = n
        elif unit == "w":
            days = n * 7
        elif unit == "m":
            days = n * 30
        else:
            days = n * 365
        return {"days": days}
    return None


def approximate_time_range(days):
    """把任意天数映射到只支持固定档的引擎（Tavily / SearXNG / DDG）的最近似档位"""
    if days <= 2:
        return "day"
    if days <= 14:
        return "week"
    if days <= 90:
        return "month"
    return "year"


def format_keenable_relative(days):
    """任意天数 → Keenable 的相对时间格式（12h / Nd / Nmo / Ny）"""
    if days <= 0.5:
        return "12h"
    if days < 1:
        return "%dh" % round(days * 24)
    if days < 30:
        return "%dd" % round(days)
    if days < 365:
        return "%dmo" % round(days / 30)
    return "%dy" % round(days / 365)


def doubao_time_range(time_range):
    """豆包：OneDay/OneWeek/OneMonth/OneYear 或 "YYYY-MM-DD..YYYY-MM-DD" """
    if not time_range:
        return None
    if isinstance(time_range.get("after"), str):
        import datetime

        end = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        return "%s..%s" % (time_range["after"], end)
    days = time_range.get("days")
    if not isinstance(days, (int, float)) or days <= 0:
        return None
    if days <= 1:
        return "OneDay"
    if days <= 7:
        return "OneWeek"
    if days <= 31:
        return "OneMonth"
    return "OneYear"


# ───────────────────────── 文本清洗 ─────────────────────────

_ENTITY_MAP = {
    "amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'", "nbsp": " ",
    "ensp": " ", "emsp": " ", "thinsp": " ", "shy": "",
    "middot": "·", "bull": "•", "hellip": "…", "prime": "′", "Prime": "″",
    "mdash": "—", "ndash": "–", "minus": "−",
    "lsquo": "‘", "rsquo": "’", "ldquo": "“", "rdquo": "”", "sbquo": "‚", "bdquo": "„",
    "laquo": "«", "raquo": "»", "lsaquo": "‹", "rsaquo": "›",
    "copy": "©", "reg": "®", "trade": "™", "sect": "§", "para": "¶", "deg": "°",
    "times": "×", "divide": "÷", "plusmn": "±", "frac12": "½", "frac14": "¼", "frac34": "¾",
    "euro": "€", "pound": "£", "yen": "¥", "cent": "¢", "curren": "¤",
    "larr": "←", "rarr": "→", "uarr": "↑", "darr": "↓", "harr": "↔",
    "le": "≤", "ge": "≥", "ne": "≠", "asymp": "≈", "infin": "∞",
    "eacute": "é", "egrave": "è", "agrave": "à", "ccedil": "ç", "uuml": "ü", "ouml": "ö", "auml": "ä", "szlig": "ß",
}


def decode_entities(text):
    """解码 HTML 实体：Bing/DDG 摘要里常见 &ensp; &middot; &hellip; 等排版实体"""
    s = str(text)
    s = re.sub(r"&#x([0-9a-fA-F]+);", lambda m: chr(int(m.group(1), 16)), s)
    s = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), s)
    s = re.sub(
        r"&([a-zA-Z][a-zA-Z0-9]*);",
        lambda m: _ENTITY_MAP.get(m.group(1), m.group(0)),
        s,
    )
    return s


def strip_tags(html):
    text = re.sub(r"<[^>]+>", " ", str(html))
    text = re.sub(r"\s+", " ", text).strip()
    return decode_entities(text)


SNIPPET_NOISE = re.compile(
    r"\b(sign up|sign in|log in|login|subscribe( to| for)?|member[- ]?only|become a member|"
    r"create (a )?free account|read more|continue reading|story continues|get started|"
    r"install (the )?app|view on|medium membership|join \w+ for free|"
    r"get updates from this writer|stories in your inbox|remember me for|unlock this|"
    r"free to read|become a patron)\b",
    re.IGNORECASE,
)


def clean_snippet(text, max_len=300):
    if not text:
        return text
    s = SNIPPET_NOISE.sub(" ", str(text))
    s = re.sub(r"^\s*(#{1,6}\s*|\[\s*x?\s*\]\s*|-\s*\[\s*x?\s*\]\s*|>\s*)", " ", s, flags=re.MULTILINE)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:max_len]


SNIPPET_MAX_BY_ENGINE = {"doubao": 2000}


def snippet_cap(engine):
    return SNIPPET_MAX_BY_ENGINE.get(engine, 300)


def unique_sources(sources, limit):
    seen = set()
    out = []
    for s in sources:
        url = s.get("url")
        if url and url not in seen:
            seen.add(url)
            out.append(s)
        if len(out) >= limit:
            break
    return out


def extract_ddg_url(rel):
    if not rel:
        return None
    m = re.search(r"uddg=([^&]+)", rel)
    if m:
        return urllib.parse.unquote(m.group(1))
    if rel.startswith("//"):
        return "https:" + rel
    return rel


def _clean_source_dict(d):
    """去掉值为空的字段，保持输出干净（对应 Node 版的对象展开写法）"""
    return {k: v for k, v in d.items() if v not in (None, "")}


# ───────────────────────── 配置文件 ─────────────────────────
#
# 目的：让 AI 在「第一次使用」时问用户一次（用 Python 还是 Node、走不走代理、
# 代理端口多少、默认引擎/条数/语言），把答案落到 config.yaml，之后每次直接读文件，
# 不再重复提问。
#
# 判断方式（AI 侧，也是脚本 --config-status 的输出）：
#   文件不存在 或 内容为空/只有注释 → 视为「未配置」，需要提问
#   文件存在且含有效键值          → 视为「已配置」，直接用，不要再问
#
# 支持的格式：极简 YAML（key: value，缩进或 [section] 分组）与 TOML（key = value）。
# 故意不引入 PyYAML —— 这个 skill 的卖点是零依赖，所以只解析本文件用到的子集。

CONFIG_FILENAME = "config.yaml"
# 凭据单独放一个文件：config.yaml 可能被提交/分享，明文 key 不该混进去
SECRETS_FILENAME = "secrets.yaml"

# 允许出现在配置文件里的键（避免手写错字静默生效）
CONFIG_KEYS = {
    "runtime", "proxy", "enabled", "url", "engine", "maxResults", "max_results",
    "lang", "market", "safeSearch", "safe_search", "timeout", "searxng", "platforms",
    "keys", "key", "disabledEngines", "disabled_engines", "engines",
}

# 这些键的值就是字面字符串，不能被 YAML 的布尔规则吞掉：
# safeSearch: off 必须是 "off"（Bing 的 adlt=off），不能变成 False。
STRING_ONLY_KEYS = {
    "safeSearch", "safe_search", "engine", "lang", "market", "runtime", "url", "searxng", "proxy.url",
}


def config_candidates():
    """配置文件候选路径：优先 skill 根目录，其次脚本同级，最后 ~/.config/free-web-search"""
    here = os.path.dirname(os.path.abspath(__file__))
    list_ = [
        os.path.normpath(os.path.join(here, "..", CONFIG_FILENAME)),  # <skill>/config.yaml  ← 推荐位置
        os.path.join(here, CONFIG_FILENAME),        # <skill>/scripts/config.yaml
    ]
    home = os.path.expanduser("~")
    if home and home != "~":
        list_.append(os.path.join(home, ".config", "free-web-search", CONFIG_FILENAME))
    return list_


def find_config_path():
    for p in config_candidates():
        if os.path.isfile(p):
            return p
    return None


def _strip_config_comment(line):
    """去掉注释与首尾空白；支持行尾 # 注释（引号内的 # 不算）"""
    in_single = in_double = False
    for i, ch in enumerate(line):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            return line[:i]
    return line


def _coerce_config_value(raw, key=""):
    v = raw.strip()
    if len(v) >= 2 and ((v[0] == '"' and v[-1] == '"') or (v[0] == "'" and v[-1] == "'")):
        return v[1:-1]
    if key in STRING_ONLY_KEYS:
        return v
    lower = v.lower()
    if lower in ("true", "yes", "on"):
        return True
    if lower in ("false", "no", "off"):
        return False
    if lower in ("null", "~"):
        return None
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    if re.fullmatch(r"-?\d*\.\d+", v):
        return float(v)
    return v


def _parse_inline_list(raw):
    """解析内联列表：`[a, b, "c"]` -> ["a","b","c"]；非列表返回 None"""
    v = str(raw).strip()
    if not (v.startswith("[") and v.endswith("]")):
        return None
    inner = v[1:-1].strip()
    if not inner:
        return []
    out = []
    for s in inner.split(","):
        s = s.strip()
        if not s:
            continue
        q = re.fullmatch(r"""["'](.*)["']""", s)
        out.append(q.group(1) if q else s)
    return out


def parse_config_text(text):
    """
    解析极简 YAML / TOML。
    只处理 `key: value` 与 `key = value`；分组支持 YAML 缩进和 TOML `[section]`，
    都会展开成扁平键（proxy.enabled / proxy.url），调用方按扁平键读取即可。
    列表支持两种写法：`key: [a, b]` 内联，或 `key:` 后跟缩进的 `- a` / `- b`。
    """
    out = {}
    section = None
    # 分组样式："yaml"（缩进归组，遇到无缩进行即结束）或 "toml"（[section] 持续到下一个段头）
    section_style = None
    # 当前正在收集的列表键（`key:` 后跟 `- item`）
    list_key = None
    for raw_line in str(text).splitlines():
        line = _strip_config_comment(raw_line)
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        body = line.strip()

        # TOML 段头：[proxy] / [proxy.settings]
        sec = re.fullmatch(r"\[([A-Za-z_][A-Za-z0-9_.-]*)\]", body)
        if sec:
            section = sec.group(1)
            section_style = "toml"
            list_key = None
            continue

        # 列表项 `- item`：归到当前的列表键名下
        item = re.match(r"^-\s+(.*)$", body)
        if item and list_key:
            out[list_key].append(_coerce_config_value(item.group(1), list_key))
            continue

        m = re.match(r"^([A-Za-z_][A-Za-z0-9_.-]*)\s*[:=]\s*(.*)$", body)
        if not m:
            continue
        key, value_raw = m.group(1), m.group(2)

        # 内联列表 `key: [a, b]`
        inline = _parse_inline_list(value_raw)
        if inline is not None:
            out[key] = inline
            list_key = None
            if indent == 0:
                section = None
                section_style = None
            continue

        if value_raw == "":
            # YAML 风格的分组标题（如 `proxy:`），后续缩进行归到它名下
            if indent == 0:
                section = key
                section_style = "yaml"
            # 可能是列表头（后续 `- item` 归到这里）；先建空数组，若后续没有列表项则删掉
            out[key] = []
            list_key = key
            continue

        if section_style == "toml" and section:
            # TOML 段内的键（无缩进），持续到下一个 [section]
            out["%s.%s" % (section, key)] = _coerce_config_value(value_raw, "%s.%s" % (section, key))
        elif section_style == "yaml" and section and indent > 0:
            # YAML 缩进分组内的键
            out["%s.%s" % (section, key)] = _coerce_config_value(value_raw, "%s.%s" % (section, key))
        else:
            # 顶层键：同时结束 YAML 分组
            out[key] = _coerce_config_value(value_raw, key)
            if indent == 0:
                section = None
                section_style = None
        list_key = None

    # 清理：`key:` 后面没跟任何 `- item` 的，不是列表，删掉占位的空数组
    for k in [k for k, v in out.items() if isinstance(v, list) and len(v) == 0]:
        del out[k]
    return out


def load_config():
    """
    读取配置。返回：
      {"configured": False, "reason": ..., "path": ...}          —— 文件不存在或为空（需要向用户提问）
      {"configured": True, "path": ..., "values": {...}}          —— 已配置
    """
    file = find_config_path()
    if not file:
        return {
            "configured": False,
            "reason": "config file not found",
            "path": config_candidates()[0],
            "searched": config_candidates(),
        }
    try:
        with open(file, "r", encoding="utf-8") as f:
            text = f.read()
    except Exception as e:  # noqa: BLE001
        return {"configured": False, "reason": "cannot read config: %s" % e, "path": file}
    values = parse_config_text(text)
    if not values:
        return {
            "configured": False,
            "reason": "config file is empty (no effective keys)",
            "path": file,
            "values": values,
        }
    # keys.* 是 API key 段（由 load_configured_keys 单独校验），不算未知键
    # searchPriority.* / fetchPriority.* 由 _extract_priorities 单独校验，也不算未知键
    priority_prefix = re.compile(r"^(search|fetch)Priority\.", re.IGNORECASE)
    unknown = [
        k for k in values
        if not re.match(r"^keys?\.", k, re.IGNORECASE)
        and not priority_prefix.match(k)
        and k.split(".")[-1] not in CONFIG_KEYS
        and k not in CONFIG_KEYS
    ]
    # 常见手误：把顶层键写在 [proxy] 段之后，TOML 会把它算成 proxy.<key>。
    # 这类键看起来「存在」但不会生效，必须提示，否则用户改了配置却毫无效果。
    misplaced = [k[len("proxy."):] for k in values if k.startswith("proxy.")]
    misplaced = [k for k in misplaced if k not in ("enabled", "url")]
    warnings = []
    if misplaced:
        warnings.append(
            "这些键位于 [proxy] 段内（或被 proxy: 缩进吞掉），不会生效：%s。"
            "把它们移到文件顶部（[proxy] 段之前 / 不缩进）即可。" % ", ".join(misplaced)
        )
    if unknown:
        warnings.append("未识别的键（可能拼错）：%s" % ", ".join(unknown))
    return {
        "configured": True,
        "path": file,
        "values": values,
        "unknownKeys": unknown,
        "misplaced": misplaced,
        "warnings": warnings,
    }


def secrets_candidates():
    """secrets.yaml 候选路径（与 config.yaml 同级，外加 ~/.config）"""
    here = os.path.dirname(os.path.abspath(__file__))
    list_ = [
        os.path.normpath(os.path.join(here, "..", SECRETS_FILENAME)),
        os.path.join(here, SECRETS_FILENAME),
    ]
    home = os.path.expanduser("~")
    if home and home != "~":
        list_.append(os.path.join(home, ".config", "free-web-search", SECRETS_FILENAME))
    return list_


def find_secrets_path():
    for p in secrets_candidates():
        if os.path.isfile(p):
            return p
    return None


# 引擎 id → 别名集合（允许在配置里用简写，如 `exa:` 或 `EXA_API_KEY:`）
def _build_key_aliases():
    m = {}
    for engine, env in KEY_ENV.items():
        m[env.lower()] = env
        m[engine.lower()] = env
        m["%sapikey" % engine.lower()] = env
        m["%s_api_key" % engine.lower()] = env
    # 豆包还有官方备用名
    m["web_search_api_key"] = "DOUBAO_SEARCH_API_KEY"
    return m


ENGINE_KEY_ALIASES = _build_key_aliases()


def normalize_key_name(raw):
    """把配置里写的键名归一成环境变量名；认不出返回 None"""
    return ENGINE_KEY_ALIASES.get(str(raw).strip().lower())


def load_configured_keys(config_values=None):
    """
    收集配置里的 key。优先级：secrets.yaml > config.yaml 的 keys: 段 > 环境变量。
    之所以单列 secrets.yaml：config.yaml 有可能被提交或分享给别人，
    而 API key 是私密信息，混在一起迟早泄露。
    """
    keys = {}      # envName -> value
    sources = {}   # envName -> 来源描述
    warnings = []

    def collect(flat_values, source_label):
        for raw_key, value in flat_values.items():
            # 支持 `keys.EXA_API_KEY`、`keys.exa`、`key.exa` 三种前缀写法
            m = re.match(r"^(?:keys?|apiKeys?)\.(.+)$", raw_key, re.IGNORECASE)
            if not m:
                continue
            env = normalize_key_name(m.group(1))
            if not env:
                warnings.append(
                    '%s: 未知的 key 名 "%s"（应写成 EXA_API_KEY / tavily / baidu 这类）'
                    % (source_label, m.group(1))
                )
                continue
            if not isinstance(value, str) or not value.strip():
                continue
            if env in keys:
                continue  # 先到先得（secrets.yaml 先收集）
            keys[env] = value.strip()
            sources[env] = source_label

    # 1) secrets.yaml（最优先）
    secrets_file = find_secrets_path()
    if secrets_file:
        try:
            with open(secrets_file, "r", encoding="utf-8") as f:
                parsed = parse_config_text(f.read())
            # secrets.yaml 允许两种写法：带 keys: 前缀，或直接把 EXA_API_KEY 写在顶层
            flat = {}
            for k, v in parsed.items():
                flat["keys.%s" % re.sub(r"^keys?\.", "", k)] = v
            collect(flat, secrets_file)
        except Exception as e:  # noqa: BLE001
            warnings.append("无法读取 %s: %s" % (secrets_file, e))

    # 2) config.yaml 里的 keys: 段（不推荐，但支持）
    collect(config_values or {}, "config.yaml")

    return {"keys": keys, "sources": sources, "secretsFile": secrets_file, "warnings": warnings}


def _extract_priorities(values, name, valid_set):
    """
    解析 searchPriority / fetchPriority 配置 → {engine: number}（数字越小越优先）。
    支持两种写法：
      1. 数字映射：searchPriority.deepseek = 1
      2. 列表 / 逗号串（顺序即优先级，数字按 1,2,3… 编）：searchPriority = deepseek,bing
    未知引擎或非数字给出警告并忽略；映射与列表同时出现时映射优先，列表只补缺。
    """
    priorities, warnings = {}, []
    prefix = name + "."
    for key, value in values.items():
        if not key.startswith(prefix):
            continue
        eng = key[len(prefix):].strip().lower()
        if eng not in valid_set:
            warnings.append('%s 里有未知引擎 "%s"，已忽略' % (name, key[len(prefix):].strip()))
            continue
        try:
            priorities[eng] = float(value)
        except (TypeError, ValueError):
            warnings.append('%s 里 "%s" 的值 "%s" 不是数字，已忽略' % (name, eng, value))
    raw = values.get(name)
    items = None
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, str) and raw.strip():
        items = [p for p in (s.strip() for s in raw.split(",")) if p]
    if items:
        for idx, item in enumerate(items, 1):
            eng = str(item).strip().lower()
            if not eng:
                continue
            if eng not in valid_set:
                warnings.append('%s 里有未知引擎 "%s"，已忽略' % (name, str(item).strip()))
            elif eng not in priorities:
                priorities[eng] = float(idx)
    return priorities, warnings


def config_to_settings(values):
    """从配置推导出运行时设置（代理、默认引擎、条数等）"""
    s = {}
    if isinstance(values.get("runtime"), str):
        s["runtime"] = values["runtime"].lower()
    if isinstance(values.get("engine"), str):
        s["engine"] = values["engine"]
    max_results = values.get("maxResults", values.get("max_results"))
    if isinstance(max_results, int):
        s["maxResults"] = max_results
    if isinstance(values.get("lang"), str):
        s["lang"] = values["lang"]
    if isinstance(values.get("market"), str):
        s["market"] = values["market"]
    safe = values.get("safeSearch", values.get("safe_search"))
    if isinstance(safe, str):
        s["safeSearch"] = safe
    if isinstance(values.get("timeout"), (int, float)):
        s["timeout"] = values["timeout"]
    if isinstance(values.get("searxng"), str):
        s["searxng"] = values["searxng"]

    # 手动禁用部分引擎：disabledEngines: [ddg, searxng]
    # 也接受逗号分隔字符串 `disabledEngines: ddg,searxng`
    disabled_raw = values.get("disabledEngines", values.get("disabled_engines"))
    if disabled_raw is not None:
        if isinstance(disabled_raw, list):
            items = disabled_raw
        else:
            items = str(disabled_raw).split(",")
        known, unknown_engines = [], []
        for item in items:
            name = str(item).strip().lower()
            if not name:
                continue
            if name in ALL_ENGINES:
                if name not in known:
                    known.append(name)
            else:
                unknown_engines.append(str(item).strip())
        s["disabledEngines"] = known
        if unknown_engines:
            s["unknownDisabled"] = unknown_engines

    # 代理：proxy.enabled / proxy.url 两种写法都认
    proxy_enabled = values.get("proxy.enabled", values.get("proxyEnabled"))
    proxy_url = values.get("proxy.url", values.get("proxyUrl"))
    if proxy_url is None and isinstance(values.get("proxy"), str):
        proxy_url = values["proxy"]
    if proxy_enabled is False:
        s["proxy"] = None          # 显式关闭：忽略环境变量
        s["proxyDisabled"] = True
    elif isinstance(proxy_url, str) and proxy_url.strip():
        s["proxy"] = proxy_url.strip()
    elif proxy_enabled is True:
        s["proxy"] = "http://127.0.0.1:7890"  # 开了但没给地址：用常见默认端口

    # 搜索 / 抓取优先级：数字越小越靠前
    sp, sp_warns = _extract_priorities(values, "searchPriority", ALL_ENGINES)
    fp, fp_warns = _extract_priorities(values, "fetchPriority", FETCH_ENGINES)
    if sp:
        s["searchPriority"] = sp
    if fp:
        s["fetchPriority"] = fp
    if sp_warns or fp_warns:
        s["priorityWarnings"] = sp_warns + fp_warns
    return s


# --init-config 写出的模板：带注释、默认值可直接用
CONFIG_TEMPLATE = """# free-web-search 配置文件
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
"""


# --init-secrets 写出的模板：只放 key，别放其他配置
SECRETS_TEMPLATE = """# free-web-search 凭据文件
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
"""


# ───────────────────────── HTTP 层 ─────────────────────────

PROXY_URL = (
    os.environ.get("HTTPS_PROXY")
    or os.environ.get("https_proxy")
    or os.environ.get("HTTP_PROXY")
    or os.environ.get("http_proxy")
    or ""
)

# 整条回退链的总预算（秒）。>0 时，单次请求的超时会被裁剪到剩余预算。
_REQUEST_DEADLINE = 0.0


def set_proxy(value):
    global PROXY_URL, _OPENER
    PROXY_URL = value or ""
    _OPENER = None


def set_deadline(budget_ms):
    """设置总预算，返回绝对截止时间（time.time() 秒）；0 表示不限制"""
    global _REQUEST_DEADLINE
    _REQUEST_DEADLINE = time.time() + budget_ms / 1000.0 if budget_ms > 0 else 0.0
    return _REQUEST_DEADLINE


def effective_timeout(timeout_s):
    """按总预算裁剪单次请求超时；预算已耗尽则立即失败"""
    if not _REQUEST_DEADLINE:
        return timeout_s
    remaining = _REQUEST_DEADLINE - time.time()
    if remaining <= 0:
        raise RuntimeError("search time budget exhausted")
    return min(timeout_s, remaining)


_SSL_CTX = ssl.create_default_context()
_OPENER = None


def _build_opener():
    """根据 PROXY_URL 构造 opener（urllib 原生支持 http/https 代理）"""
    handlers = []
    if PROXY_URL:
        handlers.append(urllib.request.ProxyHandler({"http": PROXY_URL, "https": PROXY_URL}))
    else:
        handlers.append(urllib.request.ProxyHandler({}))
    handlers.append(urllib.request.HTTPSHandler(context=_SSL_CTX))
    return urllib.request.build_opener(*handlers)


def _get_opener():
    global _OPENER
    if _OPENER is None:
        _OPENER = _build_opener()
    return _OPENER


class Response:
    def __init__(self, ok, status, text):
        self.ok = ok
        self.status = status
        self.text = text

    def json(self):
        return json.loads(self.text)


def request(url, method="GET", headers=None, body=None, timeout=15):
    """
    统一请求出口，返回 Response(ok, status, text)。
    非 2xx 不抛异常（由调用方按状态码给精确错误），网络层错误抛 RuntimeError。
    """
    timeout = effective_timeout(timeout)
    hdrs = dict(headers or {})
    data = body.encode("utf-8") if isinstance(body, str) else body
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with _get_opener().open(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", "replace")
            return Response(200 <= resp.status < 300, resp.status, text)
    except urllib.error.HTTPError as e:
        # HTTPError 也是合法响应：读出 body 供调用方拼错误信息
        try:
            text = e.read().decode("utf-8", "replace")
        except Exception:
            text = ""
        return Response(False, e.code, text)
    except (socket.timeout, TimeoutError):
        raise RuntimeError("request timeout after %ds" % timeout)
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        if isinstance(reason, (socket.timeout, TimeoutError)):
            raise RuntimeError("request timeout after %ds" % timeout)
        raise RuntimeError("connection error: %s" % reason)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError("connection error: %s" % e)


def sleep(ms):
    time.sleep(ms / 1000.0)


def fetch_html_with_retry(url, accept_lang=None):
    """带重试的 HTML 抓取：网络错误/空结果时重试，间隔 1.5s，最多 3 次"""
    last_error = None
    for attempt in range(1, 4):
        try:
            res = request(
                url,
                headers={"user-agent": USER_AGENT, "accept-language": accept_lang or ACCEPT_LANG},
                timeout=12,
            )
            if not res.ok:
                raise RuntimeError("HTTP %d from %s" % (res.status, url.split("?")[0]))
            html = res.text
            # DuckDuckGo 反爬验证页检测（HTTP 202 或验证关键字）
            if res.status == 202 or re.search(
                r"anomaly|captcha|unusual traffic|robot check", html[:4000], re.IGNORECASE
            ):
                raise RuntimeError("rate-limited (anti-bot challenge, usually temporary)")
            if len(html) > 500:
                return html
            last_error = RuntimeError("empty response (%d bytes)" % len(html))
        except Exception as e:  # noqa: BLE001
            last_error = e
        if attempt < 3:
            sleep(1500)
    raise last_error or RuntimeError("fetch failed")


# ───────────────────────── 免费引擎 ─────────────────────────


def search_ddg(query, max_results, opts):
    params = {"q": query}
    if opts.get("region"):
        params["kl"] = opts["region"]
    adlt = opts.get("safeSearch", "off")
    params["adlt"] = "1" if adlt == "strict" else "0" if adlt == "moderate" else "-1"
    tr = opts.get("timeRange")
    if tr:
        df = {"day": "d", "week": "w", "month": "m", "year": "y"}.get(
            approximate_time_range(tr.get("days", 7))
        )
        if df:
            params["df"] = df
    html = fetch_html_with_retry(DDG_HTML_URL + "?" + urllib.parse.urlencode(params))
    blocks = re.findall(r'<div class="result results_links[\s\S]*?</div>\s*</div>\s*</div>', html)
    sources = []
    for block in blocks:
        url_m = re.search(r'<a[^>]*class="result__a"[^>]*href="([^"]*)"', block)
        title_m = re.search(r'<a[^>]*class="result__a"[^>]*>(.*?)</a>', block, re.DOTALL)
        snippet_m = re.search(r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>', block, re.DOTALL)
        url = extract_ddg_url(url_m.group(1) if url_m else None)
        if not url:
            continue
        item = {"url": url}
        if title_m:
            item["title"] = strip_tags(title_m.group(1))
        if snippet_m:
            item["snippet"] = strip_tags(snippet_m.group(1))
        sources.append(item)
    return {"sources": unique_sources(sources, max_results)}


def search_ddg_lite(query, max_results, opts):
    params = {"q": query}
    adlt = opts.get("safeSearch", "off")
    params["adlt"] = "1" if adlt == "strict" else "0" if adlt == "moderate" else "-1"
    tr = opts.get("timeRange")
    if tr:
        df = {"day": "d", "week": "w", "month": "m", "year": "y"}.get(
            approximate_time_range(tr.get("days", 7))
        )
        if df:
            params["df"] = df
    html = fetch_html_with_retry(DDG_LITE_URL + "?" + urllib.parse.urlencode(params))
    link_matches = re.findall(
        r"<a[^>]*class=['\"]result-link['\"][^>]*>[\s\S]*?</a>", html
    )
    snippet_matches = re.findall(
        r"class=['\"]result-snippet['\"][^>]*>([\s\S]*?)</td>", html
    )
    sources = []
    for i, tag in enumerate(link_matches):
        href_m = re.search(r'href="([^"]*)"', tag)
        title_m = re.search(r"class=['\"]result-link['\"][^>]*>(.*?)</a>", tag, re.DOTALL)
        if not href_m:
            continue
        url = extract_ddg_url(href_m.group(1))
        if not url:
            continue
        item = {"url": url}
        if title_m:
            item["title"] = strip_tags(title_m.group(1))
        if i < len(snippet_matches):
            item["snippet"] = strip_tags(snippet_matches[i])
        sources.append(item)
    return {"sources": unique_sources(sources, max_results)}


def query_overlap_tokens(query):
    tokens = set()
    for run in re.findall(r"[\u4e00-\u9fff]+", str(query)):
        if len(run) <= 2:
            tokens.add(run)
        for i in range(len(run) - 1):
            tokens.add(run[i : i + 2])
    for word in re.split(r"[^a-z0-9]+", str(query).lower()):
        if len(word) >= 2:
            tokens.add(word)
    return list(tokens)


def looks_relevant(query, sources):
    tokens = query_overlap_tokens(query)
    if not tokens:
        return True
    for s in sources:
        hay = "%s %s %s" % (s.get("title", ""), s.get("snippet", ""), s.get("url", ""))
        hay = hay.lower()
        for t in tokens:
            if t.lower() in hay:
                return True
    return False


def search_bing(query, max_results, opts):
    profile = LANG_PROFILES.get(opts.get("lang"), LANG_PROFILES["zh"])
    market = opts.get("bingMarket") or profile["market"]
    params = {"q": query, "mkt": market}
    if opts.get("bingMarket"):
        accept_lang = MARKET_TO_LANG.get(market, ACCEPT_LANG)
    else:
        accept_lang = profile.get("acceptLang", ACCEPT_LANG)
    adlt = opts.get("safeSearch", "off")
    if adlt in ("off", "moderate", "strict"):
        params["adlt"] = adlt
    html = fetch_html_with_retry(BING_URL + "?" + urllib.parse.urlencode(params), accept_lang)
    blocks = re.findall(r'<li class="b_algo"[\s\S]*?</li>', html)
    sources = []
    for block in blocks:
        href_m = re.search(r'<a[^>]*href="(https?://[^"]+)"', block)
        title_m = re.search(r"<h2[^>]*>[\s\S]*?<a[^>]*>(.*?)</a>[\s\S]*?</h2>", block, re.DOTALL)
        snippet_m = re.search(r"<p[^>]*>([\s\S]*?)</p>", block)
        if not href_m:
            continue
        item = {"url": href_m.group(1)}
        if title_m:
            item["title"] = strip_tags(title_m.group(1))
        if snippet_m:
            item["snippet"] = strip_tags(snippet_m.group(1))
        sources.append(item)
    # Bing 无结果时会返回无关的缓存页：判为 0 结果，交给回退链换下一个引擎
    if sources and not looks_relevant(query, sources):
        return {"sources": []}
    return {"sources": unique_sources(sources, max_results)}


def search_searxng(query, max_results, opts):
    instances = opts.get("searxngInstances") or SEARXNG_INSTANCES
    errors = []
    for base in instances:
        try:
            params = {"q": query, "format": "json"}
            tr = opts.get("timeRange")
            if tr:
                trv = {"day": "day", "week": "week", "month": "month", "year": "year"}.get(
                    approximate_time_range(tr.get("days", 7))
                )
                if trv:
                    params["time_range"] = trv
            res = request(
                base + "/search?" + urllib.parse.urlencode(params),
                headers={"user-agent": USER_AGENT, "accept": "application/json"},
                timeout=8,
            )
            if not res.ok:
                errors.append("%s: HTTP %d" % (base, res.status))
                continue
            try:
                data = res.json()
            except Exception:
                errors.append("%s: invalid JSON" % base)
                continue
            if not isinstance(data, dict) or not isinstance(data.get("results"), list):
                errors.append("%s: invalid JSON" % base)
                continue
            sources = []
            for r in data["results"]:
                if not r.get("url"):
                    continue
                item = {"url": r["url"]}
                if r.get("title"):
                    item["title"] = str(r["title"])
                if r.get("content"):
                    item["snippet"] = str(r["content"])
                sources.append(item)
            if sources:
                return {"sources": unique_sources(sources, max_results)}
            errors.append("%s: 0 results" % base)
        except Exception as e:  # noqa: BLE001
            errors.append("%s: %s" % (base, e))
    detail = ", ".join(errors) if errors else "no instances configured"
    raise RuntimeError("all SearXNG instances failed: " + detail[:300])


def map_anysearch(data, max_results):
    results = (data.get("data") or {}).get("results") or []
    sources = []
    for r in results:
        if not r.get("url"):
            continue
        item = {"url": r["url"]}
        if r.get("title"):
            item["title"] = str(r["title"])
        if r.get("snippet"):
            item["snippet"] = str(r["snippet"])[:300]
        sources.append(item)
    return unique_sources(sources, max_results)


def search_anysearch(query, max_results, api_key):
    headers = {"content-type": "application/json", "user-agent": USER_AGENT}
    if api_key:
        headers["authorization"] = "Bearer " + api_key
    body = json.dumps({"query": query, "max_results": max_results})
    try:
        res = request(ANYSEARCH_URL, method="POST", headers=headers, body=body, timeout=12)
    except RuntimeError as e:
        raise RuntimeError("AnySearch request failed: %s" % e)
    if res.status in (401, 403):
        # key 无效：退回匿名额度再试一次
        retry = request(
            ANYSEARCH_URL,
            method="POST",
            headers={"content-type": "application/json", "user-agent": USER_AGENT},
            body=body,
            timeout=12,
        )
        if not retry.ok:
            raise RuntimeError("AnySearch API error (HTTP %d)" % retry.status)
        data = retry.json()
        if data.get("code") != 0:
            raise RuntimeError("AnySearch API error: %s" % (data.get("message") or data.get("code")))
        return {
            "sources": map_anysearch(data, max_results),
            "content": "AnySearch key 无效，本次已忽略该 key，改回免费匿名",
        }
    if not res.ok:
        raise RuntimeError("AnySearch API error (HTTP %d)" % res.status)
    data = res.json()
    if data.get("code") != 0:
        raise RuntimeError("AnySearch API error: %s" % (data.get("message") or data.get("code")))
    return {"sources": map_anysearch(data, max_results)}


def parse_mcp_body(text):
    """解析 MCP 响应：兼容 JSON 与 SSE（event: message\\ndata: {...}）"""
    try:
        return json.loads(text)
    except Exception:
        for line in text.split("\n"):
            if line.startswith("data: "):
                try:
                    return json.loads(line[6:])
                except Exception:
                    continue
    return None


def parse_title_url_blocks(text, max_results, snippet_header):
    """把 "Title: X\\nURL: Y\\nPublished: Z\\nHighlights:\\n..." 格式的文本块解析成 sources"""
    sources = []
    blocks = re.split(r"\n(?=Title:)", str(text))
    header_re = re.compile(r"^%s:$" % re.escape(snippet_header), re.MULTILINE)
    for block in blocks:
        title_m = re.search(r"^Title: (.+)$", block, re.MULTILINE)
        url_m = re.search(r"^URL: (\S+)$", block, re.MULTILINE)
        published_m = re.search(r"^Published: (.+)$", block, re.MULTILINE) or re.search(
            r"^Acquired: (.+)$", block, re.MULTILINE
        )
        parts = header_re.split(block)
        body = ""
        if len(parts) > 1:
            lines = [l for l in parts[1].split("\n") if l.strip() and not l.strip().startswith("...")]
            body = " ".join(lines[:3])
        if not url_m:
            continue
        item = {"url": url_m.group(1)}
        if title_m:
            item["title"] = title_m.group(1)
        if body:
            item["snippet"] = body[:300]
        if published_m and re.match(r"^\d{4}-\d{2}-\d{2}", published_m.group(1)):
            item["publishedAt"] = published_m.group(1)
        sources.append(item)
    return unique_sources(sources, max_results)


def search_exa_mcp(query, max_results):
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": int(time.time() * 1000),
            "method": "tools/call",
            "params": {"name": "web_search_exa", "arguments": {"query": query, "numResults": max_results}},
        }
    )
    res = request(
        EXA_MCP_URL,
        method="POST",
        headers={
            "content-type": "application/json",
            "accept": "application/json, text/event-stream",
            "user-agent": USER_AGENT,
        },
        body=body,
        timeout=20,
    )
    if not res.ok:
        raise RuntimeError("Exa MCP error (HTTP %d)" % res.status)
    data = parse_mcp_body(res.text)
    if not data or data.get("error"):
        err = (data or {}).get("error") or {}
        raise RuntimeError("Exa MCP error: %s" % (err.get("message") or "no data"))
    content = (data.get("result") or {}).get("content") or []
    text = "\n".join(b.get("text", "") for b in content if b.get("type") == "text")
    if (data.get("result") or {}).get("isError"):
        raise RuntimeError("Exa MCP error: " + text[:200])
    return {"sources": parse_title_url_blocks(text, max_results, "Highlights")}


def search_tavily(query, max_results, api_key, time_range):
    body = {"query": query, "max_results": min(max_results, 20), "search_depth": "basic"}
    if time_range:
        tr = approximate_time_range(time_range.get("days", 7))
        if tr:
            body["time_range"] = tr
    headers = {"content-type": "application/json", "accept": "application/json", "user-agent": USER_AGENT}
    if api_key:
        headers["authorization"] = "Bearer " + api_key
    else:
        # 无 key 时走 keyless 匿名额度（Tavily 官方支持的免 key 模式）
        headers["x-tavily-access-mode"] = "keyless"
    res = request(TAVILY_URL, method="POST", headers=headers, body=json.dumps(body), timeout=15)
    if not res.ok:
        if res.status == 401:
            raise RuntimeError("Tavily API key is invalid (HTTP 401)")
        raise RuntimeError("Tavily API error (HTTP %d): %s" % (res.status, res.text[:200]))
    data = res.json()
    sources = []
    for r in data.get("results") or []:
        if not r.get("url"):
            continue
        item = {"url": r["url"]}
        if r.get("title"):
            item["title"] = str(r["title"])
        if r.get("content"):
            item["snippet"] = str(r["content"])[:300]
        sources.append(item)
    return {"sources": unique_sources(sources, max_results)}


def search_keenable(query, max_results, api_key, time_range):
    if api_key:
        return search_keenable_rest(query, max_results, api_key, time_range)
    return search_keenable_mcp(query, max_results, time_range)


def search_keenable_rest(query, max_results, api_key, time_range):
    body = {"query": query, "mode": "realtime"}
    if time_range:
        if time_range.get("after"):
            body["published_after"] = time_range["after"]
        elif time_range.get("days") is not None:
            body["published_after"] = format_keenable_relative(time_range["days"])
    res = request(
        KEENABLE_URL,
        method="POST",
        headers={
            "x-api-key": api_key,
            "content-type": "application/json",
            "accept": "application/json",
            "user-agent": USER_AGENT,
        },
        body=json.dumps(body),
        timeout=20,
    )
    if not res.ok:
        if res.status == 401:
            raise RuntimeError("Keenable API key is invalid (HTTP 401)")
        raise RuntimeError("Keenable API error (HTTP %d): %s" % (res.status, res.text[:200]))
    data = res.json()
    sources = []
    for r in data.get("results") or []:
        if not r.get("url"):
            continue
        item = {"url": r["url"]}
        if r.get("title"):
            item["title"] = str(r["title"])
        snippet = r.get("snippet") or r.get("description")
        if snippet:
            item["snippet"] = str(snippet)[:300]
        if r.get("published_at"):
            item["publishedAt"] = str(r["published_at"])
        sources.append(item)
    return {"sources": unique_sources(sources, max_results)}


def search_keenable_mcp(query, max_results, time_range):
    args = {"query": query}
    if time_range:
        if time_range.get("after"):
            args["published_after"] = time_range["after"]
        elif time_range.get("days") is not None:
            args["published_after"] = format_keenable_relative(time_range["days"])
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": int(time.time() * 1000),
            "method": "tools/call",
            "params": {"name": "search_web_pages", "arguments": args},
        }
    )
    res = request(
        KEENABLE_MCP_URL,
        method="POST",
        headers={
            "content-type": "application/json",
            "accept": "application/json, text/event-stream",
            "user-agent": USER_AGENT,
        },
        body=body,
        timeout=25,
    )
    if not res.ok:
        raise RuntimeError("Keenable MCP error (HTTP %d)" % res.status)
    data = parse_mcp_body(res.text)
    if not data or data.get("error"):
        err = (data or {}).get("error") or {}
        raise RuntimeError("Keenable MCP error: %s" % (err.get("message") or "no data"))
    content = (data.get("result") or {}).get("content") or []
    text = "\n".join(b.get("text", "") for b in content if b.get("type") == "text")
    if (data.get("result") or {}).get("isError"):
        raise RuntimeError("Keenable MCP error: " + text[:200])
    return {"sources": parse_title_url_blocks(text, max_results, "Snippets")}


FIRECRAWL_TBS = {"day": "qdr:d", "week": "qdr:w", "month": "qdr:m", "year": "qdr:y"}


def format_firecrawl_date(date):
    y, m, d = [int(x) for x in str(date).split("-")]
    return "%d/%d/%d" % (m, d, y)


def search_firecrawl(query, max_results, api_key, time_range):
    body = {"query": query, "limit": min(max(max_results, 1), 10)}
    if time_range:
        if time_range.get("after"):
            body["tbs"] = "cdr:1,cd_min:%s" % format_firecrawl_date(time_range["after"])
        elif time_range.get("days") is not None:
            tr = approximate_time_range(time_range["days"])
            if FIRECRAWL_TBS.get(tr):
                body["tbs"] = FIRECRAWL_TBS[tr]
    headers = {"content-type": "application/json", "accept": "application/json", "user-agent": USER_AGENT}
    if api_key:
        headers["authorization"] = "Bearer " + api_key
    res = request(FIRECRAWL_URL, method="POST", headers=headers, body=json.dumps(body), timeout=20)
    if not res.ok:
        if res.status == 401:
            raise RuntimeError("Firecrawl API key is invalid (HTTP 401)")
        if res.status == 429:
            raise RuntimeError(
                "Firecrawl rate limit exceeded (HTTP 429) - configure FIRECRAWL_API_KEY for higher limits"
            )
        raise RuntimeError("Firecrawl API error (HTTP %d): %s" % (res.status, res.text[:200]))
    data = res.json()
    sources = []
    for r in (data.get("data") or {}).get("web") or []:
        if not r.get("url"):
            continue
        item = {"url": r["url"]}
        if r.get("title"):
            item["title"] = str(r["title"])
        if r.get("description"):
            item["snippet"] = str(r["description"])[:300]
        sources.append(item)
    return {"sources": unique_sources(sources, max_results)}


PARALLEL_MCP_SESSION = os.urandom(16).hex()


def map_parallel(results, max_results):
    sources = []
    for r in results:
        if not r.get("url"):
            continue
        excerpt = next((e for e in (r.get("excerpts") or []) if str(e).strip()), None)
        item = {"url": r["url"]}
        if r.get("title"):
            item["title"] = str(r["title"])
        if excerpt:
            item["snippet"] = str(excerpt)[:300]
        if r.get("publish_date"):
            item["publishedAt"] = str(r["publish_date"])
        sources.append(item)
    return unique_sources(sources, max_results)


def search_parallel(query, max_results, api_key, time_range):
    if not api_key:
        return search_parallel_mcp(query, max_results, time_range)
    body = {
        "objective": query,
        "search_queries": [query],
        "mode": "fast",
        "advanced_settings": {"max_results": min(max(max_results, 1), 20)},
    }
    if time_range:
        after = time_range.get("after") or (
            iso_days_ago(time_range["days"])[:10] if time_range.get("days") is not None else None
        )
        if after:
            body["advanced_settings"]["source_policy"] = {"after_date": after}
    res = request(
        PARALLEL_URL,
        method="POST",
        headers={
            "x-api-key": api_key,
            "content-type": "application/json",
            "accept": "application/json",
            "user-agent": USER_AGENT,
        },
        body=json.dumps(body),
        timeout=25,
    )
    if not res.ok:
        if res.status in (401, 403):
            raise RuntimeError("Parallel API key is invalid (HTTP %d)" % res.status)
        if res.status == 402:
            raise RuntimeError("Parallel quota/billing error (HTTP 402). " + res.text[:200])
        raise RuntimeError("Parallel API error (HTTP %d): %s" % (res.status, res.text[:200]))
    return {"sources": map_parallel(res.json().get("results") or [], max_results)}


def search_parallel_mcp(query, max_results, time_range):
    after = None
    if time_range:
        after = time_range.get("after") or (
            iso_days_ago(time_range["days"])[:10] if time_range.get("days") is not None else None
        )
    objective = "%s (prefer results published after %s)" % (query, after) if after else query
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": int(time.time() * 1000),
            "method": "tools/call",
            "params": {
                "name": "web_search",
                "arguments": {
                    "objective": objective,
                    "search_queries": [query],
                    "session_id": PARALLEL_MCP_SESSION,
                },
            },
        }
    )
    res = request(
        PARALLEL_MCP_URL,
        method="POST",
        headers={
            "content-type": "application/json",
            "accept": "application/json, text/event-stream",
            "user-agent": USER_AGENT,
        },
        body=body,
        timeout=25,
    )
    if not res.ok:
        raise RuntimeError("Parallel MCP error (HTTP %d)" % res.status)
    data = parse_mcp_body(res.text)
    if not data or data.get("error"):
        err = (data or {}).get("error") or {}
        raise RuntimeError("Parallel MCP error: %s" % (err.get("message") or "no data"))
    content = (data.get("result") or {}).get("content") or []
    blocks = [b.get("text", "") for b in content if b.get("type") == "text"]
    if (data.get("result") or {}).get("isError"):
        raise RuntimeError("Parallel MCP error: " + (" ".join(blocks)[:200] or "tool call failed"))
    payload = None
    for block in blocks:
        try:
            payload = json.loads(block)
            break
        except Exception:
            continue
    return {"sources": map_parallel((payload or {}).get("results") or [], max_results)}


# ───────────────────────── 付费/带 key 引擎 ─────────────────────────


def search_exa(query, max_results, api_key, time_range):
    if not api_key:
        raise RuntimeError("Exa search requires EXA_API_KEY")
    body = {
        "query": query,
        "type": "auto",
        "contents": {"highlights": {"highlightsPerUrl": 1}},
        "numResults": max_results,
    }
    if time_range:
        if time_range.get("after"):
            body["startPublishedDate"] = time_range["after"]
        elif time_range.get("days") is not None:
            body["startPublishedDate"] = iso_days_ago(time_range["days"])
    res = request(
        EXA_URL,
        method="POST",
        headers={
            "authorization": "Bearer " + api_key,
            "content-type": "application/json",
            "accept": "application/json",
            "user-agent": USER_AGENT,
        },
        body=json.dumps(body),
        timeout=20,
    )
    if not res.ok:
        if res.status == 401:
            raise RuntimeError("Exa API key is invalid (HTTP 401)")
        if res.status == 402:
            raise RuntimeError("Exa quota/billing error (HTTP 402). " + res.text[:200])
        raise RuntimeError("Exa API error (HTTP %d): %s" % (res.status, res.text[:200]))
    sources = []
    for r in res.json().get("results") or []:
        snippet = next((h for h in (r.get("highlights") or []) if h.strip()), None)
        if not snippet:
            continue
        item = {"url": r.get("url"), "snippet": snippet}
        if r.get("title"):
            item["title"] = r["title"]
        if r.get("publishedDate"):
            item["publishedAt"] = r["publishedDate"]
        sources.append(item)
    return {"sources": unique_sources(sources, max_results)}


def search_perplexity(query, max_results, api_key):
    if not api_key:
        raise RuntimeError("Perplexity search requires PERPLEXITY_API_KEY")
    body = {"model": "sonar", "max_tokens": 1024, "messages": [{"role": "user", "content": query}]}
    res = request(
        PERPLEXITY_URL,
        method="POST",
        headers={
            "authorization": "Bearer " + api_key,
            "content-type": "application/json",
            "accept": "application/json",
            "user-agent": USER_AGENT,
        },
        body=json.dumps(body),
        timeout=20,
    )
    if not res.ok:
        if res.status == 401:
            raise RuntimeError("Perplexity API key is invalid (HTTP 401)")
        raise RuntimeError("Perplexity API error (HTTP %d): %s" % (res.status, res.text[:200]))
    data = res.json()
    choices = data.get("choices") or []
    answer = ((choices[0].get("message") or {}).get("content") if choices else "") or ""
    citations = data.get("citations") or []
    sources = []
    for url in citations:
        item = {"url": url}
        if answer:
            item["snippet"] = answer[:200]
        sources.append(item)
    return {"content": answer, "sources": unique_sources(sources, max_results)}


# DeepSeek 搜索 API 的 query 上限是 128 个 Unicode 字符
DS_QUERY_MAX = 128


def deepseek_query_with_time(query, time_range):
    """
    DS Search 的 query 自带搜索语法，时间过滤直接拼 after:（精确到日）。
    - {"after": "YYYY-MM-DD"} → "after:YYYY-MM-DD"
    - {"days": n}             → N 天前的日期，同样拼 after:
    用户 query 里已写了 after: 就不再追加。总长截断到 128 字符（时间语法优先保留）。
    """
    q = str(query or "").strip()
    if not time_range or re.search(r"\bafter:", q):
        return q[:DS_QUERY_MAX]
    import datetime

    if isinstance(time_range.get("after"), str):
        date = time_range["after"]
    else:
        days = float(time_range.get("days") or 0)
        dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
        date = dt.strftime("%Y-%m-%d")
    suffix = " after:%s" % date
    room = DS_QUERY_MAX - len(suffix)
    if room < 1:
        return q[:DS_QUERY_MAX]  # 极端情况：宁丢时间过滤不丢查询词
    return (q[:room].rstrip() + suffix)[:DS_QUERY_MAX]


def search_deepseek(query, max_results, api_key, time_range):
    """DeepSeek 自研搜索引擎（/v1/tools/search）：结构化结果，带摘要/日期/权威分"""
    if not api_key:
        raise RuntimeError("DeepSeek search requires DEEPSEEK_API_KEY")
    body = {
        "query": deepseek_query_with_time(query, time_range),
        "count": min(max(int(max_results or 5), 1), 50),
    }
    res = request(
        DEEPSEEK_SEARCH_URL,
        method="POST",
        headers={
            "authorization": "Bearer " + api_key,
            "content-type": "application/json",
            "accept": "application/json",
            "user-agent": USER_AGENT,
        },
        body=json.dumps(body),
        timeout=20,
    )
    if not res.ok:
        if res.status == 401:
            raise RuntimeError("DeepSeek API key is invalid (HTTP 401)")
        if res.status == 402:
            raise RuntimeError("DeepSeek balance insufficient (HTTP 402). " + res.text[:200])
        if res.status == 429:
            raise RuntimeError("DeepSeek search rate limited (HTTP 429), retry later")
        raise RuntimeError("DeepSeek search API error (HTTP %d): %s" % (res.status, res.text[:200]))
    sources = []
    for r in res.json().get("results") or []:
        if not r.get("url"):
            continue
        item = {"url": r["url"]}
        if r.get("title"):
            item["title"] = str(r["title"])
        snippet = r.get("snippet") or r.get("summary") or ""
        if snippet:
            item["snippet"] = str(snippet)
        if r.get("date"):
            item["publishedAt"] = str(r["date"])
        sources.append(item)
    return {"sources": unique_sources(sources, max_results)}


def deepseek_fetch(url, api_key):
    """DeepSeek Fetch API（/v1/tools/fetch）：抓取网页，返回 Markdown 正文"""
    if not api_key:
        raise RuntimeError("DeepSeek fetch requires DEEPSEEK_API_KEY")
    u = str(url or "").strip()
    if not re.match(r"^https?://", u):
        raise RuntimeError("DeepSeek fetch requires an http(s) URL")
    res = request(
        DEEPSEEK_FETCH_URL,
        method="POST",
        headers={
            "authorization": "Bearer " + api_key,
            "content-type": "application/json",
            "accept": "application/json",
            "user-agent": USER_AGENT,
        },
        body=json.dumps({"url": u}),
        timeout=30,
    )
    if not res.ok:
        if res.status == 401:
            raise RuntimeError("DeepSeek API key is invalid (HTTP 401)")
        if res.status == 402:
            raise RuntimeError("DeepSeek balance insufficient (HTTP 402)")
        if res.status == 403:
            raise RuntimeError("DeepSeek fetch blocked by safety policy (HTTP 403)")
        if res.status == 404:
            raise RuntimeError(
                "DeepSeek fetch: no extractable content (HTTP 404) — 页面不可用、被站点拦截或没有可提取的正文"
            )
        if res.status == 429:
            raise RuntimeError("DeepSeek fetch rate limited (HTTP 429), retry later")
        raise RuntimeError("DeepSeek fetch API error (HTTP %d)" % res.status)
    data = res.json()
    return {"title": str(data.get("title") or ""), "content": str(data.get("content") or "")}


FETCH_ENGINES = ("deepseek", "tavily", "keenable", "firecrawl", "parallel", "anysearch", "doubao")


def fetch_page(url, engine, api_key):
    """One provider request only; never fall back, retry, or expose response errors."""
    if engine not in FETCH_ENGINES:
        raise RuntimeError("unknown fetch engine; choose: " + ", ".join(FETCH_ENGINES))
    if not isinstance(url, str):
        raise RuntimeError("fetch requires an http(s) URL")
    url = url.strip()
    try:
        parsed = urllib.parse.urlsplit(url)
        valid_url = (parsed.scheme.lower() in ("http", "https") and parsed.hostname
                     and parsed.username is None and parsed.password is None
                     and not re.search(r"\s", url))
        parsed.port  # Invalid ports raise ValueError.
    except ValueError:
        valid_url = False
    if not valid_url:
        raise RuntimeError("fetch requires an http(s) URL without credentials")
    if engine == "deepseek":
        page = deepseek_fetch(url, api_key)
    else:
        if not api_key and engine not in ("keenable", "anysearch"):
            raise RuntimeError("%s is not configured" % KEY_ENV[engine])
        headers = {"accept": "application/json", "user-agent": USER_AGENT}
        method = "POST"
        body = None
        if engine == "keenable":
            endpoint = "https://api.keenable.ai/v1/fetch" + ("" if api_key else "/public")
            endpoint += "?" + urllib.parse.urlencode({"url": url, "live": "true"})
            method = "GET"
            if api_key:
                headers["x-api-key"] = api_key
            else:
                headers["x-keenable-title"] = "free-web-search"
        else:
            headers["content-type"] = "application/json"
            if engine == "tavily":
                endpoint = "https://api.tavily.com/extract"
                headers["authorization"] = "Bearer " + api_key
                payload = {"urls": [url]}
            elif engine == "firecrawl":
                endpoint = "https://api.firecrawl.dev/v2/scrape"
                headers["authorization"] = "Bearer " + api_key
                payload = {"url": url, "formats": ["markdown"]}
            elif engine == "parallel":
                endpoint = "https://api.parallel.ai/v1/extract"
                headers["x-api-key"] = api_key
                payload = {"urls": [url], "advanced_settings": {"full_content": True}}
            elif engine == "doubao":
                # 豆包 Fetch：字段名是大写 Url；与豆包搜索共用 key 和每月免费额度
                endpoint = "https://open.feedcoopapi.com/search_api/fetch"
                headers["authorization"] = "Bearer " + api_key
                payload = {"Url": url}
            else:
                endpoint = "https://api.anysearch.com/v1/extract"
                if api_key:
                    headers["authorization"] = "Bearer " + api_key
                payload = {"url": url}
            body = json.dumps(payload)
        res = request(endpoint, method=method, headers=headers, body=body, timeout=30)
        if not res.ok:
            raise RuntimeError("%s fetch API error (HTTP %d)" % (engine, res.status))
        try:
            data = res.json()
        except (ValueError, TypeError):
            raise RuntimeError("%s fetch returned invalid JSON" % engine)
        if not isinstance(data, dict):
            raise RuntimeError("%s fetch returned invalid response" % engine)
        if engine == "tavily":
            results = data.get("results") or []
            if not results:
                raise RuntimeError("tavily fetch failed: no result (failed_results may contain the URL)")
            item = results[0]
            page = {"title": item.get("title") or "", "content": item.get("raw_content") or ""}
        elif engine == "parallel":
            results = data.get("results") or []
            if not results:
                errors = data.get("errors") or []
                status = errors[0].get("http_status_code") if errors and isinstance(errors[0], dict) else None
                raise RuntimeError("parallel fetch failed%s" % (" (HTTP %s)" % status if isinstance(status, int) else ": no result"))
            item = results[0]
            page = {"title": item.get("title") or "", "content": item.get("full_content") or ""}
        elif engine == "firecrawl":
            if data.get("success") is False:
                raise RuntimeError("firecrawl fetch failed")
            item = data.get("data") or {}
            metadata = item.get("metadata") or {}
            status = metadata.get("statusCode")
            if isinstance(status, int) and status >= 400:
                raise RuntimeError("firecrawl fetch target error (HTTP %d)" % status)
            page = {"title": metadata.get("title") or "", "content": item.get("markdown") or ""}
        elif engine == "anysearch":
            if data.get("code") != 0:
                raise RuntimeError("anysearch fetch failed")
            item = data.get("data") or {}
            page = {"title": item.get("title") or "", "content": item.get("content") or ""}
        elif engine == "doubao":
            # 先查接口级错误（额度/权限/robots 合规），再查目标网页状态码
            meta_error = (data.get("ResponseMetadata") or {}).get("Error") or {}
            if meta_error:
                raise RuntimeError(
                    "doubao fetch error %s: %s" % (meta_error.get("Code"), str(meta_error.get("Message") or "")[:160])
                )
            item = (data.get("Result") or {}).get("Data") or {}
            status = item.get("StatusCode")
            if isinstance(status, int) and status >= 400:
                raise RuntimeError("doubao fetch target error (HTTP %d)" % status)
            page = {"title": item.get("Title") or "", "content": item.get("ContentText") or ""}
        else:
            page = {"title": data.get("title") or "", "content": data.get("content") or ""}
    if not isinstance(page.get("content"), str) or not page["content"].strip():
        raise RuntimeError("%s fetch returned empty content" % engine)
    page["title"] = str(page.get("title") or "")
    return page


def search_serpbase(query, max_results, api_key, opts):
    if not api_key:
        raise RuntimeError("SerpBase search requires SERPBASE_API_KEY")
    locale = SERPBASE_LOCALE.get(opts.get("lang"), SERPBASE_LOCALE["en"])
    body = {"q": query, "hl": locale["hl"], "gl": locale["gl"], "page": 1}
    res = request(
        SERPBASE_URL,
        method="POST",
        headers={
            "X-API-Key": api_key,
            "content-type": "application/json",
            "accept": "application/json",
            "user-agent": USER_AGENT,
        },
        body=json.dumps(body),
        timeout=15,
    )
    if not res.ok:
        raise RuntimeError("SerpBase API error (HTTP %d): %s" % (res.status, res.text[:200]))
    # SerpBase 始终返回 HTTP 200，业务状态在 status 字段（0 成功 / 1001 key 无效 / 1000 请求非法）
    data = res.json()
    if data.get("status") != 0:
        if data.get("status") == 1001:
            raise RuntimeError("SerpBase API key is invalid or missing (status 1001)")
        raise RuntimeError("SerpBase API error (status %s): %s" % (data.get("status"), str(data.get("error", ""))[:200]))
    sources = []
    for r in data.get("organic") or []:
        if not r.get("link"):
            continue
        item = {"url": r["link"]}
        if r.get("title"):
            item["title"] = str(r["title"])
        if r.get("snippet"):
            item["snippet"] = str(r["snippet"])
        pub = r.get("published_at") or r.get("date")
        if pub:
            item["publishedAt"] = str(pub)
        sources.append(item)
    return {"sources": unique_sources(sources, max_results)}


def search_youcom(query, max_results, api_key):
    if not api_key:
        raise RuntimeError("You.com search requires YOUCOM_API_KEY")
    params = urllib.parse.urlencode({"query": query, "count": str(min(max_results, 20))})
    res = request(
        YOUCOM_URL + "?" + params,
        headers={"X-API-Key": api_key, "accept": "application/json", "user-agent": USER_AGENT},
        timeout=15,
    )
    if not res.ok:
        if res.status in (401, 403):
            raise RuntimeError("You.com API key is invalid (HTTP %d)" % res.status)
        if res.status == 402:
            raise RuntimeError("You.com quota/billing error (HTTP 402). " + res.text[:200])
        if res.status == 429:
            raise RuntimeError("You.com rate limit exceeded (HTTP 429)")
        raise RuntimeError("You.com API error (HTTP %d): %s" % (res.status, res.text[:200]))
    hits = (res.json().get("results") or {}).get("web") or []
    sources = []
    for r in hits:
        if not r.get("url"):
            continue
        snippets = r.get("snippets")
        if isinstance(snippets, list):
            snippet = " ".join(s for s in snippets if isinstance(s, str) and s)
        else:
            snippet = r.get("description") or ""
        item = {"url": r["url"]}
        if r.get("title"):
            item["title"] = str(r["title"])
        if snippet:
            item["snippet"] = str(snippet)[:300]
        sources.append(item)
    return {"sources": unique_sources(sources, max_results)}


def search_baidu(query, max_results, api_key, time_range):
    if not api_key:
        raise RuntimeError("Baidu search requires BAIDU_API_KEY")
    import datetime

    count = min(max(max_results, 1), 50)
    search_filter = {}
    if time_range:
        now = datetime.datetime.now(datetime.timezone.utc)
        end = (now + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        start = None
        if time_range.get("after"):
            start = time_range["after"]
        elif time_range.get("days") is not None:
            start = (now - datetime.timedelta(days=time_range["days"])).strftime("%Y-%m-%d")
        if start:
            search_filter["range"] = {"page_time": {"gte": start, "lt": end}}
    body = {
        "messages": [{"content": query, "role": "user"}],
        "search_source": "baidu_search_v2",
        "resource_type_filter": [{"type": "web", "top_k": count}],
        "search_filter": search_filter,
    }
    res = request(
        BAIDU_URL,
        method="POST",
        headers={
            "content-type": "application/json",
            "authorization": "Bearer " + api_key,
            "X-Appbuilder-From": "openclaw",
            "user-agent": USER_AGENT,
        },
        body=json.dumps(body),
        timeout=15,
    )
    if not res.ok:
        if res.status in (401, 403):
            raise RuntimeError("Baidu API key is invalid (HTTP %d)" % res.status)
        raise RuntimeError("Baidu API error (HTTP %d): %s" % (res.status, res.text[:200]))
    data = res.json()
    if data.get("code"):
        raise RuntimeError("Baidu API error: %s" % (data.get("message") or json.dumps(data)[:200]))
    sources = []
    for r in data.get("references") or []:
        if not r.get("url"):
            continue
        item = {"url": r["url"]}
        if r.get("title"):
            item["title"] = str(r["title"])
        if r.get("content"):
            item["snippet"] = str(r["content"])[:300]
        if r.get("date"):
            item["publishedAt"] = str(r["date"])
        sources.append(item)
    return {"sources": unique_sources(sources, max_results)}


def search_kimi(query, max_results, api_key, time_range):
    if not api_key:
        raise RuntimeError("Kimi search requires MOONSHOT_API_KEY")
    body = {"text_query": query, "limit": min(max(max_results, 1), 20), "timeout_seconds": 30}
    res = request(
        KIMI_URL,
        method="POST",
        headers={
            "content-type": "application/json",
            "authorization": "Bearer " + api_key,
            "user-agent": USER_AGENT,
        },
        body=json.dumps(body),
        timeout=35,
    )
    if not res.ok:
        if res.status in (401, 403):
            raise RuntimeError("Kimi API key is invalid (HTTP %d)" % res.status)
        raise RuntimeError("Kimi API error (HTTP %d): %s" % (res.status, res.text[:200]))
    data = res.json()
    sources = []
    for r in data.get("search_results") or []:
        if not r.get("url"):
            continue
        snippet = ""
        chunks = r.get("chunks")
        if isinstance(chunks, list) and chunks:
            # chunks 是 [{text}]，不是字符串数组
            parts = []
            for c in chunks:
                if isinstance(c, str):
                    parts.append(c)
                elif isinstance(c, dict) and isinstance(c.get("text"), str):
                    parts.append(c["text"])
            snippet = " ".join(p for p in parts if p)
        elif r.get("snippet"):
            snippet = str(r["snippet"])
        item = {"url": r["url"]}
        if r.get("title"):
            item["title"] = str(r["title"])
        if snippet:
            item["snippet"] = snippet[:300]
        if r.get("publish_date"):
            item["publishedAt"] = str(r["publish_date"])
        sources.append(item)
    return {"sources": unique_sources(sources, max_results)}


def search_aliyun(query, max_results, api_key, time_range):
    if not api_key:
        raise RuntimeError("Aliyun search requires DASHSCOPE_API_KEY")
    trimmed = str(query).strip()
    q = trimmed + " " if len(trimmed) < 2 else trimmed[:500]
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "search_pro", "arguments": {"query": q}},
        }
    )
    res = request(
        ALIYUN_URL,
        method="POST",
        headers={
            "authorization": "Bearer " + api_key,
            "content-type": "application/json",
            "accept": "application/json, text/event-stream",
            "user-agent": USER_AGENT,
        },
        body=body,
        timeout=35,
    )
    if not res.ok:
        if res.status in (401, 403):
            raise RuntimeError("Aliyun API key is invalid (HTTP %d)" % res.status)
        raise RuntimeError("Aliyun API error (HTTP %d): %s" % (res.status, res.text[:200]))
    envelope = parse_mcp_body(res.text)
    if not envelope:
        raise RuntimeError("Aliyun MCP response is not valid JSON: " + res.text[:200])
    if envelope.get("error"):
        err = envelope["error"]
        raise RuntimeError("Aliyun MCP error %s: %s" % (err.get("code"), err.get("message")))
    call_result = envelope.get("result")
    if not isinstance(call_result, dict):
        raise RuntimeError("Aliyun MCP result missing")
    if call_result.get("isError"):
        raise RuntimeError("Aliyun tool error: " + json.dumps(call_result)[:200])
    text_item = next(
        (c for c in (call_result.get("content") or []) if c.get("type") == "text" and isinstance(c.get("text"), str)),
        None,
    )
    if not text_item:
        return {"sources": []}
    try:
        inner = json.loads(text_item["text"])
    except Exception:
        raise RuntimeError("Aliyun inner content JSON parse error: " + text_item["text"][:200])
    sources = []
    for p in inner.get("pages") or []:
        if not p.get("url"):
            continue
        item = {"url": p["url"]}
        if p.get("title"):
            item["title"] = str(p["title"])
        if p.get("snippet"):
            item["snippet"] = str(p["snippet"])[:300]
        if p.get("published_at"):
            item["publishedAt"] = str(p["published_at"])
        sources.append(item)
    return {"sources": unique_sources(sources, max_results)}


# 豆包：错误也返回 HTTP 200，必须解析 ResponseMetadata.Error
DOUBAO_ERROR_HINTS = {
    10400: "invalid request parameters (check Query/Count/TimeRange)",
    10402: "invalid SearchType (only web|image)",
    10403: "account or permission problem - make sure the key is from the Web Search console",
    10406: "free quota exhausted (500 requests/month on the free tier)",
    10407: "no available free-tier policy - check the account status",
    10500: "Web Search service internal error - retry later",
    700429: "free-tier rate limit hit - slow down and retry",
    100013: "sub-account is not granted TorchlightApiFullAccess",
    700901: "invalid api key",
}


def search_doubao(query, max_results, api_key, time_range):
    if not api_key:
        raise RuntimeError("Doubao search requires DOUBAO_SEARCH_API_KEY")
    body = {
        "Query": str(query)[:100],
        "SearchType": "web",
        "Count": min(max(max_results, 1), 50),
        "NeedSummary": True,
        "Filter": {"NeedUrl": True},
    }
    tr = doubao_time_range(time_range)
    if tr is not None:
        body["TimeRange"] = tr
    res = request(
        DOUBAO_URL,
        method="POST",
        headers={
            "content-type": "application/json",
            "authorization": "Bearer " + api_key,
            "X-Traffic-Tag": "free-web-search-skill",
            "user-agent": USER_AGENT,
        },
        body=json.dumps(body),
        timeout=20,
    )
    if not res.ok:
        if res.status in (401, 403):
            raise RuntimeError(
                "Doubao search auth failed (HTTP %d) - check DOUBAO_SEARCH_API_KEY (Web Search console)" % res.status
            )
        raise RuntimeError("Doubao search API error (HTTP %d): %s" % (res.status, res.text[:200]))
    data = res.json()
    err = (data.get("ResponseMetadata") or {}).get("Error")
    if err and (err.get("Code") or err.get("CodeN") or err.get("Message")):
        code = err.get("CodeN") if err.get("CodeN") is not None else err.get("Code")
        hint = DOUBAO_ERROR_HINTS.get(int(code) if str(code).isdigit() else None)
        raise RuntimeError(
            "Doubao search API error (%s): %s%s"
            % (code, err.get("Message") or "request failed", (" - " + hint) if hint else "")
        )
    sources = []
    for item in (data.get("Result") or {}).get("WebResults") or []:
        if not item or not item.get("Url"):
            continue
        raw = item.get("Summary") or item.get("Content") or item.get("Snippet") or ""
        snippet = re.sub(r"\s+", " ", strip_tags(str(raw))).strip()
        src = {"url": str(item["Url"])}
        if item.get("Title"):
            src["title"] = strip_tags(str(item["Title"])).strip()
        if snippet:
            src["snippet"] = snippet[: snippet_cap("doubao")]
        if item.get("PublishTime"):
            src["publishedAt"] = str(item["PublishTime"])
        sources.append(src)
    return {"sources": unique_sources(sources, max_results)}


# ───────────────────────── 平台搜索 ─────────────────────────


def search_platform(platform, query, max_results, lang):
    n = max_results
    if platform == "github":
        res = request(
            "https://api.github.com/search/repositories?q=%s&per_page=%d"
            % (urllib.parse.quote(query), n),
            headers={"user-agent": USER_AGENT, "accept": "application/vnd.github+json"},
            timeout=15,
        )
        if not res.ok:
            raise RuntimeError("GitHub API error (HTTP %d)" % res.status)
        sources = []
        for item in res.json().get("items") or []:
            desc = item.get("description") or ""
            stars = item.get("stargazers_count")
            sources.append(
                {
                    "url": item.get("html_url"),
                    "title": item.get("full_name") or item.get("name"),
                    "snippet": ("%s%s" % (desc, (" ⭐%s" % stars) if stars else "")).strip(),
                }
            )
        return {"sources": sources}

    if platform == "v2ex":
        res = request(
            "https://www.v2ex.com/api/topics/hot.json",
            headers={"user-agent": USER_AGENT},
            timeout=15,
        )
        if not res.ok:
            raise RuntimeError("V2EX API error (HTTP %d)" % res.status)
        topics = res.json()
        q = query.lower()
        matched = [
            t
            for t in (topics if isinstance(topics, list) else [])
            if q in (t.get("title") or "").lower() or q in (t.get("content") or "").lower()
        ]
        sources = []
        for t in matched[:n]:
            item = {"url": "https://www.v2ex.com/t/%s" % t.get("id"), "title": t.get("title")}
            if t.get("content"):
                item["snippet"] = str(t["content"])[:200]
            sources.append(item)
        return {"sources": sources}

    if platform == "bilibili":
        res = request(
            "https://api.bilibili.com/x/web-interface/search/all/v2?keyword=%s" % urllib.parse.quote(query),
            headers={"user-agent": USER_AGENT, "referer": "https://www.bilibili.com"},
            timeout=15,
        )
        if not res.ok:
            raise RuntimeError("Bilibili API error (HTTP %d)" % res.status)
        data = res.json()
        if data.get("code") != 0:
            raise RuntimeError("Bilibili API error: %s" % (data.get("message") or data.get("code")))
        sources = []
        for section in (data.get("data") or {}).get("result") or []:
            for item in section.get("data") or []:
                if not item.get("arcurl"):
                    continue
                src = {
                    "url": item["arcurl"],
                    "title": re.sub(r"<[^>]+>", "", str(item.get("title"))) if item.get("title") else item.get("bvid"),
                }
                if item.get("desc"):
                    src["snippet"] = str(item["desc"])[:200]
                sources.append(src)
                if len(sources) >= n:
                    break
            if len(sources) >= n:
                break
        return {"sources": sources}

    if platform == "reddit":
        res = request(
            "https://old.reddit.com/search.json?q=%s&limit=%d&sort=relevance"
            % (urllib.parse.quote(query), n),
            headers={"user-agent": USER_AGENT + " (free-web-search-skill)", "accept": "application/json"},
            timeout=15,
        )
        if not res.ok:
            raise RuntimeError("Reddit API error (HTTP %d)" % res.status)
        children = (res.json().get("data") or {}).get("children") or []
        sources = []
        for c in children:
            p = c.get("data") or {}
            if not p.get("url"):
                continue
            item = {"url": p["url"], "title": p.get("title") or ""}
            if p.get("selftext"):
                item["snippet"] = str(p["selftext"])[:200]
            sources.append(item)
        return {"sources": sources}

    if platform == "hn":
        res = request(
            "https://hn.algolia.com/api/v1/search?query=%s&hitsPerPage=%d"
            % (urllib.parse.quote(query), n),
            headers={"user-agent": USER_AGENT, "accept": "application/json"},
            timeout=15,
        )
        if not res.ok:
            raise RuntimeError("Hacker News API error (HTTP %d)" % res.status)
        sources = []
        for h in res.json().get("hits") or []:
            if not (h.get("title") or h.get("story_title")):
                continue
            sources.append(
                {
                    "url": h.get("url") or "https://news.ycombinator.com/item?id=%s" % h.get("objectID"),
                    "title": h.get("title") or h.get("story_title"),
                    "snippet": "HN discussion · %s points · %s comments"
                    % (h.get("points") or 0, h.get("num_comments") or 0),
                }
            )
        return {"sources": sources}

    if platform == "stackoverflow":
        res = request(
            "https://api.stackexchange.com/2.3/search/advanced?order=desc&sort=relevance&q=%s"
            "&site=stackoverflow&pagesize=%d&filter=!nNPvSNVZJS" % (urllib.parse.quote(query), n),
            headers={"user-agent": USER_AGENT, "accept": "application/json"},
            timeout=15,
        )
        if not res.ok:
            raise RuntimeError("Stack Exchange API error (HTTP %d)" % res.status)
        data = res.json()
        if data.get("error_message"):
            raise RuntimeError("Stack Exchange API error: " + data["error_message"])
        sources = []
        for it in data.get("items") or []:
            sources.append(
                {
                    "url": it.get("link"),
                    "title": it.get("title"),
                    "snippet": "%s · score %s · %s answers"
                    % ("✓ answered" if it.get("is_answered") else "unanswered", it.get("score") or 0, it.get("answer_count") or 0),
                }
            )
        return {"sources": sources}

    if platform == "wikipedia":
        host = "en.wikipedia.org" if lang == "en" else "zh.wikipedia.org"
        res = request(
            "https://%s/w/api.php?action=query&list=search&srsearch=%s&format=json&srlimit=%d"
            % (host, urllib.parse.quote(query), n),
            headers={"user-agent": USER_AGENT, "accept": "application/json"},
            timeout=15,
        )
        if not res.ok:
            raise RuntimeError("Wikipedia API error (HTTP %d)" % res.status)
        sources = []
        for s in (res.json().get("query") or {}).get("search") or []:
            title = str(s.get("title"))
            item = {"url": "https://%s/wiki/%s" % (host, urllib.parse.quote(title.replace(" ", "_"))), "title": title}
            if s.get("snippet"):
                item["snippet"] = strip_tags(s["snippet"])[:200]
            sources.append(item)
        return {"sources": sources}

    if platform == "npm":
        res = request(
            "https://registry.npmjs.com/-/v1/search?text=%s&size=%d" % (urllib.parse.quote(query), n),
            headers={"user-agent": USER_AGENT, "accept": "application/json"},
            timeout=15,
        )
        if not res.ok:
            raise RuntimeError("npm registry API error (HTTP %d)" % res.status)
        sources = []
        for o in res.json().get("objects") or []:
            p = o.get("package") or {}
            if not p.get("name"):
                continue
            item = {
                "url": (p.get("links") or {}).get("npm") or "https://www.npmjs.com/package/%s" % p["name"],
                "title": p["name"],
            }
            if p.get("description") or p.get("version"):
                item["snippet"] = "v%s%s" % (
                    p.get("version") or "?",
                    (" — " + str(p.get("description"))[:160]) if p.get("description") else "",
                )
            sources.append(item)
        return {"sources": sources}

    raise RuntimeError("unknown platform: %s" % platform)


# ───────────────────────── 路由与调度 ─────────────────────────

# 已配置的 key（由 main 在启动时注入）：envName -> value
CONFIGURED_KEYS = {}


def resolve_key(engine):
    """取某个引擎的 key：secrets.yaml / config.yaml 优先，其次环境变量"""
    env = KEY_ENV.get(engine)
    if not env:
        return ""
    if CONFIGURED_KEYS.get(env):
        return CONFIGURED_KEYS[env]
    if engine == "doubao":
        return os.environ.get("DOUBAO_SEARCH_API_KEY") or os.environ.get("WEB_SEARCH_API_KEY") or ""
    return os.environ.get(env, "")


def has_key(engine):
    env = KEY_ENV.get(engine)
    if not env:
        return True  # 免 key 引擎
    return bool(resolve_key(engine))


def usable_engines(engines):
    """
    auto 模式下可用的引擎。
    不能一律用 has_key 判断，要区分三种情况：
      1. 完全免 key（bing/ddg/searxng）        -> 恒可用
      2. key 可选（exa/tavily/keenable/...）    -> 没 key 走匿名额度，也恒可用
      3. key 必需（perplexity/baidu/kimi/...）  -> 没 key 必然失败，必须剔除
    KEYLESS_ENGINES 正好覆盖 1+2。
    """
    return [e for e in engines if e in KEYLESS_ENGINES or has_key(e)]


def _prioritized(order, priorities):
    """按用户数字重排：有数字的升序在前（同数保持原相对顺序），其余保持原相对顺序垫后。"""
    if not priorities:
        return order
    numbered = sorted((e for e in order if e in priorities), key=lambda e: priorities[e])
    return numbered + [e for e in order if e not in priorities]


def route_engines(query, time_range=None, engines=None, priorities=None):
    """
    智能路由（auto 模式）：
      1. 先剔除「需要 key 但没配」的引擎 —— 它们必然失败，只会污染 attempts
      2. 有 searchPriority 时：完全按用户数字排序（不再按语言加权），未列出的按默认回退顺序垫后
      3. 无 searchPriority 时：按查询语言分组（中文 bing/baidu/aliyun/deepseek/anysearch，英文 bing/exa/tavily）
      4. 带时间过滤时，支持时间过滤的引擎整体前置
      5. 其余引擎按默认回退优先级兜底
    """
    usable = usable_engines(engines or ALL_ENGINES)
    if priorities:
        # 数字排序生效时，-t 只在「用户列出的引擎」内做时间过滤前置：
        # 能力需要优先于偏好，但未列出的引擎绝不插队。
        ordered = _prioritized([e for e in usable if e in priorities], priorities)
        if time_range:
            ordered = ([e for e in ordered if e in TIME_ENGINES]
                       + [e for e in ordered if e not in TIME_ENGINES])
    else:
        is_cjk = bool(re.search(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]", str(query or "")))
        route_head = ["bing", "baidu", "aliyun", "deepseek", "anysearch"] if is_cjk else ["bing", "exa", "tavily"]
        ordered = [e for e in usable if e in route_head]
        if time_range:
            ordered = ([e for e in ordered if e in TIME_ENGINES]
                       + [e for e in usable if e in TIME_ENGINES and e not in ordered]
                       + [e for e in ordered if e not in TIME_ENGINES])
    # 其余按默认回退优先级（而非 ALL_ENGINES 定义顺序）
    remaining = [e for e in DEFAULT_FALLBACK_ORDER if e in usable and e not in ordered]
    return ordered + remaining


def run_engine(engine, query, max_results, time_range, opts):
    """单引擎调度：把 (engine, query, n, timeRange, opts) 映射到具体实现"""
    key = resolve_key(engine)
    if engine == "ddg":
        return search_ddg(query, max_results, dict(opts, timeRange=time_range))
    if engine == "ddg-lite":
        return search_ddg_lite(query, max_results, dict(opts, timeRange=time_range))
    if engine == "bing":
        return search_bing(query, max_results, opts)
    if engine == "searxng":
        return search_searxng(query, max_results, dict(opts, timeRange=time_range))
    if engine == "anysearch":
        return search_anysearch(query, max_results, key)
    if engine == "exa":
        return search_exa(query, max_results, key, time_range) if key else search_exa_mcp(query, max_results)
    if engine == "tavily":
        return search_tavily(query, max_results, key, time_range)
    if engine == "keenable":
        return search_keenable(query, max_results, key, time_range)
    if engine == "firecrawl":
        return search_firecrawl(query, max_results, key, time_range)
    if engine == "parallel":
        return search_parallel(query, max_results, key, time_range)
    if engine == "perplexity":
        if not key:
            raise RuntimeError("PERPLEXITY_API_KEY is not configured")
        return search_perplexity(query, max_results, key)
    if engine == "deepseek":
        if not key:
            raise RuntimeError("DEEPSEEK_API_KEY is not configured")
        return search_deepseek(query, max_results, key, time_range)
    if engine == "serpbase":
        if not key:
            raise RuntimeError("SERPBASE_API_KEY is not configured")
        return search_serpbase(query, max_results, key, opts)
    if engine == "you":
        if not key:
            raise RuntimeError("YOUCOM_API_KEY is not configured")
        return search_youcom(query, max_results, key)
    if engine == "baidu":
        if not key:
            raise RuntimeError("BAIDU_API_KEY is not configured")
        return search_baidu(query, max_results, key, time_range)
    if engine == "kimi":
        if not key:
            raise RuntimeError("MOONSHOT_API_KEY is not configured")
        return search_kimi(query, max_results, key, time_range)
    if engine == "aliyun":
        if not key:
            raise RuntimeError("DASHSCOPE_API_KEY is not configured")
        return search_aliyun(query, max_results, key, time_range)
    if engine == "doubao":
        # 豆包主 key 名 DOUBAO_SEARCH_API_KEY，官方示例也用 WEB_SEARCH_API_KEY（已在 resolve_key 里回退）
        if not key:
            raise RuntimeError("DOUBAO_SEARCH_API_KEY is not configured")
        return search_doubao(query, max_results, key, time_range)
    raise RuntimeError("unknown engine: %s" % engine)


def clean_sources(sources, engine):
    out = []
    for s in sources or []:
        if s.get("snippet"):
            s = dict(s, snippet=clean_snippet(s["snippet"], snippet_cap(engine)))
        out.append(s)
    return out


def run_engine_safe(engine, query, max_results, time_range, opts):
    """单引擎执行（含空结果重试一次），返回 {ok, sources, content} 或 {ok:False, error}"""

    def attempt():
        return run_engine(engine, query, max_results, time_range, opts)

    try:
        result = attempt()
        if not (result.get("sources") or []):
            sleep(1500)
            retry = attempt()
            return dict(retry, ok=True, sources=clean_sources(retry.get("sources"), engine))
        return dict(result, ok=True, sources=clean_sources(result.get("sources"), engine))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def multi_search(query, max_results, time_range, opts, explicit_engines=None):
    """多源并发合并：按规范化 URL 去重，跨源命中的排前面，附 seenIn"""
    from concurrent.futures import ThreadPoolExecutor

    enabled = opts.get("engines") or ALL_ENGINES
    if explicit_engines:
        targets = []
        for e in explicit_engines:
            if e in ALL_ENGINES and e in enabled and e not in targets:
                targets.append(e)
    else:
        targets = route_engines(query, time_range, enabled, opts.get("searchPriority") or None)[:3]
    if not targets:
        raise RuntimeError("no enabled engines selected")

    with ThreadPoolExecutor(max_workers=len(targets)) as pool:
        futures = [pool.submit(run_engine_safe, e, query, max_results, time_range, opts) for e in targets]
        settled = [{"engine": e, "res": f.result()} for e, f in zip(targets, futures)]

    engines_used = []
    failed = []
    skipped = []
    url_map = {}
    seq = 0

    for item in settled:
        engine, res = item["engine"], item["res"]
        if not res.get("ok"):
            err = res.get("error", "")
            if re.search(r"not configured|requires", err, re.IGNORECASE):
                skipped.append(engine)
            else:
                failed.append("%s (%s)" % (engine, err))
            continue
        engines_used.append(engine)
        for source in res.get("sources") or []:
            if not source or not source.get("url"):
                continue
            raw = str(source["url"]).strip()
            try:
                p = urllib.parse.urlsplit(raw)
                norm = "%s//%s%s%s%s" % (
                    p.scheme + ":" if p.scheme else "",
                    p.netloc.lower(),
                    p.path.rstrip("/"),
                    ("?" + p.query) if p.query else "",
                    ("#" + p.fragment) if p.fragment else "",
                )
            except Exception:
                norm = raw.lower().rstrip("/")
            if norm in url_map:
                entry = url_map[norm]
                entry["seenIn"].add(engine)
                if not entry["source"].get("snippet") and source.get("snippet"):
                    entry["source"]["snippet"] = source["snippet"]
                if not entry["source"].get("title") and source.get("title"):
                    entry["source"]["title"] = source["title"]
                if not entry["source"].get("publishedAt") and source.get("publishedAt"):
                    entry["source"]["publishedAt"] = source["publishedAt"]
            else:
                url_map[norm] = {"source": dict(source), "seenIn": {engine}, "order": seq}
                seq += 1

    if not engines_used:
        details = failed + ["%s (no key)" % e for e in skipped]
        raise RuntimeError("multi search failed: " + ("; ".join(details) if details else "no engine returned results"))

    entries = list(url_map.values())
    entries.sort(key=lambda x: (-len(x["seenIn"]), x["order"]))
    merged = []
    for entry in entries[:max_results]:
        src = entry["source"]
        out = {"url": src.get("url")}
        if src.get("title"):
            out["title"] = str(src["title"])
        if src.get("snippet"):
            out["snippet"] = str(src["snippet"])
        if src.get("publishedAt"):
            out["publishedAt"] = str(src["publishedAt"])
        out["seenIn"] = sorted(entry["seenIn"])
        merged.append(out)

    notes = ["enginesUsed: [%s]" % ", ".join(engines_used)]
    if failed:
        notes.append("failed: [%s]" % ", ".join(failed))
    if skipped:
        notes.append("skipped (no key): [%s]" % ", ".join(skipped))

    return {
        "content": "Note: " + "; ".join(notes),
        "sources": merged,
        "enginesUsed": engines_used,
        "failed": failed,
        "skipped": skipped,
    }


def search(query, engine="auto", max_results=5, time_range=None, opts=None, fallback=True):
    """
    主搜索：按 engine 选择引擎，失败自动沿回退链换下一个，直到成功或全部失败。
    engine 可为 auto / multi / 具体引擎 id。
    """
    started = time.time()
    opts = opts or {}
    n = min(max(int(max_results or 5), 1), 20)
    enabled = opts.get("engines") or ALL_ENGINES

    def elapsed():
        return int((time.time() - started) * 1000)

    if engine == "multi":
        result = multi_search(query, n, time_range, opts, opts.get("explicitEngines"))
        out = {"ok": True, "query": query, "requested": engine, "engine": "multi"}
        out.update(result)
        out["elapsedMs"] = elapsed()
        return out

    priorities = opts.get("searchPriority") or None
    # 回退链：显式指定的引擎永远排第一（尊重用户选择），其后按默认优先级补齐。
    # 用 DEFAULT_FALLBACK_ORDER 而非 ALL_ENGINES 定义顺序 —— 后者把 ddg 排得很前，
    # 无代理时 ddg 的超时会吃掉全部预算，后面的 bing 根本没机会跑。
    fallback_rest = [e for e in DEFAULT_FALLBACK_ORDER if e in enabled]
    if priorities:
        fallback_rest = _prioritized(fallback_rest, priorities)
    disabled_set = set(opts.get("disabledEngines") or [])
    # 显式指定的引擎被配置禁用时，要明确告知，而不是静默换一个引擎
    disabled_note = None
    if engine not in ("auto", "multi") and engine in disabled_set:
        disabled_note = "%s is disabled in config (disabledEngines), using another engine." % engine
    if engine == "auto":
        chain = route_engines(query, time_range, enabled, priorities)
    elif engine in ALL_ENGINES:
        chain = ([engine] + [e for e in fallback_rest if e != engine]) if fallback else [engine]
    else:
        raise RuntimeError(
            'unknown engine "%s" — valid: auto, multi, %s' % (engine, ", ".join(ALL_ENGINES))
        )
    seen = set()
    chain = [e for e in chain if e in enabled and not (e in seen or seen.add(e))]
    # --no-fallback 且唯一引擎被禁用 → 直接报错，别静默返回空
    if not chain:
        raise RuntimeError(
            'engine "%s" is disabled in config (disabledEngines) and fallback is off' % engine
        )

    attempts = []
    # 总超时预算：串行回退时限制整条引擎链的总时长。剩余不足 2s 时停止继续尝试。
    # 预算可由配置文件 timeout（秒）覆盖。
    budget = opts.get("budgetMs")
    deadline = set_deadline(budget if isinstance(budget, (int, float)) and budget > 0 else 30000)
    try:
        for eng in chain:
            if deadline and deadline - time.time() < 2:
                attempts.append({"engine": eng, "error": "skipped: time budget exhausted"})
                continue
            res = run_engine_safe(eng, query, n, time_range, opts)
            if res.get("ok") and (res.get("sources") or []):
                out = {
                    "ok": True,
                    "query": query,
                    "requested": engine,
                    "engine": eng,
                    "sources": res["sources"],
                    "elapsedMs": elapsed(),
                }
                if res.get("content"):
                    out["content"] = res["content"]
                # 用了非首选引擎时给出准确提示
                if disabled_note:
                    out["content"] = "Note: %s" % disabled_note
                    out["requestedDisabled"] = engine
                elif eng != engine and engine != "auto":
                    prev = attempts[-1] if attempts else None
                    if prev and re.search(r"not configured", prev.get("error", ""), re.IGNORECASE):
                        out["content"] = "Note: %s unavailable (%s), using %s." % (engine, prev["error"], eng)
                    else:
                        out["content"] = "Note: %s unavailable or failed, using %s." % (engine, eng)
                if attempts:
                    out["attempts"] = attempts
                return out
            attempts.append({"engine": eng, "error": "returned 0 results" if res.get("ok") else res.get("error")})

        return {
            "ok": False,
            "query": query,
            "requested": engine,
            "error": (attempts[-1]["error"] if attempts else "all search engines failed"),
            "attempts": attempts,
            "elapsedMs": elapsed(),
        }
    finally:
        # 退出回退链后解除预算，避免影响同一进程内的后续调用（如 --test）
        set_deadline(0)


# ───────────────────────── CLI ─────────────────────────

TAKES_VALUE = {
    "-q", "--query", "-e", "--engine", "-n", "--max", "--max-results", "-t", "--time", "--time-range",
    "-p", "--platform", "--lang", "--market", "--safe-search", "--proxy", "--key", "--searxng",
    "--engines", "--fetch", "--fetch-engine",
}


def parse_args(argv):
    args = {"_": []}
    i = 0
    while i < len(argv):
        a = argv[i]
        if not a.startswith("-"):
            args["_"].append(a)
            i += 1
            continue
        value = None
        if "=" in a:
            a, value = a.split("=", 1)
        if a in TAKES_VALUE and value is None:
            i += 1
            value = argv[i] if i < len(argv) else None
        key = a.lstrip("-")
        if value is None:
            args[key] = True
        elif key not in args:
            args[key] = value
        else:
            args[key] = [args[key], value] if isinstance(args[key], list) else [args[key], value]
        i += 1
    return args


def print_help():
    print(
        """free-web-search — 18 引擎网页搜索（Python 版，零第三方依赖）

用法:
  python3 search.py -q "查询" [选项]

选项:
  -q, --query <文本>        搜索词（必填，除非用 --list-engines/--test）
  -e, --engine <引擎>       引擎 id / auto（智能路由，默认）/ multi（多源并发合并）
  -n, --max <数量>          结果条数，默认 5，上限 20
  -t, --time <范围>         day|week|month|year，或 12h/3d/2mo/1y，或 2026-07-01
  -p, --platform <平台>     %s
      --lang <语言>         zh|en|ru|ja|de|fr|es|ko（影响 Bing/SerpBase 本地化）
      --market <市场>       Bing mkt，如 zh-CN / en-US（覆盖 --lang）
      --safe-search <档>    off|moderate|strict（仅 Bing/DDG 生效）
      --searxng <实例>      自定义 SearXNG 实例，逗号分隔
      --fetch <URL>         抓取单页正文（默认 DeepSeek Fetch API）
      --fetch-engine <id>   deepseek|tavily|keenable|firecrawl|parallel|anysearch|doubao
      --proxy <URL>         代理，如 http://127.0.0.1:7890
      --key <engine=value>  临时指定密钥（也可用环境变量）
      --engines <列表>      multi 模式指定引擎，逗号分隔（如 bing,exa,tavily）
      --no-fallback         只用指定引擎，失败不回退
      --text                输出人类可读文本
      --json                输出 JSON（默认行为，显式兼容用）
      --list-engines        列出引擎及密钥配置状态
      --config-status       输出配置状态（AI 用它判断是否需要向用户提问）
      --init-config         生成 config.yaml 模板（已存在不覆盖，加 --force 覆盖）
      --init-secrets        生成 secrets.yaml 凭据模板（600 权限，需自行填 key）
      --test                测试引擎可用性
  -h, --help                显示帮助

环境变量（密钥）:
  %s
  HTTPS_PROXY / HTTP_PROXY  代理
"""
        % (" | ".join(PLATFORMS), "\n  ".join("%s=%s" % (e, v) for e, v in KEY_ENV.items()))
    )


def format_text(result):
    if not result.get("ok"):
        return "搜索失败: %s" % result.get("error")
    head = "引擎: %s" % result.get("engine")
    if result.get("requested") != result.get("engine"):
        head += "（请求 %s）" % result.get("requested")
    lines = [head]
    if result.get("content"):
        lines.append(result["content"])
    lines.append("")
    for s in result.get("sources") or []:
        seen = " [%s]" % ",".join(s["seenIn"]) if s.get("seenIn") else ""
        lines.append("- %s%s" % (s.get("title") or s.get("url"), seen))
        lines.append("  %s" % s.get("url"))
        if s.get("snippet"):
            lines.append("  %s" % s["snippet"])
        if s.get("publishedAt"):
            lines.append("  (%s)" % s["publishedAt"])
    if not result.get("sources"):
        lines.append("（无结果）")
    return "\n".join(lines)


def list_engines():
    rows = []
    for e in ALL_ENGINES:
        rows.append(
            {
                "engine": e,
                "keyless": e in KEYLESS_ENGINES,
                "keyConfigured": has_key(e),
                "timeFilter": e in TIME_ENGINES,
                "keyEnv": KEY_ENV.get(e),
            }
        )
    return {"ok": True, "engines": rows, "proxy": PROXY_URL or None}


def main(argv):
    args = parse_args(argv)

    if args.get("help") or args.get("h"):
        print_help()
        return 0

    # ── 配置：先读文件，CLI 参数随后覆盖 ───────────────────────────────
    cfg = load_config()
    cfg_settings = config_to_settings(cfg["values"]) if cfg.get("configured") else {}

    # ── 凭据：secrets.yaml > config.yaml 的 keys: 段 > 环境变量 ─────────
    key_info = load_configured_keys(cfg.get("values") or {})
    CONFIGURED_KEYS.update(key_info["keys"])
    # 把配置里的 key 同时写进环境变量，方便各引擎实现里已有的读取路径直接生效
    for env, value in CONFIGURED_KEYS.items():
        if not os.environ.get(env):
            os.environ[env] = value

    # 代理生效顺序：CLI --proxy > 配置显式关闭 > 配置指定 > 环境变量（模块初始化时已读入）
    if args.get("proxy"):
        set_proxy(args["proxy"])
    elif cfg_settings.get("proxyDisabled"):
        set_proxy("")  # 配置显式关闭代理：忽略环境变量
    elif cfg_settings.get("proxy"):
        set_proxy(cfg_settings["proxy"])

    # --config-status：给 AI 判断「是否已配置、要不要向用户提问」用
    if args.get("config-status"):
        # 哪些引擎现在真的能用（有 key 或本身免 key）
        key_status = {}
        for engine in ALL_ENGINES:
            if engine not in KEY_ENV:
                continue  # 免 key 引擎不列
            env = KEY_ENV[engine]
            from_ = "config" if key_info["sources"].get(env) else ("env" if os.environ.get(env) else None)
            key_status[engine] = {"configured": has_key(engine), "env": env, "source": from_}
        out = {
            "configured": cfg.get("configured"),
            "path": cfg.get("path"),
        }
        if not cfg.get("configured"):
            out["reason"] = cfg.get("reason")
        out["settings"] = cfg_settings
        if cfg.get("values") is not None:
            out["raw"] = cfg["values"]
        if cfg.get("unknownKeys"):
            out["unknownKeys"] = cfg["unknownKeys"]
        warnings = (cfg.get("warnings") or []) + (key_info.get("warnings") or []) + (cfg_settings.get("priorityWarnings") or [])
        if cfg_settings.get("unknownDisabled"):
            warnings.append(
                "disabledEngines 里有未知引擎，已忽略：%s" % ", ".join(cfg_settings["unknownDisabled"])
            )
        if warnings:
            out["warnings"] = warnings
        out["proxyInEffect"] = PROXY_URL or None
        out["secretsFile"] = key_info.get("secretsFile")
        out["keys"] = key_status
        out["keysConfiguredCount"] = sum(1 for v in key_status.values() if v["configured"])
        # auto 模式实际会用到哪些引擎（已排除禁用的和无 key 的）
        disabled = cfg_settings.get("disabledEngines") or []
        out["disabledEngines"] = disabled
        out["usableEngines"] = usable_engines([e for e in ALL_ENGINES if e not in disabled])
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    # --init-secrets：生成 secrets.yaml 模板（含各引擎 key 的占位注释）
    if args.get("init-secrets"):
        target = key_info.get("secretsFile") or secrets_candidates()[0]
        if os.path.exists(target) and not args.get("force"):
            print("secrets file already exists: %s (use --force to overwrite)" % target, file=sys.stderr)
            return 0
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            f.write(SECRETS_TEMPLATE)
        try:
            os.chmod(target, 0o600)  # 仅本人可读写
        except Exception:
            pass
        print(json.dumps(
            {"ok": True, "written": target, "note": "已设为 600 权限；该文件应在 .gitignore 中"},
            ensure_ascii=False, indent=2,
        ))
        return 0

    # --init-config：生成一份带注释的模板（已存在则不覆盖，除非 --force）
    if args.get("init-config"):
        target = cfg["path"] if cfg.get("configured") else config_candidates()[0]
        if os.path.exists(target) and not args.get("force"):
            print("config already exists: %s (use --force to overwrite)" % target, file=sys.stderr)
            return 0
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            f.write(CONFIG_TEMPLATE)
        print(json.dumps({"ok": True, "written": target}, ensure_ascii=False, indent=2))
        return 0

    # --key engine=value 临时注入密钥
    if args.get("key"):
        keys = args["key"] if isinstance(args["key"], list) else [args["key"]]
        for pair in keys:
            eng, _, val = str(pair).partition("=")
            env = KEY_ENV.get(eng)
            if not env:
                print('warning: 未知引擎 "%s"，忽略 --key' % eng, file=sys.stderr)
                continue
            os.environ[env] = val
    if args.get("searxng"):
        args["searxngInstances"] = [s.strip() for s in str(args["searxng"]).split(",") if s.strip()]
    elif cfg_settings.get("searxng"):
        args["searxngInstances"] = [s.strip() for s in str(cfg_settings["searxng"]).split(",") if s.strip()]

    if args.get("list-engines"):
        print(json.dumps(list_engines(), ensure_ascii=False, indent=2))
        return 0

    if args.get("test"):
        q = args.get("query") or args.get("q") or "DeepSeek Harness"
        results = []
        for engine in ALL_ENGINES:
            r = run_engine_safe(engine, q, 2, None, {"lang": "zh"})
            if r.get("ok"):
                srcs = r.get("sources") or []
                item = {
                    "engine": engine,
                    "status": "ok" if srcs else "empty",
                    "results": len(srcs),
                }
                if srcs and srcs[0].get("title"):
                    item["sampleTitle"] = srcs[0]["title"]
                if srcs and srcs[0].get("url"):
                    item["sampleUrl"] = srcs[0]["url"]
                results.append(item)
            else:
                results.append({"engine": engine, "status": "fail", "error": r.get("error")})
        out = {"ok": True, "query": q, "proxy": PROXY_URL or None, "results": results}
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    # 单页抓取：不走搜索回退链，避免额外调用与费用。
    # 默认后端：--fetch-engine > 配置 fetchPriority（数字排序，跳过没配 key 的）> deepseek。
    if args.get("fetch"):
        url = args.get("fetch")
        engine = args.get("fetch-engine")
        if not engine:
            fp = cfg_settings.get("fetchPriority") or {}
            ordered = sorted(fp, key=fp.__getitem__)
            engine = next(
                (e for e in ordered if e in FETCH_ENGINES and (e in ("keenable", "anysearch") or resolve_key(e))),
                "deepseek",
            )
        if engine not in FETCH_ENGINES:
            raise RuntimeError("unknown fetch engine; choose: " + ", ".join(FETCH_ENGINES))
        key = resolve_key(engine)
        if engine == "deepseek" and not key:
            raise RuntimeError("DEEPSEEK_API_KEY is not configured（写入 secrets.yaml 或设置环境变量）")
        started = time.time()
        try:
            page = fetch_page(url, engine, key)
        except Exception as exc:
            message = str(exc)
            if key:
                message = message.replace(key, "[redacted]")
            raise RuntimeError(message) from None
        out = {
            "ok": True,
            "fetch": True,
            "engine": engine + "-fetch",
            "url": url,
            "title": page["title"],
            "content": page["content"],
            "elapsedMs": int((time.time() - started) * 1000),
        }
        if args.get("text"):
            print(("%s\n\n" % out["title"] if out["title"] else "") + out["content"])
        else:
            print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    if args.get("platform") or args.get("p"):
        platform = args.get("platform") or args.get("p")
        query = args.get("query") or args.get("q")
        if not query:
            raise RuntimeError("--query is required")
        if platform not in PLATFORMS:
            raise RuntimeError('unknown platform "%s" — use: %s' % (platform, ", ".join(PLATFORMS)))
        n = min(int(args.get("max") or args.get("n") or 5), 10)
        started = time.time()
        result = search_platform(platform, query, n, args.get("lang") or "zh")
        out = {
            "ok": True,
            "query": query,
            "platform": platform,
            "engine": platform,
            "sources": result["sources"],
            "elapsedMs": int((time.time() - started) * 1000),
        }
        print(format_text(out) if args.get("text") else json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    query = args.get("query") or args.get("q") or (" ".join(args["_"]) if args["_"] else "")
    if not query or not str(query).strip():
        print_help()
        return 2

    time_raw = args.get("time") or args.get("t") or args.get("time-range")
    time_range = parse_time_range(time_raw)
    if time_raw and not time_range:
        print('warning: 无法解析时间范围 "%s"，已忽略' % time_raw, file=sys.stderr)

    # --engines a,b,c：限定候选引擎集（同时作用于 auto 路由、回退链和 multi 并发）
    engine_list = None
    if args.get("engines"):
        engine_list = [s.strip() for s in str(args["engines"]).split(",") if s.strip() in ALL_ENGINES]
        if not engine_list:
            print("warning: --engines 中没有有效引擎，已忽略", file=sys.stderr)
            engine_list = None

    # 配置文件里手动禁用的引擎（disabledEngines）。--engines 显式指定时以 CLI 为准。
    disabled_engines = cfg_settings.get("disabledEngines") or []
    if cfg_settings.get("unknownDisabled"):
        print(
            "warning: disabledEngines 里有未知引擎，已忽略: %s" % ", ".join(cfg_settings["unknownDisabled"]),
            file=sys.stderr,
        )
    for warn in cfg_settings.get("priorityWarnings") or []:
        print("warning: %s" % warn, file=sys.stderr)

    # 候选引擎集 = CLI --engines（若有）或全量，再剔除被禁用的
    candidate_engines = [e for e in (engine_list or ALL_ENGINES) if e not in disabled_engines]
    if not candidate_engines:
        raise RuntimeError("所有引擎都被禁用了 —— 检查 config.yaml 的 disabledEngines，或至少保留一个")

    # 参数优先级：CLI 显式传参 > 配置文件 > 内置默认
    opts = {
        "lang": args.get("lang") or cfg_settings.get("lang") or "zh",
        "bingMarket": args.get("market") or cfg_settings.get("market"),
        "safeSearch": args.get("safe-search") or cfg_settings.get("safeSearch") or "off",
        "searxngInstances": args.get("searxngInstances"),
        "engines": candidate_engines,
        "explicitEngines": [e for e in engine_list if e not in disabled_engines] if engine_list else None,
        "disabledEngines": disabled_engines,
        "searchPriority": cfg_settings.get("searchPriority"),
        "budgetMs": (cfg_settings["timeout"] * 1000) if isinstance(cfg_settings.get("timeout"), (int, float)) else 30000,
    }

    result = search(
        query,
        engine=args.get("engine") or args.get("e") or cfg_settings.get("engine") or "auto",
        max_results=args.get("max") or args.get("n") or cfg_settings.get("maxResults") or 5,
        time_range=time_range,
        opts=opts,
        fallback=not args.get("no-fallback"),
    )

    print(format_text(result) if args.get("text") else json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except BrokenPipeError:
        sys.exit(0)
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False, indent=2))
        sys.exit(1)
