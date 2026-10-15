# AkShare 新闻接口

本站 Flask 进程通过 AkShare 获取财经快讯，向已经登录的网站用户提供同源只读接口。首版支持东方财富财经快讯和财联社电报；新闻列表按发布时间倒序返回。该接入提供新闻数据 API 与前端调用 helper，目前没有新增菜单、新闻页面、后台定时任务或 AI 工具。

本文件是桌面 **「网站_v10121930 - 副本 (2)」** 的当前接口说明。相邻的 `AkShare新闻接口_桌面版.md` 和 `AkShare新闻接口_工作区版.md` 是另外两个网站版本的来源参考，含各自路径、端口及当时的验收记录；调用本副本时以本文件为准。

## 启动和依赖

本桌面副本使用自己的 `.venv` 和 `启动LangGraph网站.command`，启动器固定指定 **8894** 端口。现有虚拟环境由 `uv` 创建，没有内置 `pip`。先确认 `.venv/bin/python --version` 为 Python 3.11 或更新版本，再在原环境安装依赖并启动网站；新闻接口与网站共用进程和端口：

```bash
cd '/Users/jaylen/Desktop/网站_v10121930 - 副本 (2)'
.venv/bin/python --version
uv pip install --python .venv/bin/python -r requirements.txt
zsh ./启动LangGraph网站.command
```

新机器上先安装 Python 3.11 或更新版本，再新建环境；以下以 `python3.11` 为例，可换成已安装的更新版本命令：

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

后端依赖固定为 `akshare==1.19.1`。不要直接用可能指向 Python 3.9 的系统 `python3` 安装。AkShare 从上游公开页面获取数据，接口可用性取决于当前网络和上游页面结构。

可配置项如下；默认值已可直接使用，单位为秒。可通过网站进程的环境变量或现有 `.env` 设置，改动后重启网站进程。

| 环境变量 | 默认值 | 含义 |
| --- | ---: | --- |
| `NEWS_CACHE_TTL_SECONDS` | 60 | 成功抓取后，在此期间直接使用进程内缓存。 |
| `NEWS_FETCH_TIMEOUT_SECONDS` | 15 | 单次抓取子进程的硬超时。 |
| `NEWS_MAX_STALE_SECONDS` | 900 | 抓取失败时，可回退到最近一次成功缓存的最长时间。 |
| `NEWS_RETRY_SECONDS` | 30 | 抓取失败后的再次尝试间隔。 |

缓存由**每个网站进程按来源共享**，在请求到达且缓存过期时刷新；不是每 60 秒自动抓取，也不持久化到 SQLite。多进程部署时，各进程各自维护缓存。筛选和分页针对该来源本次已取得的记录进行，不等同于搜索全部历史新闻。

## 接口

所有请求均使用原网站的登录 Cookie；未登录返回 HTTP 401。成功响应为 JSON。

| 方法与路径 | 功能 |
| --- | --- |
| `GET /desktop/api/news/sources` | 获取支持的来源、AkShare 函数、原文链接能力，以及刷新配置。 |
| `GET /desktop/api/news` | 获取一个来源的新闻，支持关键词筛选与分页。 |

### 来源与刷新配置

`GET /desktop/api/news/sources` 返回以下结构：

```json
{
  "provider": "akshare",
  "default_source": "eastmoney",
  "sources": [
    {"id": "eastmoney", "name": "东方财富财经快讯", "interface": "stock_info_global_em", "has_article_url": true},
    {"id": "cls", "name": "财联社电报", "interface": "stock_info_global_cls", "has_article_url": false}
  ],
  "refresh": {"mode": "on_request", "ttl_seconds": 60, "timeout_seconds": 15, "max_stale_seconds": 900, "retry_seconds": 30}
}
```

`refresh` 中的数字会随网站配置变化。`on_request` 表示由访问新闻接口的请求触发过期刷新。

### 新闻列表请求

| 查询参数 | 默认值 | 规则 |
| --- | --- | --- |
| `source` | `eastmoney` | 仅 `eastmoney` 或 `cls`，一次只取一个来源。 |
| `limit` | `20` | 整数，1–100。 |
| `offset` | `0` | 整数，0–1000。 |
| `q` | 无 | 最长 80 字符的纯文本关键词；在标题和摘要内做不区分大小写的子串匹配。 |

未知参数、重复参数及无效值返回 HTTP 400。`limit` 和 `offset` 在关键词筛选后生效。接口没有跨来源合并、个股新闻、正文或全量历史检索功能。

`GET /desktop/api/news` 的返回字段如下：

| 字段 | 含义 |
| --- | --- |
| `provider` / `source` / `source_name` | 提供接口的库固定为 `akshare`；后两项分别是本次来源 ID 和显示名称。 |
| `items` | 新闻数组，按发布时间从新到旧排列。 |
| `items[].id` | 服务端根据来源和原文链接（无链接时结合发布时间、标题与摘要）生成的去重标识；前端可作为列表 key。 |
| `items[].source` / `items[].source_name` | 单条新闻的来源 ID 与显示名称。 |
| `items[].title` / `items[].summary` | 最长 300 / 4000 字符的纯文本；上游缺标题时取摘要前 80 字符作为展示标题。把两者作为文本渲染。 |
| `items[].published_at` | 新闻发布时间，带 `+08:00` 时区的 ISO 8601 字符串。 |
| `items[].url` | 原文 HTTP(S) 链接，可能为 `null`；财联社电报当前没有文章 URL。 |
| `items[].title_generated` | 布尔值；是否由内容补成展示标题。 |
| `pagination.limit` / `pagination.offset` | 本次分页参数。 |
| `pagination.total` / `pagination.has_more` | 本次来源记录在关键词筛选后的总数，以及是否还有下一页。 |
| `cache.hit` / `cache.stale` | 是否使用已有缓存、是否因刷新失败回退到旧缓存。 |
| `cache.fetched_at` / `cache.expires_at` | 最近一次**成功**抓取时间及正常缓存到期时间；抓取失败不会伪造新的抓取时间。 |
| `cache.age_seconds` / `cache.ttl_seconds` | 成功缓存的当前年龄及有效期，单位为秒。 |
| `cache.refresh_retry_after_seconds` | 失败退避期间距离下次可尝试刷新还需等待的秒数。 |
| `latest_published_at` | 当前来源缓存中最新一条的发布时间，带 `+08:00` 时区；不随 `q` 或 `offset` 改变。 |
| `warning` | 正常时为 `null`；回退旧缓存时为 `{code,message}`。 |

首次或到期刷新成功返回 HTTP 200。刷新失败但最近一次成功缓存还在 900 秒回退窗口内时，同样返回 HTTP 200，并设置 `cache.stale=true` 和 `warning`；页面应展示旧数据提示、`cache.fetched_at`，不要把旧新闻标成刚更新。超过回退窗口后不再返回旧数据。

| 状态码 | 触发情况 |
| ---: | --- |
| 400 | 不支持、重复或无效的查询参数。 |
| 401 | 未登录。 |
| 502 | 上游异常、返回空结果或没有有效新闻，且无可用缓存。 |
| 503 | 网站 Python 环境缺少 AkShare 依赖或抓取进程无法启动，且无可用缓存。 |
| 504 | 抓取超时，且无可用缓存。 |

错误响应沿用网站现有 `{error, code?}` 结构。服务不可用时，先核对网站 Python 环境的依赖和当前网络，再根据状态码排查。

## 调用示例

登录网站后，在浏览器同源页面的 JavaScript 中可直接请求：

```js
const response = await fetch('/desktop/api/news?source=eastmoney&limit=20', {
  credentials: 'include',
});
const data = await response.json();
if (!response.ok) throw new Error(data.error || `请求失败（${response.status}）`);
console.log(data.items, data.cache.stale);
```

Vue 工作台已有同源请求封装。页面组件若需要新闻，可从 `frontend/src/modules/workbench/newsApi.js` 引入；`getNews` 的第二个参数或 `getNewsSources` 的第一个参数可传 `AbortSignal`，以取消页面离开后的请求：

```js
import { getNews, getNewsSources } from './newsApi.js';

const sources = await getNewsSources();
const page = await getNews({ source: 'cls', q: '新能源', limit: 20, offset: 0 });
console.log(sources.sources, page.items, page.pagination.has_more);
```

命令行验证时，使用自己**已有的登录 Cookie 文件**，不要在命令行或文档里粘贴 Cookie 值：

```bash
curl -sS --cookie /path/to/existing-cookie-jar.txt 'http://127.0.0.1:8894/desktop/api/news/sources'
curl -sS --cookie /path/to/existing-cookie-jar.txt --get \
  --data-urlencode 'source=eastmoney' --data-urlencode 'limit=10' \
  --data-urlencode 'q=基金' 'http://127.0.0.1:8894/desktop/api/news'
```

公开展示新闻内容与原文链接时，还需核对 AkShare 及上游新闻来源的使用许可；程序依赖的许可不等于新闻转载许可。

## 验证与复现

先在本副本运行新闻专项测试：

```bash
cd '/Users/jaylen/Desktop/网站_v10121930 - 副本 (2)'
.venv/bin/python -m unittest discover -s tests -p 'test_news.py' -v
```

本副本的[真实 HTTP 验证记录](news-live-check.json)由 [`scripts/smoke_news_live.py`](../scripts/smoke_news_live.py) 生成。它使用**临时测试账号与临时数据库**，不读取现有账号数据；会显式向两个上游来源各发起一次抓取，因此不属于默认单元测试。记录以文件中实际的 `ok`、来源、条数、时间和状态为准，不沿用其他网站实例的验收结论：

```bash
cd '/Users/jaylen/Desktop/网站_v10121930 - 副本 (2)'
.venv/bin/python scripts/smoke_news_live.py --output docs/news-live-check.json
```

本副本 2026-10-13 10:06（北京时间）的记录为 `ok=true`：东方财富返回 HTTP 200、抓取窗口 200 条；财联社返回 HTTP 200、抓取窗口 20 条。两个来源均通过分页与关键词筛选、缓存复用检查；匿名请求为 401、无效 `limit` 为 400、POST 写请求为 405。两个来源的登录后实时抓取在**本副本相同代码的临时网站实例**中完成。

本副本同步后的完整后端回归 **179 项全部通过**，前端测试 **35 项全部通过**；`newsApi.js` 语法检查通过。这些是本副本的测试结果，不沿用相邻两份参考文档的旧数字。

本副本 8894 端口的日常网站进程已优雅重启并加载新接口：`GET /desktop/health` 为 HTTP 200，`OPTIONS /desktop/api/news` 为 HTTP 200，两个新闻 GET 路由在未登录时均返回 HTTP 401。日常启动命令为 `zsh ./启动LangGraph网站.command`。接口代码和前端调用 helper 已加入本副本；新闻展示页面需另行使用这些接口接入。
