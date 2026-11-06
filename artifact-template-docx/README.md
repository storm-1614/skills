# artifact-template-docx

一个用于生成教材课后习题 Word 作业稿的 Codex skill。它依据用户提供的题目内容和保留的 DOCX 参考模板整理文档，适合打印、书写或电子填写。

## 使用方式

在 Codex 中调用 `$artifact-template-docx`，并提供需要整理的教材章节、题目文本或相关文件。skill 会读取模板配置，沿用参考文档的页面设置、样式、表格、页眉页脚等版式，并渲染检查生成结果。

默认保留题目原文和数值，不添加未提供的事实；作答区域留空，只有在用户明确要求时才加入提示或答案。

## 模板样式

- 深蓝色习题标题，黑色正文
- 白底、细黑线的数据表
- 浅蓝色作答横线
- 紧凑排版，并避免习题标题与开头内容分到不同页

## 文件结构

```text
artifact-template-docx/
├── SKILL.md
├── README.md
├── artifact-template.json
├── agents/
│   └── openai.yaml
└── assets/
    ├── preview.png
    └── reference.docx
```

`artifact-template.json` 指向 `assets/reference.docx` 和 `assets/preview.png`。参考 DOCX 是模板来源；使用本 skill 时应保留它不变。

## 安装

将整个 `artifact-template-docx` 目录放入 Codex 可发现的 skills 目录，然后调用 `$artifact-template-docx`。不要只复制 `SKILL.md`：模板配置和 `assets/` 中的参考文件也属于此 skill。
