# Course Notes Agent

一个面向课程网站的课件笔记 Agent。

它的目标是：

- 在浏览器里复用你手动完成登录后的同一会话
- 进入课程页面并列出可处理的课件
- 选择一个或多个 PDF 课件
- 保存 PDF 到 Obsidian 附件目录
- 生成对应的课程笔记到 Obsidian 笔记目录

当前实现主要面向 SEEC 门户课程页，但整体流程也适合作为“登录站点 + 选课件 + 生成笔记”的参考项目。

## 特性

- 手动登录友好：不尝试绕过滑块或验证码
- 单会话处理：登录后在同一个 Playwright 会话中继续访问课程页
- 交互式选择：支持 `1`、`1,3,5`、`2-4`、`all`
- 可随时退出：输入 `exit` 或 `quit` 即结束
- Obsidian 集成：
  - 笔记输出到 `COURSE_NOTES_DIR`
  - PDF 输出到 `COURSE_ATTACHMENTS_DIR`
- 重复保护：默认已存在笔记时跳过，不覆盖
- 输出简洁：不再打印冗长的诊断报告

## 项目结构

```text
.
├── course-agent/
│   ├── course_notes_agent.py
│   ├── course_site.py
│   ├── login_helper.py
│   ├── run.sh
│   └── README.md
├── course.sh
├── run.sh
├── .env.example
└── README.md
```

核心说明：

- `course-agent/course_notes_agent.py`: 主入口
- `course-agent/course_site.py`: Playwright 页面交互和课件解析
- `course-agent/login_helper.py`: 登录调试入口
- `course.sh`: 仓库根目录的一键启动脚本

## 环境要求

- Python 3.11+
- 已安装 `pdftotext`
- 可用的 Chromium/Playwright 运行环境
- Obsidian Vault 路径
- LLM API Key

## 安装

1. 创建虚拟环境并安装依赖

```bash
python3 -m venv .venv
.venv/bin/pip install -U pip
```

2. 根据你的项目实际依赖继续安装需要的包

常见会用到的包括：

- `playwright`
- `python-dotenv`
- `langgraph`
- `langchain-core`
- `langchain-deepseek`

3. 安装 Playwright 浏览器

```bash
.venv/bin/playwright install chromium
```

4. 配置环境变量

```bash
cp .env.example .env
```

然后填写 `.env`。

## 配置

`.env.example` 中的主要配置项：

```env
DEEPSEEK_API_KEY=your_api_key
COURSE_URL=https://p-nju.seec.seecoder.cn/course/18
COURSE_USERNAME=your_username
COURSE_PASSWORD=your_password
OBSIDIAN_VAULT=/path/to/your/Obsidian Vault
COURSE_NOTES_DIR=SEEC
COURSE_ATTACHMENTS_DIR=Attachments
COURSE_OVERWRITE_EXISTING=false
```

说明：

- `OBSIDIAN_VAULT`: 你的 Obsidian 仓库根目录
- `COURSE_NOTES_DIR`: 笔记输出目录，可以是相对 `OBSIDIAN_VAULT` 的路径
- `COURSE_ATTACHMENTS_DIR`: PDF 输出目录，可以与笔记目录分离
- `COURSE_OVERWRITE_EXISTING=false`: 若笔记已存在则默认跳过

## 运行

直接运行：

```bash
./course.sh
```

或者：

```bash
./run.sh
```

## 使用流程

1. 脚本启动浏览器
2. 你手动完成登录、滑块和跳转
3. 回终端按 Enter
4. Agent 验证当前页面是否真的进入课程页
5. 列出可选课件
6. 输入编号选择要处理的课件
7. Agent 打开 PDF、保存附件、抽取文本并生成笔记
8. 继续下一轮选择，直到输入 `exit` 或 `quit`

## 输出行为

成功处理后通常会得到：

- 一个 PDF 文件，保存在 `COURSE_ATTACHMENTS_DIR`
- 一个 Markdown 笔记，保存在 `COURSE_NOTES_DIR`

如果笔记已存在且未开启覆盖：

- 会跳过该课件
- 不会覆盖已有笔记

## GitHub 提交建议

不要提交这些内容：

- `.env`
- `.venv/`
- `course-agent/auth/`
- `course-agent/debug/`
- `__pycache__/`

这些内容已经在 `.gitignore` 中处理。

## 限制

- 不会自动绕过验证码、滑块或 SSO
- 依赖课程站点当前页面结构
- PDF 文本抽取效果取决于原始 PDF 质量
- 笔记生成质量取决于抽取文本和模型输出

## 后续可扩展方向

- 增加 `requirements.txt` 或 `pyproject.toml`
- 为更多课程站点做适配
- 增加批量处理与断点续跑
- 增加更稳健的重复检测，而不只是“笔记文件是否存在”
