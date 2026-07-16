# oa-paper-fetch

`oa-paper-fetch` 是一个以 Codex Skill 为主入口的论文 PDF 获取工具。输入 DOI、标题、URL 或批量文件后，它会先查找合法的开放获取（Open Access，OA）版本；只有在用户明确启用学校访问时，才会复用由用户本人登录的浏览器会话，从 IEEE Xplore、Wiley Online Library 或 Elsevier ScienceDirect 获取用户有权访问的全文。

它不会使用 Sci-Hub、绕过付费墙、自动处理 CAPTCHA，也不会读取、输入或保存学校密码、MFA 验证码和恢复码。

## 工作流程

1. **优先查找 OA PDF。** 先处理直接 PDF 和 arXiv 链接；没有 DOI 但有标题时，再用 arXiv 标题搜索和 Crossref 元数据解析，随后收集 OpenAlex、Unpaywall 和 Semantic Scholar 提供的 OA 候选。
2. **按需使用学校访问。** 启用 `--institutional` 后，仅把 OA 阶段未成功、且具备 DOI 或原始 URL 的项目交给已登录浏览器；出版商白名单固定为 IEEE、Wiley 和 Elsevier。
3. **逐篇保留结果。** JSON 报告保存来源查询、候选地址、下载尝试和机构访问状态；CSV 提供便于筛选的扁平摘要。无法获取的论文仍会保留失败记录。

## 快速开始

### 安装为 Codex Skill

按照 Codex 当前的用户级 Skill 目录约定安装：

```bash
SKILLS_HOME="$HOME/.agents/skills"
mkdir -p "$SKILLS_HOME"
git clone https://github.com/Eason412/paper-fetch.git \
  "$SKILLS_HOME/oa-paper-fetch"
```

部分 Codex 安装使用 `$CODEX_HOME/skills`，或者在 `CODEX_HOME` 未设置时使用 `~/.codex/skills`。如果你的 Codex 显示的是这个 Skill 根目录，请改为安装到对应位置；不要在两个位置重复安装同名 Skill。Codex 通常会自动发现新 Skill，如果没有出现，请重启 Codex。

安装后可以直接在对话中调用：

```text
使用 $oa-paper-fetch 下载 https://arxiv.org/abs/1706.03762，
保存到 /absolute/path/to/papers。
```

`SKILL.md` 是主要操作入口；`oa_fetch.py` 和 `institutional_fetch.py` 是执行后端，通常不需要用户手工拼接命令。

### 直接运行 OA 后端

OA 层需要 Python 3.10 或更高版本，不依赖第三方 Python 包。在仓库根目录运行下面的网络冒烟示例，会下载一篇公开的 arXiv 论文：

```bash
python3 oa_fetch.py \
  --url "https://arxiv.org/abs/1706.03762" \
  --out "/absolute/path/to/papers" \
  --format text
```

如需启用 Unpaywall 查询，在本地环境中设置 `UNPAYWALL_EMAIL`。未设置时程序会跳过 Unpaywall，也不会打印该变量的值。

## 输入方式

单篇论文使用以下选项之一：

```bash
python3 oa_fetch.py --doi "10.xxxx/yyyy" --out ./pdfs
python3 oa_fetch.py --title "Attention is all you need" --out ./pdfs
python3 oa_fetch.py --url "https://arxiv.org/abs/1706.03762" --out ./pdfs
```

批量输入使用：

```bash
python3 oa_fetch.py --batch refs.md --out ./pdfs --format text
```

支持的批量格式：

- Markdown 表格：列名可使用 `title`/`题名`、`url`/`链接`、`doi` 和 `id`/`标记`；
- CSV：支持含义相同的列；
- 纯文本：每行一个 DOI、URL 或标题；空行和以 `#` 开头的行会被忽略。

## 学校机构访问

学校访问是可选功能。先在仓库根目录安装浏览器依赖：

```bash
python3 -m pip install -r requirements.txt
python3 -m playwright install chromium
```

首次使用或登录会话过期时，运行：

```bash
python3 oa_fetch.py --institutional-login
```

程序会打开一个可见浏览器，并分别打开 IEEE Xplore、ScienceDirect 和 Wiley Online Library。请在浏览器中自行选择学校机构访问并完成 SSO/MFA；程序不会点击或填写任何凭据字段。确认三个网站都已登录后，回到终端按 Enter，登录会话将保存在默认目录 `~/.oa-paper-fetch/profile`。

该 profile 包含已认证的 cookies，属于敏感数据。请只保存在本机，不要查看、同步、上传、分享或提交到 Git。

机构访问后端会优先尝试 Playwright 管理的系统 Google Chrome channel，失败后再尝试 Playwright Chromium。它启动的是独立持久化 profile，**不会接管已经打开的 Chrome，也不会复用日常 Chrome profile**。

登录完成后，使用下面的命令先查 OA，再对符合条件的失败项执行学校访问：

```bash
python3 oa_fetch.py \
  --batch "/absolute/path/to/refs.md" \
  --out "/absolute/path/to/papers" \
  --institutional \
  --format text
```

首次登录和登录修复必须使用可见浏览器。`--headless` 只适合复用已经确认可用的 profile。

### 机构访问限速

所有机构访问请求串行执行，并受到以下硬限制：

| 选项 | 默认值 | 允许范围 |
| --- | ---: | ---: |
| `--inst-delay` | 4 秒 | 不低于 4 秒 |
| `--inst-jitter` | 3 秒 | 0–10 秒 |
| `--max-institutional` | 30 | 1–30 次尝试 |

默认情况下，两次机构访问尝试之间间隔 4–7 秒。连续出现 3 次 HTTP 4xx、challenge 或登录墙后，本次运行会停止。30 次只是单次运行的安全上限，不是推荐批量大小；不要自动串联多次运行。学校和出版商的访问条款可能比这里的限制更严格。

## CLI 参数

| 选项 | 作用 |
| --- | --- |
| `--doi DOI` | 按 DOI 获取一篇论文。 |
| `--title TITLE` | 按标题解析并获取一篇论文。 |
| `--url URL` | 处理包含 DOI 的 URL、arXiv URL 或直接 PDF URL。 |
| `--batch PATH` | 读取 Markdown、CSV 或纯文本批量输入。 |
| `--out PATH` | 输出目录，默认为 `pdfs`。 |
| `--timeout SECONDS` | 单次请求超时，默认为 30 秒。 |
| `--overwrite` | 覆盖已经存在的目标 PDF。 |
| `--dry-run` | 解析元数据和候选地址并写入报告，但不下载 PDF。 |
| `--format json\|text` | stdout 始终输出 JSON；`text` 额外向 stderr 输出进度。 |
| `--version` | 输出程序版本。 |
| `--institutional` | 对符合条件的 OA 未成功项目使用已登录浏览器重试。 |
| `--institutional-login` | 打开可见的首次登录或会话刷新流程。 |
| `--browser-profile PATH` | 指定其他持久化浏览器 profile 目录。 |
| `--inst-delay SECONDS` | 设置机构访问基础延迟，不能低于 4 秒。 |
| `--inst-jitter SECONDS` | 增加 0–10 秒随机延迟。 |
| `--max-institutional N` | 把机构访问尝试限制在 1–30 次。 |
| `--headless` | 无可见窗口复用已经建立的机构访问 profile。 |

运行 `python3 oa_fetch.py --help` 可以查看当前代码提供的完整参数列表。

## 输出与退出码

每次正常获取都会向 stdout 打印完整 JSON payload。`--format text` 不会替换这个 JSON，只会向 stderr 增加适合人工阅读的进度，因此脚本仍然可以稳定解析 stdout。

输出目录包含：

- 下载成功的 PDF；
- `oa_fetch_results.json`：详细记录元数据、来源查询、候选地址、下载尝试和机构访问结果；
- `oa_fetch_results.csv`：每篇论文一行的扁平摘要，包含 `institutional_error` 等字段。

`--dry-run` 只是候选预览。找到候选地址时，它可能返回 `success: true` 和退出码 `0`，但不会写入 PDF；JSON 和 CSV 报告仍会生成。

| 退出码 | 含义 |
| ---: | --- |
| `0` | 所有项目成功，或所有 dry-run 项目都找到了候选地址。 |
| `1` | 至少一个项目仍未解决。 |
| `2` | CLI 参数无效，包括未提供 DOI、标题、URL、batch 或登录选项。 |
| `3` | 批量输入文件不存在，或输入解析后没有论文。 |
| `4` | 发生网络/传输异常、输出目录错误或报告写入错误。 |

常见机构访问错误：

| 错误 | 含义与处理 |
| --- | --- |
| `publisher_not_allowed` | 最终页面不属于三家允许的出版商；保留失败记录，不要扩展白名单。 |
| `unsafe_pdf_url` | PDF 地址越出当前出版商范围；停止该项目，不要手工绕过检查。 |
| `not_pdf_login_or_challenge` | 返回内容不是 PDF，通常需要用可见浏览器刷新登录状态。 |
| `aborted_after_repeated_blocks` | 连续 3 次阻断或登录墙后，本次运行已停止。 |
| `institutional_cap_reached` | 达到单次机构访问上限，剩余项目没有执行。 |

## 安全与访问边界

- OA 下载只接受标准端口上的 HTTP(S) URL，拒绝 localhost、metadata 地址以及私有、环回、链路本地、保留和组播网络，并在每次重定向时重新检查。
- 两个下载后端都把单个 PDF 响应限制在 80 MiB，并要求内容以 `%PDF` 文件签名开头后才写入磁盘。
- 机构访问只允许 DOI 链接和 IEEE、Wiley、Elsevier 页面；最终 PDF 必须与文章页面属于同一家出版商。
- 不要把密码、MFA、恢复码、session cookie、API secret 或 token 放入批量文件、命令参数、Issue 和日志。
- 本工具只适合用户本人有权访问的少量论文，不用于系统性采集、反爬绕过、代理轮换或自动连续批量下载。

## 当前限制

- 机构访问只支持 IEEE Xplore、Wiley Online Library 和 Elsevier ScienceDirect。
- 普通文章网页不会自动解析；`--url` 可靠支持的是含 DOI 的 URL、arXiv URL 和直接 PDF URL。
- 仅有标题且 OA 阶段没有解析出 DOI 时，无法继续进入机构访问。
- 出版商登录页和 PDF 页面可能变化；遇到登录墙时需要用户在可见浏览器中刷新会话。
- 程序不能附着到已经打开的 Chrome，也不会保存学校账号和密码。

## 开发与测试

离线测试覆盖 OA 优先编排、URL 与重定向安全、机构出版商边界、限速参数和 Skill 契约：

```bash
python3 -m unittest discover -s tests -v
```

项目结构：

```text
SKILL.md                Codex 主入口与操作契约
oa_fetch.py             OA 解析、CLI、报告与机构回退编排
institutional_fetch.py  受限的 Playwright 持久化会话后端
requirements.txt        可选的机构访问依赖
tests/                  离线契约与边界测试
```

## 问题反馈与贡献

如需报告可复现问题或提出范围明确的改进，请创建 [GitHub Issue](https://github.com/Eason412/paper-fetch/issues)。请提供经过脱敏的命令、输入结构、退出码和错误名；不要上传浏览器 profile、cookie、学校凭据或 token。

Pull Request 应保持 OA 优先、三家出版商白名单和机构访问硬限制。项目由 [Eason412](https://github.com/Eason412) 维护，采用 [MIT License](LICENSE)。
