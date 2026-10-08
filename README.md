# draft-markup

A loop for getting an article draft to sound like its author: the author strikes out the
sentences they dislike on a web page, Gemini rewrites only the paragraphs that were struck,
and the page comes back as the next version with the changes shown against the old text.

The author's job is only to point at what is wrong. Every struck phrase is also kept in the
article's `划线记录.md`, which is the raw material for the vault's 否决词表.

## How a round works

1. `python3 markup.py build <article-dir>` writes `page.html` for the current version.
2. Claude publishes `page.html` as a claude.ai Artifact with `capabilities: {db: {}}` and saves
   its URL in `meta.json` (`artifact_url`). Later rounds republish to the same URL.
3. The author selects text, presses 划掉 (optionally with a reason), and finally 全部提交.
   Marks are stored in the artifact's database (`marks/<id>`), the submission in `state/submitted`.
4. Claude reads the marks (`ArtifactData list marks` with `out_dir`) and runs
   `zsh -ic 'python3 markup.py rewrite <article-dir> <out_dir>'`.
   Each struck paragraph goes to Gemini (default `gemini-2.5-pro`) with the article's
   `brief.md`, the whole article for context, and the struck phrases with reasons. Unmarked
   paragraphs are not touched. The result is `v<N+1>.md` plus `rounds/r<N>-marks.json` and
   `rounds/r<N>-changes.json`.
5. Back to step 1.

## Article directory

| File | Content |
|---|---|
| `meta.json` | `key` (stable id), `title` (page title), `artifact_url`, optional `v1_model` |
| `brief.md` | Voice samples, banned words and the fact list Gemini must stay within |
| `v1.md`, `v2.md`, … | Versions; the highest number is current |
| `rounds/` | What was struck on each version and what changed |
| `划线记录.md` | Human-readable list of every struck phrase and reason |
| `page.html` | The marking page for the current version |

The rewrite step needs `GEMINI_API_KEY` from the user's shell and goes out through the ClashX
proxy at `127.0.0.1:7890`, as described in `~/ProgrammingProjects/personal-website/docs/copywriting/README.md`.
