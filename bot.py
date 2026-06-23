import os
import sys
import json
import re
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from google import genai
import requests

# 修复 Windows 控制台 GBK 编码无法输出 emoji/日文的问题
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ---------- 配置 ----------
load_dotenv()
SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
SLACK_APP_TOKEN = os.environ["SLACK_APP_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
INBOX = os.environ.get("SLACK_INBOX_CHANNEL", "alert-daliy-cargo-test-1")
OUTPUT = os.environ.get("SLACK_OUTPUT_CHANNEL", "news-cargo")

# Daily Cargo 官网链接(固定附在每条命中通知底部)
DAILY_CARGO_URL = "https://www.daily-cargo.com/"

# Supabase 配置
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
SUPABASE_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=minimal",
}

client_genai = genai.Client(api_key=GEMINI_API_KEY)
GEMINI_MODEL = "gemini-3.5-flash"
GEMINI_FALLBACK_MODEL = "gemini-2.0-flash"
app = App(token=SLACK_BOT_TOKEN)
# 获取自身 bot user id，用于过滤自己发出的消息（防止死循环）
try:
    _auth = app.client.auth_test()
    MY_BOT_USER_ID = _auth.get("user_id", "")
    MY_BOT_ID = _auth.get("bot_id", "")
    print(f"🤖 自身 Bot: user_id={MY_BOT_USER_ID} bot_id={MY_BOT_ID}")
except Exception:
    MY_BOT_USER_ID = ""
    MY_BOT_ID = ""
ARCHIVE_DIR = Path(__file__).parent / "archive"
ARCHIVE_DIR.mkdir(exist_ok=True)

# ---------- 筛选规则(オプテックエクスプレス向けにカスタマイズ) ----------
FILTER_PROMPT = """あなたは **オプテックエクスプレス株式会社**(Optec Express)のコンテンツスクリーニング AI です。
Daily Cargo(日刊カーゴ)の記事から、自社業務に関連する情報だけを抽出します。

# 【自社ビジネス背景】
オプテックエクスプレスは「緊急貨物専門」の国際物流会社で、以下を主力事業としています:
- IATA 公認貨物代理店としての国際航空貨物輸送(輸出入)
- 国際ハンドキャリー(成田/羽田/中部/関西/福岡/新千歳の 6 空港対応)
- 日中間メインの高速フェリー海上輸送
- 24時間365日対応の自社通関業務
- 軽貨物チャーター(温度管理車含む)
- CellChain LOGISTICS(リチウムイオン電池など危険品輸送)
- 主要顧客需要: AOG(航空機部品)、半導体、医薬品、自動車部品、展示会、温度管理貨物、DG(危険品)

→ つまり「速さ」「航空スペース」「通関」「日中航路」「危険品/温度管理」に直結する情報は最重要。

# 【タスク】
入力されたテキストまたは画像(画像は OCR で日本語抽出してから判定)が、以下 4 カテゴリーのいずれかに該当するか判定し、該当する場合は **詳細に** 抽出してください。

# 【カテゴリー定義】
1. **輸送異常**: 遅延、欠航、運休、混雑、抜港、スペース不足、ブッキング困難、ストライキ、天候障害、空港閉鎖など
2. **価格・スペース**: 航空運賃、海上運賃、スポット価格、サーチャージ、BAF/CAF、燃油、船腹/機材需給、レート改定
3. **重点業界ニーズ**: AOG、医薬品、半導体、自動車部品、展示会、リチウム電池、温度管理、危険品など、特定業界の輸送需要・トレンド
4. **政策・通関**: 通関手続き、規制改正、IMO、関税、貿易協定、輸出入規制、セキュリティ規制、危険品規則(IATA DGR / IMDG)など

# 【厳守ルール】
- カテゴリーは **最も該当する 1 つだけ** 選ぶ(複数選択禁止)
- すべての文字列フィールドは **日本語** で記述
- `keywords` には **本文中に実際に出現した単語だけ** を入れる(カテゴリ定義のサンプル例をそのまま入れない)
- `key_data` には本文中の **具体的な数字・日付・割合・金額** をそのまま抜き出す(なければ空配列)
- `routes` には本文中の **具体的な空港コード・港湾名・航路** を抜き出す(なければ空配列)
- `impact` は自社視点で「緊急貨物業務へどう影響するか」を 60 字以内で記述
- `article_title` は本文/画像から記事タイトルらしき行を抜き出す(なければ空文字)
- 不確実な場合は match: false

# 【出力フォーマット(該当する場合)】
{
  "match": true,
  "category": "輸送異常",
  "headline": "30字以内の見出し(日本語)",
  "summary": "120字以内の詳細要約(日本語、本文の重要情報を圧縮)",
  "key_data": ["運賃$5.20/kg", "3日遅延", "30%減便"],
  "routes": ["NRT-LAX", "上海港", "JL便"],
  "keywords": ["本文に実在する単語のみ"],
  "excerpt": "本文最重要の1文をそのまま引用",
  "impact": "自社の緊急貨物業務への影響(60字以内、日本語)",
  "article_title": "記事タイトル(あれば)",
  "ocr_text": "画像から抽出した全文テキスト(テキスト入力の場合は元のテキストをそのまま)"
}

# 【出力フォーマット(該当しない場合)】
{"match": false, "reason": "15字以内の理由", "ocr_text": "画像から抽出した全文テキスト(テキスト入力の場合は元のテキストをそのまま)"}

# 【出力厳守】
JSON のみを出力。markdown 記法(```)・説明文・前置きは一切禁止。
---
判断対象:"""

# ---------- ファイル操作 ----------
def today_dir():
    d = ARCHIVE_DIR / datetime.now().strftime("%Y-%m-%d")
    (d / "images").mkdir(parents=True, exist_ok=True)
    return d

def save_archive(entry):
    f = today_dir() / "messages.jsonl"
    with open(f, "a", encoding="utf-8") as fp:
        fp.write(json.dumps(entry, ensure_ascii=False) + "\n")

def load_today_entries():
    """读取当天所有存档记录"""
    f = today_dir() / "messages.jsonl"
    if not f.exists():
        return []
    entries = []
    with open(f, "r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries

def load_today_matches():
    """读取当天所有命中(match=true)的记录"""
    return [e for e in load_today_entries() if e.get("analysis", {}).get("match")]

def load_today_matches_supabase():
    """
    从 Supabase + 本地存档合并今日命中记录。
    Supabase 离线期间写入本地的记录不会丢失，两边取并集（以 slack_ts 去重）。
    """
    local = load_today_matches()
    local_ts = {e["ts"] for e in local}

    if not SUPABASE_URL:
        return local

    today = datetime.now().strftime("%Y-%m-%d")
    sb_entries = []
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/articles",
            headers=SUPABASE_HEADERS,
            params={
                "upload_date": f"eq.{today}",
                "matched": "eq.true",
                "select": "slack_ts,source_urls,analysis",
                "order": "created_at.asc",
            },
            timeout=15,
        )
        if r.status_code == 200:
            for rec in r.json():
                analysis = rec.get("analysis") or {}
                if isinstance(analysis, str):
                    try:
                        analysis = json.loads(analysis)
                    except Exception:
                        analysis = {}
                sb_entries.append({
                    "ts": rec.get("slack_ts", ""),
                    "source_urls": rec.get("source_urls") or [],
                    "analysis": analysis,
                })
        else:
            print(f"⚠️ Supabase 查询失败: {r.status_code}")
    except Exception as e:
        print(f"⚠️ Supabase 查询异常: {e}")

    # 合并：Supabase 结果优先，本地存档补充 Supabase 没有的记录
    sb_ts = {e["ts"] for e in sb_entries}
    merged = sb_entries + [e for e in local if e["ts"] not in sb_ts]
    merged.sort(key=lambda e: e["ts"])
    print(f"📊 今日命中: Supabase {len(sb_entries)} 件 + 本地补充 {len(merged)-len(sb_entries)} 件 = 共 {len(merged)} 件")
    return merged

def count_today_matches():
    """返回当天命中记录数"""
    return len(load_today_matches())

def download_slack_file(file_obj):
    url = file_obj["url_private"]
    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.content

def save_image(image_bytes, file_id, ext):
    path = today_dir() / "images" / f"{file_id}.{ext}"
    with open(path, "wb") as f:
        f.write(image_bytes)
    return str(path)

# ---------- Supabase 操作 ----------
OCR_PROMPT = """あなたは高精度 OCR エンジンです。画像内のテキストを一文字も漏らさず、すべて忠実に書き起こしてください。
【必須ルール】
- 見出し、本文、キャプション、表、数値、日付、署名、ページ番号など、画像に存在するテキストを全部抽出する
- 省略・要約は絶対禁止。途中で切らず最後まで出力する
- レイアウト（段落・改行・インデント）をできるだけ保つ
- 注釈や説明は不要。抽出したテキストのみを出力する"""

def ocr_with_gemini(image_bytes, mime="image/png"):
    """用 Gemini 对图片做纯 OCR，返回提取的全文（无截断）"""
    import time
    from google.genai import types
    parts = [
        types.Part.from_text(text=OCR_PROMPT),
        types.Part.from_bytes(data=image_bytes, mime_type=mime),
    ]
    ocr_schedule = [
        (GEMINI_MODEL,       10),
        (GEMINI_FALLBACK_MODEL, 15),
        ("gemini-2.5-flash-lite", 20),
        (GEMINI_MODEL,        0),
    ]
    for attempt, (model, wait) in enumerate(ocr_schedule):
        try:
            resp = client_genai.models.generate_content(
                model=model,
                contents=[types.Content(role="user", parts=parts)],
                config=types.GenerateContentConfig(max_output_tokens=65536),
            )
            return resp.text.strip()
        except Exception as e:
            err = str(e).lower()
            if attempt < len(ocr_schedule) - 1 and ("503" in err or "overloaded" in err or "unavailable" in err or "resource_exhausted" in err):
                print(f"⏳ OCR 过载({model})，{wait}秒后重试 ({attempt+1})")
                time.sleep(wait)
            else:
                print(f"OCR 失败: {e}")
                return ""
    return ""

def upload_file_to_supabase(file_bytes, filename, mime_type="image/png"):
    """上传文件（图片/PDF）到 Supabase Storage，返回公开 URL"""
    if not SUPABASE_URL:
        return None
    try:
        path = f"{datetime.now().strftime('%Y-%m-%d')}/{filename}"
        upload_url = f"{SUPABASE_URL}/storage/v1/object/article-images/{path}"
        headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": mime_type,
        }
        r = requests.post(upload_url, headers=headers, data=file_bytes, timeout=60)
        if r.status_code in (200, 201):
            return f"{SUPABASE_URL}/storage/v1/object/public/article-images/{path}"
        print(f"Supabase 文件上传失败: {r.status_code} {r.text[:200]}")
    except Exception as e:
        print(f"Supabase 文件上传异常: {e}")
    return None

def upload_image_to_supabase(image_bytes, filename):
    return upload_file_to_supabase(image_bytes, filename, mime_type="image/png")

def save_to_supabase(slack_ts, raw_text, ocr_text="", image_url="",
                     source_urls=None):
    """第一步：将原始抓取内容写入 Supabase（分析前），返回记录 ID"""
    if not SUPABASE_URL:
        return None
    row = {
        "slack_ts": slack_ts,
        "raw_text": raw_text or "",
        "ocr_text": ocr_text or "",
        "image_url": image_url or "",
        "source_urls": source_urls or [],
        "upload_date": datetime.now().strftime("%Y-%m-%d"),
    }
    headers = {**SUPABASE_HEADERS, "Prefer": "return=representation"}
    try:
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/articles",
            headers=headers,
            json=row,
            timeout=15,
        )
        if r.status_code in (200, 201):
            records = r.json()
            record_id = records[0]["id"] if records else None
            print(f"Supabase 入库成功: id={record_id}")
            return record_id
        else:
            print(f"Supabase 入库失败: {r.status_code} {r.text[:200]}")
    except Exception as e:
        print(f"Supabase 入库异常: {e}")
    return None

def update_supabase_analysis(record_id, analysis, matched):
    """第二步：AI 分析完成后，回写分析结果到 Supabase"""
    if not SUPABASE_URL or not record_id:
        return
    patch = {
        "analysis": analysis,
        "matched": matched,
        "category": analysis.get("category", ""),
        "headline": analysis.get("headline", ""),
        "ocr_text": analysis.get("ocr_text", ""),
    }
    try:
        r = requests.patch(
            f"{SUPABASE_URL}/rest/v1/articles?id=eq.{record_id}",
            headers=SUPABASE_HEADERS,
            json=patch,
            timeout=15,
        )
        if r.status_code in (200, 204):
            print(f"Supabase 分析回写成功: id={record_id}")
        else:
            print(f"Supabase 分析回写失败: {r.status_code} {r.text[:200]}")
    except Exception as e:
        print(f"Supabase 分析回写异常: {e}")

# ---------- 从 Slack 事件中提取文本（兜底 blocks/attachments） ----------
def extract_text_from_blocks(blocks):
    """从 Slack rich_text blocks 中递归提取纯文本"""
    texts = []
    if not blocks:
        return ""
    for block in blocks:
        btype = block.get("type", "")
        if btype == "rich_text":
            for elem in block.get("elements", []):
                for sub in elem.get("elements", []):
                    if sub.get("type") == "text":
                        texts.append(sub.get("text", ""))
                    elif sub.get("type") == "link":
                        texts.append(sub.get("url", ""))
        elif btype == "section":
            t = block.get("text", {})
            if isinstance(t, dict):
                texts.append(t.get("text", ""))
            elif isinstance(t, str):
                texts.append(t)
    return "\n".join(texts).strip()

def extract_text_from_attachments(attachments):
    """从 Slack attachments 中提取文本（转发消息等场景）"""
    texts = []
    if not attachments:
        return ""
    for att in attachments:
        if att.get("text"):
            texts.append(att["text"])
        elif att.get("fallback"):
            texts.append(att["fallback"])
        # 处理 message_blocks（转发消息内嵌的 blocks）
        msg_blocks = att.get("message_blocks") or att.get("blocks")
        if msg_blocks:
            extracted = extract_text_from_blocks(msg_blocks)
            if extracted:
                texts.append(extracted)
    return "\n".join(texts).strip()

def get_full_text(event):
    """从事件中尽可能提取完整文本：text → blocks → attachments"""
    text = event.get("text", "")
    if text and text.strip():
        return text
    # text 为空时，尝试从 blocks 提取
    blocks = event.get("blocks")
    if blocks:
        extracted = extract_text_from_blocks(blocks)
        if extracted:
            return extracted
    # 再尝试 attachments（转发消息等）
    attachments = event.get("attachments")
    if attachments:
        extracted = extract_text_from_attachments(attachments)
        if extracted:
            return extracted
    return text

# ---------- URL 提取(本文から) ----------
URL_PATTERN = re.compile(r"https?://[^\s<>\"'|*\]\[)(\u3000]+")

def extract_urls(text):
    if not text:
        return []
    # Slack のリンク表示形式 <url|label> も剥がす
    cleaned = re.sub(r"<(https?://[^|>]+)\|[^>]+>", r"\1", text)
    cleaned = re.sub(r"<(https?://[^>]+)>", r"\1", cleaned)
    return URL_PATTERN.findall(cleaned)

def _is_article_url(url):
    """判断 URL 是否为具体文章页（含数字 ID 或年份路径），过滤掉首页/列表页"""
    import re
    # 必须包含数字片段（年份或文章ID），否则认为是列表/首页
    return bool(re.search(r'/\d{4,}', url))

def fetch_url_content(urls, timeout=12, max_chars=6000):
    """抓取 URL 列表里的网页正文，返回合并后的纯文本（最多 max_chars 字符）"""
    from bs4 import BeautifulSoup
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ja,en;q=0.9",
    }
    collected = []
    per_url = max_chars // max(len(urls[:3]), 1)
    for url in urls[:3]:
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or "utf-8"
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(resp.text, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
                tag.decompose()
            # 依次尝试语义标签，取最长的有效内容
            content = ""
            for sel in ["article", "main", '[class*="article"]', '[class*="content"]', "body"]:
                el = soup.select_one(sel)
                if el:
                    candidate = el.get_text(separator="\n", strip=True)
                    if len(candidate) > len(content):
                        content = candidate
                    if len(content) > 200:
                        break
            if content:
                collected.append(content[:per_url])
                print(f"🌐 抓取成功 {url[:70]} → {len(content)} 字")
            else:
                print(f"🌐 抓取内容为空 {url[:70]}")
        except Exception as e:
            print(f"🌐 抓取失败 {url[:70]}: {e}")
    return "\n\n".join(collected)

# ---------- AI 分析 ----------
def parse_json_loose(s):
    s = s.strip()
    if s.startswith("```"):
        m = re.search(r"```(?:json)?\s*(.+?)\s*```", s, re.DOTALL)
        if m:
            s = m.group(1).strip()
    try:
        parsed = json.loads(s)
        if isinstance(parsed, list):
            parsed = parsed[0] if parsed else {"match": False, "reason": "empty response"}
        return parsed
    except json.JSONDecodeError:
        pass
    # Gemini が複数の JSON オブジェクトを返した場合：raw_decode で全部抽出し match:true を優先
    decoder = json.JSONDecoder()
    objects, pos = [], 0
    while pos < len(s):
        stripped = s[pos:].lstrip()
        if not stripped:
            break
        pos += len(s[pos:]) - len(stripped)
        try:
            obj, end = decoder.raw_decode(stripped)
            if isinstance(obj, dict):
                objects.append(obj)
            elif isinstance(obj, list):
                objects.extend(o for o in obj if isinstance(o, dict))
            pos += end
        except json.JSONDecodeError:
            pos += 1
    if not objects:
        raise ValueError(f"JSON parse failed: {s[:200]}")
    matched = [o for o in objects if o.get("match")]
    return matched[0] if matched else objects[0]

def _call_gemini(model, parts):
    from google.genai import types
    resp = client_genai.models.generate_content(
        model=model,
        contents=[types.Content(role="user", parts=parts)],
    )
    return resp.text

def analyze_with_gemini(text=None, image_bytes=None, mime=None):
    import time
    from google.genai import types
    parts = [types.Part.from_text(text=FILTER_PROMPT)]
    if image_bytes:
        parts.append(types.Part.from_bytes(data=image_bytes, mime_type=mime or "image/png"))
    if text:
        parts.append(types.Part.from_text(text=text))

    # 三个模型轮流试，最多6轮，总等待上限 ~2 分钟
    M1, M2, M3 = GEMINI_MODEL, GEMINI_FALLBACK_MODEL, "gemini-2.5-flash-lite"
    schedule = [
        (M1, 10),
        (M2, 15),
        (M3, 20),
        (M1, 25),
        (M2, 30),
        (M3,  0),   # 最后一次
    ]
    for attempt, (model, wait) in enumerate(schedule):
        try:
            return _call_gemini(model, parts)
        except Exception as e:
            err = str(e).lower()
            if "503" in err or "overloaded" in err or "unavailable" in err or "resource_exhausted" in err:
                if attempt < len(schedule) - 1:
                    print(f"⏳ Gemini 过载({model})，{wait}秒后重试 ({attempt+1}/{len(schedule)})")
                    time.sleep(wait)
                else:
                    raise RuntimeError(f"Gemini API 连续{len(schedule)}次过载，请稍后再试")
            else:
                raise

# ---------- 通知フォーマット ----------
def format_match_message(result, slack_permalink=None, source_urls=None):
    """
    命中した記事を Slack 用 Markdown に整形。
    末尾に必ず Daily Cargo 公式サイトリンクと、可能なら原文 Slack permalink を付ける。
    """
    category = result.get("category", "?")
    headline = result.get("headline", "")
    summary = result.get("summary", "")
    keywords = result.get("keywords", [])
    excerpt = result.get("excerpt", "")
    key_data = result.get("key_data", [])
    routes = result.get("routes", [])
    impact = result.get("impact", "")
    article_title = result.get("article_title", "")

    # 分类对应的 emoji
    emoji = {
        "輸送異常": "🚨",
        "価格・スペース": "💴",
        "重点業界ニーズ": "📦",
        "政策・通関": "📜",
    }.get(category, "📊")

    lines = [f"{emoji} *【{category}】* {headline}".rstrip()]

    if article_title:
        lines.append(f"_{article_title}_")

    lines.append("")
    lines.append(summary)

    if excerpt:
        lines.append("")
        lines.append(f"> {excerpt}")

    # 详细字段:有内容才显示
    detail_parts = []
    if key_data:
        detail_parts.append(f"📌 *数値*: {' / '.join(key_data)}")
    if routes:
        detail_parts.append(f"✈️ *航路/拠点*: {' / '.join(routes)}")
    if keywords:
        detail_parts.append(f"🏷️ *キーワード*: {'、'.join(keywords)}")
    if impact:
        detail_parts.append(f"🎯 *自社影響*: {impact}")
    if detail_parts:
        lines.append("")
        lines.extend(detail_parts)

    # 原文链接（只显示一个）
    if source_urls:
        link = f"🔗 <{source_urls[0]}|Daily Cargo原文>"
    else:
        link = f"🔗 <{DAILY_CARGO_URL}|Daily Cargo原文>"

    lines.append("")
    lines.append(link)

    return "\n".join(lines)

# ---------- 日報フォーマット ----------
CATEGORY_ORDER = ["輸送異常", "価格・スペース", "重点業界ニーズ", "政策・通関"]
CATEGORY_EMOJI = {
    "輸送異常": "🚨",
    "価格・スペース": "💴",
    "重点業界ニーズ": "📦",
    "政策・通関": "📜",
}
# 日报触发关键词
REPORT_TRIGGERS = {"日報", "日报", "生成日报", "レポート"}

def format_daily_report():
    """把当天所有命中记录汇总为一份日报，返回 (header, body) 元组"""
    matches = load_today_matches_supabase()
    date_str = datetime.now().strftime("%Y年%m月%d日")

    header = f"📋 Daily Cargo 日報 — {date_str}"
    lines = []

    # 按分类分组（保留完整 entry 以获取 source_urls）
    grouped = {}
    for entry in matches:
        a = entry.get("analysis", {})
        cat = a.get("category", "その他")
        grouped.setdefault(cat, []).append(entry)

    # 全局递增编号，同一分类归在一个组标题下
    KEYCAPS = ["①","②","③","④","⑤","⑥","⑦","⑧","⑨","⑩",
               "⑪","⑫","⑬","⑭","⑮","⑯","⑰","⑱","⑲","⑳"]
    seq = 1
    for cat in CATEGORY_ORDER:
        items = grouped.get(cat)
        if not items:
            continue
        emoji = CATEGORY_EMOJI.get(cat, "📊")

        # 分类组标题（只显示一次）
        lines.append("")
        lines.append(f"{emoji} *【{cat}】*（{len(items)}件）")
        lines.append("─────────────────")

        for entry in items:
            item = entry.get("analysis", {})
            entry_urls = entry.get("source_urls", [])
            num_mark = KEYCAPS[seq - 1] if seq <= len(KEYCAPS) else f"*[{seq}]*"
            lines.append(f"{num_mark} *{item.get('headline', '')}*")
            lines.append(item.get("summary", ""))

            detail = []
            if item.get("key_data"):
                detail.append(f"📌 数値: {' / '.join(item['key_data'])}")
            if item.get("routes"):
                detail.append(f"✈️ 航路: {' / '.join(item['routes'])}")
            if item.get("impact"):
                detail.append(f"🎯 自社影響: {item['impact']}")
            if detail:
                lines.append(" ｜ ".join(detail))
            # 原文链接（只显示一个）
            if entry_urls:
                lines.append(f"🔗 <{entry_urls[0]}|Daily Cargo原文>")
            else:
                lines.append(f"🔗 <{DAILY_CARGO_URL}|Daily Cargo原文>")
            lines.append("")
            seq += 1

    return header, "\n".join(lines)

# ---------- メッセージハンドラ ----------
@app.event("message")
def handle_message(event, say, client):
    print(f"🔔 RAW: ch={event.get('channel')} text={event.get('text','')[:40]!r} files={len(event.get('files',[]))}")
    subtype = event.get("subtype")
    # 只过滤自身消息（防死循环）和无关事件，允许其他 bot 发送的新闻通过
    if subtype in ("message_changed", "message_deleted", "channel_join",
                   "channel_leave"):
        return
    msg_bot_id = event.get("bot_id", "")
    if msg_bot_id and msg_bot_id == MY_BOT_ID:
        return
    if event.get("user") == MY_BOT_USER_ID:
        return

    channel_id = event.get("channel")
    try:
        info = client.conversations_info(channel=channel_id)
        channel_name = info["channel"]["name"]
    except Exception as e:
        print(f"⚠️ conversations_info 失败: {e}")
        channel_name = "?"
    print(f"📡 channel_name={channel_name!r} INBOX={INBOX!r} match={channel_name == INBOX}")
    if channel_name != INBOX:
        return

    raw_text = event.get("text", "")
    text = get_full_text(event)
    files = event.get("files", [])
    ts = event["ts"]
    if text != raw_text:
        print(f"📝 blocks/attachments 提取文本: {text[:80]!r}")

    # ---------- 日报触发检测 ----------
    if text.strip() in REPORT_TRIGGERS:
        print(f"📋 日报触发: text={text.strip()!r}")
        say(text="📋 日報を生成中...", thread_ts=ts)
        try:
            matches = load_today_matches_supabase()
            print(f"📋 今日命中数: {len(matches)}")
            if not matches:
                say(text="⚠️ 本日は命中記事がありません", thread_ts=ts)
                return
            header, body = format_daily_report()
            print(f"📋 日报生成完成, 推送到 #{OUTPUT}")
            resp = client.chat_postMessage(
                channel=OUTPUT,
                text=f"*{header}*\n{body}",
                unfurl_links=False,
                unfurl_media=False,
            )
            print(f"📋 推送结果: ok={resp.get('ok')}")
            say(
                text=f"✅ 日報を #{OUTPUT} へ送信しました（{len(matches)}件）",
                thread_ts=ts,
            )
        except Exception as e:
            say(
                text=f"❌ 日報生成エラー: {type(e).__name__}: {str(e)[:200]}",
                thread_ts=ts,
            )
        return

    # ---------- 通常の記事分析フロー ----------
    # 流程：① 入库 Supabase → ② AI 分析 → ③ 命中投送 test2
    source_urls = extract_urls(text)

    say(text="📥 受信しました、処理中...", thread_ts=ts)

    try:
        # ====== 第①步：原始内容入库 Supabase ======
        image_files = [f for f in files if f.get("mimetype", "").startswith("image/")]
        pdf_files = [f for f in files if f.get("mimetype") == "application/pdf"]
        saved_items = []  # [(record_id, img_bytes, mime, img_path, img_name), ...]

        # 抓取 URL 正文（仅纯文本消息时），只抓文章页，跳过首页/列表页
        fetched_text = ""
        if not image_files and not pdf_files and source_urls:
            article_urls = [u for u in source_urls if _is_article_url(u)]
            if article_urls:
                fetched_text = fetch_url_content(article_urls)
            else:
                print(f"⏭️ 跳过非文章URL: {source_urls[:2]}")

        # 合并原文 + 抓取内容，供 Gemini 分析用
        if fetched_text:
            analysis_text = f"{text}\n\n【URL本文】\n{fetched_text}".strip() if text.strip() else fetched_text
        else:
            analysis_text = text

        if image_files or pdf_files:
            for f in image_files:
                img_bytes = download_slack_file(f)
                ext = f.get("filetype", "png")
                mime = f.get("mimetype", "image/png")
                img_path = save_image(img_bytes, f["id"], ext)
                sb_image_url = upload_image_to_supabase(
                    img_bytes, f"{f['id']}.{ext}"
                )
                record_id = save_to_supabase(
                    slack_ts=ts,
                    raw_text=text,
                    ocr_text="",
                    image_url=sb_image_url,
                    source_urls=source_urls,
                )
                saved_items.append((record_id, img_bytes, mime, img_path, f.get("name")))
            for f in pdf_files:
                pdf_bytes = download_slack_file(f)
                fname = f.get("name") or f"{f['id']}.pdf"
                sb_pdf_url = upload_file_to_supabase(pdf_bytes, fname, mime_type="application/pdf")
                record_id = save_to_supabase(
                    slack_ts=ts,
                    raw_text=text,
                    ocr_text="",
                    image_url=sb_pdf_url,
                    source_urls=source_urls,
                )
                saved_items.append((record_id, pdf_bytes, "application/pdf", None, fname))
        elif analysis_text:
            # 纯文本（含 URL 抓取补充内容）直接入库
            record_id = save_to_supabase(
                slack_ts=ts,
                raw_text=text,
                ocr_text=fetched_text,
                source_urls=source_urls,
            )
            saved_items.append((record_id, None, None, None, None))
        else:
            say(text="⚠️ テキストも画像もありません、スキップします", thread_ts=ts)
            return

        print(f"✅ 第①步完成：{len(saved_items)} 条记录已入库 Supabase")

        # ====== 第②步：AI 分析 ======
        say(text="🔍 データ保存完了、AI 分析中...", thread_ts=ts)

        for record_id, img_bytes, mime, img_path, img_name in saved_items:
            # Gemini 筛选分析（使用合并后的 analysis_text）
            result_text = analyze_with_gemini(
                text=analysis_text if analysis_text else None,
                image_bytes=img_bytes,
                mime=mime,
            )
            try:
                result = parse_json_loose(result_text)
            except Exception:
                say(text=f"❌ AI の出力が JSON ではありません:\n```{result_text[:300]}```", thread_ts=ts)
                continue

            matched = bool(result.get("match"))

            # 分析结果回写 Supabase（更新之前入库的记录）
            update_supabase_analysis(record_id, result, matched)

            # ====== 第③步：只记录结果，不逐条投送（等日報触发） ======
            if matched:
                try:
                    plink = client.chat_getPermalink(
                        channel=channel_id, message_ts=ts
                    )
                    permalink = plink.get("permalink")
                except Exception:
                    permalink = None

                save_archive({
                    "ts": ts,
                    "datetime": datetime.now().isoformat(),
                    "user": event.get("user"),
                    "text": text,
                    "image_path": img_path,
                    "image_name": img_name,
                    "source_urls": source_urls,
                    "permalink": permalink,
                    "analysis": result,
                })

                n = count_today_matches()
                say(
                    text=f"✅ 命中【{result.get('category')}】（本日{n}件目）※「日報」で一括送信",
                    thread_ts=ts,
                )
            else:
                say(
                    text=f"⏭️ 命中せず({result.get('reason', '—')})",
                    thread_ts=ts,
                )

    except Exception as e:
        say(
            text=f"❌ 処理エラー: {type(e).__name__}: {str(e)[:200]}",
            thread_ts=ts,
        )
        raise

if __name__ == "__main__":
    print(f"🚀 Bot 起動: #{INBOX} を監視、命中時は #{OUTPUT} へ転送")
    print(f"📁 アーカイブ: {ARCHIVE_DIR}")
    print(f"📰 Daily Cargo: {DAILY_CARGO_URL}")
    print(f"🗄️ Supabase: {SUPABASE_URL or 'なし'}")

    SocketModeHandler(app, SLACK_APP_TOKEN).start()
