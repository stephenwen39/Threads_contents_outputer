"""資料模型。"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any


@dataclass
class Post:
    """一則 Threads 貼文。"""

    id: str
    text: str
    timestamp: Optional[str] = None
    like_count: int = 0
    reply_count: int = 0
    url: Optional[str] = None
    media_urls: List[str] = field(default_factory=list)
    author: Optional[str] = None  # 貼文作者 username（用來過濾轉貼/他人內容）

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class VideoOutline:
    """單支 YouTube 影片的大綱。"""

    title: str
    angle: str  # 切入角度 / 影片定位
    target_audience: str  # 目標觀眾
    hook: str  # 開場吸睛點
    sections: List[str] = field(default_factory=list)  # 內容段落大綱
    key_messages: List[str] = field(default_factory=list)  # 核心訊息 / 價值觀
    call_to_action: str = ""  # 行動呼籲
    source_post_ids: List[str] = field(default_factory=list)  # 來源貼文

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AnalysisResult:
    """完整分析結果。"""

    username: str
    post_count: int
    summary: str  # 整體價值觀 / 內容風格摘要
    core_values: List[str]  # 展現的價值觀
    recurring_themes: List[str]  # 反覆出現的主題
    videos: List[VideoOutline]
    generated_by: str = "llm"  # 一律由 LLM 產生

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d
