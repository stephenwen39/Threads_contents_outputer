"""Threads 公開貼文抓取。

Threads 沒有開放的公開 API，但其網頁會把貼文資料以 JSON 形式嵌在 HTML 中
（登出狀態下可取得最近的公開貼文）。本模組：

1. 將輸入正規化為純 username。
2. 抓取個人頁 HTML，解析嵌入的 JSON，取出 profile 與貼文。
3. 解析出 user id 後，盡力呼叫內部 GraphQL endpoint 做分頁取得更多貼文
   （doc_id 會被 Meta 輪替，失敗時不影響已取得的資料）。

注意：未登入只能取得「公開」帳號的近期貼文，無法保證取得「全部」歷史貼文。
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests

from .models import Post


class FetchError(Exception):
    """抓取失敗（帳號不存在、不公開、或網路問題）。"""


# 用爬蟲 UA（Googlebot）取得伺服器端渲染（SSR）的頁面，
# 登出狀態的一般瀏覽器 UA 會拿到不含貼文的 app 外殼。
_DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Threads / Instagram 共用的 web app id（GraphQL 必要 header）
_IG_APP_ID = "238260118697367"
# 已知（可能已輪替）的個人貼文 GraphQL doc_id，僅作 best-effort 分頁
_PROFILE_THREADS_DOC_IDS = [
    "6232751443445612",
    "25073444231308545",
]


def normalize_username(raw: str) -> str:
    """把使用者輸入（@name、網址、純名稱）正規化為 username。"""
    if not raw:
        raise FetchError("請輸入 Threads 帳號")
    s = raw.strip()
    # 從 URL 取出 @handle
    m = re.search(r"threads\.(?:net|com)/@?([A-Za-z0-9_.]+)", s)
    if m:
        s = m.group(1)
    s = s.lstrip("@").strip().strip("/")
    s = s.split("?")[0].split("/")[0]
    if not s:
        raise FetchError("無法解析帳號名稱")
    return s


class ThreadsFetcher:
    def __init__(self, timeout: int = 20, max_posts: int = 200, polite_delay: float = 1.0):
        self.timeout = timeout
        self.max_posts = max_posts
        self.polite_delay = polite_delay
        self.session = requests.Session()
        self.session.headers.update(_DEFAULT_HEADERS)

    # ---------- public ----------
    def fetch(self, raw_username: str) -> Tuple[Dict[str, Any], List[Post]]:
        """回傳 (profile_info, posts)。"""
        username = normalize_username(raw_username)
        html = self._get_profile_html(username)
        datasets = list(_iter_embedded_json(html))

        user_id = _find_user_id(datasets, username)
        profile = _find_profile(datasets, username)

        posts: Dict[str, Post] = {}
        for p in _extract_posts(datasets, username):
            posts[p.id] = p

        # best-effort：用 GraphQL 分頁取得更多
        if user_id:
            try:
                lsd = _find_lsd(html)
                for p in self._graphql_more_posts(user_id, username, lsd):
                    posts.setdefault(p.id, p)
                    if len(posts) >= self.max_posts:
                        break
            except Exception:
                pass  # 分頁失敗不影響已抓到的資料

        post_list = sorted(
            posts.values(),
            key=lambda x: x.timestamp or "",
            reverse=True,
        )[: self.max_posts]

        if not post_list:
            raise FetchError(
                f"找不到 @{username} 的公開貼文。可能原因：帳號不存在、設為不公開、"
                "尚未發文，或 Threads 頁面結構有變動。"
            )

        profile = profile or {"username": username}
        return profile, post_list

    # ---------- internal ----------
    def _get_profile_html(self, username: str) -> str:
        last_err: Optional[Exception] = None
        for host in ("https://www.threads.com", "https://www.threads.net"):
            url = f"{host}/@{username}"
            try:
                resp = self.session.get(url, timeout=self.timeout)
                if resp.status_code == 404:
                    last_err = FetchError(f"帳號 @{username} 不存在 (404)")
                    continue
                resp.raise_for_status()
                if resp.text:
                    return resp.text
            except requests.RequestException as e:  # noqa: PERF203
                last_err = e
        if isinstance(last_err, FetchError):
            raise last_err
        raise FetchError(f"無法連線到 Threads：{last_err}")

    def _graphql_more_posts(
        self, user_id: str, username: str, lsd: Optional[str]
    ) -> Iterable[Post]:
        url = "https://www.threads.com/api/graphql"
        headers = {
            "X-IG-App-ID": _IG_APP_ID,
            "Content-Type": "application/x-www-form-urlencoded",
            "X-FB-LSD": lsd or "",
            "Sec-Fetch-Site": "same-origin",
            "Origin": "https://www.threads.com",
            "Referer": f"https://www.threads.com/@{username}",
        }
        for doc_id in _PROFILE_THREADS_DOC_IDS:
            data = {
                "lsd": lsd or "",
                "variables": json.dumps({"userID": str(user_id)}),
                "doc_id": doc_id,
            }
            try:
                time.sleep(self.polite_delay)
                resp = self.session.post(
                    url, headers=headers, data=data, timeout=self.timeout
                )
                if resp.status_code != 200:
                    continue
                payload = resp.json()
            except Exception:
                continue
            found = False
            for p in _extract_posts([payload], username):
                found = True
                yield p
            if found:
                return


# ---------------- JSON 解析輔助 ----------------

def _iter_embedded_json(html: str) -> Iterable[Any]:
    """從 HTML 取出所有 <script type="application/json"> 內的 JSON 物件。"""
    pattern = re.compile(
        r'<script[^>]*type="application/json"[^>]*>(.*?)</script>',
        re.DOTALL,
    )
    for m in pattern.finditer(html):
        raw = m.group(1).strip()
        if not raw:
            continue
        try:
            yield json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue


def _walk(obj: Any) -> Iterable[Any]:
    """深度走訪所有 dict / list 節點。"""
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            yield cur
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)


def _find_user_id(datasets: List[Any], username: str) -> Optional[str]:
    uname = username.lower()
    fallback: Optional[str] = None
    for ds in datasets:
        for node in _walk(ds):
            if not isinstance(node, dict):
                continue
            node_user = str(node.get("username", "")).lower()
            pk = node.get("pk") or node.get("id")
            if node_user == uname and pk:
                pk = str(pk)
                if pk.isdigit():
                    return pk
                fallback = fallback or pk
    return fallback


def _find_profile(datasets: List[Any], username: str) -> Optional[Dict[str, Any]]:
    uname = username.lower()
    best: Optional[Dict[str, Any]] = None
    for ds in datasets:
        for node in _walk(ds):
            if not isinstance(node, dict):
                continue
            if str(node.get("username", "")).lower() != uname:
                continue
            info = {
                "username": node.get("username"),
                "full_name": node.get("full_name"),
                "biography": node.get("biography"),
                "follower_count": node.get("follower_count"),
                "is_verified": node.get("is_verified"),
                "profile_pic_url": node.get("profile_pic_url"),
            }
            # 偏好欄位較完整的那一份
            score = sum(1 for v in info.values() if v not in (None, ""))
            if best is None or score > best.get("_score", 0):
                info["_score"] = score
                best = info
    if best:
        best.pop("_score", None)
    return best


def _looks_like_post(node: Dict[str, Any]) -> bool:
    has_text = isinstance(node.get("caption"), dict) and "text" in node.get("caption", {})
    has_code = "code" in node
    has_time = "taken_at" in node
    return (has_text or has_code) and has_time


def _extract_posts(datasets: List[Any], username: str) -> List[Post]:
    seen: Dict[str, Post] = {}
    for ds in datasets:
        for node in _walk(ds):
            if not isinstance(node, dict) or not _looks_like_post(node):
                continue
            post = _node_to_post(node, username)
            if post and post.text.strip():
                seen.setdefault(post.id, post)
    return list(seen.values())


def _node_to_post(node: Dict[str, Any], username: str) -> Optional[Post]:
    code = node.get("code")
    pk = node.get("pk") or node.get("id") or code
    if not pk:
        return None
    caption = node.get("caption")
    text = ""
    if isinstance(caption, dict):
        text = caption.get("text") or ""
    if not text:
        text = node.get("text") or ""

    taken_at = node.get("taken_at")
    timestamp = None
    if isinstance(taken_at, (int, float)) and taken_at > 0:
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(taken_at))

    reply_count = 0
    tpa = node.get("text_post_app_info")
    if isinstance(tpa, dict):
        reply_count = tpa.get("direct_reply_count") or tpa.get("reply_count") or 0

    media_urls = _extract_media(node)
    url = f"https://www.threads.com/@{username}/post/{code}" if code else None

    return Post(
        id=str(pk),
        text=text,
        timestamp=timestamp,
        like_count=int(node.get("like_count") or 0),
        reply_count=int(reply_count or 0),
        url=url,
        media_urls=media_urls,
    )


def _extract_media(node: Dict[str, Any]) -> List[str]:
    urls: List[str] = []

    def add_from_versions(iv: Any) -> None:
        if isinstance(iv, dict):
            candidates = iv.get("candidates")
            if isinstance(candidates, list) and candidates:
                u = candidates[0].get("url")
                if u:
                    urls.append(u)

    add_from_versions(node.get("image_versions2"))
    carousel = node.get("carousel_media")
    if isinstance(carousel, list):
        for item in carousel:
            if isinstance(item, dict):
                add_from_versions(item.get("image_versions2"))
    return urls[:5]


def _find_lsd(html: str) -> Optional[str]:
    m = re.search(r'"LSD",\[\],\{"token":"([^"]+)"', html)
    if m:
        return m.group(1)
    m = re.search(r'"lsd":"([^"]+)"', html)
    return m.group(1) if m else None
