"""分析 Threads 貼文 → 產生 YouTube 影片大綱（一律使用 LLM）。

設計原則：
- 「將貼文輸入 LLM，由 LLM 決定輸出」。不使用任何本地啟發式判斷。
- 文本量可能很大，因此採兩階段流程：
  1) 若貼文總量超過單次上限，先分批請 LLM 萃取每批的價值觀／主題／重點摘要（digest）。
  2) 再把所有 digest（或全部貼文）交給 LLM，由它決定要切成幾支影片與各自大綱。
- API 金鑰由「使用者提供」（例如 ChatGPT / OpenAI API key），使用者自己的額度。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Callable, List, Optional

from .models import AnalysisResult, Post, VideoOutline

ProgressCb = Optional[Callable[[str], None]]

logger = logging.getLogger(__name__)


class AnalyzerError(Exception):
    """分析失敗（缺少金鑰、LLM 回傳異常等）。"""


# 單次送進 LLM 的貼文文字字元上限（超過則啟用分批 digest）
_SINGLE_PASS_CHAR_LIMIT = 24000
# 每一批 digest 的字元上限
_BATCH_CHAR_LIMIT = 12000
# 每則貼文擷取的最大字元數
_PER_POST_CHAR_LIMIT = 1500


def _emit(progress: ProgressCb, msg: str) -> None:
    """有 progress callback 就用它（例如更新 spinner 文字），否則記到 log。"""
    if progress is not None:
        progress(msg)
    else:
        logger.info(msg)


def generate_video_outlines(
    username: str,
    posts: List[Post],
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    progress: ProgressCb = None,
) -> AnalysisResult:
    """主入口：一律使用 LLM。沒有金鑰會直接報錯。

    progress：可選的回呼，用來回報目前進度（CLI 會用它更新旋轉指示器文字）。
    """
    api_key = api_key or os.getenv("OPENAI_API_KEY")
    model = model or os.getenv("OPENAI_MODEL") or "gpt-4o-mini"
    base_url = base_url or os.getenv("OPENAI_BASE_URL") or None

    if not api_key:
        raise AnalyzerError(
            "需要 LLM API 金鑰才能分析。請提供你的 OpenAI（ChatGPT）API key，"
            "可在執行時用 --api-key 帶入，或設定環境變數 OPENAI_API_KEY。"
        )
    if not posts:
        raise AnalyzerError("沒有可分析的貼文。")

    client = _make_client(api_key, base_url)

    total_chars = sum(len(p.text or "") for p in posts)
    if total_chars <= _SINGLE_PASS_CHAR_LIMIT:
        _emit(progress, f"使用 LLM（{model}）分析 {len(posts)} 則貼文中…")
        digests = [_posts_to_block(posts)]
    else:
        _emit(progress, f"貼文量較大（約 {total_chars} 字），啟用分批摘要…")
        digests = _build_digests(client, model, username, posts, progress)

    return _final_synthesis(client, model, username, posts, digests, progress)


# ---------------- LLM client ----------------

def _make_client(api_key: str, base_url: Optional[str]):
    try:
        from openai import OpenAI
    except ImportError as e:  # noqa: BLE001
        raise AnalyzerError("尚未安裝 openai 套件，請先 pip install openai") from e
    return OpenAI(api_key=api_key, base_url=base_url) if base_url else OpenAI(api_key=api_key)


def _supports_custom_temperature(model: str) -> bool:
    """推理型／新世代模型（gpt-5、o 系列）只支援預設 temperature。"""
    m = (model or "").lower()
    return not any(m.startswith(p) for p in ("gpt-5", "o1", "o3", "o4"))


def _build_attempts(model: str, temperature: float) -> List[dict]:
    """依模型決定送出的參數組合，避免對不支援的模型送出注定失敗的請求。

    仍保留逐步退階作為保險（例如自架的 OpenAI 相容服務不支援 response_format）。
    """
    rf = {"response_format": {"type": "json_object"}}
    if _supports_custom_temperature(model):
        return [{"temperature": temperature, **rf}, rf, {}]
    return [dict(rf), {}]


def _chat_json(client, model: str, system: str, user: str, temperature: float = 0.6) -> dict:
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    attempts = _build_attempts(model, temperature)
    last_err: Optional[Exception] = None
    for kwargs in attempts:
        try:
            resp = client.chat.completions.create(model=model, messages=messages, **kwargs)
            content = resp.choices[0].message.content or "{}"
            return _loads_json(content)
        except Exception as e:  # noqa: BLE001
            last_err = e
            msg = str(e).lower()
            logger.debug("LLM 呼叫失敗（kwargs=%s）：%s", list(kwargs), e)
            # 只有在「參數不被支援」時才退階重試，其餘錯誤直接拋出
            if not any(k in msg for k in ("temperature", "response_format", "unsupported", "not supported")):
                break
    raise AnalyzerError(_format_llm_error(model, last_err))


def _format_llm_error(model: str, err: Optional[Exception]) -> str:
    """把 OpenAI 的錯誤轉成清楚、可行動的訊息。"""
    detail = str(err) if err else "未知錯誤"
    low = detail.lower()
    hint = ""
    if any(k in low for k in ("does not exist", "do not have access", "model_not_found", "model not found")):
        hint = (
            f"\n→ 此金鑰／帳號可能無法使用模型「{model}」。"
            "請改用 --model gpt-4o-mini 測試；若可行，代表是 gpt-5 的權限/方案問題，"
            "請到 OpenAI 後台確認該模型的存取權限。"
        )
    elif any(k in low for k in ("context length", "maximum context", "context_length_exceeded", "too many tokens")):
        hint = "\n→ 輸入內容過長，請調低 --max-posts（例如 50）後再試。"
    elif any(k in low for k in ("insufficient_quota", "quota", "billing", "exceeded your current")):
        hint = "\n→ 金鑰額度或付款有問題，請到 OpenAI 後台檢查用量與付款設定。"
    elif any(k in low for k in ("incorrect api key", "invalid api key", "invalid_api_key", "401")):
        hint = "\n→ API key 可能無效或已撤銷，請確認 --api-key 是否正確。"
    elif "rate limit" in low or "429" in low:
        hint = "\n→ 觸發速率限制，請稍後再試。"
    return f"呼叫 LLM 失敗（model={model}）：{detail}{hint}"


def _loads_json(content: str) -> dict:
    try:
        return json.loads(content)
    except (json.JSONDecodeError, ValueError):
        # 容錯：抽出第一個 { ... } 區塊
        start = content.find("{")
        end = content.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(content[start : end + 1])
            except (json.JSONDecodeError, ValueError) as e:
                raise AnalyzerError(f"LLM 回傳非合法 JSON：{e}") from e
        raise AnalyzerError("LLM 回傳非合法 JSON")


# ---------------- 文字組裝 ----------------

def _post_line(p: Post) -> str:
    meta = []
    if p.timestamp:
        meta.append(p.timestamp)
    meta.append(f"❤{p.like_count}")
    meta.append(f"💬{p.reply_count}")
    text = (p.text or "")[:_PER_POST_CHAR_LIMIT]
    return f"[id={p.id} | {' '.join(meta)}] {text}"


def _posts_to_block(posts: List[Post]) -> str:
    return "\n".join(_post_line(p) for p in posts)


def _batch_posts(posts: List[Post]) -> List[List[Post]]:
    batches: List[List[Post]] = []
    cur: List[Post] = []
    cur_chars = 0
    for p in posts:
        n = min(len(p.text or ""), _PER_POST_CHAR_LIMIT)
        if cur and cur_chars + n > _BATCH_CHAR_LIMIT:
            batches.append(cur)
            cur, cur_chars = [], 0
        cur.append(p)
        cur_chars += n
    if cur:
        batches.append(cur)
    return batches


# ---------------- 階段一：分批 digest ----------------

_DIGEST_SYSTEM = """你是社群內容分析師。你會收到某 Threads 帳號的一批貼文。
請濃縮萃取這批貼文的重點，只回傳合法 JSON（不要 markdown、不要多餘文字）：
{
  "values": ["這批內容展現的價值觀"],
  "themes": ["反覆出現或重要的主題"],
  "highlights": ["值得做成影片的具體觀點或故事（附帶能對應的 post id）"],
  "notable_post_ids": ["最具代表性的貼文id"]
}
請用繁體中文。"""


def _build_digests(
    client, model: str, username: str, posts: List[Post], progress: ProgressCb = None
) -> List[str]:
    digests: List[str] = []
    batches = _batch_posts(posts)
    for i, batch in enumerate(batches, 1):
        _emit(progress, f"摘要第 {i}/{len(batches)} 批（{len(batch)} 則貼文）…")
        user = (
            f"帳號：@{username}（第 {i}/{len(batches)} 批，{len(batch)} 則貼文）\n\n"
            + _posts_to_block(batch)
        )
        data = _chat_json(client, model, _DIGEST_SYSTEM, user)
        digests.append(
            f"[批次 {i}] "
            + json.dumps(data, ensure_ascii=False)
        )
    return digests


# ---------------- 階段二：彙整並產生影片大綱 ----------------

_FINAL_SYSTEM = """你是一位資深的 YouTube 內容策略師與社群內容分析師。
你會收到某 Threads 帳號的貼文資料（可能是原始貼文，或多批貼文的濃縮摘要）。請你：
1. 歸納這個帳號展現的「價值觀」與「反覆出現的內容主題」。
2. 依內容的資訊量與主題分布，自行判斷這些內容總共適合切分成「幾支」YouTube 影片
  （少則 1 支，多則數支；不要為了湊數硬切，也不要把不同主題硬塞同一支）。
3. 為每一支影片產出完整、可直接開拍的大綱。

只回傳合法 JSON（不要 markdown、不要多餘文字），結構如下：
{
  "summary": "整體價值觀與內容風格的摘要（繁體中文）",
  "core_values": ["價值觀1", "價值觀2"],
  "recurring_themes": ["主題1", "主題2"],
  "videos": [
    {
      "title": "吸睛的影片標題",
      "angle": "這支影片的切入角度／定位",
      "target_audience": "目標觀眾",
      "hook": "開場 15 秒的吸睛鉤子",
      "sections": ["段落1大綱", "段落2大綱", "段落3大綱"],
      "key_messages": ["要傳達的核心訊息／價值觀"],
      "call_to_action": "結尾的行動呼籲",
      "source_post_ids": ["參考到的貼文id"]
    }
  ]
}
所有文字內容請用繁體中文。"""


def _final_synthesis(
    client,
    model: str,
    username: str,
    posts: List[Post],
    digests: List[str],
    progress: ProgressCb = None,
) -> AnalysisResult:
    if len(digests) > 1:
        _emit(progress, "彙整所有內容並產生影片大綱…")
    body = "\n\n".join(digests)
    user = (
        f"Threads 帳號：@{username}\n"
        f"總貼文數：{len(posts)}\n\n"
        f"以下是貼文資料：\n{body}"
    )
    data = _chat_json(client, model, _FINAL_SYSTEM, user, temperature=0.7)

    videos = [
        VideoOutline(
            title=v.get("title", "未命名影片"),
            angle=v.get("angle", ""),
            target_audience=v.get("target_audience", ""),
            hook=v.get("hook", ""),
            sections=list(v.get("sections", []) or []),
            key_messages=list(v.get("key_messages", []) or []),
            call_to_action=v.get("call_to_action", ""),
            source_post_ids=[str(x) for x in (v.get("source_post_ids", []) or [])],
        )
        for v in data.get("videos", [])
    ]
    if not videos:
        raise AnalyzerError("LLM 未回傳任何影片大綱。")

    return AnalysisResult(
        username=username,
        post_count=len(posts),
        summary=data.get("summary", ""),
        core_values=list(data.get("core_values", []) or []),
        recurring_themes=list(data.get("recurring_themes", []) or []),
        videos=videos,
        generated_by="llm",
    )
