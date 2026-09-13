"""Read-only Daum public HTML adapter. No browser, login, script execution or OCR."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import hashlib
from html.parser import HTMLParser
import re
import urllib.error
import urllib.parse
import urllib.request

from .notice_management import KST, NoticeError

LIST_URL = "https://m.cafe.daum.net/odin/CT9G"
MAX_HTML_BYTES = 3 * 1024 * 1024
MAX_ARTICLES = 100
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


@dataclass
class Node:
    tag: str
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)
    closed: bool = False

    def has_class(self, value):
        return value in self.attrs.get("class", "").split()

    def walk(self):
        # A partially consumed generator raises GeneratorExit on close. Debuggers
        # configured to break on raised exceptions then pause the whole GUI even
        # though collection succeeded. Use an ordinary iterator of node references
        # (the document already has a bounded node count), preserving preorder.
        nodes = []
        pending = [self]
        while pending:
            node = pending.pop()
            nodes.append(node)
            pending.extend([child for child in reversed(node.children) if isinstance(child, Node)])
        return iter(nodes)


class Document(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = Node("document")
        self.stack = [self.root]
        self.nodes = 0
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        self.nodes += 1
        if self.nodes > 100_000 or len(self.stack) > 100:
            raise NoticeError("카페 HTML 구조가 허용 범위를 벗어났습니다.")
        node = Node(tag, dict(attrs), closed=tag in VOID_TAGS)
        self.stack[-1].children.append(node)
        if tag not in VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                self.stack[i].closed = True
                del self.stack[i:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def text_of(node, *, layout=False):
    if isinstance(node, str):
        return node
    style = (node.attrs.get("style") or "").lower().replace(" ", "")
    if (node.tag in {"script", "style", "noscript", "s", "strike", "del"}
            or "line-through" in style or "display:none" in style or "hidden" in node.attrs
            or node.attrs.get("aria-hidden") == "true"):
        return ""
    if node.tag in {"br", "hr"}:
        return "\n" if layout else " "
    text = "".join(text_of(child, layout=layout) for child in node.children)
    if layout and node.tag in {"td", "th"}:
        return text + " | "
    if layout and node.tag in {"p", "div", "li", "tr", "h1", "h2", "h3", "h4", "table"}:
        return "\n" + text + "\n"
    return text


def normalize_text(value):
    return "\n".join(line for line in (re.sub(r"[\s\u200b\ufeff]+", " ", line).strip()
                                      for line in value.splitlines()) if line)


def first_class_text(node, class_name):
    for child in node.walk():
        if child.has_class(class_name):
            return normalize_text(text_of(child))
    return ""


def canonical_article(href):
    parsed = urllib.parse.urlsplit(urllib.parse.urljoin(LIST_URL, href))
    if parsed.scheme != "https" or parsed.netloc not in {"m.cafe.daum.net", "cafe.daum.net"}:
        raise NoticeError("공식 오딘 카페 밖의 게시글 주소입니다.")
    match = re.fullmatch(r"/odin/([A-Za-z0-9_-]{1,20})/(\d{1,12})/?", parsed.path)
    if not match:
        raise NoticeError("게시글 주소 형식이 변경되었습니다.")
    key = f"{match[1]}/{match[2]}"
    return key, f"https://m.cafe.daum.net/odin/{key}"


def date_of(text, now):
    text = text.strip()
    match = re.search(r"(?<!\d)(\d{2}|\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})(?!\d)", text)
    if match:
        year = int(match[1])
        try:
            return datetime(year + 2000 if year < 100 else year, int(match[2]), int(match[3]), tzinfo=KST).date().isoformat()
        except ValueError:
            return None
    if text in {"오늘", "방금", "방금 전"} or re.fullmatch(r"\d{1,2}:\d{2}", text):
        return now.date().isoformat()
    if text == "어제":
        return (now - timedelta(days=1)).date().isoformat()
    match = re.fullmatch(r"(?:(\d+)시간\s*)?(?:(\d+)분\s*)?(?:(\d+)초\s*)?전", text)
    if match and any(match.groups()):
        hours, minutes, seconds = (int(value or 0) for value in match.groups())
        return (now - timedelta(hours=hours, minutes=minutes, seconds=seconds)).date().isoformat()
    return None


def category_for(title):
    value = re.sub(r"\s+", "", title).lower()
    if any([word in value for word in ("계정보호", "운영정책", "개인정보처리방침", "이용약관")]):
        return None
    if "런처" in value and any([word in value for word in ("오류", "해결", "pc이용")]):
        return None
    if "서버이전" in value or "이전권" in value:
        return "transfer"
    if "점검" in value or "정검" in value:
        return "maintenance"
    if any([word in value for word in ("클래스변경", "직업변경", "신규전직", "신규클래스", "신규캐릭터", "신캐릭")]):
        return "class_change"
    if "업데이트" in value or "확인된문제" in value:
        return "update"
    if any([word in value for word in ("이벤트", "아이템", "소환체", "공허충")]):
        return "event"
    return "general"


def parse_pinned(html, now):
    root = Document(html).root
    containers = [n for n in root.walk() if n.attrs.get("id") == "noticeContainer"]
    if len(containers) != 1 or not containers[0].closed:
        raise NoticeError("상단 공지 영역을 끝까지 확인하지 못했습니다. 기존 공지를 유지합니다.")
    container = containers[0]
    lists = [n for n in container.walk() if n.tag == "ul" and n.has_class("list_cafe")]
    if not lists or any([not n.closed for n in lists]):
        raise NoticeError("공지 목록이 없거나 불완전합니다. 기존 공지를 유지합니다.")
    if any([n.tag == "li" and not n.has_class("notice") for ul in lists for n in ul.walk()]):
        raise NoticeError("공지 행 표시 방식이 변경되었습니다. 빈 목록으로 처리하지 않습니다.")
    rows = [n for n in container.walk() if n.tag == "li" and n.has_class("notice")]
    if len(rows) > MAX_ARTICLES:
        raise NoticeError("상단 공지 수가 허용 범위를 초과했습니다.")
    results = []
    seen = set()
    for row in rows:
        links = [n for n in row.walk() if n.tag == "a" and n.has_class("link_cafe")]
        title = first_class_text(row, "txt_detail")
        date_text = first_class_text(row, "created_at")
        if not row.closed or len(links) != 1 or not title:
            raise NoticeError("일부 공지 행을 읽지 못했습니다. 공지 해제 처리를 유보합니다.")
        key, url = canonical_article(links[0].attrs.get("href", ""))
        if key in seen:
            continue
        seen.add(key)
        results.append({"id": key, "url": url, "title": title, "published_date": date_of(date_text, now),
                        "date_label": date_text.strip(), "category": category_for(title), "rank": len(results)})
    return results


def parse_article(html, listing, now):
    root = Document(html).root
    bodies = [n for n in root.walk() if n.attrs.get("id") == "article" and n.has_class("tx-content-container")]
    if len(bodies) != 1 or not bodies[0].closed:
        raise NoticeError("게시글 본문을 끝까지 읽지 못했습니다. 기존 본문을 유지합니다.")
    body = normalize_text(text_of(bodies[0], layout=True))
    title = first_class_text(root, "article_title")
    if not title:
        raise NoticeError("게시글 제목을 확인하지 못했습니다.")
    published = listing["published_date"]
    for node in root.walk():
        if node.has_class("num_subject"):
            candidate = date_of(text_of(node), now)
            if candidate:
                published = candidate
                break
    images = [n.attrs.get("data-img-src") or n.attrs.get("src") for n in bodies[0].walk() if n.tag == "img"]
    images = [url for url in images if url and urllib.parse.urlsplit(url).scheme in {"https", "http"}]
    digest = hashlib.sha256((title + "\n" + body + "\n" + "\n".join(images)).encode("utf-8")).hexdigest()
    return {"title": title, "body": body, "published_date": published, "images": images,
            "content_hash": digest, "review_note": "본문 텍스트 없음 · 이미지/원문 확인 필요" if not body else ""}


@dataclass
class Page:
    text: str = ""
    etag: str = ""
    modified: str = ""
    not_modified: bool = False


def fetch_page(url, cached=None):
    if url != LIST_URL:
        canonical_article(url)
    headers = {"User-Agent": "BossTimer-Notice/1.0 (public notice reader)", "Accept": "text/html"}
    for source, target in (("etag", "If-None-Match"), ("modified", "If-Modified-Since")):
        value = (cached or {}).get(source) or ""
        if value and len(value) < 1024 and "\r" not in value and "\n" not in value:
            headers[target] = value
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            final = response.url
            if url == LIST_URL:
                if final.split("?")[0].rstrip("/") != LIST_URL:
                    raise NoticeError("공지 목록이 다른 페이지로 이동했습니다.")
            elif canonical_article(final)[0] != canonical_article(url)[0]:
                raise NoticeError("요청한 공지와 다른 게시글로 이동했습니다.")
            if "html" not in response.headers.get("Content-Type", "").lower():
                raise NoticeError("카페가 HTML 페이지를 반환하지 않았습니다.")
            raw = response.read(MAX_HTML_BYTES + 1)
            if len(raw) > MAX_HTML_BYTES:
                raise NoticeError("카페 응답 크기가 허용 범위를 초과했습니다.")
            charset = response.headers.get_content_charset() or "utf-8"
            return Page(raw.decode(charset), response.headers.get("ETag", ""), response.headers.get("Last-Modified", ""))
    except urllib.error.HTTPError as exc:
        if exc.code == 304 and cached:
            return Page(not_modified=True)
        raise
