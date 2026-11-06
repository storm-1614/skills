---
name: pdf-translate
description: Use when a user asks to translate an academic PDF, especially English to Chinese, or wants monolingual or bilingual PDFs that preserve the source layout.
---

# PDF Translate

Translate PDFs with PDFMathTranslate-next (`pdf2zh_next`) and BabelDOC so the output retains the source page layout, figures, tables, and formulas. The default engine is the user's configured Codex CLI model, accessed through a temporary loopback adapter.

For each input, the skill produces:

- `*-mono.pdf` — translated text in the source layout.
- `*-dual.pdf` — source and translated text in the source layout.

## Workflow

### 1. Prepare tools

Run the bundled setup script with Bash:

```bash
bash <skill-dir>/scripts/setup.sh
```

It installs `pdf2zh_next` with `uv` and Python 3.12 if needed. Codex is the preferred translation engine when `codex` is on PATH and signed in. The script can use an OpenAI-compatible API, Claude Code, or SiliconFlowFree when Codex is unavailable.

The first translation downloads BabelDOC's layout model and fonts (hundreds of MB) into `~/.cache/babeldoc`; later runs reuse them.

### 2. Translate

```bash
bash <skill-dir>/scripts/translate.sh "<input.pdf>" [options]
```

When Codex is selected, `translate.sh` starts `scripts/codex_bridge.py` on an ephemeral port bound to `127.0.0.1`, points PDFMathTranslate at that local OpenAI-compatible endpoint, and stops the bridge on exit. The adapter invokes `codex exec` with the configured Codex model for each translation request. It logs request metadata only. BabelDOC remains responsible for parsing and rebuilding the PDF.

Automatic engine order:

1. `codex` — local Codex CLI; model from `~/.codex/config.toml`, or `gpt-6-luna` if unset. Does not require an OpenAI API key.
2. `openai` — an OpenAI-compatible endpoint when an API key is configured.
3. `claudecode` — Claude Code CLI.
4. `siliconflowfree` — free service; text is forwarded through the project maintainer's server.

Common options:

- `--engine codex|openai|claudecode|siliconflowfree`
- `--model <name>` — override the model; with `codex`, this is passed to `codex exec`.
- `--output <dir>` — output directory (default: `output/` beside the input PDF).
- `--pages "1-5,8"` — translate selected pages while retaining the full PDF.
- `--lang-in en --lang-out zh-CN` — language codes; default is English to Simplified Chinese.
- `--qps N --pool-max-workers N` — request rate and worker count. Codex defaults to 3 for each; use 1 for a slower, more conservative run.
- `--no-dual` / `--no-mono` — skip one output.
- `-- <pdf2zh_next options>` — pass through additional options.

### 3. Report results

Report the output paths, engine and model used, and whether BabelDOC reported fallback paragraphs. If the Codex bridge fails, report the CLI error and offer an explicit alternate engine; do not silently switch the user's chosen model.

## Troubleshooting

- **`pdf2zh_next` missing** — run `setup.sh` and ensure `~/.local/bin` is on PATH.
- **Codex bridge fails to start** — check that `codex` is installed and signed in; `python3` is also required.
- **Codex request timeout or rate errors** — lower `--qps` and `--pool-max-workers`; retries can reuse PDFMathTranslate's translation cache.
- **First run is slow** — BabelDOC downloads layout and font assets once. Codex translation also takes longer than a direct API because each request uses `codex exec`.
- **Large PDFs or memory pressure** — split with `--pages` or pass `--max-pages-per-part 50`.
- **OpenAI-compatible API errors** — verify the endpoint supports `/v1/chat/completions`; otherwise choose another engine.

For language codes, glossaries, and advanced options, read `references/advanced.md`.
