"""Optional AI writing adapter. Annotation and deterministic application live in markup.py."""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
import markup

DEFAULT_MODEL = "gemini-2.5-pro"
OPENROUTER_MODELS = (
    "anthropic/claude-opus-4.6",
    "anthropic/claude-sonnet-4.5",
    "deepseek/deepseek-v3.2",
    "google/gemini-3.7-flash",
    "google/gemini-2.5-pro",
)


def model_notes(provider, model):
    if "03-25" in model or "0325" in model:
        raise ValueError("The old Gemini Pro 03-25/0325 preview is retired; choose a current exact model ID explicitly.")
    if provider == "openrouter" and "/" not in model:
        raise ValueError("OpenRouter requires an exact provider/model ID")
    if provider == "gemini" and "/" in model:
        raise ValueError("Gemini direct requires a Google model name, not an OpenRouter ID")
    print(f"Writing provider: {provider}; requested model: {model}; no model/provider fallback", file=sys.stderr)
    if "gemini-3.7-flash" in model:
        print("Warning: Google routes the 3.7 Flash API name to 3.8 Flash. OpenRouter still lists 3.7; the original version cannot be guaranteed.", file=sys.stderr)
    if "gemini-2.5-pro" in model:
        print("Warning: Gemini 2.5 Pro direct access may be limited to existing users/projects; access errors require an explicit model choice.", file=sys.stderr)


class Client:
    def __init__(self, provider, model):
        self.provider, self.model, self.observed = provider, model, []
        model_notes(provider, model)

    def generate(self, prompt, as_json=False):
        key_name = "GEMINI_API_KEY" if self.provider == "gemini" else "OPENROUTER_API_KEY"
        key = os.environ.get(key_name)
        if not key:
            raise ValueError(f"{key_name} is required for the selected writing provider")
        if self.provider == "gemini":
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
            payload = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                       "generationConfig": {"temperature": 0.2 if as_json else 0.8}}
            if as_json:
                payload["generationConfig"]["responseMimeType"] = "application/json"
            headers = {"Content-Type": "application/json", "x-goog-api-key": key}
        else:
            url = "https://openrouter.ai/api/v1/chat/completions"
            payload = {"model": self.model, "messages": [{"role": "user", "content": prompt}],
                       "temperature": 0.2 if as_json else 0.8}
            headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
        request = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers)
        try:
            with urllib.request.build_opener().open(request, timeout=600) as response:
                data = json.load(response)
        except urllib.error.HTTPError as error:
            raise ValueError(f"{self.provider} request failed: HTTP {error.code}; no fallback attempted") from None
        except urllib.error.URLError:
            raise ValueError(f"{self.provider} network request failed; no fallback attempted") from None
        try:
            if self.provider == "gemini":
                actual = data.get("modelVersion")
                candidate = data["candidates"][0]
                if candidate.get("finishReason", "STOP") != "STOP":
                    raise ValueError("Gemini response did not finish successfully")
                text = "".join(part.get("text", "") for part in candidate["content"]["parts"] if not part.get("thought"))
            else:
                actual = data.get("model")
                choice = data["choices"][0]
                if choice.get("finish_reason", "stop") != "stop":
                    raise ValueError("OpenRouter response did not finish successfully")
                text = choice["message"]["content"]
            if not isinstance(text, str) or not text.strip():
                raise ValueError("model returned an empty response")
        except (KeyError, IndexError, TypeError):
            raise ValueError(f"{self.provider} returned an invalid or rejected response") from None
        if actual:
            self.observed.append(str(actual))
            print(f"Provider reported model/version: {actual}", file=sys.stderr)
        return text.strip()


CLASSIFY = """作者在一篇文章上划掉了一些文字，并在每处写了批注。请判断每条批注属于哪一类：
- replace：批注本身就是要换上去的文字（作者想要的新说法，可能带引号，也可能不带），应该原样替换被划掉的部分。
- delete：批注的意思是删掉被划的部分（比如「删」「删掉」「不要」）。
- comment：批注是意见、疑问或要求（比如「太像 AI」「什么东西？」「书面一点」），需要改写而不是原样替换。
拿不准时选 comment。replace 的 text 填要换上去的文字，去掉作者为了标示而加的外层引号；delete 和 comment 的 text 留空。

只输出 JSON 数组，每项 {{"id": ..., "kind": "replace"|"delete"|"comment", "text": ...}}，不要别的内容。

{items}
"""


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



def classify(marks, blocks, client):
    todo = [m for m in marks if "replace" not in m and m.get("reason", "").strip() and not m.get("reviewed")]
    if not todo:
        return
    items = "\n\n".join(f"id: {m['id']}\n所在段落：{blocks[m['p']]['text']}\n被划掉的：「{m['quote']}」\n批注：{m['reason']}" for m in todo)
    result = json.loads(client.generate(CLASSIFY.format(items=items), as_json=True))
    if not isinstance(result, list):
        raise ValueError("classification must return an array")
    decisions = {}
    for item in result:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or item["id"] in decisions:
            raise ValueError("classification has invalid or duplicate ids")
        if item.get("kind") not in ("replace", "delete", "comment") or not isinstance(item.get("text"), str):
            raise ValueError("classification has invalid kind/text")
        if item["kind"] == "replace" and not item["text"]:
            raise ValueError("classification returned an empty replacement")
        decisions[item["id"]] = item
    if set(decisions) != {m["id"] for m in todo}:
        raise ValueError("classification omitted or added mark ids")
    for m in todo:
        item = decisions[m["id"]]
        if item["kind"] == "replace":
            m["replace"] = item["text"]
        elif item["kind"] == "delete":
            m["replace"] = ""
        print(f"Mark {m['id']}: {item['kind']}")


def rewrite(d, marks_path, provider="gemini", model=None):
    model = model or (DEFAULT_MODEL if provider == "gemini" else "google/gemini-2.5-pro")
    client = Client(provider, model)
    marks = markup.load_marks(marks_path, d)
    v = markup.versions(d)[-1]
    blocks = markup.split_blocks((d / f"v{v}.md").read_text())
    overrides_file = d / "rounds" / f"r{v}-overrides.json"
    if overrides_file.exists():
        overrides = json.loads(overrides_file.read_text())
        if not isinstance(overrides, dict) or set(overrides) - {m["id"] for m in marks}:
            raise ValueError("invalid classification overrides")
        for m in marks:
            if m["id"] in overrides:
                value = overrides[m["id"]]
                if value is not None and not isinstance(value, str):
                    raise ValueError("override must be text or null")
                if value is None:
                    m.pop("replace", None)
                    m["reviewed"] = True
                else:
                    m["replace"] = value
    classify(marks, blocks, client)
    task = markup.make_task(d, marks)
    results = markup.result_template(task)
    for g, result in zip(task["groups"], results["groups"]):
        lines = "\n".join(f"- 「{m['quote']}」 原因：{m.get('reason', '')}" for m in g["marks"])
        if g["preserve"]:
            lines += "\n作者亲写的文字，必须一字不改保留：\n" + "\n".join(g["preserve"])
        prompt = PROMPT.format(brief=task["brief"], article=task["article"], block=g["text"], marks=lines)
        if len(g["paragraphs"]) > 1:
            prompt = prompt.replace("这一段", "这些连续的段落（允许合并、拆分）")
        answer = client.generate(prompt)
        if answer.startswith("```"):
            raise ValueError("model returned a fenced response; expected only revised Markdown")
        if not any(blocks[p]["kind"] in ("h1", "h2", "h3") for p in g["paragraphs"]) and re.search(r"^#{1,6} ", answer, re.M):
            raise ValueError("model inserted a heading into a non-heading group; no revision written")
        result["text"] = "" if answer == "[删除]" else answer
    results["writer"] = {"provider": provider, "requestedModel": model, "reportedModels": client.observed}
    markup.apply(d, task, results, model=f"{provider}:{model}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--list-models", action="store_true", help="show recommended model IDs and routing caveats")
    ap.add_argument("command", nargs="?", choices=["rewrite"])
    ap.add_argument("dir", nargs="?", type=Path)
    ap.add_argument("marks", nargs="?", type=Path)
    ap.add_argument("--provider", choices=["gemini", "openrouter"], default="gemini")
    ap.add_argument("--model", help="exact model ID; defaults to Gemini 2.5 Pro for selected provider")
    a = ap.parse_args()
    if a.list_models:
        print("Default: gemini / gemini-2.5-pro (direct; existing-user/project access may apply)")
        print("OpenRouter recommendations:\n" + "\n".join(OPENROUTER_MODELS))
        print("3.7 Flash: Google routes the API name to 3.8; original-version behavior is not guaranteed.")
        print("Gemini Pro 0325/03-25 is retired; google/gemini-2.5-pro is a separate current selection, not an alias.")
        return
    if not a.command or a.dir is None or a.marks is None:
        ap.error("use rewrite DIR MARKS, or --list-models")
    try:
        rewrite(a.dir.expanduser(), a.marks, a.provider, a.model)
    except (ValueError, OSError, KeyError, TypeError) as error:
        ap.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
