#!/usr/bin/env python3
"""LangGraph 版课程课件笔记工作流。"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, TypedDict

from dotenv import load_dotenv
from langgraph.graph import END, START, StateGraph

WORKFLOW_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = WORKFLOW_ROOT.parent
if str(WORKFLOW_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKFLOW_ROOT))

from course_site import LiveCourseSession

if (WORKFLOW_ROOT / ".env").exists():
    load_dotenv(WORKFLOW_ROOT / ".env")
else:
    load_dotenv(PROJECT_ROOT / ".env")

DEBUG_DIR = WORKFLOW_ROOT / "debug"
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
MATERIAL_FILE_SUFFIXES = {
    ".pdf",
    ".ppt",
    ".pptx",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".zip",
    ".rar",
    ".7z",
}


class WorkflowState(TypedDict, total=False):
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
    no_materials_found: bool
    probe_only: bool


def _ensure_debug_dir() -> Path:
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    return DEBUG_DIR


def _configured_output_root() -> Path | None:
    raw_root = os.getenv("COURSE_OUTPUT_ROOT", "").strip() or os.getenv("OBSIDIAN_VAULT", "").strip()
    if not raw_root:
        return None
    output_root = Path(os.path.expanduser(raw_root))
    if not output_root.is_absolute():
        output_root = WORKFLOW_ROOT / output_root
    return output_root


def _resolve_output_path(raw_path: str, base_dir: Path | None) -> Path:
    target = Path(os.path.expanduser(raw_path))
    if target.is_absolute():
        return target
    if base_dir is not None:
        return base_dir / target
    return WORKFLOW_ROOT / target


def _probe_only_enabled() -> bool:
    return os.getenv("COURSE_PROBE_ONLY", "").strip().lower() in {"1", "true", "yes", "on"}


def _resolve_notes_dir() -> Path:
    output_root = _configured_output_root()
    notes_dir = os.getenv("COURSE_NOTES_DIR", "").strip()
    if not notes_dir:
        raise RuntimeError("缺少 COURSE_NOTES_DIR")
    target = _resolve_output_path(notes_dir, output_root)
    target.mkdir(parents=True, exist_ok=True)
    return target


def _resolve_attachments_dir(notes_dir: Path) -> Path:
    attachments_dir_env = os.getenv("COURSE_ATTACHMENTS_DIR", "").strip()
    if attachments_dir_env:
        raw_path = Path(os.path.expanduser(attachments_dir_env))
        if raw_path.is_absolute():
            attachments_dir = raw_path
        else:
            attachments_dir = notes_dir / raw_path
    else:
        attachments_dir = notes_dir / "Attachments"
    attachments_dir.mkdir(parents=True, exist_ok=True)
    return attachments_dir


def _sanitize_filename(name: str) -> str:
    return re.sub(r"[\\\\/:*?\"<>|]", "-", name).strip() or "untitled"


def _is_supported_material_suffix(suffix: str) -> bool:
    return suffix.lower() in MATERIAL_FILE_SUFFIXES


def _detect_extension_from_bytes(payload: bytes) -> str:
    if payload.startswith(b"%PDF-"):
        return ".pdf"
    if payload.startswith(b"PK\x03\x04"):
        return ".zip"
    return ""


def _candidate_material_filename(item: dict[str, Any]) -> str:
    raw_name = str(item.get("file_name", "")).strip() or "untitled"
    raw_suffix = Path(raw_name).suffix
    if _is_supported_material_suffix(raw_suffix):
        return _sanitize_filename(raw_name)

    suggested = str(item.get("suggested_filename", "")).strip()
    suggested_suffix = Path(suggested).suffix
    if _is_supported_material_suffix(suggested_suffix):
        return _sanitize_filename(raw_name + suggested_suffix)

    return _sanitize_filename(raw_name)


def _resolved_material_filename(item: dict[str, Any], payload: bytes) -> str:
    raw_name = str(item.get("file_name", "")).strip() or "untitled"
    raw_suffix = Path(raw_name).suffix
    if _is_supported_material_suffix(raw_suffix):
        return _sanitize_filename(raw_name)

    guessed_suffix = _detect_extension_from_bytes(payload)
    if guessed_suffix:
        return _sanitize_filename(raw_name + guessed_suffix)

    return _candidate_material_filename(item)


def _find_supported_existing_attachment(attachments_dir: Path, stem: str) -> Path | None:
    for candidate in sorted(attachments_dir.glob(f"{stem}.*")):
        if _is_supported_material_suffix(candidate.suffix):
            return candidate
    return None


def _resolve_pdftotext_command() -> str | None:
    configured = os.getenv("PDFTOTEXT_BIN", "").strip()
    candidates = [configured] if configured else []
    candidates.append("pdftotext")

    for candidate in candidates:
        expanded = os.path.expanduser(candidate)
        if Path(expanded).is_file() or shutil.which(expanded):
            return expanded
    return None


def _extract_pdf_text(path: Path) -> tuple[str, str | None]:
    command = _resolve_pdftotext_command()
    if command is None:
        return "", "未找到 pdftotext；请安装 Poppler，或在 PDFTOTEXT_BIN 中指定可执行文件路径。"

    try:
        result = subprocess.run(
            [command, str(path), "-"],
            capture_output=True,
            text=False,
            timeout=60,
        )
    except FileNotFoundError:
        return "", f"无法执行 pdftotext：{command}"
    except subprocess.TimeoutExpired:
        return "", f"pdftotext 解析超时：{path.name}"

    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip() or f"退出码 {result.returncode}"
        return "", f"pdftotext 解析失败：{detail}"

    stdout = result.stdout or b""
    for encoding in ("utf-8", "gb18030", "cp936"):
        try:
            return stdout.decode(encoding), None
        except UnicodeDecodeError:
            continue
    return stdout.decode("utf-8", errors="replace"), "pdftotext 输出解码失败，已用替换字符处理。"


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


def _llm_model() -> str:
    return os.getenv("COURSE_WORKFLOW_MODEL", "").strip() or os.getenv("COURSE_AGENT_MODEL", DEFAULT_MODEL)


def _llm_temperature() -> float:
    raw = os.getenv("COURSE_LLM_TEMPERATURE", "").strip()
    if not raw:
        return 0.0
    return float(raw)


def _llm_timeout_seconds() -> int:
    raw = os.getenv("COURSE_LLM_TIMEOUT", "").strip()
    if not raw:
        return 120
    return max(1, int(raw))


def _resolve_llm_config() -> dict[str, Any]:
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("缺少 DEEPSEEK_API_KEY")
    base_url = os.getenv("COURSE_LLM_BASE_URL", "").strip() or DEFAULT_DEEPSEEK_BASE_URL
    return {
        "model": _llm_model(),
        "temperature": _llm_temperature(),
        "timeout": _llm_timeout_seconds(),
        "api_key": api_key,
        "base_url": base_url.rstrip("/"),
    }


def _http_post_json(url: str, headers: dict[str, str], payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={**headers, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LLM API 请求失败: HTTP {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"LLM API 网络错误: {exc}") from exc
    return json.loads(body)


def _invoke_llm(prompt: str) -> str:
    config = _resolve_llm_config()
    payload = {
        "model": config["model"],
        "temperature": config["temperature"],
        "messages": [{"role": "user", "content": prompt}],
    }
    data = _http_post_json(
        f"{config['base_url']}/chat/completions",
        {"Authorization": f"Bearer {config['api_key']}"},
        payload,
        timeout=config["timeout"],
    )
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("DeepSeek API 未返回 choices")
    message = choices[0].get("message", {})
    content = message.get("content", "")
    if isinstance(content, list):
        return "\n".join(str(part.get("text", "")) for part in content if isinstance(part, dict)).strip()
    return str(content).strip()


def _build_note_via_llm(
    snapshot: dict[str, Any],
    material: dict[str, Any],
    slide_link: str,
    pdf_text: str,
) -> str:
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
你要生成一篇 Markdown 课程笔记，风格参考下面这个样例骨架：

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
    return _invoke_llm(prompt)


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


def bootstrap_node(state: WorkflowState) -> WorkflowState:
    report_dir = _ensure_debug_dir()
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return {
        **state,
        "report_dir": str(report_dir),
        "json_path": str(report_dir / f"course-page-{timestamp}.json"),
        "should_exit": False,
        "no_materials_found": False,
        "probe_only": _probe_only_enabled(),
    }


def wait_login_node(state: WorkflowState) -> WorkflowState:
    session = state["session"]
    snapshot = session.wait_for_manual_login()
    return {
        **state,
        "logged_in": True,
        "login_snapshot": snapshot,
    }


def route_after_bootstrap(state: WorkflowState) -> str:
    return "inspect" if state.get("logged_in") else "wait_login"


def inspect_node(state: WorkflowState) -> WorkflowState:
    session = state["session"]
    snapshot = session.inspect_course_page()
    Path(state["json_path"]).write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if state.get("probe_only"):
        print(_render_probe_report(snapshot, Path(state["json_path"])))
    return {
        **state,
        "discovery_snapshot": snapshot,
    }


def _render_probe_report(snapshot: dict[str, Any], json_path: Path) -> str:
    page = snapshot.get("page", {})
    materials = list(snapshot.get("materials", []))
    resources = list(snapshot.get("resources", []))
    lines = [
        "探测模式结果：",
        f"- 页面 URL: {page.get('url', '')}",
        f"- 页面标题: {page.get('title', '')}",
        f"- active_section: {snapshot.get('active_section', '') or 'course-root'}",
        f"- 登录表单: {'yes' if page.get('login_form_present') else 'no'}",
        f"- 成功特征: {', '.join(page.get('course_hints_found', [])) or '(none)'}",
        f"- 通用结构: {', '.join(page.get('generic_page_hints_found', [])) or '(none)'}",
        f"- 公开内容文本: {', '.join(page.get('public_page_text_hints_found', [])) or '(none)'}",
        f"- 资源链接数: {page.get('resource_link_count', 0)}",
        f"- materials: {len(materials)}",
        f"- resource candidates: {len(resources)}",
        f"- debug json: {json_path}",
    ]
    if materials:
        lines.append("材料候选：")
        for index, item in enumerate(materials[:12], start=1):
            lines.append(
                f"{index}. {item.get('file_name', '(empty)')} | source={item.get('source', '') or '(none)'} | href={item.get('href', '') or '(none)'}"
            )
    elif resources:
        lines.append("资源候选：")
        for index, item in enumerate(resources[:12], start=1):
            lines.append(
                f"{index}. ext={item.get('ext', '') or '(none)'} | href={item.get('href', '') or '(none)'} | reason={','.join(item.get('reason', [])) or '(none)'}"
            )
    return "\n".join(lines)


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


def select_node(state: WorkflowState) -> WorkflowState:
    if state.get("probe_only"):
        print("探测模式已开启，跳过选择与处理。")
        return {
            **state,
            "selected_materials": [],
            "should_exit": True,
        }

    materials = list(state["discovery_snapshot"].get("materials", []))
    if not materials:
        print("没有发现可选课件。")
        return {
            **state,
            "selected_materials": [],
            "no_materials_found": True,
            "should_exit": True,
        }

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


def open_selected_node(state: WorkflowState) -> WorkflowState:
    if state.get("should_exit"):
        return {**state, "opened_materials": []}

    session = state["session"]
    selected = state.get("selected_materials", [])
    preferred_section = str(state.get("discovery_snapshot", {}).get("active_section", "")).strip()
    opened: list[dict[str, Any]] = []

    for item in selected:
        file_name = str(item.get("file_name", "")).strip()
        if not file_name:
            continue
        print(f"正在解析课件打开目标: {file_name}")
        opened_item = {**item, **session.open_material(item, preferred_section=preferred_section)}
        opened.append(opened_item)
        mode = opened_item.get("open_mode", "")
        target_url = opened_item.get("target_url", "")
        if target_url:
            print(f"  -> {mode}: {target_url}")
        else:
            print(f"  -> {mode or 'unresolved'}")
            trace = opened_item.get("open_trace", [])
            if trace:
                print(f"  -> trace: {' -> '.join(trace)}")

    return {**state, "opened_materials": opened}


def process_selected_node(state: WorkflowState) -> WorkflowState:
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

        print(f"正在保存课件并生成笔记: {file_name}")
        try:
            candidate_pdf_name = _candidate_material_filename(item)
            candidate_stem = Path(candidate_pdf_name).stem
            candidate_note_path = notes_dir / f"{candidate_stem}.md"
            existing_attachment = _find_supported_existing_attachment(attachments_dir, candidate_stem)

            if candidate_note_path.exists() and existing_attachment is not None and not overwrite_existing:
                processed.append(
                    {
                        **item,
                        "status": "skipped-existing-note",
                        "pdf_path": str(existing_attachment),
                        "note_path": str(candidate_note_path),
                    }
                )
                print(f"正在跳过已存在笔记: {file_name}")
                print(f"  -> pdf exists: {existing_attachment}")
                print(f"  -> note exists: {candidate_note_path}")
                continue

            pdf_bytes = session.fetch_material_bytes(item)
            safe_pdf_name = _resolved_material_filename(item, pdf_bytes)
            pdf_path = attachments_dir / safe_pdf_name
            note_path = notes_dir / f"{Path(safe_pdf_name).stem}.md"
            relative_slide_path = os.path.relpath(pdf_path, start=notes_dir)
            slide_link = relative_slide_path.replace(os.sep, "/")
            note_exists = note_path.exists()
            pdf_exists = pdf_path.exists()

            if note_exists and not overwrite_existing:
                if not pdf_exists:
                    pdf_path.write_bytes(pdf_bytes)
                    print(f"  -> pdf: {pdf_path}")
                processed.append(
                    {
                        **item,
                        "status": "skipped-existing-note",
                        "pdf_path": str(pdf_path),
                        "note_path": str(note_path),
                    }
                )
                print(f"正在跳过已存在笔记: {file_name}")
                if pdf_exists:
                    print(f"  -> pdf exists: {pdf_path}")
                print(f"  -> note exists: {note_path}")
                continue

            pdf_path.write_bytes(pdf_bytes)
            print(f"  -> pdf: {pdf_path}")
            pdf_text, extract_warning = _extract_pdf_text(pdf_path)
            if extract_warning:
                print(f"  -> pdf text warning: {extract_warning}")
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
                    "extract_warning": extract_warning or "",
                }
            )
            print(f"  -> note: {note_path}")
        except Exception as exc:
            processed.append({**item, "status": f"error: {exc}"})
            print(f"  -> error: {exc}")

    return {**state, "processed_materials": processed}


def finalize_node(state: WorkflowState) -> WorkflowState:
    if state.get("should_exit"):
        if state.get("probe_only"):
            return {
                **state,
                "final_message": f"探测完成。调试快照已写入 {state.get('json_path', '')}",
            }
        if state.get("no_materials_found"):
            return {
                **state,
                "final_message": "没有发现可选课件。已结束本轮，建议检查页面结构或调试快照后重试。",
            }
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
    graph = StateGraph(WorkflowState)
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


def run_workflow_once(
    session: LiveCourseSession,
    logged_in: bool = False,
    login_snapshot: dict[str, Any] | None = None,
) -> WorkflowState:
    graph = build_graph()
    initial_state: WorkflowState = {"session": session, "logged_in": logged_in}
    if login_snapshot is not None:
        initial_state["login_snapshot"] = login_snapshot
    result = graph.invoke(initial_state)
    return result


def main() -> None:
    with LiveCourseSession(headless=False) as session:
        try:
            snapshot, authenticated, reason = session.open_course_page_with_auth_check()
        except Exception:
            snapshot = None
            authenticated = False
            reason = "无法直接访问课程页"

        if authenticated:
            login_snapshot = snapshot
            print(f"课程页可直接访问：{snapshot['url']}")
            print(f"页面标题：{snapshot['title']}")
            print(f"判定依据：{reason}")
        else:
            print("课程页当前不能直接处理，转入手动登录流程。")
            if snapshot is not None:
                print(f"当前页面：{snapshot['url']}")
                print(f"页面标题：{snapshot['title']}")
                print(f"判定依据：{reason}")
            login_snapshot = session.wait_for_manual_login()
        while True:
            result = run_workflow_once(
                session=session,
                logged_in=True,
                login_snapshot=login_snapshot,
            )
            print(result.get("final_message", "运行完成。"))
            if result.get("should_exit"):
                break


if __name__ == "__main__":
    main()
