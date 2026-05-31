#!/usr/bin/env python3
"""
课程站点 live session 辅助模块。

设计约束：
- 不尝试破解滑块
- 不尝试跨进程恢复登录态
- 只在同一个浏览器 context/page 内复用人工完成后的登录会话
"""
from __future__ import annotations

import base64
import os
import re
import time
from pathlib import Path
from typing import Any, TypedDict
from urllib.parse import urljoin, urlsplit, urlunsplit

from dotenv import load_dotenv
from playwright.sync_api import Browser, BrowserContext, Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

WORKFLOW_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = WORKFLOW_ROOT.parent

if (WORKFLOW_ROOT / ".env").exists():
    load_dotenv(WORKFLOW_ROOT / ".env")
else:
    load_dotenv(PROJECT_ROOT / ".env")

LOGIN_PASSWORD_SELECTORS = [
    "input[type='password']",
    "input[name*='password' i]",
    "input[id*='password' i]",
]
LOGIN_IDENTITY_SELECTORS = [
    "input[name*='user' i]",
    "input[id*='user' i]",
    "input[name*='email' i]",
    "input[id*='email' i]",
    "input[type='email']",
]
LOGIN_ACTION_SELECTORS = [
    "form button[type='submit']",
    "form input[type='submit']",
    "button:has-text('登录')",
    "a:has-text('登录')",
    "button:has-text('Sign in')",
    "button:has-text('Log in')",
]
COURSE_PAGE_HINT_SELECTORS = [
    "[data-testid*='course']",
    ".course",
    ".course-title",
    ".ant-breadcrumb",
    ".ant-list",
    ".ant-table",
    ".breadcrumb",
    "#page",
    "#region-main",
]
GENERIC_PAGE_HINT_SELECTORS = [
    "[role='main']",
    "main",
]
LOGOUT_HINT_SELECTORS = [
    "a[href*='logout']",
    "button:has-text('退出登录')",
    "a:has-text('退出登录')",
    "button:has-text('登出')",
    "a:has-text('登出')",
    "button:has-text('Log out')",
    "a:has-text('Log out')",
    "button:has-text('Logout')",
    "a:has-text('Logout')",
    "button:has-text('Sign out')",
    "a:has-text('Sign out')",
]
DEFAULT_LOGGED_OUT_TEXT = [
    "尚未登录",
    "您尚未登录",
    "请先登录",
    "统一身份认证",
    "please log in",
    "you are not logged in",
    "not logged in",
]
DEFAULT_INACCESSIBLE_TEXT = [
    "access denied",
    "forbidden",
    "permission denied",
    "unauthorized",
    "403",
    "404",
    "not found",
    "page not found",
    "无权访问",
    "禁止访问",
    "未找到",
    "页面不存在",
]
DEFAULT_PUBLIC_PAGE_TEXT = [
    "lecture",
    "lectures",
    "note",
    "notes",
    "schedule",
    "syllabus",
    "assignment",
    "assignments",
    "lab",
    "labs",
    "slides",
    "resource",
    "resources",
    "课件",
    "讲义",
    "课程",
    "作业",
    "实验",
    "资源",
    "大纲",
]
DEFAULT_AUTH_COOKIE_KEYWORDS = [
    "session",
    "sess",
    "token",
    "auth",
    "jwt",
    "moodle",
]
RESOURCE_SECTION_KEYWORDS = [
    "课件",
    "讲义",
    "资料",
    "作业",
    "实验",
    "lecture",
    "slides",
    "notes",
    "material",
    "resource",
]


class PageSnapshot(TypedDict):
    url: str
    title: str
    text_preview: str
    login_form_present: bool
    course_hints_found: list[str]
    generic_page_hints_found: list[str]
    logout_hints_found: list[str]
    success_text_hints_found: list[str]
    public_page_text_hints_found: list[str]
    matched_auth_cookies: list[str]
    explicit_logged_out: bool
    explicit_inaccessible: bool
    resource_link_count: int


class ResourceCandidate(TypedDict, total=False):
    href: str
    text: str
    title: str
    ext: str
    score: int
    reason: list[str]
    container_text: str


class MaterialEntry(TypedDict, total=False):
    file_name: str
    ext: str
    size: str
    uploaded_at: str
    action_label: str
    href: str
    source: str
    row_text: str
    link_text: str
    target_url: str
    target_title: str
    open_mode: str
    suggested_filename: str
    blob_data_base64: str
    open_trace: list[str]


class DiscoverySnapshot(TypedDict):
    page: PageSnapshot
    resources: list[ResourceCandidate]
    active_section: str
    materials: list[MaterialEntry]


MATERIAL_EXTENSIONS = {"pdf", "ppt", "pptx", "doc", "docx", "xls", "xlsx", "zip", "rar", "7z"}
MATERIAL_FILENAME_PATTERN = re.compile(
    r"([A-Za-z0-9_\u4e00-\u9fa5][A-Za-z0-9_\u4e00-\u9fa5 .()\-\[\]]{0,120}?\.(?:pdf|ppt|pptx|doc|docx|xls|xlsx|zip|rar|7z))",
    flags=re.IGNORECASE,
)


def get_course_url() -> str:
    course_url = os.getenv("COURSE_URL", "").strip()
    if not course_url:
        raise RuntimeError("缺少 COURSE_URL，请先在 .env 中配置课程页面地址")
    return course_url


def get_login_url(course_url: str | None = None) -> str:
    configured = os.getenv("COURSE_LOGIN_URL", "").strip()
    if configured:
        return configured
    target = course_url or get_course_url()
    parts = urlsplit(target)
    return urlunsplit((parts.scheme, parts.netloc, "/login", "", ""))


def _env_list(name: str, defaults: list[str] | None = None) -> list[str]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return list(defaults or [])
    return [item.strip() for item in re.split(r"[,;\n]", raw) if item.strip()]


def _looks_like_login_url(url: str) -> bool:
    lowered = url.lower()
    return "/login" in lowered or "signin" in lowered or "sign-in" in lowered or "sso" in lowered


def _looks_like_material_url(url: str) -> bool:
    lowered = url.lower()
    if any(lowered.endswith(f".{ext}") or f".{ext}?" in lowered for ext in MATERIAL_EXTENSIONS):
        return True
    return (
        "/mod/resource/view.php" in lowered
        or "download" in lowered
        or "pluginfile.php" in lowered
        or "oss-cn-" in lowered
    )


def _page_has_any_selector(page: Page, selectors: list[str]) -> bool:
    for selector in selectors:
        try:
            if page.locator(selector).count() > 0:
                return True
        except Exception:
            continue
    return False


def _page_hit_selectors(page: Page, selectors: list[str]) -> list[str]:
    hits: list[str] = []
    for selector in selectors:
        try:
            if page.locator(selector).count() > 0:
                hits.append(selector)
        except Exception:
            continue
    return hits


def _page_has_login_form(page: Page) -> bool:
    if _page_has_any_selector(page, LOGIN_PASSWORD_SELECTORS):
        return True
    if _looks_like_login_url(page.url):
        return _page_has_any_selector(page, LOGIN_IDENTITY_SELECTORS) and _page_has_any_selector(page, LOGIN_ACTION_SELECTORS)
    return False


def _matched_tokens(text: str, tokens: list[str]) -> list[str]:
    lowered = text.lower()
    hits: list[str] = []
    for token in tokens:
        normalized = token.strip()
        if normalized and normalized.lower() in lowered:
            hits.append(normalized)
    return hits


def _matched_auth_cookies(page: Page) -> list[str]:
    keywords = [item.lower() for item in _env_list("COURSE_AUTH_COOKIE_NAMES", DEFAULT_AUTH_COOKIE_KEYWORDS)]
    try:
        cookies = page.context.cookies([page.url])
    except Exception:
        cookies = []

    matches: list[str] = []
    for cookie in cookies:
        name = str(cookie.get("name", "")).strip()
        lowered = name.lower()
        if lowered and any(keyword in lowered for keyword in keywords):
            matches.append(name)
    return sorted(set(matches))


def _normalize_text(text: str, max_len: int = 240) -> str:
    normalized = re.sub(r"\s+", " ", text).strip()
    return normalized[:max_len]


def _extract_size_from_text(text: str) -> str:
    match = re.search(r"(\d+(?:\.\d+)?\s*(?:KB|MB|GB))", text, flags=re.IGNORECASE)
    return match.group(1) if match else ""


def _extract_time_from_text(text: str) -> str:
    match = re.search(r"(20\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2})", text)
    return match.group(1) if match else ""


def _looks_like_material_name(text: str, ext: str) -> bool:
    lowered = text.lower()
    return ext in MATERIAL_EXTENSIONS and f".{ext}" in lowered


def _extract_filename_candidates(text: str) -> list[str]:
    if not text:
        return []

    seen: set[str] = set()
    candidates: list[str] = []
    for match in MATERIAL_FILENAME_PATTERN.finditer(text):
        candidate = match.group(1).strip()
        lowered = candidate.lower()
        if lowered.startswith("pdf "):
            continue
        if re.search(r"\b\d+(?:\.\d+)?\s*(?:kb|mb|gb)\b", lowered):
            continue
        if lowered in seen:
            continue
        seen.add(lowered)
        candidates.append(candidate)
    return candidates


def _clean_href_filename(path_name: str) -> str:
    lowered = path_name.lower()
    match = re.match(r"^[0-9a-f]{24,}_(.+\.(?:pdf|ppt|pptx|doc|docx|xls|xlsx|zip|rar|7z))$", path_name, flags=re.IGNORECASE)
    if match:
        return match.group(1)
    return path_name if any(lowered.endswith(f".{ext}") for ext in MATERIAL_EXTENSIONS) else ""


def _pick_material_filename_for_resource(href: str, text: str, title: str, context: str, ext: str) -> str:
    path_name = Path(urlsplit(href).path).name
    cleaned_path_name = _clean_href_filename(path_name)

    context_candidates = _extract_filename_candidates(context)
    if cleaned_path_name:
        lowered_path = cleaned_path_name.lower()
        for candidate in context_candidates:
            if lowered_path.endswith(candidate.lower()):
                return candidate
        if _extract_filename_candidates(cleaned_path_name):
            return _extract_filename_candidates(cleaned_path_name)[0]
        return cleaned_path_name

    for source in (text, title):
        source_candidates = _extract_filename_candidates(source)
        if len(source_candidates) == 1:
            return source_candidates[0]

    if len(context_candidates) == 1:
        return context_candidates[0]

    return ""


def _page_resource_link_count(page: Page) -> int:
    try:
        count = page.evaluate(
            r"""
            () => {
              const filePattern = /\.(pdf|ppt|pptx|doc|docx|xls|xlsx|zip|rar|7z)(?:$|\?)/i;
              let total = 0;
              for (const link of document.querySelectorAll("a[href]")) {
                const href = (link.getAttribute("href") || "").trim();
                const text = (link.innerText || link.textContent || "").trim();
                const title = (link.getAttribute("title") || "").trim();
                const combined = `${href} ${text} ${title}`.toLowerCase();
                if (
                  filePattern.test(href) ||
                  combined.includes("/mod/resource/view.php") ||
                  combined.includes("download")
                ) {
                  total += 1;
                }
              }
              return total;
            }
            """
        )
    except Exception:
        return 0
    try:
        return int(count)
    except Exception:
        return 0


def _materials_from_resource_candidates(resources: list[ResourceCandidate]) -> list[MaterialEntry]:
    materials: list[MaterialEntry] = []
    seen_keys: set[tuple[str, str]] = set()

    for item in resources:
        href = str(item.get("href", "")).strip()
        ext = str(item.get("ext", "")).strip().lower()
        text = _normalize_text(str(item.get("text", "")), max_len=300)
        title = _normalize_text(str(item.get("title", "")), max_len=300)
        context = _normalize_text(str(item.get("container_text", "")), max_len=500)

        if not href or ext not in MATERIAL_EXTENSIONS:
            continue

        file_name = _pick_material_filename_for_resource(href, text, title, context, ext)
        if not file_name:
            continue

        filename_count_in_context = len(_extract_filename_candidates(context))

        key = (file_name.lower(), href)
        if key in seen_keys:
            continue
        seen_keys.add(key)

        materials.append(
            {
                "file_name": file_name,
                "ext": ext,
                "size": _extract_size_from_text(context) if filename_count_in_context <= 1 else "",
                "uploaded_at": _extract_time_from_text(context),
                "action_label": "direct-link",
                "href": href,
                "source": "resource-candidate",
                "row_text": context,
                "link_text": text or title,
            }
        )

    return materials


def _merge_materials(primary: list[MaterialEntry], secondary: list[MaterialEntry]) -> list[MaterialEntry]:
    merged: list[MaterialEntry] = []
    seen_keys: set[tuple[str, str]] = set()

    for item in [*primary, *secondary]:
        file_name = str(item.get("file_name", "")).strip().lower()
        href = str(item.get("href", "")).strip()
        key = (file_name, href)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        merged.append(item)

    return merged


def snapshot_page(page: Page) -> PageSnapshot:
    body_text = page.inner_text("body") if page.locator("body").count() else ""
    title = page.title()
    preview = _normalize_text(body_text, max_len=1000)
    success_selectors = COURSE_PAGE_HINT_SELECTORS + _env_list("COURSE_AUTH_SUCCESS_SELECTORS")
    public_text_hits = _matched_tokens(f"{title}\n{preview}", _env_list("COURSE_AUTH_PUBLIC_TEXT", DEFAULT_PUBLIC_PAGE_TEXT))
    success_text_hits = _matched_tokens(f"{title}\n{preview}", _env_list("COURSE_AUTH_SUCCESS_TEXT"))
    logged_out_text_hits = _matched_tokens(f"{title}\n{preview}", _env_list("COURSE_AUTH_LOGGED_OUT_TEXT", DEFAULT_LOGGED_OUT_TEXT))
    inaccessible_text_hits = _matched_tokens(f"{title}\n{preview}", _env_list("COURSE_AUTH_INACCESSIBLE_TEXT", DEFAULT_INACCESSIBLE_TEXT))
    return {
        "url": page.url,
        "title": title,
        "text_preview": preview,
        "login_form_present": _page_has_login_form(page),
        "course_hints_found": _page_hit_selectors(page, success_selectors),
        "generic_page_hints_found": _page_hit_selectors(page, GENERIC_PAGE_HINT_SELECTORS),
        "logout_hints_found": _page_hit_selectors(page, LOGOUT_HINT_SELECTORS),
        "success_text_hints_found": success_text_hits,
        "public_page_text_hints_found": public_text_hits,
        "matched_auth_cookies": _matched_auth_cookies(page),
        "explicit_logged_out": bool(logged_out_text_hits),
        "explicit_inaccessible": bool(inaccessible_text_hits),
        "resource_link_count": _page_resource_link_count(page),
    }


def evaluate_authentication(snapshot: PageSnapshot) -> tuple[bool, str]:
    reasons: list[str] = []
    if snapshot["course_hints_found"]:
        reasons.append(f"命中页面成功特征: {', '.join(snapshot['course_hints_found'][:4])}")
    if snapshot.get("generic_page_hints_found"):
        reasons.append(f"命中通用页面结构: {', '.join(snapshot['generic_page_hints_found'][:2])}")
    if snapshot["success_text_hints_found"]:
        reasons.append(f"命中成功文本: {', '.join(snapshot['success_text_hints_found'][:4])}")
    if snapshot.get("public_page_text_hints_found"):
        reasons.append(f"命中公开内容文本: {', '.join(snapshot['public_page_text_hints_found'][:4])}")
    if snapshot["logout_hints_found"]:
        reasons.append(f"命中退出登录线索: {', '.join(snapshot['logout_hints_found'][:4])}")
    if snapshot["matched_auth_cookies"]:
        reasons.append(f"命中认证 cookie: {', '.join(snapshot['matched_auth_cookies'][:4])}")
    if snapshot.get("resource_link_count", 0) > 0:
        reasons.append(f"命中资源链接: {snapshot['resource_link_count']} 个")

    if snapshot["explicit_logged_out"]:
        return False, "命中明确未登录文案"
    if snapshot.get("explicit_inaccessible"):
        return False, "命中不可访问文案"
    if snapshot["course_hints_found"] or snapshot["success_text_hints_found"] or snapshot["logout_hints_found"]:
        return True, "；".join(reasons)
    if _looks_like_login_url(snapshot["url"]) and snapshot["login_form_present"]:
        return False, "当前 URL 像登录页，且检测到登录表单"
    if snapshot["matched_auth_cookies"] and not snapshot["login_form_present"]:
        return True, "；".join(reasons)
    if snapshot["login_form_present"]:
        return False, "检测到登录表单"
    if (
        not _looks_like_login_url(snapshot["url"])
        and (
            snapshot.get("resource_link_count", 0) > 0
            and bool(snapshot.get("public_page_text_hints_found"))
        )
    ):
        return True, "；".join(reasons) or "未检测到登录要求，按公开内容页处理"
    if not _looks_like_login_url(snapshot["url"]):
        return False, "已离开登录页，但缺少课程页或公开内容页信号"
    return False, "仍像登录页，且没有明显成功信号"


def is_authenticated_snapshot(snapshot: PageSnapshot) -> bool:
    return evaluate_authentication(snapshot)[0]


def _has_course_page_signals(snapshot: PageSnapshot) -> bool:
    if snapshot["course_hints_found"] or snapshot["success_text_hints_found"]:
        return True
    return bool(snapshot.get("resource_link_count", 0) > 0 and snapshot.get("public_page_text_hints_found"))


def _has_explicit_login_url() -> bool:
    return bool(os.getenv("COURSE_LOGIN_URL", "").strip())


def _is_manual_login_entry_snapshot(snapshot: PageSnapshot) -> bool:
    return bool(
        snapshot["login_form_present"]
        or snapshot["explicit_logged_out"]
        or _looks_like_login_url(snapshot["url"])
    )


class LiveCourseSession:
    def __init__(self, headless: bool = False) -> None:
        self.headless = headless
        self._playwright = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None

    def __enter__(self) -> "LiveCourseSession":
        self._playwright = sync_playwright().start()
        self.browser = self._playwright.chromium.launch(headless=self.headless)
        self.context = self.browser.new_context()
        self.page = self.context.new_page()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.context is not None:
            self.context.close()
        if self.browser is not None:
            self.browser.close()
        if self._playwright is not None:
            self._playwright.stop()

    def open_login(self) -> None:
        assert self.page is not None
        target_url = get_login_url() if _has_explicit_login_url() else get_course_url()
        self.page.goto(target_url, wait_until="domcontentloaded")
        self.page.wait_for_load_state("networkidle")

    def open_course_page_with_auth_check(self) -> tuple[PageSnapshot, bool, str]:
        assert self.page is not None
        self.page.goto(get_course_url(), wait_until="domcontentloaded")
        self.page.wait_for_load_state("networkidle")
        snapshot = snapshot_page(self.page)
        authenticated, reason = evaluate_authentication(snapshot)
        return snapshot, authenticated, reason

    def snapshot_current_page_with_auth_check(self) -> tuple[PageSnapshot, bool, str]:
        assert self.page is not None
        snapshot = snapshot_page(self.page)
        authenticated, reason = evaluate_authentication(snapshot)
        return snapshot, authenticated, reason

    def wait_for_manual_login(self) -> PageSnapshot:
        assert self.page is not None

        print("正在打开浏览器，等待你手动登录...\n")
        print("完成滑块和登录后，回终端按 Enter。")
        print("只有同一个页面真正进入课程页，才继续后续工作流。\n")

        initial_snapshot: PageSnapshot | None = None
        if self.page.url not in {"", "about:blank"}:
            try:
                initial_snapshot = snapshot_page(self.page)
            except Exception:
                initial_snapshot = None

        if initial_snapshot is None or not (
            _is_manual_login_entry_snapshot(initial_snapshot) or _has_course_page_signals(initial_snapshot)
        ):
            self.open_login()
        while True:
            try:
                input()
            except EOFError:
                raise RuntimeError("登录流程被中断")

            try:
                current_snapshot, current_authenticated, current_reason = self.snapshot_current_page_with_auth_check()
            except Exception as exc:
                print(f"校验当前页面失败：{exc}")
                print("回到浏览器继续完成登录，再按一次 Enter。\n")
                continue

            if current_authenticated and _has_course_page_signals(current_snapshot):
                print(f"登录成功，课程页可访问：{current_snapshot['url']}")
                print(f"页面标题：{current_snapshot['title']}")
                print(f"判定依据：{current_reason}")
                return current_snapshot

            try:
                snapshot, authenticated, reason = self.open_course_page_with_auth_check()
            except Exception as exc:
                print(f"校验课程页失败：{exc}")
                print("回到浏览器继续完成登录，再按一次 Enter。\n")
                continue

            if authenticated:
                print(f"登录成功，课程页可访问：{snapshot['url']}")
                print(f"页面标题：{snapshot['title']}")
                print(f"判定依据：{reason}")
                return snapshot

            print(f"尚未确认登录成功，当前页面：{current_snapshot['url']}")
            print(f"页面标题：{current_snapshot['title']}")
            print(f"判定依据：{current_reason}")
            if snapshot["url"] != current_snapshot["url"]:
                print(f"回跳课程页后仍不可用：{snapshot['url']}")
                print(f"课程页标题：{snapshot['title']}")
                print(f"课程页判定依据：{reason}")
            print("回到浏览器继续完成登录，再按一次 Enter。\n")

    def ensure_course_page(self) -> PageSnapshot:
        snapshot, authenticated, reason = self.open_course_page_with_auth_check()
        if not authenticated:
            raise RuntimeError(f"当前 live session 已失效，需要重新手动登录：{reason}")
        return snapshot

    def _open_section(self, section_name: str) -> bool:
        assert self.page is not None
        candidates = [
            self.page.get_by_text(section_name, exact=True),
            self.page.locator(f"text={section_name}"),
        ]
        for locator in candidates:
            try:
                if locator.count() == 0:
                    continue
                locator.first.click(timeout=3000)
                self.page.wait_for_load_state("networkidle")
                time.sleep(0.5)
                return True
            except Exception:
                continue
        return False

    def _restore_section(self, preferred_section: str | None = None) -> None:
        section = (preferred_section or "").strip()
        if section and section != "course-root":
            self._open_section(section)

    def inspect_course_page(self) -> DiscoverySnapshot:
        assert self.page is not None
        snapshot = self.ensure_course_page()
        active_section = "course-root"

        for section_name in ["课件", "资料", "任务"]:
            if self._open_section(section_name):
                snapshot = snapshot_page(self.page)
                active_section = section_name
                break

        materials = self._extract_material_rows(active_section)
        resources = self.page.evaluate(_resource_discovery_js(), {"keywords": RESOURCE_SECTION_KEYWORDS})
        normalized: list[ResourceCandidate] = []
        for item in resources:
            href = str(item.get("href", "")).strip()
            ext = str(item.get("ext", "")).lower()
            if href and href.startswith("/"):
                href = urljoin(snapshot["url"], href)
            normalized.append(
                {
                    "href": href,
                    "text": _normalize_text(str(item.get("text", ""))),
                    "title": _normalize_text(str(item.get("title", ""))),
                    "ext": ext,
                    "score": int(item.get("score", 0)),
                    "reason": list(item.get("reason", [])),
                    "container_text": _normalize_text(str(item.get("container_text", "")), max_len=400),
                }
            )
        normalized.sort(key=lambda item: item.get("score", 0), reverse=True)
        materials = _merge_materials(materials, _materials_from_resource_candidates(normalized))
        return {
            "page": snapshot,
            "resources": normalized,
            "active_section": active_section,
            "materials": materials,
        }

    def _extract_material_rows(self, preferred_section: str | None = None) -> list[MaterialEntry]:
        assert self.page is not None
        rows = self.page.evaluate(_material_rows_js())
        materials: list[MaterialEntry] = []
        for row in rows:
            file_name = _normalize_text(str(row.get("file_name", "")), max_len=300)
            if not file_name:
                continue
            href_candidates = [str(item).strip() for item in row.get("href_candidates", []) if str(item).strip()]
            resolved_href = self._pick_direct_material_href(href_candidates)
            source = "dom"
            if not resolved_href:
                resolved_href = self._resolve_material_open_url(file_name, preferred_section=preferred_section)
                source = "click" if resolved_href else ""

            ext = Path(file_name).suffix.lower().lstrip(".")
            materials.append(
                {
                    "file_name": file_name,
                    "ext": ext,
                    "size": _extract_size_from_text(str(row.get("size", "")) or str(row.get("row_text", ""))),
                    "uploaded_at": _extract_time_from_text(str(row.get("uploaded_at", "")) or str(row.get("row_text", ""))),
                    "action_label": _normalize_text(str(row.get("action_label", "")), max_len=40),
                    "href": resolved_href,
                    "source": source,
                    "row_text": _normalize_text(str(row.get("row_text", "")), max_len=500),
                    "link_text": _normalize_text(str(row.get("link_text", "")), max_len=300),
                }
            )
        return materials

    def _pick_direct_material_href(self, href_candidates: list[str]) -> str:
        for href in href_candidates:
            lowered = href.lower()
            if any(lowered.endswith(f".{ext}") or f".{ext}?" in lowered for ext in ["pdf", "ppt", "pptx", "doc", "docx", "xls", "xlsx"]):
                return href
            if "oss-cn-" in lowered or "download" in lowered or "/mod/resource/view.php" in lowered:
                return href
        return ""

    def _resolve_material_open_url(self, file_name: str, preferred_section: str | None = None) -> str:
        assert self.page is not None
        row_locator = self.page.locator("tr, .ant-table-row, li, .ant-list-item, .ant-card, .activity, .resource").filter(has_text=file_name).first
        if row_locator.count() == 0:
            return ""

        # Try reading href-like attributes from the row after narrowing by file name.
        hrefs = row_locator.evaluate(
            """
            (node) => {
              const values = [];
              for (const el of node.querySelectorAll('[href], [data-href], [data-url]')) {
                values.push(el.getAttribute('href') || el.getAttribute('data-href') || el.getAttribute('data-url') || '');
              }
              return values.filter(Boolean);
            }
            """
        )
        direct = self._pick_direct_material_href([str(item) for item in hrefs])
        if direct:
            return direct

        open_locator = row_locator.get_by_text("打开", exact=True).first
        if open_locator.count() == 0:
            link_locator = row_locator.locator("a[href]").first
            if link_locator.count() == 0:
                return ""
            try:
                href = (link_locator.get_attribute("href") or "").strip()
                if href:
                    return urljoin(self.page.url, href)
            except Exception:
                return ""
            return ""

        before_url = self.page.url
        captured_url = ""

        try:
            with self.page.expect_popup(timeout=3000) as popup_info:
                open_locator.click(timeout=3000)
            popup = popup_info.value
            popup.wait_for_load_state("domcontentloaded", timeout=5000)
            captured_url = popup.url
            popup.close()
        except PlaywrightTimeoutError:
            try:
                open_locator.click(timeout=3000)
                self.page.wait_for_load_state("domcontentloaded", timeout=5000)
                current = self.page.url
                if current != before_url:
                    captured_url = current
                    self.page.go_back(wait_until="domcontentloaded", timeout=5000)
                    self.page.wait_for_load_state("networkidle", timeout=5000)
                    self._restore_section(preferred_section)
            except Exception:
                return ""
        except Exception:
            return ""

        return captured_url

    def _candidate_open_locators(self, file_name: str, discovered_href: str) -> list[Any]:
        assert self.page is not None

        locators: list[Any] = []
        if discovered_href:
            try:
                if discovered_href.startswith("/"):
                    discovered_href = urljoin(self.page.url, discovered_href)
                locators.append(self.page.locator(f'a[href="{discovered_href}"]').first)
            except Exception:
                pass

        link_text = file_name.strip()
        if link_text:
            try:
                locators.append(self.page.get_by_text(link_text, exact=True).first)
            except Exception:
                pass
            try:
                locators.append(self.page.locator("a[href]").filter(has_text=link_text).first)
            except Exception:
                pass

        return locators

    def _build_open_result(
        self,
        *,
        file_name: str,
        target_url: str,
        target_title: str,
        open_mode: str,
        suggested_filename: str = "",
        blob_data_base64: str = "",
        open_trace: list[str] | None = None,
    ) -> MaterialEntry:
        return {
            "file_name": file_name,
            "target_url": target_url,
            "target_title": target_title,
            "open_mode": open_mode,
            "suggested_filename": suggested_filename,
            "blob_data_base64": blob_data_base64,
            "open_trace": list(open_trace or []),
        }

    def _capture_post_click_result(
        self,
        *,
        file_name: str,
        before_url: str,
        preferred_section: str | None = None,
        open_trace: list[str] | None = None,
        trace_label: str = "post-click",
    ) -> MaterialEntry | None:
        assert self.page is not None

        trace = open_trace if open_trace is not None else []
        current_url = ""
        current_title = ""
        try:
            current_url = self.page.url
        except Exception:
            current_url = ""
        try:
            current_title = self.page.title()
        except Exception:
            current_title = ""

        viewer_url = self._extract_inline_pdf_url()
        if viewer_url:
            blob_data = self._read_blob_from_page(self.page, viewer_url) if viewer_url.startswith("blob:") else ""
            trace.append(f"{trace_label}:inline-viewer:{viewer_url}")
            return self._build_open_result(
                file_name=file_name,
                target_url=viewer_url,
                target_title=current_title,
                open_mode="inline-viewer",
                suggested_filename=Path(urlsplit(viewer_url).path).name,
                blob_data_base64=blob_data,
                open_trace=trace,
            )

        if current_url and current_url != before_url:
            if not _looks_like_material_url(current_url):
                trace.append(f"{trace_label}:same-tab-ignored:{current_url}")
                return None
            blob_data = self._read_blob_from_page(self.page, current_url) if current_url.startswith("blob:") else ""
            trace.append(f"{trace_label}:same-tab:{current_url}")
            result = self._build_open_result(
                file_name=file_name,
                target_url=current_url,
                target_title=current_title,
                open_mode="same-tab",
                suggested_filename=Path(urlsplit(current_url).path).name,
                blob_data_base64=blob_data,
                open_trace=trace,
            )
            try:
                self.page.go_back(wait_until="domcontentloaded", timeout=5000)
                self.page.wait_for_load_state("networkidle", timeout=5000)
                self._restore_section(preferred_section)
                trace.append(f"{trace_label}:restore-ok")
            except Exception:
                trace.append(f"{trace_label}:restore-failed")
            return result

        return None

    def _build_network_response_result(
        self,
        *,
        file_name: str,
        target_url: str,
        preferred_section: str | None = None,
        open_trace: list[str] | None = None,
    ) -> MaterialEntry:
        trace = open_trace if open_trace is not None else []
        blob_data = self._read_blob_from_page(self.page, target_url) if target_url.startswith("blob:") else ""
        self.ensure_course_page()
        self._restore_section(preferred_section)
        trace.append(f"response:{target_url}")
        return self._build_open_result(
            file_name=file_name,
            target_url=target_url,
            target_title="",
            open_mode="network-response",
            suggested_filename=Path(urlsplit(target_url).path).name,
            blob_data_base64=blob_data,
            open_trace=trace,
        )

    def _material_row_locator(self, file_name: str):
        assert self.page is not None
        return self.page.locator(
            "tbody tr, .ant-table-tbody > tr, .ant-list-item, li, .activity, .resource"
        ).filter(has_text=file_name).first

    def _find_row_open_locator(self, row_locator):
        open_locator = row_locator.get_by_text("打开", exact=True).first
        if open_locator.count() > 0:
            return open_locator
        link_locator = row_locator.locator("a[href]").first
        if link_locator.count() > 0:
            return link_locator
        return None

    def _open_from_row(
        self,
        row_locator,
        file_name: str,
        preferred_section: str | None = None,
        open_trace: list[str] | None = None,
        direct_mode: str = "direct-href",
    ) -> MaterialEntry | None:
        trace = open_trace if open_trace is not None else []
        if row_locator.count() == 0:
            trace.append("row:not-found")
            return None

        href_candidates = row_locator.evaluate(
            """
            (node) => {
              const values = [];
              for (const el of node.querySelectorAll('[href], [data-href], [data-url]')) {
                values.push(el.getAttribute('href') || el.getAttribute('data-href') || el.getAttribute('data-url') || '');
              }
              return values.filter(Boolean);
            }
            """
        )
        direct_href = self._pick_direct_material_href([str(item).strip() for item in href_candidates if str(item).strip()])
        if direct_href:
            resolved_href = urljoin(self.page.url, direct_href)
            suggested_name = Path(urlsplit(direct_href).path).name
            if "/mod/resource/view.php" in direct_href.lower():
                suggested_name = ""
            trace.append(f"row:direct:{direct_href}")
            return self._build_open_result(
                file_name=file_name,
                target_url=resolved_href,
                target_title="",
                open_mode=direct_mode,
                suggested_filename=suggested_name,
                open_trace=trace,
            )

        open_locator = self._find_row_open_locator(row_locator)
        if open_locator is None:
            trace.append("row:missing-open-locator")
            return None

        trace.append("row:locator")
        return self._open_via_locator(
            open_locator,
            file_name,
            preferred_section=preferred_section,
            open_trace=trace,
        )

    def _open_via_locator(
        self,
        locator,
        file_name: str,
        preferred_section: str | None = None,
        open_trace: list[str] | None = None,
    ) -> MaterialEntry | None:
        assert self.page is not None
        assert self.context is not None

        trace = open_trace if open_trace is not None else []
        if locator.count() == 0:
            trace.append("locator:missing")
            return None

        before_url = self.page.url
        captured_responses: list[str] = []

        def track_response(response) -> None:
            try:
                url = response.url
                ctype = (response.headers or {}).get("content-type", "").lower()
                if "application/pdf" in ctype or ".pdf" in url.lower():
                    captured_responses.append(url)
            except Exception:
                pass

        self.page.on("response", track_response)
        try:
            try:
                trace.append("try:popup")
                with self.context.expect_page(timeout=5000) as page_info:
                    locator.click(timeout=3000)
                popup = page_info.value
                popup.wait_for_load_state("domcontentloaded", timeout=5000)
                target_url = popup.url
                target_title = popup.title()
                blob_data = self._read_blob_from_page(popup, target_url) if target_url.startswith("blob:") else ""
                popup.close()
                trace.append(f"popup:{target_url or '(empty)'}")
                return self._build_open_result(
                    file_name=file_name,
                    target_url=target_url,
                    target_title=target_title,
                    open_mode="popup",
                    suggested_filename=Path(urlsplit(target_url).path).name,
                    blob_data_base64=blob_data,
                    open_trace=trace,
                )
            except PlaywrightTimeoutError:
                trace.append("popup:timeout")
                result = self._capture_post_click_result(
                    file_name=file_name,
                    before_url=before_url,
                    preferred_section=preferred_section,
                    open_trace=trace,
                    trace_label="popup-timeout",
                )
                if result is not None:
                    return result
                if captured_responses:
                    return self._build_network_response_result(
                        file_name=file_name,
                        target_url=captured_responses[-1],
                        preferred_section=preferred_section,
                        open_trace=trace,
                    )
            except Exception:
                trace.append("popup:error")
                result = self._capture_post_click_result(
                    file_name=file_name,
                    before_url=before_url,
                    preferred_section=preferred_section,
                    open_trace=trace,
                    trace_label="popup-error",
                )
                if result is not None:
                    return result

            try:
                trace.append("try:download")
                with self.page.expect_download(timeout=5000) as download_info:
                    locator.click(timeout=3000)
                download = download_info.value
                trace.append(f"download:{download.url or '(empty)'}")
                return self._build_open_result(
                    file_name=file_name,
                    target_url=download.url,
                    target_title="",
                    open_mode="download",
                    suggested_filename=download.suggested_filename,
                    open_trace=trace,
                )
            except PlaywrightTimeoutError:
                trace.append("download:timeout")
                result = self._capture_post_click_result(
                    file_name=file_name,
                    before_url=before_url,
                    preferred_section=preferred_section,
                    open_trace=trace,
                    trace_label="download-timeout",
                )
                if result is not None:
                    return result
                if captured_responses:
                    return self._build_network_response_result(
                        file_name=file_name,
                        target_url=captured_responses[-1],
                        preferred_section=preferred_section,
                        open_trace=trace,
                    )
            except Exception:
                trace.append("download:error")
                result = self._capture_post_click_result(
                    file_name=file_name,
                    before_url=before_url,
                    preferred_section=preferred_section,
                    open_trace=trace,
                    trace_label="download-error",
                )
                if result is not None:
                    return result

            try:
                trace.append("try:same-tab")
                locator.click(timeout=3000)
                try:
                    self.page.wait_for_load_state("domcontentloaded", timeout=5000)
                except Exception:
                    pass
                result = self._capture_post_click_result(
                    file_name=file_name,
                    before_url=before_url,
                    preferred_section=preferred_section,
                    open_trace=trace,
                    trace_label="same-tab",
                )
                if result is not None:
                    return result
            except Exception:
                trace.append("same-tab:error")
                pass

            if captured_responses:
                return self._build_network_response_result(
                    file_name=file_name,
                    target_url=captured_responses[-1],
                    preferred_section=preferred_section,
                    open_trace=trace,
                )

            trace.append("open:unresolved")
            return None
        finally:
            try:
                self.page.remove_listener("response", track_response)
            except Exception:
                pass

    def open_material(self, material: MaterialEntry, preferred_section: str | None = None) -> MaterialEntry:
        assert self.page is not None
        assert self.context is not None

        file_name = str(material.get("file_name", "")).strip()
        discovered_href = str(material.get("href", "")).strip()
        discovered_source = str(material.get("source", "")).strip()
        open_trace: list[str] = []

        if discovered_href and not discovered_href.startswith("blob:"):
            resolved_href = urljoin(self.page.url, discovered_href) if discovered_href.startswith("/") else discovered_href
            suggested_name = Path(urlsplit(resolved_href).path).name
            if "/mod/resource/view.php" in resolved_href.lower():
                suggested_name = ""
            open_trace.append(f"direct-href:{resolved_href}")
            return self._build_open_result(
                file_name=file_name,
                target_url=resolved_href,
                target_title="",
                open_mode="discovered-href" if discovered_source else "direct-href",
                suggested_filename=suggested_name,
                open_trace=open_trace,
            )

        for locator in self._candidate_open_locators(file_name, discovered_href):
            attempt_trace = [*open_trace, "phase:current-page", "strategy:discovered-locator"]
            opened = self._open_via_locator(
                locator,
                file_name,
                preferred_section=preferred_section,
                open_trace=attempt_trace,
            )
            if opened is not None:
                return opened
            open_trace = attempt_trace

        row_locator = self._material_row_locator(file_name)
        attempt_trace = [*open_trace, "phase:current-page", "strategy:row-fallback"]
        opened = self._open_from_row(
            row_locator,
            file_name,
            preferred_section=preferred_section,
            open_trace=attempt_trace,
            direct_mode="row-direct-href",
        )
        if opened is not None:
            return opened
        open_trace = attempt_trace

        self.ensure_course_page()
        self._restore_section(preferred_section)

        for locator in self._candidate_open_locators(file_name, discovered_href):
            attempt_trace = [*open_trace, "phase:restored-page", "strategy:discovered-locator"]
            opened = self._open_via_locator(
                locator,
                file_name,
                preferred_section=preferred_section,
                open_trace=attempt_trace,
            )
            if opened is not None:
                return opened
            open_trace = attempt_trace

        row_locator = self._material_row_locator(file_name)
        if row_locator.count() == 0 and preferred_section and preferred_section != "课件":
            self._open_section("课件")
            row_locator = self._material_row_locator(file_name)
        attempt_trace = [*open_trace, "phase:restored-page", "strategy:row-fallback"]
        opened = self._open_from_row(
            row_locator,
            file_name,
            preferred_section=preferred_section,
            open_trace=attempt_trace,
        )
        if opened is not None:
            return opened
        open_trace = attempt_trace
        if row_locator.count() == 0:
            open_trace.append("final:missing-row")
            return self._build_open_result(
                file_name=file_name,
                target_url="",
                target_title="",
                open_mode="missing-row",
                open_trace=open_trace,
            )

        self.ensure_course_page()
        self._restore_section(preferred_section)
        open_trace.append("final:unresolved")
        return self._build_open_result(
            file_name=file_name,
            target_url="",
            target_title="",
            open_mode="unresolved",
            open_trace=open_trace,
        )

    def _extract_inline_pdf_url(self) -> str:
        assert self.page is not None
        try:
            result = self.page.evaluate(
                """
                () => {
                  const attrs = [];
                  for (const selector of ['iframe', 'embed', 'object']) {
                    for (const el of document.querySelectorAll(selector)) {
                      const value = el.getAttribute('src') || el.getAttribute('data') || '';
                      if (value) attrs.push(value);
                    }
                  }
                  return attrs;
                }
                """
            )
        except Exception:
            result = []

        for raw in result or []:
            value = str(raw).strip()
            lowered = value.lower()
            if ".pdf" in lowered or "pdf" in lowered:
                return urljoin(self.page.url, value)

        for frame in self.page.frames:
            try:
                url = frame.url
            except Exception:
                continue
            lowered = url.lower()
            if ".pdf" in lowered or "pdf" in lowered:
                return url
        return ""

    def _read_blob_from_page(self, page: Page, blob_url: str) -> str:
        try:
            return page.evaluate(
                """
                async ({ url }) => {
                  const response = await fetch(url);
                  const buffer = await response.arrayBuffer();
                  const bytes = new Uint8Array(buffer);
                  let binary = "";
                  const chunkSize = 0x8000;
                  for (let i = 0; i < bytes.length; i += chunkSize) {
                    binary += String.fromCharCode(...bytes.subarray(i, i + chunkSize));
                  }
                  return btoa(binary);
                }
                """,
                {"url": blob_url},
            )
        except Exception:
            return ""

    def fetch_material_bytes(self, material: MaterialEntry) -> bytes:
        assert self.page is not None
        assert self.context is not None

        blob_data = str(material.get("blob_data_base64", "")).strip()
        if blob_data:
            return base64.b64decode(blob_data)

        target_url = str(material.get("target_url", "")).strip()
        if not target_url:
            raise RuntimeError(f"课件没有可用 target_url: {material.get('file_name', '')}")

        if target_url.startswith("blob:"):
            raise RuntimeError("blob URL 未在打开阶段缓存内容")

        response = self.context.request.get(target_url, fail_on_status_code=True)
        return response.body()


def _resource_discovery_js() -> str:
    return r"""
({ keywords }) => {
  const lowerKeywords = keywords.map((kw) => kw.toLowerCase());
  const nodes = [...document.querySelectorAll("a[href], button, [role='button']")];
  const results = [];
  const seen = new Set();
  const filePattern = /([A-Za-z0-9_\u4e00-\u9fa5\-. ]+\.(pdf|ppt|pptx|doc|docx|xls|xlsx|zip|rar|7z))/ig;

  const normalize = (value) => (value || "").replace(/\s+/g, " ").trim();
  const pickContainer = (node) => {
    const initial =
      node.closest("li, tr, .ant-list-item, .ant-card, .ant-collapse-item, .ant-table-row, article, section, p, div") ||
      node.parentElement ||
      node;
    let container = initial;
    let text = normalize(container?.innerText || container?.textContent || "");
    let depth = 0;
    while (container?.parentElement && text.length < 40 && depth < 4) {
      container = container.parentElement;
      text = normalize(container?.innerText || container?.textContent || "");
      depth += 1;
    }
    return { container, text };
  };

  const scoreNode = (href, text, title, containerText) => {
    let score = 0;
    const reason = [];
    const lowerHref = href.toLowerCase();
    const lowerText = text.toLowerCase();
    const lowerTitle = title.toLowerCase();
    const lowerContainer = containerText.toLowerCase();
    const extMatch = lowerHref.match(/\.([a-z0-9]+)(?:$|\?)/);
    let ext = extMatch ? extMatch[1] : "";
    const pdfLabel = lowerText === "pdf" || lowerTitle === "pdf" || /\bpdf\b/.test(lowerText) || /\bpdf\b/.test(lowerTitle);
    if (ext) {
      score += 3;
      reason.push(`ext:${ext}`);
    }
    if (pdfLabel) {
      score += 2;
      reason.push("pdf-label");
      if (!ext) ext = "pdf";
    }
    if (lowerKeywords.some((kw) => lowerText.includes(kw))) {
      score += 3;
      reason.push("text-keyword");
    }
    if (lowerKeywords.some((kw) => lowerTitle.includes(kw))) {
      score += 2;
      reason.push("title-keyword");
    }
    if (lowerKeywords.some((kw) => lowerContainer.includes(kw))) {
      score += 2;
      reason.push("container-keyword");
    }
    const fileMatches = [...containerText.matchAll(filePattern)];
    if (fileMatches.length > 0) {
      score += 4;
      reason.push("container-file");
      if (!ext) ext = fileMatches[0][2].toLowerCase();
    }
    if (lowerHref.includes("download")) {
      score += 2;
      reason.push("download-href");
    }
    return { score, reason, ext, fileMatches };
  };

  for (const node of nodes) {
    const href = node.href || node.getAttribute("data-href") || node.getAttribute("href") || "";
    const text = normalize(node.innerText || node.textContent || "");
    const title = normalize(node.getAttribute("title") || "");
    const { text: containerText } = pickContainer(node);
    const { score, reason, ext, fileMatches } = scoreNode(href, text, title, containerText);
    if (score < 3) continue;
    const detectedName = fileMatches.length > 0 ? normalize(fileMatches[0][1]) : "";
    const dedupeKey = `${href}@@${text}@@${detectedName}`;
    if (seen.has(dedupeKey)) continue;
    seen.add(dedupeKey);
    results.push({
      href,
      text: detectedName || text,
      title,
      ext: ext || (fileMatches.length > 0 ? fileMatches[0][2].toLowerCase() : ""),
      score,
      reason,
      container_text: containerText.slice(0, 500),
    });
  }

  return results;
}
"""


def _material_rows_js() -> str:
    return r"""
() => {
  const filePattern = /([A-Za-z0-9_一-龥\-. ]+\.(pdf|ppt|pptx|doc|docx|xls|xlsx))/ig;
  const normalize = (value) => (value || "").replace(/\s+/g, " ").trim();
  const rowSelectors = [
    "tbody tr",
    ".ant-table-tbody > tr",
    ".ant-list-item",
    "li.activity",
    "li.resource",
    ".activity.resource",
    "li",
  ];
  const nodes = [...new Set(rowSelectors.flatMap((selector) => [...document.querySelectorAll(selector)]))];
  const results = new Map();

  for (const node of nodes) {
    const rowText = normalize(node.innerText || node.textContent || "");
    if (!rowText) continue;

    const primaryLink = node.querySelector(".activityinstance a[href], a.aalink[href], a[href]");
    const primaryLinkText = normalize(primaryLink?.innerText || primaryLink?.textContent || "");
    const primaryHref = primaryLink?.getAttribute("href") || "";

    const match = filePattern.exec(rowText);
    filePattern.lastIndex = 0;

    let fileName = "";
    if (match) {
      fileName = normalize(match[1]).replace(/^\d+\s+/, "");
    } else if (primaryHref.includes("/mod/resource/view.php") && primaryLinkText) {
      fileName = primaryLinkText.replace(/\s+文件$/u, "").trim();
    }

    if (!fileName) continue;

    const hrefCandidates = [];
    for (const el of node.querySelectorAll("[href], [data-href], [data-url]")) {
      const value = el.getAttribute("href") || el.getAttribute("data-href") || el.getAttribute("data-url") || "";
      if (value) hrefCandidates.push(value);
    }

    const actionLabel =
      normalize(node.querySelector("button, a, [role='button']")?.innerText || "") ||
      (rowText.includes("打开") ? "打开" : rowText.includes("下载") ? "下载" : primaryLinkText ? "link" : "");
    const sizeMatch = rowText.match(/(\d+(?:\.\d+)?\s*(?:KB|MB|GB))/i);
    const timeMatch = rowText.match(/(20\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2})/);
    const entry = {
      file_name: fileName,
      size: sizeMatch ? sizeMatch[1] : "",
      uploaded_at: timeMatch ? timeMatch[1] : "",
      action_label: actionLabel,
      href_candidates: hrefCandidates,
      row_text: rowText.slice(0, 600),
      link_text: primaryLinkText,
    };

    const existing = results.get(fileName);
    const score = (entry.size ? 1 : 0) + (entry.uploaded_at ? 1 : 0) + (entry.action_label ? 1 : 0) + (entry.href_candidates.length ? 1 : 0);
    const existingScore = existing
      ? (existing.size ? 1 : 0) + (existing.uploaded_at ? 1 : 0) + (existing.action_label ? 1 : 0) + ((existing.href_candidates || []).length ? 1 : 0)
      : -1;
    if (!existing || score >= existingScore) {
      results.set(fileName, entry);
    }
  }

  return [...results.values()];
}
"""


def format_snapshot(snapshot: DiscoverySnapshot) -> str:
    page = snapshot["page"]
    lines = [
        "## Page",
        f"- URL: {page['url']}",
        f"- Title: {page['title']}",
        f"- Login form present: {page['login_form_present']}",
        f"- Course hints: {', '.join(page['course_hints_found']) or '(none)'}",
        f"- Generic page hints: {', '.join(page.get('generic_page_hints_found', [])) or '(none)'}",
        f"- Logout hints: {', '.join(page.get('logout_hints_found', [])) or '(none)'}",
        f"- Success text hints: {', '.join(page.get('success_text_hints_found', [])) or '(none)'}",
        f"- Public page text hints: {', '.join(page.get('public_page_text_hints_found', [])) or '(none)'}",
        f"- Auth cookies: {', '.join(page.get('matched_auth_cookies', [])) or '(none)'}",
        f"- Explicit logged out: {page.get('explicit_logged_out', False)}",
        f"- Explicit inaccessible: {page.get('explicit_inaccessible', False)}",
        f"- Resource link count: {page.get('resource_link_count', 0)}",
        f"- Active section: {snapshot.get('active_section', '(unknown)')}",
        "",
        "## Materials",
    ]
    materials = snapshot.get("materials", [])
    if not materials:
        lines.append("- (none)")
    else:
        for index, item in enumerate(materials, start=1):
            lines.append(
                f"- [{index}] {item.get('file_name', '(empty)')} | {item.get('size', '(size?)')} | "
                f"{item.get('uploaded_at', '(time?)')} | action={item.get('action_label', '(none)')}"
            )
            lines.append(f"  href={item.get('href', '') or '(unresolved)'}")
            lines.append(f"  source={item.get('source', '') or '(none)'}")
        lines.append("")
    lines.extend([
        "## Resource Candidates",
    ])
    resources = snapshot["resources"]
    if not resources:
        lines.append("- (none)")
        return "\n".join(lines)

    for index, item in enumerate(resources, start=1):
        lines.append(
            f"- [{index}] score={item.get('score', 0)} ext={item.get('ext', '') or '(none)'} "
            f"text={item.get('text', '') or '(empty)'}"
        )
        lines.append(f"  href={item.get('href', '') or '(empty)'}")
        lines.append(f"  reason={', '.join(item.get('reason', [])) or '(none)'}")
        lines.append(f"  context={item.get('container_text', '') or '(empty)'}")
    return "\n".join(lines)
