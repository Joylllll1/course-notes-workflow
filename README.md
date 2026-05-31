# Course Notes Workflow

一个面向课程网站的课件笔记自动化工作流。

它的目标很直接：在同一个浏览器会话里复用你手动完成的登录，进入课程页，列出可处理课件，保存附件，并生成对应的 Markdown 笔记。

当前实现已经兼容 SEEC 门户课程页和软院 Moodle 课程页，也能处理一部分公开课程资源页。整体上它是一个固定编排的工作流：课件选择由用户确认，LLM 主要负责把课件文本整理成笔记。

## 特性

- 手动登录友好：不尝试绕过滑块或验证码
- 公开课程页友好：若 `COURSE_URL` 可直接访问，则不强制先登录
- 单会话处理：登录后在同一个 Playwright 会话中继续访问课程页
- 交互式选择：支持 `1`、`1,3,5`、`2-4`、`all`
- 可随时退出：输入 `exit` 或 `quit` 即结束
- 输出目录可配置：笔记输出到 `COURSE_NOTES_DIR`，附件输出到 `COURSE_ATTACHMENTS_DIR`；相对附件目录默认解析到笔记目录下
- 重复保护：默认已存在笔记时跳过，不覆盖
- 新站点探测模式：先分析页面和候选材料，再决定是否进入正式处理

## 项目结构

```text
.
├── course_notes_workflow.py
├── course_site.py
├── login_helper.py
├── requirements.txt
├── run.sh
├── course.sh
├── .env.example
├── .gitignore
└── README.md
```

## 环境要求

- Python 3.11+
- 可用的 Chromium/Playwright 运行环境
- 已安装 `pdftotext`
- 任意本地输出目录
- 可用的 LLM API Key

Windows 用户：`pdftotext.exe` 推荐使用 [Poppler for Windows releases](https://github.com/oschwartz10612/poppler-windows/releases/) 提供的预编译包。

## 安装

macOS / Linux：

1. 创建虚拟环境并安装依赖

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

2. 安装 Playwright 浏览器

```bash
.venv/bin/playwright install chromium
```

3. 配置环境变量

```bash
cp .env.example .env
```

然后填写 `.env`。

如果 `pdftotext` 不在系统 `PATH` 中，可以额外配置 `PDFTOTEXT_BIN`。Windows 常见写法类似 `C:\\path\\to\\pdftotext.exe`。

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m playwright install chromium
Copy-Item .env.example .env
.\run.ps1
```

## 配置

`.env.example` 中的主要配置项：

```env
COURSE_WORKFLOW_MODEL=deepseek-v4-flash
DEEPSEEK_API_KEY=your_api_key

# 可选：DeepSeek 兼容接口配置
# COURSE_LLM_BASE_URL=https://api.deepseek.com
# COURSE_LLM_TEMPERATURE=0
# COURSE_LLM_TIMEOUT=120

# 必填：课程页地址。默认先打开它；需要登录时通常由站点自己跳到登录页
COURSE_URL=https://p-nju.seec.seecoder.cn/course/18

# 可选：只有课程页不能自动跳到正确登录入口时才需要配置
# COURSE_LOGIN_URL=https://your-site.example.com/login

# 新站点调试：true 时只探测页面和候选材料，不下载、不生成笔记
COURSE_PROBE_ONLY=false

COURSE_OUTPUT_ROOT=/path/to/your/output
COURSE_NOTES_DIR=SEEC
COURSE_ATTACHMENTS_DIR=Attachments
COURSE_OVERWRITE_EXISTING=false
```

常用项：

- `COURSE_URL`: 必填，课程页地址。工作流默认先访问它；如果课程页需要登录，优先沿用站点从课程页触发出来的登录跳转链
- `COURSE_LOGIN_URL`: 可选兜底项。只有课程页不会自动跳到正确登录入口，或必须从特定 SSO/CAS 入口进入时才需要配置
- `COURSE_PROBE_ONLY=false`: 默认关闭；设为 `true/1` 后只做页面探测与候选材料分析，不进入选择、下载和笔记生成
- `COURSE_OUTPUT_ROOT`: 可选，`COURSE_NOTES_DIR` 的相对路径基准
- `COURSE_NOTES_DIR`: 笔记输出目录，可以是绝对路径或相对 `COURSE_OUTPUT_ROOT` 的路径
- `COURSE_ATTACHMENTS_DIR`: 附件输出目录；绝对路径会直接使用，相对路径默认解析到 `COURSE_NOTES_DIR` 下
- `COURSE_OVERWRITE_EXISTING=false`: 若笔记已存在则默认跳过

兼容和进阶项：

- `OBSIDIAN_VAULT`: 旧配置兼容项；未设置 `COURSE_OUTPUT_ROOT` 时才会作为输出根目录使用
- `PDFTOTEXT_BIN`: 可选，手动指定 `pdftotext` 或 `pdftotext.exe` 的路径
- `COURSE_WORKFLOW_MODEL`: 可选，指定 DeepSeek 模型名
- `COURSE_LLM_BASE_URL`: 可选，自定义 DeepSeek 兼容 base URL，默认 `https://api.deepseek.com`
- `COURSE_LLM_TEMPERATURE`: 可选，默认 `0`
- `COURSE_LLM_TIMEOUT`: 可选，默认 `120` 秒
- `COURSE_AUTH_SUCCESS_SELECTORS`: 可选，补充登录成功后的页面选择器，使用逗号或换行分隔
- `COURSE_AUTH_SUCCESS_TEXT`: 可选，补充登录成功后的页面文本特征，使用逗号或换行分隔
- `COURSE_AUTH_LOGGED_OUT_TEXT`: 可选，补充明确未登录文案，使用逗号或换行分隔
- `COURSE_AUTH_INACCESSIBLE_TEXT`: 可选，补充不可访问文案，使用逗号或换行分隔
- `COURSE_AUTH_COOKIE_NAMES`: 可选，补充认证 cookie 名称关键词，使用逗号或换行分隔

路径示例：

- `COURSE_OUTPUT_ROOT=/Users/wjl/Documents/Obsidian Vault`
- `COURSE_NOTES_DIR=SEEC`
- `COURSE_ATTACHMENTS_DIR=Attachments`
- 结果：
  - 笔记目录是 `/Users/wjl/Documents/Obsidian Vault/SEEC`
  - 附件目录是 `/Users/wjl/Documents/Obsidian Vault/SEEC/Attachments`

如果你想把附件放到笔记目录外，直接把 `COURSE_ATTACHMENTS_DIR` 写成绝对路径，例如：

```env
COURSE_ATTACHMENTS_DIR=/Users/wjl/Documents/Obsidian Vault/Attachments
```

## LLM 支持

当前只支持 DeepSeek。使用 `DEEPSEEK_API_KEY`，默认模型是 `deepseek-v4-flash`，默认接口是 `https://api.deepseek.com/chat/completions`。

示例：

```env
COURSE_WORKFLOW_MODEL=deepseek-v4-flash
DEEPSEEK_API_KEY=your_api_key
```

## 运行

macOS / Linux：

```bash
./run.sh
```

或：

```bash
./course.sh
```

Windows PowerShell：

```powershell
.\run.ps1
```

## 使用流程

正常处理时：

1. 运行 `./run.sh`
2. 工作流先访问 `COURSE_URL`
3. 如果课程页可直接访问，直接进入课件处理；如果不可直接访问，就在浏览器里完成手动登录后回终端按 Enter
4. 工作流确认当前会话已经真正进入课程页
5. 列出可选课件，输入编号选择
6. 工作流打开课件、保存附件、抽取文本并生成 Markdown 笔记
7. 继续下一轮选择，直到输入 `exit` 或 `quit`

## 探测模式

如果你在接一个新站点，建议先不要直接下载课件。可以在 `.env` 里打开：

```env
COURSE_PROBE_ONLY=true
```

这时工作流只会：

- 访问课程页并完成登录判定
- 探测页面结构、资源候选和材料候选
- 输出简要探测报告
- 把完整调试快照写到 `debug/course-page-*.json`

适合排查登录误判、材料发现失败，以及某个课件为什么打开失败。

推荐流程：

1. 先设置 `COURSE_PROBE_ONLY=true`
2. 运行 `./run.sh`
3. 看终端探测报告和 `debug/course-page-*.json`
4. 确认能发现候选材料后，再改回 `COURSE_PROBE_ONLY=false` 处理课件

## 输出行为

成功处理后会生成一个附件文件和一个 Markdown 笔记。附件默认写到 `COURSE_NOTES_DIR` 下的 `COURSE_ATTACHMENTS_DIR`；如果 `COURSE_ATTACHMENTS_DIR` 是绝对路径，就直接写到那个目录。若笔记已存在且未开启覆盖，工作流会跳过该课件，不覆盖已有笔记。

## Windows 说明

- 直接运行入口是 `run.ps1`，不是 `run.sh`。
- 需要提前安装 `pdftotext.exe`，推荐使用 [Poppler for Windows releases](https://github.com/oschwartz10612/poppler-windows/releases/)。
- 如果 `pdftotext.exe` 没有加入 `PATH`，请在 `.env` 里设置 `PDFTOTEXT_BIN`。

## 限制

- 不会自动绕过验证码、滑块或 SSO
- 依赖课程站点当前页面结构
- PDF 文本抽取效果取决于原始 PDF 质量
- 笔记生成质量取决于抽取文本和模型输出
