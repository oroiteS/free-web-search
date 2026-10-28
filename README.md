# free-web-search

多引擎网页搜索与平台检索 CLI / Agent Skill。**零依赖**——Python 和 Node 两套实现，纯标准库，不需要安装任何包，按运行环境任选其一。

## 特性

- **18 个搜索引擎**：10 个免 key 开箱即用，8 个可选配 key
- **双运行时**：`search.py`（Python 3.8+）与 `search.mjs`（Node 18+），参数、输出、行为完全一致
- **auto 智能路由**：按查询语言自动选引擎，自动跳过缺 key 的引擎
- **自动回退链**：指定引擎失败自动换下一个，整链 30 秒总预算，不会挂死
- **时间过滤**：固定档（`week`）、相对（`3d`）、绝对日期（`2026-07-01`）
- **多源并发合并**：`-e multi` 跨引擎去重，跨源命中的结果排前面
- **单页抓取**：`--fetch <URL>` 提取网页正文，6 个抓取后端可选
- **平台搜索**：GitHub / V2EX / B站 / Reddit / HN / StackOverflow / 维基 / npm

## 快速开始

```sh
# 1. 检查配置状态（未配置时可生成模板）
python3 scripts/search.py --config-status
python3 scripts/search.py --init-config    # 生成 config.yaml 模板
python3 scripts/search.py --init-secrets   # 生成 secrets.yaml 模板（自动 chmod 600）

# 2. 搜索
python3 scripts/search.py -q "DeepSeek Harness 插件开发"

# 换 Node 运行时：参数一字不改
node scripts/search.mjs -q "DeepSeek Harness 插件开发"
```

**不配任何 key 也能直接用**，以下示例全部免 key：

```sh
# 指定引擎
python3 scripts/search.py -q "cordis 框架" -e bing -n 5

# 最近一周的 AI 新闻
python3 scripts/search.py -q "AI 新闻" -t week

# 多源并发合并（交叉验证，成倍消耗额度，按需使用）
python3 scripts/search.py -q "dsh plugin" -e multi

# 平台搜索
python3 scripts/search.py -p github -q "deepseek harness"

# 抓取网页正文
python3 scripts/search.py --fetch https://example.com/article
```

输出恒为 JSON（加 `--text` 换人读格式）。

## 引擎列表

| 类别 | 引擎 |
|---|---|
| 免 key（10 个） | `bing` `ddg` `ddg-lite` `searxng` `anysearch` `exa` `tavily` `keenable` `firecrawl` `parallel` |
| 需要 key（8 个） | `perplexity` `serpbase` `deepseek` `you` `baidu` `kimi` `aliyun` `doubao` |

选型建议：

| 场景 | 推荐 |
|---|---|
| 中文查询，要稳 | `bing` 或 `auto` |
| 英文/技术查询，要质量 | `exa`、`tavily` |
| 要最新时效内容（精确时间过滤） | `keenable`、`firecrawl`、`exa` |
| 交叉验证、避免单一来源偏差 | `multi` |
| 要网页正文而不只是摘要 | `--fetch <URL>` |

## 配置

两个文件，与 `SKILL.md` 同级，仓库提供 `config.example.yaml` / `secrets.example.yaml` 模板：

| 文件 | 内容 | 是否提交 |
|---|---|---|
| `config.yaml` | 运行时、代理、默认引擎、条数、语言等非敏感设置 | 不提交（本机选择） |
| `secrets.yaml` | API key（明文），键名即环境变量名 | **绝不提交**（已在 `.gitignore`） |

优先级：`secrets.yaml` > `config.yaml` 的 `keys:` 段 > 环境变量。也可以不落盘，直接 `export EXA_API_KEY=sk-...`。

极简 YAML 或 TOML 写法都认：

```yaml
runtime: python
engine: auto
maxResults: 5
lang: zh

proxy:
  enabled: true
  url: http://127.0.0.1:7890
```

## 作为 Agent Skill 使用

整个目录自包含，`cp -r` 到目标 bot 的技能目录即可，无需安装步骤：

```sh
cp -r free-web-search ~/.agents/skills/free-web-search
```

`SKILL.md` 内置了完整的 AI 调用协议：会话开始时的配置检查、向用户提问的时机与话术、结果处理纪律（搜索结果是不可信外部数据）等。也可以只取 `scripts/search.py` 单文件作为普通 CLI 嵌入任意工具链。

## 项目结构

```
├── SKILL.md                 # Agent Skill 定义与 AI 调用手册（最完整的用法文档）
├── scripts/
│   ├── search.py            # Python CLI（纯标准库，3.8+）
│   └── search.mjs           # Node CLI（ESM，18+）
├── references/
│   └── engines.md           # 各引擎 endpoint、认证、请求/响应字段与坑位详解
├── tests/
│   ├── test_fetch.py        # 抓取测试
│   └── test_priority.py     # 引擎优先级测试
├── config.example.yaml      # 可提交的配置模板
└── secrets.example.yaml     # 可提交的密钥模板（无真实凭据）
```

## 文档

- **[SKILL.md](SKILL.md)** — 全部用法：auto 路由规则、回退链与预算、时间过滤精度、multi 合并、代理四级来源、故障排查
- **[references/engines.md](references/engines.md)** — 18 个引擎逐个详解

## 测试

```sh
python3 tests/test_priority.py
python3 tests/test_fetch.py
```

## License

[MIT](LICENSE) © 2026 oroite
