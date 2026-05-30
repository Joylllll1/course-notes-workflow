# Course Notes Agent

这个目录现在是一个相对独立的项目单元，尽量把课程课件笔记 agent 需要的代码、脚本和说明都收在这里。

如果你想单独上传 GitHub，优先上传这个目录即可。

## 包含内容

- `course_notes_agent.py`: 主入口，登录后列出课件、选择课件、保存 PDF、生成 Obsidian 笔记
- `course_site.py`: Playwright 会话与课程页面交互逻辑
- `login_helper.py`: 手动登录和页面预览的调试入口
- `run.sh`: 当前目录内的一键启动脚本
- `.env.example`: 示例配置
- `.gitignore`: 这个子项目自己的忽略规则

## 运行

如果你当前就在这个目录里：

```bash
./run.sh
```

如果你还在仓库根目录，也可以：

```bash
./course.sh
```

## 安装依赖

先创建虚拟环境，然后安装 Python 依赖：

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium
```

另外还需要系统里有 `pdftotext` 命令，因为 PDF 文本提取走的是外部命令，不属于 Python 包。

## 配置

建议直接在这个目录里放 `.env`：

```bash
cp .env.example .env
```

代码会优先读取当前目录下的 `.env`，如果没有，再回退去读取上一级目录的 `.env`。

主要配置项：

- `DEEPSEEK_API_KEY`
- `COURSE_URL`
- `OBSIDIAN_VAULT`
- `COURSE_NOTES_DIR`
- `COURSE_ATTACHMENTS_DIR`
- `COURSE_OVERWRITE_EXISTING`

## 单独上传 GitHub 时的建议

建议至少保留：

- `course_notes_agent.py`
- `course_site.py`
- `login_helper.py`
- `run.sh`
- `.env.example`
- `.gitignore`
- `README.md`

不要提交：

- `.env`
- `.venv/`
- `auth/`
- `debug/`
- `__pycache__/`
