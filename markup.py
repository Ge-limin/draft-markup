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
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", md)
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    s = re.sub(r"`([^`]+)`", r"\1", s)
    return s


def split_blocks(md):
    blocks = []
    for raw in re.split(r"\n\s*\n", md.strip()):
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
        else:
            kind, text = "p", plain(raw)
        blocks.append({"p": len(blocks), "kind": kind, "md": raw, "text": text})
    return blocks


def versions(d):
    vs = sorted(int(m.group(1)) for f in d.glob("v*.md") if (m := re.fullmatch(r"v(\d+)\.md", f.name)))
    if not vs:
        sys.exit(f"{d}: no v1.md")
    return vs


# ---------------------------------------------------------------- build

def build(d):
    meta = json.loads((d / "meta.json").read_text())
    v = versions(d)[-1]
    blocks = split_blocks((d / f"v{v}.md").read_text())
    changes_file = d / "rounds" / f"r{v - 1}-changes.json"
    changes = json.loads(changes_file.read_text()) if changes_file.exists() else {"model": meta.get("v1_model", ""), "changes": []}
    data = {
        "key": meta["key"], "title": meta["title"], "version": v, "model": changes.get("model", ""),
        "blocks": [{k: b[k] for k in ("p", "kind", "text")} for b in blocks],
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


def gemini(model, prompt):
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        data=json.dumps({"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                         "generationConfig": {"temperature": 0.8}}).encode(),
        headers={"Content-Type": "application/json", "x-goog-api-key": os.environ["GEMINI_API_KEY"]})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({"https": "http://127.0.0.1:7890"}))
    d = json.load(opener.open(req, timeout=600))
    return "".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"] if not p.get("thought")).strip()


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
    by_p = {}
    for m in marks:
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
        block = "\n\n".join(blocks[p]["md"] for p in g)
        what = "这一段" if len(g) == 1 else f"这连续的 {len(g)} 段（可以合并、拆分或删减段落）"
        out = gemini(model, PROMPT.replace("这一段", what).format(brief=brief, article=md, block=block, marks=lines))
        out = re.sub(r"^```(?:markdown)?\s*|\s*```$", "", out.strip())
        return g[0], ("" if out.strip() == "[删除]" else out.strip()), ms

    with ThreadPoolExecutor(6) as ex:
        results = {g0: (text, ms) for g0, text, ms in ex.map(one, groups)}
    group_of = {p: g for g in groups for p in g}

    out_blocks, changes = [], []
    for b in blocks:
        g = group_of.get(b["p"])
        if g is None:
            out_blocks.append(b["md"])
            continue
        if b["p"] != g[0]:
            continue
        text, ms = results[g[0]]
        first = len(out_blocks)
        pieces = split_blocks(text) if text else []
        out_blocks.extend(x["md"] for x in pieces)
        changes.append({"oldP": g, "oldText": "\n\n".join(blocks[p]["text"] for p in g),
                        "newBlocks": list(range(first, len(out_blocks))),
                        "marks": [{"quote": m["quote"], "reason": m.get("reason", "")} for m in ms]})
    (d / f"v{v + 1}.md").write_text("\n\n".join(out_blocks) + "\n")
    rounds = d / "rounds"
    rounds.mkdir(exist_ok=True)
    (rounds / f"r{v}-marks.json").write_text(json.dumps(marks, ensure_ascii=False, indent=1))
    (rounds / f"r{v}-changes.json").write_text(json.dumps({"model": model, "changes": changes}, ensure_ascii=False, indent=1))
    with open(d / "划线记录.md", "a") as f:
        f.write(f"\n## 第 {v} 版 → 第 {v + 1} 版（{date.today()}，{model}）\n\n")
        for m in sorted(marks, key=lambda m: (int(m["p"]), m["start"])):
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
