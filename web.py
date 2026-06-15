"""零依賴網頁介面（使用 Python 內建 http.server）。

當不方便安裝 Streamlit 時，可改用這個輕量網頁版：
    python web.py
然後用瀏覽器打開 http://127.0.0.1:8000

只需要 requests 與 openai 兩個套件（皆已列於 requirements.txt）。
"""

from __future__ import annotations

import html
import json
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # noqa: BLE001
    pass

from threads_outputer.analyzer import AnalyzerError, generate_video_outlines
from threads_outputer.fetcher import FetchError, ThreadsFetcher

HOST = "127.0.0.1"
PORT = int(os.getenv("PORT", "8000"))


PAGE = """<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Threads → YouTube 影片大綱產生器</title>
<style>
  :root {{ color-scheme: light dark; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, "PingFang TC", "Microsoft JhengHei", sans-serif;
         max-width: 880px; margin: 0 auto; padding: 24px; line-height: 1.6; }}
  h1 {{ font-size: 1.6rem; }}
  .card {{ border: 1px solid #8884; border-radius: 12px; padding: 18px; margin: 16px 0; }}
  label {{ display:block; font-weight:600; margin-top:10px; }}
  input {{ width:100%; padding:10px; border-radius:8px; border:1px solid #8886; margin-top:4px;
           font-size:1rem; background:transparent; color:inherit; }}
  button {{ margin-top:16px; padding:12px 20px; font-size:1rem; border:0; border-radius:10px;
            background:#6c5ce7; color:#fff; cursor:pointer; }}
  button:disabled {{ opacity:.6; cursor:progress; }}
  .muted {{ color:#8a8a8a; font-size:.9rem; }}
  .video {{ border-left:4px solid #6c5ce7; padding-left:14px; margin:18px 0; }}
  .tag {{ display:inline-block; background:#6c5ce733; border-radius:999px;
          padding:2px 10px; margin:2px; font-size:.85rem; }}
  .err {{ color:#e74c3c; font-weight:600; }}
  ul {{ margin:6px 0; }}
</style>
</head>
<body>
  <h1>🎬 Threads → YouTube 影片大綱產生器</h1>
  <p class="muted">輸入一個 Threads 帳號，自動抓取公開貼文，交給 LLM 分析價值觀與內容，
  並由 LLM 決定可製作哪些 YouTube 影片與完整大綱。本工具一律使用 LLM，請提供你自己的 API 金鑰。</p>
  <form class="card" method="post" action="/run">
    <label>Threads 帳號</label>
    <input name="threads_id" placeholder="例如 iam_wei_stephen 或 @zuck 或個人頁網址"
           value="{threads_id}" required>
    <label>OpenAI / ChatGPT API Key（必填）</label>
    <input name="api_key" type="password" placeholder="sk-..." value="{api_key}">
    <label>模型（選填，預設 gpt-4o-mini）</label>
    <input name="model" placeholder="gpt-4o-mini" value="{model}">
    <label>Base URL（選填，OpenAI 相容服務）</label>
    <input name="base_url" placeholder="https://api.openai.com/v1" value="{base_url}">
    <label>最多抓取貼文數（選填）</label>
    <input name="max_posts" type="number" value="{max_posts}">
    <button type="submit">🚀 產生影片大綱</button>
  </form>
  {result}
</body>
</html>"""


def render_page(
    threads_id="",
    api_key="",
    model="",
    base_url="",
    max_posts=100,
    result_html="",
):
    return PAGE.format(
        threads_id=html.escape(threads_id),
        api_key=html.escape(api_key),
        model=html.escape(model),
        base_url=html.escape(base_url),
        max_posts=max_posts,
        result=result_html,
    )


def render_result(profile, result) -> str:
    e = html.escape
    parts = ['<div class="card">']
    parts.append(
        f"<h2>@{e(result.username)}"
        + (f" · {e(profile.get('full_name') or '')}" if profile.get("full_name") else "")
        + "</h2>"
    )
    meta = []
    if profile.get("follower_count") is not None:
        meta.append(f"追蹤者 {profile['follower_count']}")
    meta.append(f"分析貼文 {result.post_count} 則")
    meta.append(f"建議 {len(result.videos)} 支影片")
    meta.append("分析方式：LLM")
    parts.append(f'<p class="muted">{" · ".join(e(m) for m in meta)}</p>')
    if profile.get("biography"):
        parts.append(f"<p><em>{e(profile['biography'])}</em></p>")

    parts.append("<h3>📊 整體價值觀與內容摘要</h3>")
    parts.append(f"<p>{e(result.summary)}</p>")
    parts.append("<h3>核心價值觀</h3><p>")
    parts.append("".join(f'<span class="tag">{e(v)}</span>' for v in result.core_values))
    parts.append("</p><h3>反覆出現的主題</h3><p>")
    parts.append("".join(f'<span class="tag">{e(t)}</span>' for t in result.recurring_themes))
    parts.append("</p>")

    parts.append(f"<h3>🎬 建議的 {len(result.videos)} 支影片</h3>")
    for i, v in enumerate(result.videos, 1):
        parts.append('<div class="video">')
        parts.append(f"<h4>影片 {i}：{e(v.title)}</h4>")
        parts.append(f"<p><b>切入角度：</b>{e(v.angle)}</p>")
        parts.append(f"<p><b>目標觀眾：</b>{e(v.target_audience)}</p>")
        parts.append(f"<p><b>開場鉤子：</b>{e(v.hook)}</p>")
        if v.sections:
            parts.append("<p><b>內容大綱：</b></p><ul>")
            parts.append("".join(f"<li>{e(s)}</li>" for s in v.sections))
            parts.append("</ul>")
        if v.key_messages:
            parts.append("<p><b>核心訊息：</b>" + "、".join(e(k) for k in v.key_messages) + "</p>")
        if v.call_to_action:
            parts.append(f"<p><b>行動呼籲：</b>{e(v.call_to_action)}</p>")
        parts.append("</div>")
    parts.append("</div>")
    return "".join(parts)


class Handler(BaseHTTPRequestHandler):
    def _send(self, body: str, status: int = 200):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):  # 安靜模式
        pass

    def do_GET(self):
        if self.path.split("?")[0] != "/":
            self._send("Not Found", 404)
            return
        self._send(
            render_page(api_key=os.getenv("OPENAI_API_KEY", ""), model=os.getenv("OPENAI_MODEL", ""))
        )

    def do_POST(self):
        if self.path != "/run":
            self._send("Not Found", 404)
            return
        length = int(self.headers.get("Content-Length", 0))
        form = parse_qs(self.rfile.read(length).decode("utf-8"))

        def get(k, d=""):
            return (form.get(k, [d])[0] or d).strip()

        threads_id = get("threads_id")
        api_key = get("api_key") or os.getenv("OPENAI_API_KEY", "")
        model = get("model") or None
        base_url = get("base_url") or None
        try:
            max_posts = int(get("max_posts", "100") or 100)
        except ValueError:
            max_posts = 100

        result_html = ""
        try:
            fetcher = ThreadsFetcher(max_posts=max_posts)
            profile, posts = fetcher.fetch(threads_id)
            result = generate_video_outlines(
                profile.get("username", threads_id),
                posts,
                api_key=api_key or None,
                model=model,
                base_url=base_url,
            )
            result_html = render_result(profile, result)
        except (FetchError, AnalyzerError) as ex:
            result_html = f'<div class="card err">錯誤：{html.escape(str(ex))}</div>'
        except Exception as ex:  # noqa: BLE001
            result_html = f'<div class="card err">發生未預期的錯誤：{html.escape(str(ex))}</div>'

        self._send(
            render_page(
                threads_id=threads_id,
                api_key=api_key,
                model=model or "",
                base_url=base_url or "",
                max_posts=max_posts,
                result_html=result_html,
            )
        )


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}"
    print(f"網頁已啟動：{url}（按 Ctrl+C 結束）")
    try:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    except Exception:  # noqa: BLE001
        pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已關閉。")
        server.shutdown()


if __name__ == "__main__":
    main()
