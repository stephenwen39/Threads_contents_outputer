"""Streamlit 易用介面。

啟動：
    streamlit run app.py
"""

from __future__ import annotations

import os

import streamlit as st

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # noqa: BLE001
    pass

from threads_outputer.analyzer import AnalyzerError, generate_video_outlines
from threads_outputer.fetcher import FetchError, ThreadsFetcher

st.set_page_config(page_title="Threads → YouTube 影片大綱產生器", page_icon="🎬", layout="wide")

st.title("🎬 Threads → YouTube 影片大綱產生器")
st.caption(
    "輸入一個 Threads 帳號，自動抓取其公開貼文，分析價值觀與內容，"
    "並產生可拍成 YouTube 影片的完整大綱。"
)

with st.sidebar:
    st.header("設定")
    api_key = st.text_input(
        "OpenAI / ChatGPT API Key（必填）",
        value=os.getenv("OPENAI_API_KEY", ""),
        type="password",
        help="本工具一律使用 LLM 分析，會用你提供的金鑰與額度進行推論。",
    )
    model = st.text_input("模型", value=os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
    base_url = st.text_input(
        "OpenAI Base URL（選填）", value=os.getenv("OPENAI_BASE_URL", "")
    )
    max_posts = st.slider("最多抓取貼文數", 10, 300, 100, step=10)

col1, col2 = st.columns([3, 1])
with col1:
    threads_id = st.text_input(
        "Threads 帳號", placeholder="例如：zuck 或 @zuck 或貼上個人頁網址"
    )
with col2:
    st.write("")
    st.write("")
    run = st.button("🚀 產生影片大綱", type="primary", use_container_width=True)


def _render(profile, result):
    cols = st.columns(4)
    cols[0].metric("帳號", "@" + result.username)
    cols[1].metric("分析貼文數", result.post_count)
    cols[2].metric("建議影片數", len(result.videos))
    cols[3].metric("分析方式", "LLM")

    if profile.get("biography"):
        st.info(f"**簡介**：{profile['biography']}")

    st.subheader("📊 整體價值觀與內容摘要")
    st.write(result.summary)

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**展現的價值觀**")
        for v in result.core_values:
            st.markdown(f"- {v}")
    with c2:
        st.markdown("**反覆出現的主題**")
        for t in result.recurring_themes:
            st.markdown(f"- {t}")

    st.subheader(f"🎬 建議的 {len(result.videos)} 支影片")
    for i, v in enumerate(result.videos, 1):
        with st.expander(f"影片 {i}：{v.title}", expanded=i == 1):
            st.markdown(f"**切入角度**：{v.angle}")
            st.markdown(f"**目標觀眾**：{v.target_audience}")
            st.markdown(f"**開場鉤子**：{v.hook}")
            st.markdown("**內容大綱**")
            for s in v.sections:
                st.markdown(f"- {s}")
            if v.key_messages:
                st.markdown("**核心訊息**：" + "、".join(v.key_messages))
            if v.call_to_action:
                st.markdown(f"**行動呼籲**：{v.call_to_action}")

    import json

    st.download_button(
        "⬇️ 下載結果（JSON）",
        data=json.dumps(
            {"profile": profile, "analysis": result.to_dict()},
            ensure_ascii=False,
            indent=2,
        ),
        file_name=f"{result.username}_youtube_outlines.json",
        mime="application/json",
    )


if run:
    if not threads_id.strip():
        st.warning("請先輸入 Threads 帳號")
    elif not (api_key or os.getenv("OPENAI_API_KEY")):
        st.warning("請在左側填入你的 OpenAI / ChatGPT API Key（本工具一律使用 LLM 分析）")
    else:
        try:
            with st.spinner("抓取公開貼文中…"):
                fetcher = ThreadsFetcher(max_posts=max_posts)
                profile, posts = fetcher.fetch(threads_id)
            st.success(f"已取得 {len(posts)} 則貼文")
            with st.spinner("使用 LLM 分析內容並產生影片大綱中…"):
                result = generate_video_outlines(
                    profile.get("username", threads_id),
                    posts,
                    api_key=api_key or None,
                    model=model or None,
                    base_url=base_url or None,
                )
            _render(profile, result)
            with st.expander("🔎 查看抓取到的原始貼文"):
                for p in posts:
                    st.markdown(f"- ({p.timestamp or '—'}) ❤️{p.like_count} 💬{p.reply_count}\n\n  {p.text[:300]}")
        except (FetchError, AnalyzerError) as e:
            st.error(str(e))
        except Exception as e:  # noqa: BLE001
            st.error(f"發生未預期的錯誤：{e}")
