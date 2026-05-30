#!/usr/bin/env python3
"""LangGraph 版课程课件笔记 Agent。"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, TypedDict

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage
from langchain_deepseek import ChatDeepSeek
from langgraph.graph import END, START, StateGraph

AGENT_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = AGENT_ROOT.parent
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))

from course_site import LiveCourseSession

if (AGENT_ROOT / ".env").exists():
    load_dotenv(AGENT_ROOT / ".env")
else:
    load_dotenv(PROJECT_ROOT / ".env")

DEBUG_DIR = AGENT_ROOT / "debug"
DEFAULT_MODEL = "deepseek-chat"


class AgentState(TypedDict, total=False):
    session: LiveCourseSession
    logged_in: bool
    login_snapshot: dict[str, Any]
    discovery_snapshot: dict[str, Any]
    selected_materials: list[dict[str, Any]]
    opened_materials: list[dict[str, Any]]
    processed_materials: list[dict[str, Any]]
    report_dir: str
    json_path: str
    final_message: str
    should_exit: bool


def _ensure_debug_dir() -> Path:
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    return DEBUG_DIR


def _resolve_notes_dir() -> Path:
    vault = Path(os.path.expanduser(os.getenv("OBSIDIAN_VAULT", "").strip()))
    notes_dir = os.getenv("COURSE_NOTES_DIR", "").strip()
    if not notes_dir:
        raise RuntimeError("缺少 COURSE_NOTES_DIR")
    target = Path(os.path.expanduser(notes_dir))
    if not target.is_absolute():
        target = vault / target
    target.mkdir(parents=True, exist_ok=True)
    return target


def _resolve_attachments_dir(notes_dir: Path) -> Path:
    attachments_dir_env = os.getenv("COURSE_ATTACHMENTS_DIR", "").strip()
    if attachments_dir_env:
        vault = Path(os.path.expanduser(os.getenv("OBSIDIAN_VAULT", "").strip()))
        attachments_dir = Path(os.path.expanduser(attachments_dir_env))
        if not attachments_dir.is_absolute():
            attachments_dir = vault / attachments_dir
    else:
        attachments_dir = notes_dir / "Attachments"
    attachments_dir.mkdir(parents=True, exist_ok=True)
    return attachments_dir


def _sanitize_filename(name: str) -> str:
    return re.sub(r"[\\\\/:*?\"<>|]", "-", name).strip() or "untitled"


def _extract_pdf_text(path: Path) -> str:
    result = subprocess.run(
        ["pdftotext", str(path), "-"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        return ""
    return result.stdout


def _trim_text_for_llm(text: str, max_chars: int = 24000) -> str:
    normalized = text.strip()
    if len(normalized) <= max_chars:
        return normalized
    head = normalized[: max_chars // 2]
    tail = normalized[-max_chars // 2 :]
    return head + "\n\n[...内容过长，已省略中间部分...]\n\n" + tail


def _derive_belonging(snapshot: dict[str, Any]) -> str:
    preview = snapshot.get("page", {}).get("text_preview", "")
    match = re.search(r"(\d{4}-[^\s]+)", preview)
    if match:
        return match.group(1)
    return "SEEC"


def _creator_name() -> str:
    return os.getenv("COURSE_NOTE_CREATOR", "").strip() or os.getenv("USER", "wjl")


def _should_overwrite_existing() -> bool:
    value = os.getenv("COURSE_OVERWRITE_EXISTING", "").strip().lower()
    return value in {"1", "true", "yes", "y", "on"}


def _compact_materials_for_report(items: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    compacted: list[dict[str, Any]] = []
    for item in items[:limit]:
        compacted.append(
            {
                "file_name": item.get("file_name", ""),
                "size": item.get("size", ""),
                "uploaded_at": item.get("uploaded_at", ""),
                "action_label": item.get("action_label", ""),
                "target_url": item.get("target_url", ""),
                "open_mode": item.get("open_mode", ""),
                "status": item.get("status", ""),
                "note_path": item.get("note_path", ""),
                "pdf_path": item.get("pdf_path", ""),
            }
        )
    return compacted


def _build_note_via_llm(
    snapshot: dict[str, Any],
    material: dict[str, Any],
    slide_link: str,
    pdf_text: str,
) -> str:
    llm = ChatDeepSeek(model=os.getenv("COURSE_AGENT_MODEL", DEFAULT_MODEL), temperature=0)
    belonging = _derive_belonging(snapshot)
    creator = _creator_name()
    sample_style = """
Belonging: [[Compiler]]
Creator: Joy
Slides: [[2. 1-语法分析-自顶向下的分析技术.pdf]] && [[2. 2-语法分析-自底向上的分析技术-2(1).pdf]]

---

## 1. 主题概述

### 关键概念

- 用项目符号整理概念和定义
- 必要时加入 `详见 [[相关术语]]`
""".strip()
    trimmed_pdf_text = _trim_text_for_llm(pdf_text, max_chars=28000)
    prompt = f"""
你要生成一篇 Obsidian 课程笔记，风格参考下面这个样例骨架：

{sample_style}

请严格遵循这些风格：
- 顶部必须有：
  - `Belonging: [[...]]`
  - `Creator: ...`
  - `Slides: [[{slide_link}]]`
- 紧接着一行 `---`
- 然后用 `## 1.`、`## 2.` 这样的编号章节组织内容
- 小节使用 `###`
- 以中文为主，保留必要英文术语
- 适当使用 `[[双链术语]]`
- 内容不要空泛，要像学习笔记，不是营销式摘要
- 如果某一页/某一段信息不完整，就谨慎表达，不要编造

本次课件信息：
- 课程归属：{belonging}
- 作者：{creator}
- 文件名：{material.get('file_name', '')}
- 文件大小：{material.get('size', '')}
- 上传时间：{material.get('uploaded_at', '')}

课件提取文本：
{trimmed_pdf_text}
""".strip()
    response = llm.invoke([HumanMessage(content=prompt)])
    content = response.content if isinstance(response.content, str) else "\n".join(response.content)
    return content.strip()


def _fallback_note(snapshot: dict[str, Any], material: dict[str, Any], pdf_name: str, pdf_text: str) -> str:
    belonging = _derive_belonging(snapshot)
    creator = _creator_name()
    excerpt = pdf_text[:3000].strip() or "未能抽取到足够文本。"
    return (
        f"Belonging: [[{belonging}]]\n"
        f"Creator: {creator}\n"
        f"Slides: [[{material.get('slide_link', f'Attachments/{pdf_name}') }]]\n\n"
        "---\n\n"
        f"## 1. {material.get('file_name', '')}\n\n"
        "### 课件信息\n\n"
        f"- 文件大小：{material.get('size', '')}\n"
        f"- 上传时间：{material.get('uploaded_at', '')}\n\n"
        "### 内容摘录\n\n"
        f"{excerpt}\n"
    )


def bootstrap_node(state: AgentState) -> AgentState:
    report_dir = _ensure_debug_dir()
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return {
        **state,
        "report_dir": str(report_dir),
        "json_path": str(report_dir / f"course-page-{timestamp}.json"),
        "should_exit": False,
    }


def wait_login_node(state: AgentState) -> AgentState:
    session = state["session"]
    snapshot = session.wait_for_manual_login()
    return {
        **state,
        "logged_in": True,
        "login_snapshot": snapshot,
    }


def route_after_bootstrap(state: AgentState) -> str:
    return "inspect" if state.get("logged_in") else "wait_login"


def inspect_node(state: AgentState) -> AgentState:
    session = state["session"]
    snapshot = session.inspect_course_page()
    Path(state["json_path"]).write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        **state,
        "discovery_snapshot": snapshot,
    }


def _render_material_choices(materials: list[dict[str, Any]]) -> str:
    lines = ["可选课件："]
    for index, item in enumerate(materials, start=1):
        file_name = item.get("file_name", "(empty)")
        size = item.get("size", "(size?)")
        uploaded = item.get("uploaded_at", "(time?)")
        lines.append(f"{index}. {file_name} | {size} | {uploaded}")
    return "\n".join(lines)


def _parse_selection(raw: str, total: int) -> list[int]:
    text = raw.strip().lower()
    if not text:
        raise ValueError("未输入任何编号")
    if text in {"exit", "quit"}:
        raise SystemExit
    if text == "all":
        return list(range(total))

    selected: set[int] = set()
    for part in text.split(","):
        token = part.strip()
        if not token:
            continue
        if "-" in token:
            left, right = token.split("-", 1)
            start = int(left)
            end = int(right)
            if start > end:
                start, end = end, start
            for value in range(start, end + 1):
                if value < 1 or value > total:
                    raise ValueError(f"编号超出范围: {value}")
                selected.add(value - 1)
            continue
        value = int(token)
        if value < 1 or value > total:
            raise ValueError(f"编号超出范围: {value}")
        selected.add(value - 1)
    if not selected:
        raise ValueError("没有解析出任何有效编号")
    return sorted(selected)


def select_node(state: AgentState) -> AgentState:
    materials = list(state["discovery_snapshot"].get("materials", []))
    if not materials:
        print("没有发现可选课件。")
        return {**state, "selected_materials": []}

    print()
    print(_render_material_choices(materials))
    print("输入编号，例如 `1`、`1,3,5`、`2-4`，或输入 `all`。")
    print("输入 `exit` 或 `quit` 退出会话。")

    while True:
        raw = input("选择要处理的课件: ")
        try:
            indexes = _parse_selection(raw, len(materials))
            break
        except SystemExit:
            print("收到退出指令，准备结束会话。")
            return {**state, "selected_materials": [], "should_exit": True}
        except ValueError as exc:
            print(f"输入无效：{exc}")

    selected = [materials[index] for index in indexes]
    print(f"已选择 {len(selected)} 个课件。")
    return {**state, "selected_materials": selected}


def open_selected_node(state: AgentState) -> AgentState:
    if state.get("should_exit"):
        return {**state, "opened_materials": []}

    session = state["session"]
    selected = state.get("selected_materials", [])
    opened: list[dict[str, Any]] = []

    for item in selected:
        file_name = str(item.get("file_name", "")).strip()
        if not file_name:
            continue
        print(f"正在解析课件打开目标: {file_name}")
        opened_item = {**item, **session.open_material(file_name)}
        opened.append(opened_item)
        mode = opened_item.get("open_mode", "")
        target_url = opened_item.get("target_url", "")
        if target_url:
            print(f"  -> {mode}: {target_url}")
        else:
            print(f"  -> {mode or 'unresolved'}")

    return {**state, "opened_materials": opened}


def process_selected_node(state: AgentState) -> AgentState:
    if state.get("should_exit"):
        return {**state, "processed_materials": []}

    session = state["session"]
    snapshot = state["discovery_snapshot"]
    notes_dir = _resolve_notes_dir()
    attachments_dir = _resolve_attachments_dir(notes_dir)
    overwrite_existing = _should_overwrite_existing()
    processed: list[dict[str, Any]] = []

    for item in state.get("opened_materials", []):
        file_name = str(item.get("file_name", "")).strip()
        target_url = str(item.get("target_url", "")).strip()
        if not file_name or not target_url:
            processed.append({**item, "status": "skipped-unresolved"})
            continue

        safe_pdf_name = _sanitize_filename(file_name)
        pdf_path = attachments_dir / safe_pdf_name
        note_path = notes_dir / f"{Path(safe_pdf_name).stem}.md"
        relative_slide_path = os.path.relpath(pdf_path, start=notes_dir)
        slide_link = relative_slide_path.replace(os.sep, "/")

        if note_path.exists() and not overwrite_existing:
            processed.append(
                {
                    **item,
                    "status": "skipped-existing-note",
                    "pdf_path": str(pdf_path),
                    "note_path": str(note_path),
                }
            )
            print(f"正在跳过已存在笔记: {file_name}")
            print(f"  -> note exists: {note_path}")
            continue

        print(f"正在保存课件并生成笔记: {file_name}")
        try:
            pdf_bytes = session.fetch_material_bytes(item)
            pdf_path.write_bytes(pdf_bytes)
            pdf_text = _extract_pdf_text(pdf_path)
            try:
                note_content = _build_note_via_llm(
                    snapshot,
                    {**item, "slide_link": slide_link},
                    slide_link,
                    pdf_text,
                )
            except Exception:
                note_content = _fallback_note(
                    snapshot,
                    {**item, "slide_link": slide_link},
                    safe_pdf_name,
                    pdf_text,
                )
            note_path.write_text(note_content, encoding="utf-8")
            processed.append(
                {
                    **item,
                    "status": "processed",
                    "pdf_path": str(pdf_path),
                    "note_path": str(note_path),
                }
            )
            print(f"  -> note: {note_path}")
        except Exception as exc:
            processed.append({**item, "status": f"error: {exc}"})
            print(f"  -> error: {exc}")

    return {**state, "processed_materials": processed}


def finalize_node(state: AgentState) -> AgentState:
    if state.get("should_exit"):
        return {**state, "final_message": "会话结束。"}

    processed = state.get("processed_materials", [])
    success_count = sum(1 for item in processed if item.get("status") == "processed")
    skipped_count = sum(1 for item in processed if str(item.get("status", "")).startswith("skipped-"))
    error_count = sum(1 for item in processed if str(item.get("status", "")).startswith("error:"))

    parts = [f"本轮处理完成：成功 {success_count}"]
    if skipped_count:
        parts.append(f"跳过 {skipped_count}")
    if error_count:
        parts.append(f"失败 {error_count}")
    return {**state, "final_message": "，".join(parts) + "。"}


def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("bootstrap", bootstrap_node)
    graph.add_node("wait_login", wait_login_node)
    graph.add_node("inspect", inspect_node)
    graph.add_node("select", select_node)
    graph.add_node("open_selected", open_selected_node)
    graph.add_node("process_selected", process_selected_node)
    graph.add_node("finalize", finalize_node)

    graph.add_edge(START, "bootstrap")
    graph.add_conditional_edges(
        "bootstrap",
        route_after_bootstrap,
        {
            "wait_login": "wait_login",
            "inspect": "inspect",
        },
    )
    graph.add_edge("wait_login", "inspect")
    graph.add_edge("inspect", "select")
    graph.add_edge("select", "open_selected")
    graph.add_edge("open_selected", "process_selected")
    graph.add_edge("process_selected", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile()


def run_agent_once(
    session: LiveCourseSession,
    logged_in: bool = False,
    login_snapshot: dict[str, Any] | None = None,
) -> AgentState:
    graph = build_graph()
    initial_state: AgentState = {"session": session, "logged_in": logged_in}
    if login_snapshot is not None:
        initial_state["login_snapshot"] = login_snapshot
    result = graph.invoke(initial_state)
    return result


def main() -> None:
    with LiveCourseSession(headless=False) as session:
        login_snapshot = session.wait_for_manual_login()
        while True:
            result = run_agent_once(
                session=session,
                logged_in=True,
                login_snapshot=login_snapshot,
            )
            print(result.get("final_message", "运行完成。"))
            if result.get("should_exit"):
                break


if __name__ == "__main__":
    main()
