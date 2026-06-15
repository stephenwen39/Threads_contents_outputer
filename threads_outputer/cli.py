"""命令列介面。

用法：
    python -m threads_outputer.cli <threads_id> [--max-posts N] [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import sys

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # noqa: BLE001
    pass

from .analyzer import AnalyzerError, generate_video_outlines
from .fetcher import FetchError, ThreadsFetcher
from .models import AnalysisResult


def _print_result(result: AnalysisResult, profile: dict) -> None:
    line = "=" * 60
    print(line)
    print(f"帳號：@{result.username}")
    if profile.get("full_name"):
        print(f"名稱：{profile['full_name']}")
    if profile.get("follower_count") is not None:
        print(f"追蹤者：{profile['follower_count']}")
    print(f"分析貼文數：{result.post_count}")
    print(f"分析方式：{'LLM' if result.generated_by == 'llm' else '本地啟發式'}")
    print(line)
    print("\n【整體價值觀與內容摘要】")
    print(result.summary)
    print("\n【展現的價值觀】")
    print("、".join(result.core_values) or "—")
    print("\n【反覆出現的主題】")
    print("、".join(result.recurring_themes) or "—")

    print(f"\n建議可製作 {len(result.videos)} 支 YouTube 影片：\n")
    for i, v in enumerate(result.videos, 1):
        print(line)
        print(f"影片 {i}：{v.title}")
        print(line)
        print(f"  切入角度：{v.angle}")
        print(f"  目標觀眾：{v.target_audience}")
        print(f"  開場鉤子：{v.hook}")
        print("  內容大綱：")
        for s in v.sections:
            print(f"    - {s}")
        if v.key_messages:
            print("  核心訊息：" + "、".join(v.key_messages))
        if v.call_to_action:
            print(f"  行動呼籲：{v.call_to_action}")
        print()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="輸入 Threads 帳號，產生 YouTube 影片大綱"
    )
    parser.add_argument("threads_id", help="Threads 帳號（@name、網址或純名稱皆可）")
    parser.add_argument("--max-posts", type=int, default=200, help="最多抓取貼文數")
    parser.add_argument(
        "--api-key",
        dest="api_key",
        default=None,
        help="OpenAI（ChatGPT）API key；未提供則讀取環境變數 OPENAI_API_KEY",
    )
    parser.add_argument("--model", default=None, help="LLM 模型（預設 gpt-4o-mini）")
    parser.add_argument("--base-url", dest="base_url", default=None, help="OpenAI 相容 API base url")
    parser.add_argument("--json", dest="json_out", help="將結果輸出成 JSON 檔")
    args = parser.parse_args(argv)

    try:
        print(f"抓取 @{args.threads_id} 的公開貼文中…", file=sys.stderr)
        fetcher = ThreadsFetcher(max_posts=args.max_posts)
        profile, posts = fetcher.fetch(args.threads_id)
        print(f"已取得 {len(posts)} 則貼文，使用 LLM 分析中…", file=sys.stderr)
        result = generate_video_outlines(
            profile.get("username", args.threads_id),
            posts,
            api_key=args.api_key,
            model=args.model,
            base_url=args.base_url,
        )
    except (FetchError, AnalyzerError) as e:
        print(f"錯誤：{e}", file=sys.stderr)
        return 1

    _print_result(result, profile)

    if args.json_out:
        out = {"profile": profile, "analysis": result.to_dict()}
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"\n已輸出 JSON 至 {args.json_out}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
