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

import logging
import json
import re
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests

from .models import Post

logger = logging.getLogger(__name__)


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
    def __init__(
        self,
        timeout: int = 20,
        max_posts: int = 200,
        polite_delay: float = 1.0,
        max_retries: int = 3,
    ):
        self.timeout = timeout
        self.max_posts = max_posts
        self.polite_delay = polite_delay
        self.max_retries = max_retries
        self.session = requests.Session()
        self.session.headers.update(_DEFAULT_HEADERS)
        # 由 fetch() 設定，供呼叫端判斷是否可能未取得全部歷史貼文
        self.pagination_succeeded: bool = False

    # ---------- public ----------
    def fetch(self, raw_username: str) -> Tuple[Dict[str, Any], List[Post]]:
        """回傳 (profile_info, posts)。"""
        username = normalize_username(raw_username)
        logger.info("抓取 @%s 的個人頁…", username)
        html = self._get_profile_html(username)
        datasets = list(_iter_embedded_json(html))

        user_id = _find_user_id(datasets, username)
        profile = _find_profile(datasets, username)

        posts: Dict[str, Post] = {}
        for p in _extract_posts(datasets, username):
            posts[p.id] = p
        logger.info("由頁面內嵌資料取得 %d 則貼文", len(posts))

        # best-effort：用 GraphQL 分頁取得更多
        self.pagination_succeeded = False
        if user_id:
            try:
                lsd = _find_lsd(html)
                for p in self._graphql_more_posts(user_id, username, lsd):
                    if p.id not in posts:
                        posts[p.id] = p
                        self.pagination_succeeded = True
                    if len(posts) >= self.max_posts:
                        break
            except Exception as e:  # 分頁失敗不影響已抓到的資料
                logger.debug("GraphQL 分頁失敗（忽略）：%s", e)
        if self.pagination_succeeded:
            logger.info("GraphQL 分頁後共 %d 則貼文", len(posts))

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
            for attempt in range(1, self.max_retries + 1):
                try:
                    resp = self.session.get(url, timeout=self.timeout)
                    if resp.status_code == 404:
                        last_err = FetchError(f"帳號 @{username} 不存在 (404)")
                        break  # 404 不重試，換下一個 host
                    if resp.status_code == 429 or resp.status_code >= 500:
                        last_err = FetchError(
                            f"暫時性錯誤 HTTP {resp.status_code}"
                        )
                        self._backoff(attempt, resp)
                        continue  # 重試
                    resp.raise_for_status()
                    if resp.text:
                        return resp.text
                    last_err = FetchError("回應內容為空")
                    self._backoff(attempt)
                except requests.RequestException as e:
                    last_err = e
                    logger.debug("連線 %s 失敗（第 %d 次）：%s", url, attempt, e)
                    self._backoff(attempt)
        if isinstance(last_err, FetchError):
            raise last_err
        raise FetchError(f"無法連線到 Threads：{last_err}")

    def _backoff(self, attempt: int, resp: Optional[requests.Response] = None) -> None:
        """指數退避；若有 Retry-After 標頭則優先採用。"""
        delay = min(self.polite_delay * (2 ** (attempt - 1)), 10.0)
        if resp is not None:
            retry_after = resp.headers.get("Retry-After")
            if retry_after and retry_after.isdigit():
                delay = min(float(retry_after), 30.0)
        logger.debug("退避 %.1fs 後重試", delay)
        time.sleep(delay)

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
                    logger.debug("GraphQL doc_id=%s 回應 HTTP %s", doc_id, resp.status_code)
                    continue
                payload = resp.json()
            except Exception as e:
                logger.debug("GraphQL doc_id=%s 失敗：%s", doc_id, e)
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
    """只取出「目標帳號本人」的貼文，過濾轉貼/引用他人或回覆串等內容。"""
    target = username.lower()
    seen: Dict[str, Post] = {}
    skipped_foreign = 0
    for ds in datasets:
        for node in _walk(ds):
            if not isinstance(node, dict) or not _looks_like_post(node):
                continue
            post = _node_to_post(node, username)
            if not post or not post.text.strip():
                continue
            # 作者可辨識且非目標 → 視為他人內容，跳過
            if post.author and post.author.lower() != target:
                skipped_foreign += 1
                continue
            seen.setdefault(post.id, post)
    if skipped_foreign:
        logger.debug("略過 %d 則非 @%s 本人的貼文（轉貼/引用/回覆）", skipped_foreign, username)
    return list(seen.values())


def _node_author(node: Dict[str, Any]) -> Optional[str]:
    user = node.get("user")
    if isinstance(user, dict):
        u = user.get("username")
        if u:
            return str(u)
    return None


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

    author = _node_author(node)
    media_urls = _extract_media(node)
    handle = author or username
    url = f"https://www.threads.com/@{handle}/post/{code}" if code else None

    return Post(
        id=str(pk),
        text=text,
        timestamp=timestamp,
        like_count=int(node.get("like_count") or 0),
        reply_count=int(reply_count or 0),
        url=url,
        media_urls=media_urls,
        author=author,
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
