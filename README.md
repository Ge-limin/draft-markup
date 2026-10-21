# draft-markup

Mark what needs changing. Choose separately who does the writing.

`draft-markup` has two independent parts:

- **Annotation tool:** a local browser page and `markup.py` for marking text, exporting
  feedback, applying edits, and comparing versions. No AI account, API key, or Claude
  Artifact host is required.
- **Writing tool:** optional `writer.py` for interpreting feedback and rewriting the
  marked paragraphs. It defaults to **Gemini 2.5 Pro**. Other recommended models can be
  explicitly selected through OpenRouter.

[中文说明](#中文说明) · [Real writing references](examples/real/README.md) · [MIT license](LICENSE)

## Requirements

Python 3.9+ and a browser. Python uses the standard library only. The generated page
uses system fonts and local browser storage; it makes no AI or hosting requests.
Node.js is only needed for the optional frontend checks.

Keep working articles outside the source repository. An article directory starts with
`meta.json` containing a unique `key` and `title`, and `v1.md` containing the draft.
`brief.md` supplies writing instructions and facts when using the optional writer.

## Annotate without AI

```sh
python3 markup.py build /path/to/article
```

Open the printed `page.html` path in your browser. Select text, add a comment, and mark
it. You can specify an exact replacement or deletion, or leave a normal editorial
comment. Changes are saved locally; use **Download annotation JSON** (下载批注 JSON) to
download an independent file. **Import annotation JSON** (导入批注 JSON) restores a matching export.
The UI is currently Chinese; article text can use other languages.

Exports identify the article, version, and source-text hash. Imports reject mismatched
versions, invalid ranges, and overlapping selections. A storage failure is shown on
screen; export your annotations before closing the page if local saving is unavailable.

### Try an entire offline editing round

From the repository root on macOS or Linux:

```sh
demo_dir=$(mktemp -d)
cp examples/demo/* "$demo_dir/"
python3 markup.py build "$demo_dir"
python3 markup.py prepare "$demo_dir" "$demo_dir/marks.json"
python3 markup.py apply "$demo_dir" "$demo_dir/task.json" "$demo_dir/task.results.json"
python3 markup.py build "$demo_dir"
```

Open or reload the printed page to compare versions. The fictional example contains
an explicit deletion, so its generated result template is ready to apply with no AI.
To use your own annotations, substitute the JSON downloaded from the page.

## Let any editor or assistant supply the changes

```sh
python3 markup.py prepare /path/to/article /path/to/annotations.json
# Complete task.results.json using task.json, manually or with any assistant.
python3 markup.py apply /path/to/article /path/to/task.json /path/to/task.results.json
python3 markup.py build /path/to/article
```

`prepare` writes a task file and a separate result template. The task includes the
article, optional brief, marked passages, and exact author wording that must survive.
Fill each result group's `text` with the revised Markdown; an empty string deletes
that group. Explicit replacements and deletions are applied by the core itself.
Existing task files are never overwritten: for a later round, use an unused path such
as `--output /path/to/article/task-v2.json` and its matching `task-v2.results.json`.

`apply` validates the source version and hash, task identity, required groups, and
protected author wording before creating the next version. It rejects missing,
duplicate, or unexpected groups. Paragraphs outside the marked groups are retained.
The tool does not evaluate factual accuracy or literary quality; review the diff.

## Optional writing tool: Gemini by default

Set `GEMINI_API_KEY` in your environment using your preferred secret manager, then:

```sh
python3 writer.py rewrite /path/to/article /path/to/annotations.json
python3 markup.py build /path/to/article
```

The default is Google's Gemini API with `gemini-2.5-pro`, preserving the existing
writing preference. This is the stable model, **not the retired 03-25 experimental
version**. Google currently limits 2.5 model access to users who have actively used
those models before; a new project may not have access. Choose another available
model explicitly if needed. [Google model lifecycle](https://ai.google.dev/gemini-api/docs/deprecations)

To use OpenRouter, set `OPENROUTER_API_KEY` and select it explicitly:

```sh
python3 writer.py rewrite /path/to/article /path/to/annotations.json \
  --provider openrouter --model anthropic/claude-opus-4.6
python3 writer.py --list-models
```

Recommended options reflect the author's writing preferences, not a benchmark ranking.
Catalog notes were checked on **2026-10-21**; listing a model does not guarantee account
access or unchanged behavior.

| Writing option | OpenRouter ID | Note |
|---|---|---|
| Claude Opus 4.6 | `anthropic/claude-opus-4.6` | [Catalog entry](https://openrouter.ai/anthropic/claude-opus-4.6) |
| Claude Sonnet 4.5 | `anthropic/claude-sonnet-4.5` | [Catalog entry](https://openrouter.ai/anthropic/claude-sonnet-4.5) |
| DeepSeek V3.2 | `deepseek/deepseek-v3.2` | [Catalog entry](https://openrouter.ai/deepseek/deepseek-v3.2) |
| Gemini 3.7 Flash | `google/gemini-3.7-flash` | [Still listed](https://openrouter.ai/google/gemini-3.7-flash); the Google API name now routes to 3.8 Flash, so do not assume the original behavior |
| Gemini 2.5 Pro, stable | `google/gemini-2.5-pro` | [Current entry](https://openrouter.ai/google/gemini-2.5-pro); distinct from the old “Gemini Pro 0325” name |

Google documents the 3.7-to-3.8 routing change and retirement of the old 03-25 model
in its [release notes](https://ai.google.dev/gemini-api/docs/changelog). The old
[OpenRouter 03-25 preview page](https://openrouter.ai/google/gemini-2.5-pro-preview-03-25)
redirects to the stable model. The writer does not silently translate retired IDs,
switch providers, or try a different model after a failure.

The writer classifies ordinary comments as replacements, deletions, or rewrite requests,
then processes neighboring marked paragraphs together. Explicit choices from the
annotation page bypass classification. Inspect the classification output and final diff.
Model calls may incur charges; standard `HTTPS_PROXY` and `NO_PROXY` configure networking.

## Files and compatibility

| File | Purpose |
|---|---|
| `meta.json` | Required `key`, `title`; optional historical model metadata |
| `v1.md`, `v2.md`, … | Draft versions; the highest numbered version is current |
| `brief.md` | Optional context for manual tasks; instructions and facts for AI rewriting |
| `links.json` | Optional checked links, applied only to passages changed in the current round |
| `page.html` | Generated annotation and comparison page |
| Exported annotation JSON | Version-bound marks, notes, and optional exact replacements |
| `task.json`, `task.results.json` | Portable editing task and result template |
| `rounds/`, `划线记录.md` | Per-round changes and readable annotation history |

The CLI still accepts legacy annotation arrays and exported Artifact records.
Legacy `markup.py rewrite` now gives a migration message: use `writer.py rewrite` for
AI-assisted writing, or `prepare` / `apply` for independent editing. Existing Markdown
versions and comparison records remain usable. The standalone page no longer syncs
annotations to a Claude Artifact database.

Markdown support is intentionally small: headings, paragraphs, simple lists,
one-line quotes, and basic inline formatting. Review complex formatting carefully.

## Data and checks

Building, annotating, preparing, and applying edits are local. Generated pages and task
files contain article text and may include previous versions, feedback, and brief
content; share only what you intend to disclose. The optional writer sends the draft,
brief, and relevant feedback to the provider you explicitly select. API keys stay in
the shell environment and are not embedded in the page.

Published article data is limited to the fictional demo and
[two author-selected Chinese references](examples/real/README.md). Working briefs,
other drafts, and personal voice corpora are not included. Review staged files before
committing; ignore rules are only a guard against accidental additions.

```sh
python3 -m unittest discover -s tests -v
node --test tests/frontend.test.js
```

## 中文说明

**划线批注独立运行，写作默认使用 Gemini。**

- `markup.py` 和网页负责划线、批注、本地保存、导入导出、应用修改及版本对比。
  不需要 Claude、Gemini 或任何 AI 账号。
- `writer.py` 是可选的写作入口，负责理解批注和局部改写，默认使用
  `gemini-2.5-pro`。你也可以明确选择 OpenRouter 和上表中的模型。

### 不使用 AI 的流程

运行 `python3 markup.py build 文章目录`，打开生成的 `page.html`。
选中文字后添加修改意见，也可以明确指定原样替换或删除。网页在本地保存批注；
点击「下载批注 JSON」下载文件，「导入批注 JSON」可恢复同一文章、同一版本的批注。

随后运行 `prepare` 生成任务与结果模板。原样替换和删除可以直接应用；需要改写的内容，
可以自己填写，也可以交给任意助手。完成后用 `apply` 生成下一版，再用 `build` 查看差异。
上面的离线演示给出了完整命令，无需 API 密钥。
已有任务文件不会被覆盖，下一轮可用 `--output 文章目录/task-v2.json` 指定新文件。

任务绑定文章、版本和原文哈希。版本不符、修改组遗漏或重复、作者原话未保留时，
工具会报错，不会自动扩大修改范围。普通批注的类型判断属于可选写作端，
划线工具本身不需要调用模型。

### 默认写作与推荐备选

设置环境变量 `GEMINI_API_KEY` 后，用
`python3 writer.py rewrite 文章目录 批注文件` 调用默认 Gemini 写作。
默认沿用正式版 2.5 Pro；它与旧的 Pro 03-25 不等同，Google 当前对 2.5 系列有历史使用资格限制。

如需 OpenRouter，设置 `OPENROUTER_API_KEY`，明确传入
`--provider openrouter --model 模型ID`。推荐备选为 Claude Opus 4.6、Claude Sonnet 4.5、
DeepSeek V3.2、Gemini 3.7 Flash；旧的 Gemini Pro 0325 只保留名称说明，正式版 2.5 Pro
作为另一个明确选项。3.7 Flash 的 Google API 名称已转路由到 3.8 Flash，不能保证重现旧体验。
具体 ID 和官方来源见上表。工具不会遇错后自动换模型或切换计费账号。

### 资料和隐私

[真实参考](examples/real/README.md)包含你在仓库中可阅读的两篇中文正文与划线记录，
与虚构的可运行示例分开。日常稿件、brief 和生成文件应放在仓库外。
独立批注流程只在本地处理；调用写作端时才把相关材料发送给所选服务。
原 `markup.py rewrite` 命令已迁移到 `writer.py rewrite`，网页不再同步 Claude Artifact 数据库。
