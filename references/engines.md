# 引擎调用参考

每个引擎的 endpoint、认证、请求体、响应字段映射和实测踩过的坑。照这个文档可以直接在任意语言里重写调用，不依赖本 skill 的脚本。

## 目录

- [通用约定](#通用约定)
- [免 key 引擎](#免-key-引擎)
  - [Bing](#bing)
  - [DuckDuckGo HTML / Lite](#duckduckgo-html--lite)
  - [SearXNG](#searxng)
  - [AnySearch](#anysearch)
  - [Exa（MCP 匿名）](#examcp-匿名)
  - [Tavily](#tavily)
  - [Keenable（MCP 匿名 / REST）](#keenablemcp-匿名--rest)
  - [Firecrawl](#firecrawl)
  - [Parallel（MCP 匿名 / REST）](#parallelmcp-匿名--rest)
- [带 key 引擎](#带-key-引擎)
  - [Exa（REST）](#exarest)
  - [Perplexity](#perplexity)
  - [DeepSeek 搜索 API](#deepseek-搜索-apideepseek)
  - [DeepSeek Fetch API](#deepseek-fetch-api)
  - [SerpBase](#serpbase)
  - [You.com](#youcom)
  - [百度千帆](#百度千帆)
  - [Kimi（Moonshot）](#kimimoonshot)
  - [阿里云百炼](#阿里云百炼)
  - [豆包（火山联网搜索）](#豆包火山联网搜索)
- [平台搜索](#平台搜索)
- [密钥对照表](#密钥对照表)

## 通用约定

**User-Agent**（所有请求都带，抓 HTML 的尤其必要）：

```
Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36
```

**统一输出结构** —— 所有引擎解析后都归一到这个形状，上层才好回退和合并：

```json
{ "url": "...", "title": "...", "snippet": "...", "publishedAt": "..." }
```

只有 `url` 必填；其余字段没有就省略，不要写空字符串或 `null`（合并去重时会误判）。

**统一清洗**：`snippet` 出口处统一去噪（登录/订阅/付费墙话术）、折叠空白、截断。各引擎内部不做，避免重复处理。

**统一排序**：按返回顺序保留，不做二次打分。`multi` 模式才按跨源命中数重排。

## 免 key 引擎

### Bing

**默认引擎，最稳，中文优化。**

```
GET https://www.bing.com/search?q=<query>&mkt=zh-CN&adlt=off
```

| 参数 | 说明 |
|---|---|
| `mkt` | 市场，`zh-CN` / `en-US` / `ja-JP` 等，决定结果本地化 |
| `adlt` | 安全搜索 `off` / `moderate` / `strict` |
| `Accept-Language` | 必须和 `mkt` 一致，否则中文 header 会污染非中文结果 |

**解析**：抓 HTML，结果块是 `<li class="b_algo">...</li>`。

- URL：块内第一个 `<a href="https://...">`
- 标题：`<h2>...<a>标题</a>...</h2>`
- 摘要：第一个 `<p>...</p>`

**坑（重要）**：**Bing 查无结果时会返回一张完全无关的缓存 SERP** —— `<li class="b_algo">` 照常存在，但内容是别的查询的热门页。直接返回等于把垃圾喂给模型。

识别方法：把 query 切成 token（中文取 1-2 字组合即二元组，拉丁/数字取长度 ≥2 的词），检查是否有 token 出现在结果的 `title + snippet + url` 里。**一个都没有就判为 0 结果**，交给回退链换引擎。

```python
# 中文二元组 + 英文词
tokens = set()
for run in re.findall(r"[\u4e00-\u9fff]+", query):
    if len(run) <= 2: tokens.add(run)
    for i in range(len(run) - 1): tokens.add(run[i:i+2])
for w in re.split(r"[^a-z0-9]+", query.lower()):
    if len(w) >= 2: tokens.add(w)
```

不支持时间过滤。

### DuckDuckGo HTML / Lite

```
GET https://html.duckduckgo.com/html/?q=<query>&adlt=-1&df=w
GET https://lite.duckduckgo.com/lite/?q=<query>&adlt=-1&df=w
```

| 参数 | 说明 |
|---|---|
| `adlt` | `-1` = off，`0` = moderate，`1` = strict |
| `df` | 时间过滤：`d` / `w` / `m` / `y`（只支持固定档） |
| `kl` | 区域，如 `cn-zh`（可选） |

**HTML 版解析**：块 `<div class="result results_links ...">...</div>`，取 `class="result__a"` 的 `href` 和文本、`class="result__snippet"` 的文本。

**Lite 版解析**：`class="result-link"` 的 `<a>`，摘要按索引对齐 `class="result-snippet"` 的 `<td>`。

**URL 还原**：DDG 的 href 是跳转链接，真实 URL 在 `uddg=` 参数里，需要 `urlDecode`。`//` 开头补 `https:`。

**坑**：**反爬很凶**。HTTP 202 或页面含 `anomaly` / `captcha` / `unusual traffic` / `robot check` 就是被限流了，直接抛错换引擎，不要重试太多次。国内必须走代理。

### SearXNG

```
GET https://<instance>/search?q=<query>&format=json&time_range=week
```

| 参数 | 说明 |
|---|---|
| `format` | 必须是 `json`（部分实例默认关了 JSON 输出，会返回 403） |
| `time_range` | `day` / `week` / `month` / `year` |

**响应**：`{ "results": [ { "url", "title", "content" } ] }`。注意摘要字段叫 `content` 不是 `snippet`。

**公共实例**（按可用性排序，随时可能挂）：

```
https://opnxng.com
https://priv.au
https://searx.be
https://searx.tiekoetter.com
https://search.inetol.net
https://paulgo.io
```

**坑**：单个实例失败要**继续试下一个**，并把每个实例的错误都收集起来（只报最后一个会误导排查）。全部失败时错误信息里要有每个实例的原因。

### AnySearch

```
POST https://api.anysearch.com/v1/search
Content-Type: application/json

{ "query": "...", "max_results": 5 }
```

匿名额度，**不需要 key**。有 key 时加 `Authorization: Bearer <key>` 提额。

**响应**：`{ "code": 0, "data": { "results": [ { "url", "title", "snippet" } ] } }`

**坑**：`code !== 0` 就是业务错误，即使 HTTP 200。key 无效返回 401/403 时，**退回匿名再试一次**（不要直接失败，匿名额度通常还能用），并在结果里提示用户 key 无效。

不支持时间过滤。

### Exa（MCP 匿名）

Exa 的公开 MCP 端点，**不需要 key**。

```
POST https://mcp.exa.ai/mcp
Content-Type: application/json
Accept: application/json, text/event-stream

{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "web_search_exa",
    "arguments": { "query": "...", "numResults": 5 }
  }
}
```

**响应可能是 JSON 也可能是 SSE**，必须两种都兼容：

```
event: message
data: {"result":{"content":[{"type":"text","text":"..."}]}}
```

解析时先尝试 `JSON.parse(整个 body)`，失败再逐行找 `data: ` 前缀。

**内层 text 是自定义文本格式**，需要二次解析：

```
Title: DeepSeek Harness | Explore the limits of intelligence
URL: https://www.deepseek.com/en/harness/
Published: 2026-10-26T00:00:00.000Z
Highlights:
第一段摘要...
第二段...
```

按 `\n(?=Title:)` 切块，正则取 `Title:` / `URL:` / `Published:`，`Highlights:` 之后取前 3 行拼成摘要。

**坑**：`Published` 可能是 `N/A` 这类占位符，**只有匹配 `^\d{4}-\d{2}-\d{2}` 才当作日期**。

### Tavily

```
POST https://api.tavily.com/search
Content-Type: application/json

{ "query": "...", "max_results": 5, "search_depth": "basic", "time_range": "week" }
```

**免 key 的关键**：不加 `Authorization` 头，改加

```
x-tavily-access-mode: keyless
```

有 key 时用 `Authorization: Bearer <key>`，走账号档（额度更高、更稳）。

| 参数 | 说明 |
|---|---|
| `search_depth` | `basic` / `advanced`（advanced 更慢更贵） |
| `time_range` | `day` / `week` / `month` / `year`（只支持固定档） |
| `max_results` | 上限 20 |

**响应**：`{ "results": [ { "url", "title", "content" } ] }`，摘要字段是 `content`。

### Keenable（MCP 匿名 / REST）

**无 key** 走 MCP：

```
POST https://api.keenable.ai/mcp
Content-Type: application/json
Accept: application/json, text/event-stream

{
  "jsonrpc": "2.0", "id": 1, "method": "tools/call",
  "params": {
    "name": "search_web_pages",
    "arguments": { "query": "...", "published_after": "7d" }
  }
}
```

**有 key** 走 REST：

```
POST https://api.keenable.ai/v1/search
X-API-Key: <key>
Content-Type: application/json

{ "query": "...", "mode": "realtime", "published_after": "7d" }
```

**响应（REST）**：`{ "results": [ { "url", "title", "snippet"|"description", "published_at" } ] }`

**响应（MCP）**：和 Exa 一样是 `Title:/URL:/Published:/Snippets:` 文本块，但摘要小标题是 **`Snippets:`** 不是 `Highlights:`，而且日期字段可能是 **`Acquired:`** 而不是 `Published:`。

**`published_after` 支持两种格式**：

- 相对：`12h` / `7d` / `1mo` / `1y`（**这是 Keenable 特有格式**，不是 `7days`）
- 绝对：`YYYY-MM-DD`

**坑**：MCP 响应的 `isError: true` 时，`content` 里装的是错误文本而不是结果，要判出来。

### Firecrawl

```
POST https://api.firecrawl.dev/v2/search
Content-Type: application/json

{ "query": "...", "limit": 5, "tbs": "qdr:w" }
```

**`/v2/search` 无需 key 即可调用**（官方匿名限流），配 `Authorization: Bearer <key>` 提额。

| 参数 | 说明 |
|---|---|
| `limit` | 上限 10 |
| `tbs` | Google 时间语法，见下 |

**`tbs` 两种写法**：

- 固定档：`qdr:d`（日）/ `qdr:w`（周）/ `qdr:m`（月）/ `qdr:y`（年）
- 绝对区间：`cdr:1,cd_min:M/D/YYYY`（注意是 **月/日/年**，不是 ISO）

**响应**：`{ "data": { "web": [ { "url", "title", "description" } ] } }`，摘要在 `description`。

### Parallel（MCP 匿名 / REST）

**无 key** 走 MCP：

```
POST https://search.parallel.ai/mcp
Content-Type: application/json
Accept: application/json, text/event-stream

{
  "jsonrpc": "2.0", "id": 1, "method": "tools/call",
  "params": {
    "name": "web_search",
    "arguments": {
      "objective": "...",
      "search_queries": ["..."],
      "session_id": "<32位十六进制随机串>"
    }
  }
}
```

**有 key** 走 REST：

```
POST https://api.parallel.ai/v1/search
x-api-key: <key>
Content-Type: application/json

{
  "objective": "...",
  "search_queries": ["..."],
  "mode": "fast",
  "advanced_settings": {
    "max_results": 5,
    "source_policy": { "after_date": "2026-07-01" }
  }
}
```

**响应（REST）**：`{ "results": [ { "url", "title", "excerpts": ["..."], "publish_date" } ] }` —— 摘要在 `excerpts` **数组**里，取第一个非空项。

**响应（MCP）**：`result.content[].text` 里是**字符串化的 JSON**，需要再 `JSON.parse` 一次才能拿到 `results`。结构同 REST。

**坑（重要）**：

- MCP 的 `web_search` 工具**没有 `max_results` 和 `after_date` 参数**，条数由服务端决定，时间过滤只能退化成把「prefer results published after X」写进 `objective` 的自然语言软提示，**精度不可靠**。
- `session_id` 要固定一个随机串复用，不要每次请求都换。

## 带 key 引擎

### Exa（REST）

```
POST https://api.exa.ai/search
Authorization: Bearer <EXA_API_KEY>
Content-Type: application/json

{
  "query": "...",
  "type": "auto",
  "numResults": 5,
  "contents": { "highlights": { "highlightsPerUrl": 1 } },
  "startPublishedDate": "2026-07-01T00:00:00.000Z"
}
```

**响应**：`{ "results": [ { "url", "title", "highlights": ["..."], "publishedDate" } ] }`

**坑**：

- 摘要在 `highlights` **数组**里，取第一个非空项；如果所有 highlight 都是空的，**这条结果就没有摘要** —— 可以选择丢弃该条（原插件就是这么做的）或保留 URL。
- `startPublishedDate` 是完整 ISO 时间戳，不是 `YYYY-MM-DD`。
- 402 = key 有效但额度/计费有问题，不要报成「key 无效」。

### Perplexity

```
POST https://api.perplexity.ai/chat/completions
Authorization: Bearer <PERPLEXITY_API_KEY>
Content-Type: application/json

{
  "model": "sonar",
  "max_tokens": 1024,
  "messages": [{ "role": "user", "content": "<query>" }]
}
```

**响应**：`{ "choices": [{ "message": { "content": "..." } }], "citations": ["https://..."] }`

**这是对话式搜索**，返回的是一段**自然语言答案** + 引用 URL 列表，不是结构化结果。所以：

- `content` 字段要保留答案正文，别丢。
- `sources` 只有 URL（`citations`），摘要只能用答案前 200 字填充。

### DeepSeek 搜索 API（deepseek）

DeepSeek 自研搜索引擎的独立 REST 接口，返回**结构化结果**，与 `--fetch` 共用同一个 `DEEPSEEK_API_KEY`。

```
POST https://api.deepseek.com/v1/tools/search
Authorization: Bearer <DEEPSEEK_API_KEY>
Content-Type: application/json

{ "query": "DeepSeek 模型 site:github.com lang:zh after:2025", "count": 10 }
```

请求字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `query` | string | **必填**。`site:` `-site:` `"..."` `-` `lang:` `after:` `before:` 语法都支持。**上限 128 个 Unicode 字符**，超长必须截断 |
| `count` | int | 1–50，默认 10 |
| `ori_question` | string | 可选，触发搜索的原始问题，帮助理解意图 |
| `need_content` | bool | 默认 false；开了才返回网页正文（体积大，CLI 默认不开） |

**响应**：`{ "results": [{ "rerank_score", "title", "url", "snippet", "summary", "content", "site_name", "site_icon", "date", "authority" }] }`

字段映射：

- `snippet`（100–200 字符，给前端看）→ 输出的 `snippet`；为空时退回 `summary`（600–800 字符，给模型看）
- `date`（UTC RFC 3339）→ `publishedAt`
- `rerank_score`（0–3，≥1.5 优质）、`authority`（0–4，≥3 极权威）→ 接口已按相关性排序，CLI 不重复排序

**时间过滤**：靠 query 语法实现，精确到日。`-t week` → 拼接 `after:<7天前日期>`；`-t 2026-07-01` → `after:2026-07-01`。用户 query 里已写了 `after:` 就不重复追加。**拼接后总长仍要 ≤128 字符，截断时保留时间语法、裁查询词**。

**计费**：按成功请求次数计费（每 1000 次 5 元），`results` 为空不计费；Search QPS 100。

**错误**：401 key 无效；402 余额不足；429 超限（都可重试）；400 参数非法（query 超 128/全空白等）。

### DeepSeek Fetch API

抓取单个网页返回 Markdown 正文，供 `--fetch` 使用，与 Search 独立计费（每 1000 次 5 元）、独立限速（QPS 25）。

```
POST https://api.deepseek.com/v1/tools/fetch
Authorization: Bearer <DEEPSEEK_API_KEY>
Content-Type: application/json

{ "url": "https://example.com/article" }
```

**响应**：`{ "title": "...", "content": "Markdown 正文" }`

**错误码语义**（与一般 API 不同，别混）：

| 状态码 | 含义 | 是否重试 |
|---|---|---|
| 403 | 被安全策略拒绝 | 否 |
| 404 | **不是 URL 不存在**，而是「拿不到可用正文」：被站点拦截/内容类型不支持/无可提取正文 | 否 |
| 402 | 余额不足 | 充值后 |
| 429 | 超限（账号级 + 目标域名级两道） | 是 |

URL 要求：≤8192 字符、必须含 host 的 HTTP/HTTPS、不能带用户名密码。

### SerpBase

```
POST https://api.serpbase.dev/google/search
X-API-Key: <SERPBASE_API_KEY>
Content-Type: application/json

{ "q": "...", "hl": "zh-CN", "gl": "cn", "page": 1 }
```

| 参数 | 说明 |
|---|---|
| `hl` | 界面语言，`zh-CN` / `en` / `ja` 等 |
| `gl` | 国家，`cn` / `us` / `jp` 等 |

**坑（重要）**：**SerpBase 永远返回 HTTP 200**，业务状态在响应体的 `status` 字段：

| status | 含义 |
|---|---|
| `0` | 成功 |
| `1001` | key 无效或缺失 |
| `1000` | 请求非法 |

只看 HTTP 状态码会把所有错误都当成成功。

**响应**：`{ "status": 0, "organic": [ { "link", "title", "snippet", "published_at"|"date" } ] }` —— 注意 URL 字段叫 **`link`** 不是 `url`。

### You.com

```
GET https://ydc-index.io/v1/search?query=<query>&count=5
X-API-Key: <YOUCOM_API_KEY>
```

**响应**：`{ "results": { "web": [ { "url", "title", "snippets": ["..."], "description" } ] } }`

**坑**：摘要在 `snippets` **数组**里（用空格拼起来），没有时退回 `description`。

### 百度千帆

```
POST https://qianfan.baidubce.com/v2/ai_search/web_search
Authorization: Bearer <BAIDU_API_KEY>
X-Appbuilder-From: openclaw
Content-Type: application/json

{
  "messages": [{ "content": "<query>", "role": "user" }],
  "search_source": "baidu_search_v2",
  "resource_type_filter": [{ "type": "web", "top_k": 10 }],
  "search_filter": {
    "range": { "page_time": { "gte": "2026-07-01", "lt": "2026-10-27" } }
  }
}
```

| 参数 | 说明 |
|---|---|
| `top_k` | 1-50 |
| `search_filter.range.page_time` | 时间区间，**左闭右开**，日期格式 `YYYY-MM-DD` |

**响应**：`{ "references": [ { "url", "title", "content", "date" } ] }` —— 摘要在 `content`。

**坑**：响应顶层有 `code` 字段（非 0 即错误），HTTP 200 也可能是业务错误。`X-Appbuilder-From` 头不能省，否则会被拒。

### Kimi（Moonshot）

```
POST https://api.moonshot.cn/v1/tools/search
Authorization: Bearer <MOONSHOT_API_KEY>
Content-Type: application/json

{ "text_query": "...", "limit": 5, "timeout_seconds": 30 }
```

**响应**：`{ "search_results": [ { "url", "title", "chunks": [{ "text": "..." }], "snippet", "publish_date" } ] }`

**坑（容易踩）**：`chunks` 是 **`[{text: "..."}]` 对象数组，不是字符串数组**。直接 `join` 会得到 `[object Object]`。要取 `chunk.text`。没有 `chunks` 时才退回 `snippet`。

### 阿里云百炼

```
POST https://dashscope.aliyuncs.com/api/v1/mcps/EnhancedSearch/mcp
Authorization: Bearer <DASHSCOPE_API_KEY>
Content-Type: application/json
Accept: application/json, text/event-stream

{
  "jsonrpc": "2.0", "id": 1, "method": "tools/call",
  "params": { "name": "search_pro", "arguments": { "query": "..." } }
}
```

**响应三层嵌套**，一层都不能少：

```
{ "result": { "content": [ { "type": "text", "text": "<内层 JSON 字符串>" } ] } }
                                                            ↓ JSON.parse
{ "pages": [ { "url", "title", "snippet", "published_at" } ] }
```

**坑**：

- 查询词**长度必须 ≥ 2**，太短的要在尾部补空格（`"a "`）。
- 响应可能是 SSE 帧，要兼容 `data:` 前缀。
- `result.isError: true` 时 `content` 里是错误文本。

### 豆包（火山联网搜索）

```
POST https://open.feedcoopapi.com/search_api/web_search
Authorization: Bearer <DOUBAO_SEARCH_API_KEY>
X-Traffic-Tag: free-web-search-skill
Content-Type: application/json

{
  "Query": "...",
  "SearchType": "web",
  "Count": 10,
  "NeedSummary": true,
  "Filter": { "NeedUrl": true },
  "TimeRange": "OneWeek"
}
```

**注意请求体是 PascalCase**（`Query` / `Count` / `TimeRange`），和别家不一样。

| 参数 | 说明 |
|---|---|
| `Query` | 上限 100 字符，超了要截断 |
| `Count` | 1-50 |
| `SearchType` | `web` / `image` |
| `TimeRange` | `OneDay` / `OneWeek` / `OneMonth` / `OneYear`，或 `"YYYY-MM-DD..YYYY-MM-DD"` |

**响应**：`{ "Result": { "WebResults": [ { "Url", "Title", "Summary", "Content", "PublishTime" } ] } }`

**坑（重要）**：

- **错误也返回 HTTP 200**，必须解析 `ResponseMetadata.Error`，用 `Code`/`CodeN` 判错误类型。常见码：`10406` = 免费额度用尽（每月 500 次），`700429` = 限流，`700901` = key 无效。
- 摘要在 `Summary`，是**千字级**的正文摘要（实测 1300-1800 字），不是普通 snippet。截断上限要给到 2000 而不是 300，否则信息全丢。
- `Title`/`Summary` 里可能带 HTML 标签，要 strip。
- key 环境变量主名 `DOUBAO_SEARCH_API_KEY`，官方示例也用 `WEB_SEARCH_API_KEY`，后者可作回退。

## 平台搜索

这些走各平台公开 API，**零依赖、零 key**（GitHub 有速率限制但匿名可用）。

| 平台 | Endpoint | 关键字段 |
|---|---|---|
| `github` | `GET https://api.github.com/search/repositories?q=&per_page=` | `items[].html_url` / `full_name` / `description` / `stargazers_count` |
| `v2ex` | `GET https://www.v2ex.com/api/topics/hot.json` | **只有热帖**，需本地按关键词过滤；`id` → `https://www.v2ex.com/t/<id>` |
| `bilibili` | `GET https://api.bilibili.com/x/web-interface/search/all/v2?keyword=` | `data.result[].data[].arcurl` / `title`（含 `<em>` 高亮标签要 strip） |
| `reddit` | `GET https://old.reddit.com/search.json?q=&limit=&sort=relevance` | `data.children[].data.url` / `title` / `selftext` |
| `hn` | `GET https://hn.algolia.com/api/v1/search?query=&hitsPerPage=` | `hits[].url` / `title` / `story_title` / `points` / `num_comments` |
| `stackoverflow` | `GET https://api.stackexchange.com/2.3/search/advanced?order=desc&sort=relevance&q=&site=stackoverflow` | `items[].link` / `title` / `score` / `is_answered` |
| `wikipedia` | `GET https://<lang>.wikipedia.org/w/api.php?action=query&list=search&srsearch=&format=json` | `query.search[].title` / `snippet`（含 `<span class="searchmatch">` 要 strip） |
| `npm` | `GET https://registry.npmjs.com/-/v1/search?text=&size=` | `objects[].package.name` / `version` / `description` |

**坑**：

- V2EX 的 API 只能拿热帖，不能按关键词搜 —— 本质是「在热帖里过滤」，命中率低，要提前告知用户。
- HN 有外链的用 `url`，纯讨论帖没有 `url`，要退回 `https://news.ycombinator.com/item?id=<objectID>`。
- StackExchange 的 `filter=!nNPvSNVZJS` 是必须的，否则不返回 `link` 字段。
- Wikipedia 的 snippet 带高亮 HTML，必须 strip。
- Bilibili 的 `title` 带 `<em class="keyword">` 高亮标签。

## 密钥对照表

| 引擎 | 环境变量 | 获取地址 |
|---|---|---|
| `anysearch` | `ANYSEARCH_API_KEY`（可选，匿名可用） | — |
| `exa` | `EXA_API_KEY`（可选，MCP 匿名可用） | https://dashboard.exa.ai/api-keys |
| `tavily` | `TAVILY_API_KEY`（可选，keyless 可用） | https://app.tavily.com/home |
| `keenable` | `KEENABLE_API_KEY`（可选，MCP 匿名可用） | https://keenable.ai/login |
| `firecrawl` | `FIRECRAWL_API_KEY`（可选，匿名可用） | https://www.firecrawl.dev |
| `parallel` | `PARALLEL_API_KEY`（可选，MCP 匿名可用） | https://platform.parallel.ai |
| `perplexity` | `PERPLEXITY_API_KEY` | https://www.perplexity.ai/settings/api |
| `serpbase` | `SERPBASE_API_KEY` | https://serpbase.dev |
| `deepseek` | `DEEPSEEK_API_KEY`（与 --fetch 共用） | https://platform.deepseek.com/api_keys |
| `you` | `YOUCOM_API_KEY` | https://you.com/platform/api-keys |
| `baidu` | `BAIDU_API_KEY` | 百度千帆控制台 |
| `kimi` | `MOONSHOT_API_KEY` | https://platform.moonshot.cn |
| `aliyun` | `DASHSCOPE_API_KEY` | https://bailian.console.aliyun.com |
| `doubao` | `DOUBAO_SEARCH_API_KEY`（回退 `WEB_SEARCH_API_KEY`；搜索与 Fetch 共享每月 500 次免费额度） | https://console.volcengine.com/search-infinity/web-search |

## 从零复刻的最短路径

只要实现这三个，就有一个能用的搜索器（全部免 key、无需代理即可直连）：

1. **Bing** —— 抓 HTML + `b_algo` 解析 + 相关性校验，覆盖 90% 场景
2. **Tavily** —— `x-tavily-access-mode: keyless`，结构化 JSON，最省事
3. **AnySearch** —— 匿名 REST，中文效果好

再加 `Exa MCP` 提升英文质量、`Keenable` 补时效、`multi` 做交叉验证，就是完整形态。
