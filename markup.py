"""Mark-and-rewrite loop for article drafts.

An article lives in its own directory:
    meta.json          {"key", "title", "artifact_url"?}
    brief.md           voice samples, banned words and facts for Gemini
    v1.md, v2.md ...   versions; the highest number is current
    rounds/            marks submitted on each version and what changed
    page.html          the marking page built from the current version

Commands:
    python3 markup.py build   <dir>                    # page.html from the current version
    python3 markup.py rewrite <dir> <marks> [--model M] # marks: a JSON file or a directory of JSON docs
The rewrite command needs GEMINI_API_KEY; run it inside `zsh -ic '...'` so the key from the
user's shell is present. Requests go through the ClashX proxy at 127.0.0.1:7890.
"""
import argparse
import json
import os
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

HERE = Path(__file__).parent
DEFAULT_MODEL = "gemini-2.5-pro"


# ---------------------------------------------------------------- blocks

def plain(md):
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1", md)
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    s = re.sub(r"`([^`]+)`", r"\1", s)
    return s


def link_spans(md):
    """Plain text of a block plus the [start, end, url] of each link in that text."""
    spans, out, pos = [], [], 0
    for m in re.finditer(r"\[([^\]]+)\]\(([^)]+)\)", md):
        out.append(plain(md[pos:m.start()]))
        start = sum(len(x) for x in out)
        label = plain(m.group(1))
        out.append(label)
        spans.append([start, start + len(label), m.group(2)])
        pos = m.end()
    out.append(plain(md[pos:]))
    return "".join(out), spans


def plain_to_md(md):
    """index[k] = position in md of plain-text character k (links, ** and ` stripped)."""
    index, pos = [], 0
    pat = re.compile(r"\[([^\]]+)\]\(([^)]+)\)|\*\*(.+?)\*\*|`([^`]+)`")
    for m in pat.finditer(md):
        index += range(pos, m.start())
        g = 1 if m.group(1) is not None else 3 if m.group(3) is not None else 4
        index += range(m.start(g), m.end(g))
        pos = m.end()
    index += range(pos, len(md))
    return index


def apply_verbatim(md, text_offset, edits):
    """Apply [(start, end, replacement)] given in the block's plain-text offsets, right to left."""
    idx = plain_to_md(md)
    idx.append(len(md))
    for start, end, new in sorted(edits, reverse=True):
        a, b = idx[text_offset + start], idx[text_offset + end - 1] + 1 if end > start else idx[text_offset + start]
        md = md[:a] + new + md[b:]
    return md


def prefix_len(md, kind):
    """Length of the markup before a block's text: '## ' for headings, '> ' for a one-line quote."""
    if kind.startswith("h") and kind != "hr":
        return len(md) - len(md.lstrip("#").lstrip())
    if kind == "quote":
        return len(md) - len(md.lstrip().lstrip(">").lstrip())
    return 0


def split_blocks(md):
    blocks = []
    chunks = []
    for raw in re.split(r"\n\s*\n", md.strip()):
        # a heading line followed directly by text is two blocks
        if raw.lstrip().startswith("#") and "\n" in raw.strip():
            head, rest = raw.strip().split("\n", 1)
            chunks += [head, rest]
        else:
            chunks.append(raw)
    for raw in chunks:
        raw = raw.strip("\n")
        if not raw.strip():
            continue
        m = re.match(r"^(#{1,3}) (.*)$", raw, re.S)
        if m:
            kind, text = f"h{len(m.group(1))}", plain(m.group(2))
        elif raw.strip() in ("---", "***"):
            kind, text = "hr", ""
        elif all(line.lstrip().startswith(("- ", "* ")) for line in raw.splitlines()):
            kind, text = "list", plain(raw)
        elif "\n" not in raw.strip() and raw.lstrip().startswith(">"):
            kind, text = "quote", plain(raw[prefix_len(raw, "quote"):])
        else:
            kind, text = "p", plain(raw)
        if kind != "hr":
            text, links = link_spans(raw[prefix_len(raw, kind):])
        else:
            links = []
        blocks.append({"p": len(blocks), "kind": kind, "md": raw, "text": text, "links": links})
    return blocks


def versions(d):
    vs = sorted(int(m.group(1)) for f in d.glob("v*.md") if (m := re.fullmatch(r"v(\d+)\.md", f.name)))
    if not vs:
        sys.exit(f"{d}: no v1.md")
    return vs


def apply_links(md, links):
    """Link the first plain occurrence of each anchor in body paragraphs (links.json: [{anchors, url}])."""
    blocks = re.split(r"(\n\s*\n)", md)
    done = []
    for item in links:
        body = "".join(x for k, x in enumerate(blocks) if not (k % 2 or x.lstrip().startswith(("- ", "* "))))
        if f"]({item['url']})" in body:        # already linked in the body: link each URL once (lists don't count)
            continue
        for anchor in item["anchors"]:
            hit = False
            for k, b in enumerate(blocks):
                if k % 2 or b.lstrip().startswith(("#", "- ", "* ", "---")) or item["url"] in b:
                    continue
                for m in re.finditer(re.escape(anchor), b):
                    before = b[:m.start()]
                    if before.count("[") > before.count("]"):      # already inside a link
                        continue
                    label = item.get("label", anchor)
                    i0 = m.start() + anchor.index(label)
                    blocks[k] = b[:i0] + f"[{label}]({item['url']})" + b[i0 + len(label):]
                    hit = True
                    break
                if hit:
                    break
            if hit:
                done.append(anchor)
                break
    return "".join(blocks), done


# ---------------------------------------------------------------- build

def build(d):
    meta = json.loads((d / "meta.json").read_text())
    v = versions(d)[-1]
    blocks = split_blocks((d / f"v{v}.md").read_text())
    changes_file = d / "rounds" / f"r{v - 1}-changes.json"
    changes = json.loads(changes_file.read_text()) if changes_file.exists() else {"model": meta.get("v1_model", ""), "changes": []}
    prev = split_blocks((d / f"v{v - 1}.md").read_text()) if v > 1 else []
    for c in changes["changes"]:
        c["oldP"] = c["oldP"] if isinstance(c["oldP"], list) else [c["oldP"]]
    data = {
        "key": meta["key"], "title": meta["title"], "version": v, "model": changes.get("model", ""),
        "blocks": [{k: b[k] for k in ("p", "kind", "text", "links")} for b in blocks],
        "prevBlocks": [{k: b[k] for k in ("p", "kind", "text")} for b in prev],
        "changes": changes["changes"],
    }
    page = (HERE / "template.html").read_text()
    page = page.replace("/*TITLE*/", meta["title"]).replace(
        "/*DATA*/null", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
    (d / "page.html").write_text(page)
    print(f"{d / 'page.html'}: version {v}, {len(blocks)} blocks, {len(changes['changes'])} changed in the last round")


# ---------------------------------------------------------------- rewrite

def load_marks(path, v):
    p = Path(path)
    docs = []
    files = sorted(p.rglob("*.json")) if p.is_dir() else [p]
    for f in files:
        obj = json.loads(f.read_text())
        items = obj if isinstance(obj, list) else [obj]
        for it in items:
            body = it.get("data", it)
            if "quote" in body:
                docs.append({"id": it.get("id", f.stem), **body})
    return [m for m in docs if int(m.get("v", 0)) == v]


def gemini(model, prompt, as_json=False):
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        data=json.dumps({"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                         "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"} if as_json
                         else {"temperature": 0.8}}).encode(),
        headers={"Content-Type": "application/json", "x-goog-api-key": os.environ["GEMINI_API_KEY"]})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({"https": "http://127.0.0.1:7890"}))
    d = json.load(opener.open(req, timeout=600))
    return "".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"] if not p.get("thought")).strip()


CLASSIFY = """作者在一篇文章上划掉了一些文字，并在每处写了批注。请判断每条批注属于哪一类：
- replace：批注本身就是要换上去的文字（作者想要的新说法，可能带引号，也可能不带），应该原样替换被划掉的部分。
- delete：批注的意思是删掉被划的部分（比如「删」「删掉」「不要」）。
- comment：批注是意见、疑问或要求（比如「太像 AI」「什么东西？」「书面一点」），需要改写而不是原样替换。
拿不准时选 comment。replace 的 text 填要换上去的文字，去掉作者为了标示而加的外层引号；delete 和 comment 的 text 留空。

只输出 JSON 数组，每项 {{"id": ..., "kind": "replace"|"delete"|"comment", "text": ...}}，不要别的内容。

{items}
"""


def classify(marks, blocks, model):
    """Decide for each annotated mark whether its note is replacement text, a deletion, or a comment."""
    todo = [m for m in marks if "replace" not in m and m.get("reason", "").strip()]
    if not todo:
        return
    items = "\n\n".join(
        f"id: {m['id']}\n所在段落：{blocks[int(m['p'])]['text']}\n被划掉的：「{m['quote']}」\n批注：{m['reason']}" for m in todo)
    try:
        result = json.loads(gemini(model, CLASSIFY.format(items=items), as_json=True))
    except (json.JSONDecodeError, KeyError) as e:
        print(f"classification failed ({e}); every note is treated as a comment")
        return
    kinds = {r["id"]: r for r in result if isinstance(r, dict) and "id" in r}
    for m in todo:
        r = kinds.get(m["id"], {"kind": "comment"})
        if r["kind"] == "replace" and r.get("text", "").strip():
            m["replace"] = r["text"].strip()
        elif r["kind"] == "delete":
            m["replace"] = ""
        label = {"replace": f"替换为「{m.get('replace', '')}」", "delete": "删掉"}.get(r["kind"], "意见，交给改写")
        print(f"  [{label}] 「{m['quote'][:24]}」 批注：{m['reason'][:40]}")


PROMPT = """{brief}

# 这次的任务：只改写文章里的一段

作者读了下面这篇文章，划掉了他不喜欢的句子或词。请只改写指定的那一段：
- 被划掉的部分必须换掉：换一种说法、重写，或者干脆删掉（作者写了原因的，照原因改）。
- 没被划掉的句子尽量保持原样，除非为了衔接必须微调。
- 不许引入任何新的事实、数字、经历或心情，只能用上面「事实」里有的。
- 和前后段落要接得上，不要重复前后段已经说过的话。
- 如果这一段删掉更好，只输出 [删除]。
- 只输出改写后的这一段（Markdown，保留原来的标题级别或列表格式），不要解释，不要加引号。

# 整篇文章（供上下文，不要改其他段）

{article}

# 要改写的这一段

{block}

# 作者在这一段里划掉的部分

{marks}
"""


def rewrite(d, marks_path, model):
    meta = json.loads((d / "meta.json").read_text())
    v = versions(d)[-1]
    md = (d / f"v{v}.md").read_text()
    blocks = split_blocks(md)
    marks = load_marks(marks_path, v)
    if not marks:
        sys.exit(f"no marks for version {v}")
    overrides_file = d / "rounds" / f"r{v}-overrides.json"
    overrides = json.loads(overrides_file.read_text()) if overrides_file.exists() else {}
    for m in marks:            # Claude's corrections to the automatic classification, by mark id
        if m["id"] in overrides:
            o = overrides[m["id"]]
            if o is None:
                m.pop("replace", None)
                m["reviewed"] = True
            else:
                m["replace"] = o
    print("notes:")
    classify([m for m in marks if not m.get("reviewed")], blocks, model)
    # the author's own wording is used as written
    verbatim = [m for m in marks if "replace" in m]
    for p in {int(m["p"]) for m in verbatim}:
        b = blocks[p]
        head = prefix_len(b["md"], b["kind"])
        body = b["md"][head:]
        edits = [(m["start"], m["end"], m["replace"]) for m in verbatim if int(m["p"]) == p]
        b["md"] = b["md"][:head] + apply_verbatim(body, 0, edits)
        b["text"], _ = link_spans(b["md"][head:])
    md = "\n\n".join(b["md"] for b in blocks)
    by_p = {}
    for m in marks:
        if "replace" not in m:
            by_p.setdefault(int(m["p"]), []).append(m)
    brief = (d / "brief.md").read_text()

    # neighbouring struck blocks are rewritten together so the result reads as one passage
    groups = []
    for p in sorted(by_p):
        if groups and p == groups[-1][-1] + 1:
            groups[-1].append(p)
        else:
            groups.append([p])

    def one(g):
        ms = sorted((m for p in g for m in by_p[p]), key=lambda m: (int(m["p"]), m["start"]))
        lines = "\n".join(f"- 「{m['quote']}」" + (f"　原因：{m['reason']}" if m.get("reason") else "") for m in ms)
        own = [m["replace"] for p in g for m in verbatim if int(m["p"]) == p and m["replace"]]
        if own:
            lines += "\n\n下面这些是作者亲手写进这几段的文字，必须一字不改地保留：\n" + "\n".join(f"- 「{t}」" for t in own)
        block = "\n\n".join(blocks[p]["md"] for p in g)
        what = "这一段" if len(g) == 1 else f"这连续的 {len(g)} 段（可以合并、拆分或删减段落）"
        out = gemini(model, PROMPT.replace("这一段", what).format(brief=brief, article=md, block=block, marks=lines))
        out = re.sub(r"^```(?:markdown)?\s*|\s*```$", "", out.strip())
        if not any(blocks[p]["kind"].startswith("h") for p in g):   # models sometimes repeat the title
            out = "\n".join(line for line in out.splitlines() if not re.match(r"^#{1,6} ", line))
        return g[0], ("" if out.strip() == "[删除]" else out.strip()), ms

    with ThreadPoolExecutor(6) as ex:
        results = {g0: (text, ms) for g0, text, ms in ex.map(one, groups)}
    group_of = {p: g for g in groups for p in g}

    old_blocks = split_blocks((d / f"v{v}.md").read_text())
    verbatim_ps = {int(m["p"]) for m in verbatim}
    out_blocks, changes = [], []
    for b in blocks:
        g = group_of.get(b["p"])
        if g is None:
            if b["p"] in verbatim_ps:
                changes.append({"oldP": [b["p"]], "oldText": old_blocks[b["p"]]["text"], "newBlocks": [len(out_blocks)],
                                "marks": [{"quote": m["quote"], "reason": m.get("reason", ""), "replace": m["replace"]}
                                          for m in verbatim if int(m["p"]) == b["p"]]})
            out_blocks.append(b["md"])
            continue
        if b["p"] != g[0]:
            continue
        text, ms = results[g[0]]
        first = len(out_blocks)
        pieces = split_blocks(text) if text else []
        out_blocks.extend(x["md"] for x in pieces)
        changes.append({"oldP": g, "oldText": "\n\n".join(old_blocks[p]["text"] for p in g),
                        "newBlocks": list(range(first, len(out_blocks))),
                        "marks": [{"quote": m["quote"], "reason": m.get("reason", "")} for m in ms]
                                 + [{"quote": m["quote"], "reason": m.get("reason", ""), "replace": m["replace"]}
                                    for p in g for m in verbatim if int(m["p"]) == p]})
    new_doc = "\n\n".join(x["md"] for x in split_blocks("\n\n".join(out_blocks))) + "\n"
    links_file = d / "links.json"
    if links_file.exists():
        new_doc, linked = apply_links(new_doc, json.loads(links_file.read_text()))
        print("linked:", ", ".join(linked) or "nothing")
    (d / f"v{v + 1}.md").write_text(new_doc)
    rounds = d / "rounds"
    rounds.mkdir(exist_ok=True)
    (rounds / f"r{v}-marks.json").write_text(json.dumps(marks, ensure_ascii=False, indent=1))
    (rounds / f"r{v}-changes.json").write_text(json.dumps({"model": model, "changes": changes}, ensure_ascii=False, indent=1))
    with open(d / "划线记录.md", "a") as f:
        f.write(f"\n## 第 {v} 版 → 第 {v + 1} 版（{date.today()}，{model}）\n\n")
        for m in sorted(marks, key=lambda m: (int(m["p"]), m["start"])):
            if "replace" in m:
                f.write(f"- 「{m['quote']}」→ 原样改为「{m['replace']}」" + ("（删掉）" if not m["replace"] else "") + "\n")
            else:
                f.write(f"- 「{m['quote']}」" + (f"——{m['reason']}" if m.get("reason") else "") + "\n")
    print(f"v{v + 1}.md: rewrote {len(changes)} blocks from {len(marks)} marks with {model}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "rewrite"])
    ap.add_argument("dir")
    ap.add_argument("marks", nargs="?")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    a = ap.parse_args()
    d = Path(a.dir).expanduser()
    if a.cmd == "build":
        build(d)
    else:
        if not a.marks:
            sys.exit("rewrite needs the marks file or directory")
        rewrite(d, a.marks, a.model)
