#!/usr/bin/env python3
"""
课程网站 live session 调试入口。

这个脚本不做跨进程登录态保存，只用于：
- 打开登录页
- 等你手动完成滑块和登录
- 在同一个会话里预览课程页文本
"""
from __future__ import annotations

import sys
from pathlib import Path

AGENT_ROOT = Path(__file__).resolve().parent
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))

from course_site import LiveCourseSession


def main() -> None:
    with LiveCourseSession(headless=False) as session:
        session.wait_for_manual_login()
        snapshot = session.inspect_course_page()
        preview = snapshot["page"]["text_preview"]
        print(f"课程页面已获取，预览长度 {len(preview)} 字符。")
        print(preview)


if __name__ == "__main__":
    main()
