# draft-markup

Strike out a sentence. Keep the rest of your draft.

A small Python and HTML tool for reviewing Markdown drafts: mark text in a browser,
add a note, rewrite selected paragraphs with Gemini, then compare the next version
side by side. The interface and built-in instructions are in Chinese; drafts may
use other languages.

[中文说明](#中文说明) · [MIT license](LICENSE)

## What is included

- A marking page with annotations, undo, and comparison with the previous version.
- A Python CLI that builds the page and processes exported annotations.
- Direct replacements and deletions, plus Gemini rewrites for editorial comments.
- Versioned Markdown, per-round JSON records, and a readable annotation log.
- A fictional runnable example and [two real Chinese writing references](examples/real/README.md),
  including article text and annotation logs shared by their author.

This is an assisted editing workflow, not a standalone hosted editor. Online submission
uses a Claude Artifact host exposing `window.claude.use("db")`. Publishing the page,
retrieving annotations, and rerunning the CLI are manual or assistant-operated steps;
this repository does not supply that hosting integration. A local page displays the
draft and saves marks in browser storage, but has no built-in JSON export button.

## Try it without an API key

Requires Python 3.9+ and a browser. Python uses only the standard library.
Run these commands from the repository root on macOS or Linux:

```sh
demo_dir=$(mktemp -d)
cp examples/demo/* "$demo_dir/"
python3 markup.py build "$demo_dir"
# Open the printed page.html path in your browser.

python3 markup.py rewrite "$demo_dir" "$demo_dir/marks.json"
python3 markup.py build "$demo_dir"
# Reload page.html to compare the two versions.
```

The example annotation explicitly specifies a deletion, so this complete CLI round
makes no model request. Use the same directory for subsequent rounds.

## Model-backed editing

Set `GEMINI_API_KEY` in your shell using your preferred secret manager. Do not put it
in article files or in the HTML page. Standard `HTTPS_PROXY` and `NO_PROXY`
environment variables control proxy settings; no machine-specific proxy is required.

```sh
python3 markup.py build /path/to/article
python3 markup.py rewrite /path/to/article /path/to/exported-marks.json
python3 markup.py build /path/to/article
```

The configured default model is `gemini-2.5-pro`. Use `--model MODEL_ID` on `rewrite`
to select another Gemini model available to your account. Model requests use the
Google Gemini API and may incur charges.

In a compatible Artifact host, enable the database capability (`capabilities: {db: {}}`),
publish `page.html`, and annotate the draft. Marks are stored under `marks/<id>`;
submission records are stored at `state/submitted`. After submission, retrieve the
marks through the host's data tools and run the CLI. Rebuild and republish the page
after each round.

Annotations are classified as replacements, deletions, or comments. Inspect the printed
classification and the resulting diff. To force a decision, provide a `replace` string
in an input mark (`""` means delete), or create `rounds/r<N>-overrides.json` mapping
mark IDs to replacement strings, `""` for deletion, or `null` for editorial comments.
Neighboring paragraphs with comments are rewritten together. Review factual accuracy
and retained author wording before accepting any model output.

## Article files and annotation format

Keep your working article directories **outside this repository**. The selected
[real references](examples/real/README.md) are intentionally published snapshots.

| File | Purpose |
|---|---|
| `meta.json` | Required `key` and `title`; optional `artifact_url` and `v1_model` |
| `brief.md` | Writing instructions and permitted facts; required by `rewrite` |
| `v1.md`, `v2.md`, … | Drafts; the highest numbered version is current |
| `links.json` | Optional checked links: `[{"anchors": ["text"], "url": "https://example.com"}]` |
| `rounds/` | Annotation records, change mappings, and optional classification overrides |
| `划线记录.md` | Append-only human-readable annotation history |
| `page.html` | Generated review page with current and previous draft text |

Use a unique `key` for each article. The CLI accepts an annotation JSON array, a single
record, or a directory of JSON records. Each mark has `id`, `v` (version), `p`
(zero-based block index), `start`, `end` (exclusive), `quote`, and an optional `reason`.
An explicit `replace` field bypasses model classification for that mark. See the
[fictional example](examples/demo/marks.json). Only marks for the current version are used.

## Data handling and limits

Building a page is local. The generated HTML contains article text, including the
previous version and feedback when present: sharing the page shares that content.
The template loads fonts from Google Fonts. Local marks remain in browser storage;
Artifact mode additionally sends marks and submission metadata to the host database.
Model-backed rewriting sends the full draft, brief, and relevant annotations to Google.

The Markdown parser is intentionally small: headings, paragraphs, simple lists,
one-line quotes, and basic inline formatting. This is an early tool; review diffs,
especially for complex formatting or overlapping selections. Automated classification
is a heuristic, not a human approval step. There is no automatic fact checker.
The ignore rules help keep article files and secrets out of Git, but review staged
files before committing. Published article data is limited to the fictional example
and the two author-approved references; private briefs and working directories are not included.

Run the offline checks with `python3 -m unittest discover -s tests -v`.

## 中文说明

划掉不满意的句子，保留稿件其余部分。

`draft-markup` 用于 Markdown 稿件的划线修改：在网页选中文字、写批注，
由 Gemini 修改相关段落，再左右对比新旧版本。网页界面和内置提示词为中文，
正文可以使用其他语言。

仓库还收录了作者授权公开的[两篇真实中文文章及划线记录](examples/real/README.md)：
《OpenAI 公开了一批数学成果，so did I》（反差方案）和
《Sharing our AI progress in mathematics》，可对照正文查看实际修改意见。

### 使用方式

需要 Python 3.9+ 和浏览器，不需要安装第三方 Python 库。上面的离线示例可直接运行：
它复制一份虚构图书馆通知到临时目录，生成网页、执行一条明确的删除批注，再生成对比页，
全程无需 API 密钥。

自然语言批注的分类与段落改写需要在环境变量中设置 `GEMINI_API_KEY`。
默认模型为 `gemini-2.5-pro`，可通过 `rewrite --model MODEL_ID` 指定账号可用的其他
Gemini 模型。网络配置使用标准 `HTTPS_PROXY`、`NO_PROXY` 环境变量，模型调用可能产生费用。

当前在线提交流程依赖支持 `window.claude.use("db")` 的 Claude Artifact 宿主：
发布生成的 `page.html`，完成划线并提交，取回批注数据后运行 `rewrite`，然后重新生成、
发布页面。发布和取回数据由人或助手操作，仓库不提供完整托管服务。
本地打开网页可以划线并保存在浏览器中，但目前没有内置的 JSON 导出按钮。

### 批注与版本

- 给出替换句或删除指令：模型先判断批注类型，再按分类结果执行。
- 给出意见或疑问：交给 Gemini 改写，连续的相关段落一起处理。
- 需要确定执行方式时：在批注 JSON 中设置 `replace`，或用每轮的 overrides 文件指定。
- 每轮生成新版本、差异记录和 `划线记录.md`；接受前检查分类、事实和作者原话。

文章目录包含 `meta.json`、`brief.md`、`v1.md` 等输入，以及自动生成的 `rounds/`、
后续版本和 `page.html`。字段及命令见上方表格和示例。每篇文章使用不同的 `key`。

### 隐私与边界

日常工作稿件请放在仓库外。此仓库包含工具代码、虚构示例，以及作者明确选定的两篇真实中文
正文与划线记录；未包含私人 brief、文风语料库、其他稿件或密钥。
生成的网页包含当前正文、上一版正文及已有反馈，分享页面会同时分享这些内容。
网页会加载 Google Fonts；本地划线保存在浏览器中，Artifact 模式还会将划线发送给宿主数据库。
使用模型改写时，全文、brief 和相关批注会发送给 Google。

这是一个早期的辅助改稿工具，Markdown 支持范围有限，复杂格式和重叠划线需要人工核对。
批注自动分类不是人工审批，也没有自动事实核查。提交代码前仍应检查暂存文件；
`.gitignore` 只是防止误提交的辅助措施。离线检查命令：
`python3 -m unittest discover -s tests -v`。

采用 [MIT 许可证](LICENSE)。
