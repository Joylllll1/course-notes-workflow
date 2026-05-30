# Course Agent

这个目录集中放课程课件笔记 agent 的所有核心代码和运行产物约定。

## 主要文件

- `course_notes_agent.py`: 主入口，登录后列出课件、选择课件、保存 PDF、生成 Obsidian 笔记
- `course_site.py`: Playwright 会话与课程页面交互逻辑
- `login_helper.py`: 仅用于手动登录和页面预览的调试入口

## 运行方式

在仓库根目录执行：

```bash
./course.sh
```

或：

```bash
./run.sh
```

## 配置

复制根目录 `.env.example` 为 `.env` 后填写：

- `DEEPSEEK_API_KEY`
- `COURSE_URL`
- `OBSIDIAN_VAULT`
- `COURSE_NOTES_DIR`
- `COURSE_ATTACHMENTS_DIR`

## 不建议提交到 GitHub 的内容

以下内容已经在 `.gitignore` 里忽略：

- `.env`
- `.venv/`
- `course-agent/auth/`
- `course-agent/debug/`
- `__pycache__/`
