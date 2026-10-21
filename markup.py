"""Offline draft annotation, task export, and deterministic revision application."""
import argparse
import html
import json
import os
import hashlib
import tempfile
import re
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).parent


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
    for raw in re.split(r"\n\s*\n", md.strip("\n")):
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
        "key": meta["key"], "title": meta["title"], "version": v, "sourceHash": source_hash(d, v), "model": changes.get("model", ""),
        "blocks": [{**{k: b[k] for k in ("p", "kind", "text")},
                    "links": [[len(b["text"][:start].encode("utf-16-le")) // 2,
                               len(b["text"][:end].encode("utf-16-le")) // 2, url]
                              for start, end, url in b["links"]]} for b in blocks],
        "prevBlocks": [{k: b[k] for k in ("p", "kind", "text")} for b in prev],
        "changes": changes["changes"],
    }
    page = (HERE / "template.html").read_text()
    page = page.replace("/*TITLE*/", html.escape(meta["title"])).replace(
        "/*DATA*/null", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
    (d / "page.html").write_text(page)
    print(f"{d / 'page.html'}: version {v}, {len(blocks)} blocks, {len(changes['changes'])} changed in the last round")


# ---------------------------------------------------------------- offline tasks


def source_hash(d, v):
    return hashlib.sha256((d / f"v{v}.md").read_bytes()).hexdigest()


def json_text(obj):
    return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"


def digest(obj):
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def utf16_offset(text, offset):
    if type(offset) is not int or offset < 0:
        raise ValueError("mark offset must be a nonnegative integer")
    units = 0
    for i, char in enumerate(text):
        if units == offset:
            return i
        units += 2 if ord(char) > 0xffff else 1
    if units == offset:
        return len(text)
    raise ValueError("mark offset splits a UTF-16 character or is out of range")


def validate_marks(marks, blocks, v, encoding="utf16"):
    if encoding not in ("utf16", "codepoint"):
        raise ValueError("unsupported offsetEncoding")
    if not isinstance(marks, list) or not marks:
        raise ValueError("no marks supplied")
    clean, ids = [], set()
    for mark in marks:
        if not isinstance(mark, dict):
            raise ValueError("mark must be an object")
        m = dict(mark)
        if not isinstance(m.get("id"), str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", m["id"]) or m["id"] in ids:
            raise ValueError("mark ids must be unique strings of 1-100 letters, digits, underscores or hyphens")
        ids.add(m["id"])
        if type(m.get("v")) is not int or m["v"] != v:
            raise ValueError("mark version does not match current draft")
        p = m.get("p")
        if type(p) is not int or not 0 <= p < len(blocks):
            raise ValueError("mark paragraph is out of range")
        text = blocks[p]["text"]
        for field in ("start", "end"):
            value = m.get(field)
            if type(value) is not int:
                raise ValueError("mark offset must be an integer")
            m[field] = utf16_offset(text, value) if encoding == "utf16" else value
        if not 0 <= m["start"] < m["end"] <= len(text):
            raise ValueError("mark range is empty or out of bounds")
        if text[m["start"]:m["end"]] != m.get("quote"):
            raise ValueError("mark quote does not match the source range")
        if not isinstance(m.get("reason", ""), str):
            raise ValueError("mark reason must be text")
        if "replace" in m and not isinstance(m["replace"], str):
            raise ValueError("replacement must be text")
        if "replace" not in m and m.get("reason", "").strip() in ("删", "删掉", "删除", "delete"):
            m["replace"] = ""
        clean.append(m)
    clean.sort(key=lambda m: (m["p"], m["start"]))
    for a, b in zip(clean, clean[1:]):
        if a["p"] == b["p"] and b["start"] < a["end"]:
            raise ValueError("overlapping marks are not allowed")
    return clean


def load_marks(path, d):
    """Read browser envelopes or legacy lists/Artifact documents; never discard stale marks."""
    p = Path(path)
    v = versions(d)[-1]
    key = json.loads((d / "meta.json").read_text())["key"]
    blocks = split_blocks((d / f"v{v}.md").read_text())
    collected = []
    for f in sorted(p.rglob("*.json")) if p.is_dir() else [p]:
        obj = json.loads(f.read_text())
        if isinstance(obj, dict) and "schema" in obj:
            if obj["schema"] != "draft-markup/marks-v1":
                raise ValueError(f"unsupported marks schema in {f}")
            if obj.get("key") != key or obj.get("version") != v or obj.get("sourceHash") != source_hash(d, v):
                raise ValueError("marks key/version/sourceHash does not match current draft")
            collected.extend(validate_marks(obj.get("marks"), blocks, v, obj.get("offsetEncoding", "utf16")))
        else:
            items = obj if isinstance(obj, list) else [obj]
            legacy = []
            for it in items:
                if not isinstance(it, dict):
                    raise ValueError("invalid legacy mark")
                body = it.get("data", it)
                if isinstance(body, dict) and "quote" in body:
                    legacy.append({"id": it.get("id", f.stem), **body})
            if legacy:
                collected.extend(validate_marks(legacy, blocks, v))
    return validate_marks(collected, blocks, v, "codepoint")


def make_task(d, marks):
    v = versions(d)[-1]
    meta = json.loads((d / "meta.json").read_text())
    original = (d / f"v{v}.md").read_text()
    blocks = split_blocks(original)
    marks = validate_marks(marks, blocks, v, "codepoint")
    for b in blocks:
        edits = [(m["start"], m["end"], m["replace"]) for m in marks if m["p"] == b["p"] and "replace" in m]
        if edits:
            head = prefix_len(b["md"], b["kind"])
            b["md"] = b["md"][:head] + apply_verbatim(b["md"][head:], 0, edits)
    runs = []
    for p in sorted({m["p"] for m in marks if "replace" not in m}):
        if runs and p == runs[-1][-1] + 1:
            runs[-1].append(p)
        else:
            runs.append([p])
    task = {
        "schema": "draft-markup/task-v1", "key": meta["key"], "version": v,
        "sourceHash": source_hash(d, v), "offsetEncoding": "codepoint", "marks": marks,
        "article": "\n\n".join(b["md"] for b in blocks), "originalArticle": original,
        "brief": (d / "brief.md").read_text() if (d / "brief.md").exists() else "",
        "links": json.loads((d / "links.json").read_text()) if (d / "links.json").exists() else [],
        "instructions": "Rewrite only each group's paragraphs. Use supplied facts; preserve every string in preserve exactly. Return draft-markup/results-v1 JSON with all group ids and revised Markdown text. Empty text deletes a group. Do not modify other paragraphs.",
        "groups": [{"id": f"g{i}", "paragraphs": run,
                    "text": "\n\n".join(blocks[p]["md"] for p in run),
                    "marks": [m for m in marks if m["p"] in run and "replace" not in m],
                    "preserve": [m["replace"] for m in marks if m["p"] in run and m.get("replace")]}
                   for i, run in enumerate(runs)]}
    task["taskHash"] = digest(task)
    return task


def result_template(task):
    return {"schema": "draft-markup/results-v1", **{k: task[k] for k in ("key", "version", "sourceHash", "taskHash")},
            "groups": [{"id": g["id"], "text": None} for g in task["groups"]]}


def prepare(d, marks_path, output=None):
    task = make_task(d, load_marks(marks_path, d))
    output = Path(output) if output else d / "task.json"
    result_path = output.with_name(output.stem + ".results.json")
    if output.exists() or result_path.exists():
        raise ValueError("task output already exists; choose a new --output path")
    output.write_text(json_text(task))
    try:
        result_path.write_text(json_text(result_template(task)))
    except BaseException:
        output.unlink()
        raise
    print(f"Task: {output}\nResult template: {result_path}\n{len(task['groups'])} groups need external editing")
    return task


def commit_files(files, version_path):
    """Stage everything, publish the new version last, and roll back any reported write failure."""
    staged, backups, published = {}, {}, []
    try:
        for path, content in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            backups[path] = path.read_bytes() if path.exists() else None
            fd, temp = tempfile.mkstemp(prefix=".markup-", dir=path.parent)
            staged[path] = Path(temp)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        for path in [p for p in files if p != version_path] + [version_path]:
            os.replace(staged[path], path)
            published.append(path)
    except BaseException:
        for path in reversed(published):
            if backups[path] is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(backups[path])
        raise
    finally:
        for temp in staged.values():
            temp.unlink(missing_ok=True)


def apply(d, task, results, model="external/manual"):
    if task.get("schema") != "draft-markup/task-v1":
        raise ValueError("unsupported task schema")
    v = versions(d)[-1]
    expected = make_task(d, task.get("marks"))
    if task != expected:
        raise ValueError("task is stale or modified: key/version/sourceHash/taskHash/context mismatch")
    if results.get("schema") != "draft-markup/results-v1" or any(results.get(k) != task[k] for k in ("key", "version", "sourceHash", "taskHash")):
        raise ValueError("result identity does not match task")
    values = results.get("groups")
    if not isinstance(values, list):
        raise ValueError("result groups must be a list")
    by_id = {}
    for value in values:
        if not isinstance(value, dict) or not isinstance(value.get("id"), str) or not isinstance(value.get("text"), str):
            raise ValueError("each result needs an id and text; fill all null placeholders")
        if value["id"] in by_id:
            raise ValueError("duplicate result group")
        by_id[value["id"]] = value["text"]
    if set(by_id) != {g["id"] for g in task["groups"]}:
        raise ValueError("missing or unexpected result groups")
    for g in task["groups"]:
        for own in set(g["preserve"]):
            if by_id[g["id"]].count(own) < g["preserve"].count(own):
                raise ValueError("result does not preserve the author's exact replacement")
    old = split_blocks(task["originalArticle"])
    # Rebuild per original paragraph to retain stable indices even when a direct edit is empty.
    blocks = [dict(b) for b in old]
    for b in blocks:
        edits = [(m["start"], m["end"], m["replace"]) for m in task["marks"] if m["p"] == b["p"] and "replace" in m]
        if edits:
            head = prefix_len(b["md"], b["kind"])
            b["md"] = b["md"][:head] + apply_verbatim(b["md"][head:], 0, edits)
    grouped = {p: g for g in task["groups"] for p in g["paragraphs"]}
    out, changes = [], []
    for b in blocks:
        g = grouped.get(b["p"])
        if g and b["p"] != g["paragraphs"][0]:
            continue
        ps = g["paragraphs"] if g else [b["p"]]
        text = by_id[g["id"]] if g else b["md"]
        ms = [m for m in task["marks"] if m["p"] in ps]
        if ms and task["links"]:
            text, _ = apply_links(text, task["links"])
        pieces = split_blocks(text) if text.strip() else []
        first = len(out)
        out.extend(x["md"] for x in pieces)
        if ms:
            changes.append({"oldP": ps, "oldText": "\n\n".join(old[p]["text"] for p in ps),
                            "newBlocks": list(range(first, len(out))), "marks": ms})
    new_doc = "\n\n".join(out) + "\n"
    log_path = d / "划线记录.md"
    log = log_path.read_text() if log_path.exists() else ""
    log += f"\n## 第 {v} 版 → 第 {v + 1} 版（{date.today()}，{model}）\n\n"
    for m in task["marks"]:
        log += f"- 「{m['quote']}」"
        if "replace" in m:
            log += f"→ 原样改为「{m['replace']}」" + ("（删掉）" if not m["replace"] else "")
        elif m.get("reason"):
            log += f"——{m['reason']}"
        log += "\n"
    rounds = d / "rounds"
    version_path = d / f"v{v + 1}.md"
    files = {rounds / f"r{v}-marks.json": json_text(task["marks"]),
             rounds / f"r{v}-changes.json": json_text({"model": model, "changes": changes}),
             rounds / f"r{v}-task.json": json_text(task), rounds / f"r{v}-results.json": json_text(results),
             log_path: log, version_path: new_doc}
    if any(p.exists() for p in files if p != log_path):
        raise ValueError("revision or round files already exist; resolve the previous incomplete operation first")
    commit_files(files, version_path)
    print(f"v{v + 1}.md: applied {len(task['marks'])} marks; {len(changes)} changed groups")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build").add_argument("dir", type=Path)
    prep = sub.add_parser("prepare")
    prep.add_argument("dir", type=Path)
    prep.add_argument("marks", type=Path)
    prep.add_argument("--output", type=Path)
    app = sub.add_parser("apply")
    app.add_argument("dir", type=Path)
    app.add_argument("task", type=Path)
    app.add_argument("results", type=Path)
    old = sub.add_parser("rewrite", help="moved to writer.py")
    old.add_argument("args", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    try:
        if a.cmd == "rewrite":
            ap.error("AI writing moved to writer.py rewrite; offline use: prepare, then apply")
        d = a.dir.expanduser()
        if a.cmd == "build":
            build(d)
        elif a.cmd == "prepare":
            prepare(d, a.marks, a.output)
        else:
            apply(d, json.loads(a.task.read_text()), json.loads(a.results.read_text()))
    except (ValueError, OSError, KeyError, TypeError) as error:
        ap.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
