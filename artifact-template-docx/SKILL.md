---
name: artifact-template-docx
description: "Create a document using the 教材习题作业稿 DOCX template and its retained reference file. Use when the user selects this template, names 教材习题作业稿 DOCX, or explicitly invokes $artifact-template-docx. 按修改后的参考文档整理教材章节课后习题，保留紧凑排版、深蓝题号、白底黑线数据表和浅蓝作答横线，生成适合打印和填写的 Word 作业稿。"
---

# 教材习题作业稿 DOCX

Create a document from this template. Keep the reference file unchanged.

## Workflow

1. Read `artifact-template.json` and resolve its paths relative to this skill directory.
2. Load [@documents](plugin://documents@openai-primary-runtime) and invoke its reference/template workflow with the retained file.
3. Treat the user's prompt and available sources as the content input. Do not invent facts merely to fill a template slot.
4. Clone or import the reference instead of replacing its visual system with generic defaults.
5. Render and verify the finished document, then return the final artifact.

## Fidelity

Preserve page setup, sections, styles, lists, tables, headers, footers, and recurring page elements.

User instructions control requested content and explicit deviations. The retained reference controls layout and formatting where the user has not requested a change.

## Homework worksheet conventions

- Start with the first exercise heading. Omit a separate cover, document title block, and student-information fields unless the user asks for them.
- Keep the compact, plain page style from the reference: dark-blue exercise headings, black body text, white tables with thin black grid lines, and light-blue answer lines.
- Keep each exercise heading with its opening question text or data table. If the block will not fit, move the heading and the block together to the next page; do not leave a heading alone at the bottom of a page.
- Keep each exercise's prompt and related data table together when possible. Use page breaks between complete exercises as needed to avoid splitting their opening content.
- Preserve source question wording and numerical data. Leave answer areas blank by default; include hints or solutions only when the user asks for them.
- Size the answer area to the amount of work each exercise requires. Keep the result compact while leaving enough room for clear handwriting or typed responses.
