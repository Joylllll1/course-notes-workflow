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
import re
import time
from pathlib import Path
from typing import Any, TypedDict
from urllib.parse import urljoin, urlsplit, urlunsplit

from dotenv import load_dotenv
from playwright.sync_api import Browser, BrowserContext, Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

AGENT_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = AGENT_ROOT.parent

load_dotenv(PROJECT_ROOT / ".env")

LOGIN_HINT_SELECTORS = [
    "input[type='password']",
    "input[name='password']",
    "input[id='password']",
    "button[type='submit']",
    "button:has-text('登录')",
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
    "main",
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
    target_url: str
    target_title: str
    open_mode: str
    suggested_filename: str
    blob_data_base64: str


class DiscoverySnapshot(TypedDict):
    page: PageSnapshot
    resources: list[ResourceCandidate]
    active_section: str
    materials: list[MaterialEntry]


def get_course_url() -> str:
    import os

    course_url = os.getenv("COURSE_URL", "").strip()
    if not course_url:
        raise RuntimeError("缺少 COURSE_URL，请先在 .env 中配置课程页面地址")
    return course_url


def get_login_url(course_url: str | None = None) -> str:
    target = course_url or get_course_url()
    parts = urlsplit(target)
    return urlunsplit((parts.scheme, parts.netloc, "/login", "", ""))


def _looks_like_login_url(url: str) -> bool:
    lowered = url.lower()
    return "/login" in lowered or "signin" in lowered or "sign-in" in lowered or "sso" in lowered


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


def _normalize_text(text: str, max_len: int = 240) -> str:
    normalized = re.sub(r"\s+", " ", text).strip()
    return normalized[:max_len]


def snapshot_page(page: Page) -> PageSnapshot:
    body_text = page.inner_text("body") if page.locator("body").count() else ""
    return {
        "url": page.url,
        "title": page.title(),
        "text_preview": _normalize_text(body_text, max_len=1000),
        "login_form_present": _page_has_any_selector(page, LOGIN_HINT_SELECTORS),
        "course_hints_found": _page_hit_selectors(page, COURSE_PAGE_HINT_SELECTORS),
    }


def is_authenticated_snapshot(snapshot: PageSnapshot) -> bool:
    if _looks_like_login_url(snapshot["url"]):
        return False
    if snapshot["login_form_present"]:
        return False
    if snapshot["course_hints_found"]:
        return True
    title = snapshot["title"].lower()
    preview = snapshot["text_preview"].lower()
    login_tokens = ["登录", "sign in", "log in", "统一身份认证"]
    if any(token in title or token in preview for token in login_tokens):
        return False
    return True


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
        self.page.goto(get_login_url(), wait_until="domcontentloaded")

    def wait_for_manual_login(self) -> PageSnapshot:
        assert self.page is not None
        course_url = get_course_url()

        print("正在打开浏览器，等待你手动登录...\n")
        print("完成滑块和登录后，回终端按 Enter。")
        print("只有同一个页面真正进入课程页，才继续后续 agent 流程。\n")

        self.open_login()
        while True:
            try:
                input()
            except EOFError:
                raise RuntimeError("登录流程被中断")

            try:
                self.page.goto(course_url, wait_until="domcontentloaded")
                self.page.wait_for_load_state("networkidle")
                snapshot = snapshot_page(self.page)
            except Exception as exc:
                print(f"校验课程页失败：{exc}")
                print("回到浏览器继续完成登录，再按一次 Enter。\n")
                continue

            if is_authenticated_snapshot(snapshot):
                print(f"登录成功，课程页可访问：{snapshot['url']}")
                print(f"页面标题：{snapshot['title']}")
                return snapshot

            print(f"尚未确认登录成功，当前课程页：{snapshot['url']}")
            print(f"页面标题：{snapshot['title']}")
            print("回到浏览器继续完成登录，再按一次 Enter。\n")

    def ensure_course_page(self) -> PageSnapshot:
        assert self.page is not None
        self.page.goto(get_course_url(), wait_until="domcontentloaded")
        self.page.wait_for_load_state("networkidle")
        snapshot = snapshot_page(self.page)
        if not is_authenticated_snapshot(snapshot):
            raise RuntimeError("当前 live session 已失效，需要重新手动登录")
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

    def inspect_course_page(self) -> DiscoverySnapshot:
        assert self.page is not None
        snapshot = self.ensure_course_page()
        active_section = "course-root"

        for section_name in ["课件", "资料", "任务"]:
            if self._open_section(section_name):
                snapshot = snapshot_page(self.page)
                active_section = section_name
                break

        materials = self._extract_material_rows()
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
        return {
            "page": snapshot,
            "resources": normalized,
            "active_section": active_section,
            "materials": materials,
        }

    def _extract_material_rows(self) -> list[MaterialEntry]:
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
                resolved_href = self._resolve_material_open_url(file_name)
                source = "click" if resolved_href else ""

            ext = Path(file_name).suffix.lower().lstrip(".")
            materials.append(
                {
                    "file_name": file_name,
                    "ext": ext,
                    "size": _normalize_text(str(row.get("size", "")), max_len=60),
                    "uploaded_at": _normalize_text(str(row.get("uploaded_at", "")), max_len=80),
                    "action_label": _normalize_text(str(row.get("action_label", "")), max_len=40),
                    "href": resolved_href,
                    "source": source,
                    "row_text": _normalize_text(str(row.get("row_text", "")), max_len=500),
                }
            )
        return materials

    def _pick_direct_material_href(self, href_candidates: list[str]) -> str:
        for href in href_candidates:
            lowered = href.lower()
            if any(lowered.endswith(f".{ext}") or f".{ext}?" in lowered for ext in ["pdf", "ppt", "pptx", "doc", "docx", "xls", "xlsx"]):
                return href
            if "oss-cn-" in lowered or "download" in lowered:
                return href
        return ""

    def _resolve_material_open_url(self, file_name: str) -> str:
        assert self.page is not None
        row_locator = self.page.locator("tr, .ant-table-row, li, .ant-list-item, .ant-card").filter(has_text=file_name).first
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
                    self._open_section("课件")
            except Exception:
                return ""
        except Exception:
            return ""

        return captured_url

    def open_material(self, file_name: str) -> MaterialEntry:
        assert self.page is not None
        assert self.context is not None

        self.ensure_course_page()
        self._open_section("课件")

        row_locator = self.page.locator("tbody tr, .ant-table-tbody > tr, .ant-list-item, li").filter(has_text=file_name).first
        if row_locator.count() == 0:
            return {
                "file_name": file_name,
                "target_url": "",
                "target_title": "",
                "open_mode": "missing-row",
                "suggested_filename": "",
            }

        open_locator = row_locator.get_by_text("打开", exact=True).first
        if open_locator.count() == 0:
            return {
                "file_name": file_name,
                "target_url": "",
                "target_title": "",
                "open_mode": "missing-open-button",
                "suggested_filename": "",
            }

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
                with self.context.expect_page(timeout=5000) as page_info:
                    open_locator.click(timeout=3000)
                popup = page_info.value
                popup.wait_for_load_state("domcontentloaded", timeout=5000)
                target_url = popup.url
                target_title = popup.title()
                blob_data = self._read_blob_from_page(popup, target_url) if target_url.startswith("blob:") else ""
                popup.close()
                return {
                    "file_name": file_name,
                    "target_url": target_url,
                    "target_title": target_title,
                    "open_mode": "popup",
                    "suggested_filename": Path(urlsplit(target_url).path).name,
                    "blob_data_base64": blob_data,
                }
            except PlaywrightTimeoutError:
                pass
            except Exception:
                pass

            try:
                with self.page.expect_download(timeout=5000) as download_info:
                    open_locator.click(timeout=3000)
                download = download_info.value
                return {
                    "file_name": file_name,
                    "target_url": download.url,
                    "target_title": "",
                    "open_mode": "download",
                    "suggested_filename": download.suggested_filename,
                }
            except PlaywrightTimeoutError:
                pass
            except Exception:
                pass

            try:
                open_locator.click(timeout=3000)
                self.page.wait_for_load_state("domcontentloaded", timeout=5000)
                current_url = self.page.url
                current_title = self.page.title()
                viewer_url = self._extract_inline_pdf_url()
                if viewer_url:
                    blob_data = self._read_blob_from_page(self.page, viewer_url) if viewer_url.startswith("blob:") else ""
                    return {
                        "file_name": file_name,
                        "target_url": viewer_url,
                        "target_title": current_title,
                        "open_mode": "inline-viewer",
                        "suggested_filename": Path(urlsplit(viewer_url).path).name,
                        "blob_data_base64": blob_data,
                    }
                if current_url != before_url:
                    blob_data = self._read_blob_from_page(self.page, current_url) if current_url.startswith("blob:") else ""
                    result = {
                        "file_name": file_name,
                        "target_url": current_url,
                        "target_title": current_title,
                        "open_mode": "same-tab",
                        "suggested_filename": Path(urlsplit(current_url).path).name,
                        "blob_data_base64": blob_data,
                    }
                    self.page.go_back(wait_until="domcontentloaded", timeout=5000)
                    self.page.wait_for_load_state("networkidle", timeout=5000)
                    self._open_section("课件")
                    return result
            except Exception:
                pass

            if captured_responses:
                target_url = captured_responses[-1]
                blob_data = self._read_blob_from_page(self.page, target_url) if target_url.startswith("blob:") else ""
                self.ensure_course_page()
                self._open_section("课件")
                return {
                    "file_name": file_name,
                    "target_url": target_url,
                    "target_title": "",
                    "open_mode": "network-response",
                    "suggested_filename": Path(urlsplit(target_url).path).name,
                    "blob_data_base64": blob_data,
                }

            self.ensure_course_page()
            self._open_section("课件")
            return {
                "file_name": file_name,
                "target_url": "",
                "target_title": "",
                "open_mode": "unresolved",
                "suggested_filename": "",
            }
        finally:
            try:
                self.page.remove_listener("response", track_response)
            except Exception:
                pass

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
    return """
({ keywords }) => {
  const lowerKeywords = keywords.map((kw) => kw.toLowerCase());
  const nodes = [...document.querySelectorAll("a[href], button, [role='button']")];
  const results = [];
  const seen = new Set();
  const filePattern = /([A-Za-z0-9_\u4e00-\u9fa5\\-\\. ]+\\.(pdf|ppt|pptx|doc|docx|xls|xlsx|zip|rar|7z))/ig;

  const normalize = (value) => (value || "").replace(/\\s+/g, " ").trim();
  const scoreNode = (href, text, containerText) => {
    let score = 0;
    const reason = [];
    const lowerHref = href.toLowerCase();
    const lowerText = text.toLowerCase();
    const lowerContainer = containerText.toLowerCase();
    const extMatch = lowerHref.match(/\\.([a-z0-9]+)(?:$|\\?)/);
    const ext = extMatch ? extMatch[1] : "";
    if (ext) {
      score += 3;
      reason.push(`ext:${ext}`);
    }
    if (lowerKeywords.some((kw) => lowerText.includes(kw))) {
      score += 3;
      reason.push("text-keyword");
    }
    if (lowerKeywords.some((kw) => lowerContainer.includes(kw))) {
      score += 2;
      reason.push("container-keyword");
    }
    const fileMatches = [...containerText.matchAll(filePattern)];
    if (fileMatches.length > 0) {
      score += 4;
      reason.push("container-file");
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
    const container = node.closest("li, tr, .ant-list-item, .ant-card, .ant-collapse-item, .ant-table-row, section, article, div");
    const containerText = normalize(container ? container.innerText || container.textContent || "" : "");
    const { score, reason, ext, fileMatches } = scoreNode(href, text, containerText);
    if (score < 3) continue;
    const detectedName = fileMatches.length > 0 ? normalize(fileMatches[0][1]) : "";
    const dedupeKey = `${href}@@${text}@@${detectedName}`;
    if (seen.has(dedupeKey)) continue;
    seen.add(dedupeKey);
    results.push({
      href,
      text: detectedName || text,
      title: normalize(node.getAttribute("title") || ""),
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
    return """
() => {
  const filePattern = /([A-Za-z0-9_\u4e00-\u9fa5\\-\\. ]+\\.(pdf|ppt|pptx|doc|docx|xls|xlsx))/ig;
  const normalize = (value) => (value || "").replace(/\\s+/g, " ").trim();
  const rowSelectors = [
    "tbody tr",
    ".ant-table-tbody > tr",
    ".ant-list-item",
    "li",
  ];
  const nodes = [...new Set(rowSelectors.flatMap((selector) => [...document.querySelectorAll(selector)]))];
  const results = new Map();

  for (const node of nodes) {
    const rowText = normalize(node.innerText || node.textContent || "");
    if (!rowText) continue;
    const match = filePattern.exec(rowText);
    filePattern.lastIndex = 0;
    if (!match) continue;

    const fileName = normalize(match[1]).replace(/^\\d+\\s+/, "");
    const hrefCandidates = [];
    for (const el of node.querySelectorAll("[href], [data-href], [data-url]")) {
      const value = el.getAttribute("href") || el.getAttribute("data-href") || el.getAttribute("data-url") || "";
      if (value) hrefCandidates.push(value);
    }

    const actionLabel =
      normalize(node.querySelector("button, a, [role='button']")?.innerText || "") ||
      (rowText.includes("打开") ? "打开" : rowText.includes("下载") ? "下载" : "");
    const sizeMatch = rowText.match(/(\\d+(?:\\.\\d+)?\\s*(?:KB|MB|GB))/i);
    const timeMatch = rowText.match(/(20\\d{2}-\\d{2}-\\d{2}\\s+\\d{2}:\\d{2})/);
    const entry = {
      file_name: fileName,
      size: sizeMatch ? sizeMatch[1] : "",
      uploaded_at: timeMatch ? timeMatch[1] : "",
      action_label: actionLabel,
      href_candidates: hrefCandidates,
      row_text: rowText.slice(0, 600),
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
