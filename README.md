# Threads → YouTube 影片大綱產生器

輸入一個 **Threads 帳號**，自動：

1. 抓取該帳號的公開貼文
2. 用 **LLM** 分析貼文展現的**價值觀**與**反覆出現的主題**
3. 由 LLM 依資訊量自行判斷可切成**幾支 YouTube 影片**，並產出每支影片的完整大綱
4. 把結果輸出成 **JSON 檔**

> 一律「將貼文輸入 LLM，由 LLM 決定輸出」，不使用本地啟發式。金鑰由使用者自備、用自己的額度。

---

## 快速開始（clone 後在自己電腦用 CLI 產 JSON）

需求：**Python 3.9 以上**、一組 **OpenAI（ChatGPT）API key**。

```bash
# 1. 取得程式碼
git clone <你的-repo-網址>
cd threads_outputer

# 2.（建議）建立虛擬環境
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 3. 安裝 CLI 依賴（輕量，只有 requests / openai / python-dotenv）
pip install -r requirements.txt

# 4. 設定 API key（擇一）
export OPENAI_API_KEY="sk-..."   # Windows PowerShell: $env:OPENAI_API_KEY="sk-..."
#   或在指令加 --api-key sk-...
#   或 cp .env.example .env 後填入 OPENAI_API_KEY

# 5. 執行，產出 JSON
python -m threads_outputer.cli iam_wei_stephen --model gpt-5 --json result.json
```

執行後會在終端機印出影片大綱，並把完整結果寫到 `result.json`。

### （選配）安裝成指令

```bash
pip install .
threads-outputer iam_wei_stephen --model gpt-5 --json result.json
```

---

## CLI 參數

| 參數 | 說明 | 預設 |
| --- | --- | --- |
| `threads_id` | Threads 帳號（`name`、`@name` 或個人頁網址皆可） | 必填 |
| `--api-key` | OpenAI / ChatGPT API key；未給則讀環境變數 `OPENAI_API_KEY` | 無 |
| `--model` | LLM 模型，例如 `gpt-5`、`gpt-4o-mini` | `gpt-4o-mini` |
| `--base-url` | OpenAI 相容服務的 base url（選填） | 無 |
| `--max-posts` | 最多抓取貼文數 | `200` |
| `--json` | 輸出 JSON 檔路徑 | 無（不輸出檔案） |

範例：

```bash
python -m threads_outputer.cli @somebody --max-posts 100 --model gpt-4o-mini --json out.json
```

---

## 輸出 JSON 格式

```jsonc
{
  "profile": { "username": "...", "full_name": "...", "follower_count": 0 },
  "analysis": {
    "username": "...",
    "post_count": 40,
    "summary": "整體價值觀與內容風格摘要",
    "core_values": ["..."],
    "recurring_themes": ["..."],
    "generated_by": "llm",
    "videos": [
      {
        "title": "影片標題",
        "angle": "切入角度",
        "target_audience": "目標觀眾",
        "hook": "開場鉤子",
        "sections": ["段落大綱"],
        "key_messages": ["核心訊息"],
        "call_to_action": "行動呼籲",
        "source_post_ids": ["來源貼文id"]
      }
    ]
  }
}
```

---

## 運作原理

- **抓取**：Threads 無開放公開 API，本工具解析個人頁 HTML 內嵌的 JSON 取得公開貼文。
- **分析**：將貼文交給 LLM 歸納價值觀、主題，再產生影片大綱；貼文量大時自動分批摘要再彙整，避免超出 context。

## 限制

- 僅能取得**公開帳號**的貼文。
- 未登入狀態下，Threads 通常只提供近期數十篇貼文，無法保證取得全部歷史貼文。
- 使用 LLM 會依你的金鑰計費；篇數越多、模型越大，費用與時間越高。
- Threads 頁面結構由 Meta 控制，若其改版可能需要更新解析邏輯。

## 專案結構

```
threads_outputer/
  __init__.py
  models.py     # 資料模型
  fetcher.py    # Threads 公開貼文抓取
  analyzer.py   # LLM 分析 + 產生 YouTube 影片大綱
  cli.py        # 命令列介面
requirements.txt        # 依賴
pyproject.toml          # 套件與 CLI 指令設定
```

## 授權

MIT License，詳見 [LICENSE](LICENSE)。
