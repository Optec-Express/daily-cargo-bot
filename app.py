"""
Daily Cargo Bot — Vercel (Flask entry point)
Vercel auto-detects Flask and serves this file.
"""
import json
import os
import re
import time
from datetime import datetime
from urllib.parse import quote as url_quote
from zoneinfo import ZoneInfo
import requests as http
from flask import Flask, request, jsonify
from google import genai
from google.genai import types

app = Flask(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
SLACK_BOT_TOKEN = os.environ.get('SLACK_BOT_TOKEN', '').strip()
GEMINI_API_KEY  = os.environ.get('GEMINI_API_KEY', '').strip()
SUPABASE_URL    = os.environ.get('SUPABASE_URL', '').strip()
SUPABASE_KEY    = os.environ.get('SUPABASE_SERVICE_KEY', '').strip()
INBOX           = os.environ.get('SLACK_INBOX_CHANNEL', 'alert-daliy-cargo-test-1').strip()
OUTPUT          = os.environ.get('SLACK_OUTPUT_CHANNEL', 'news-cargo').strip()
DAILY_CARGO_URL = 'https://www.daily-cargo.com/'

GEMINI_MODELS   = ['gemini-2.5-flash-lite', 'gemini-2.0-flash', 'gemini-2.0-flash-lite']
REPORT_TRIGGERS = {'日報', '日报', '生成日报', 'レポート'}
CATEGORY_ORDER  = ['輸送異常', '価格・スペース', '重点業界ニーズ', '政策・通関', '市場動向・実績']
CATEGORY_EMOJI  = {'輸送異常': '🚨', '価格・スペース': '💰', '重点業界ニーズ': '🏭', '政策・通関': '📜', '市場動向・実績': '📊'}
KEYCAPS = ['①','②','③','④','⑤','⑥','⑦','⑧','⑨','⑩',
           '⑪','⑫','⑬','⑭','⑮','⑯','⑰','⑱','⑲','⑳']

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

# 【カテゴリー定義】
1. **輸送異常**: 遅延、欠航、運休、混雑、スペース不足、ストライキ、天候障害、空港閉鎖など
2. **価格・スペース**: 航空運賃、海上運賃、スポット価格、サーチャージ、燃油、レート改定
3. **重点業界ニーズ**: AOG、医薬品、半導体、自動車部品、展示会、リチウム電池、温度管理、危険品
4. **政策・通関**: 通関手続き、規制改正、関税、貿易協定、輸出入規制、危険品規則
5. **市場動向・実績**: 空港・航空会社・上屋の貨物取扱量実績、前年比、市場シェア、路線需給動向、業界統計データ

# 【厳守ルール】
- カテゴリーは最も該当する1つだけ
- すべての文字列フィールドは日本語
- key_data: 本文中の具体的な数字・日付・金額(なければ空配列)
- routes: 本文中の空港コード・港湾名・航路(なければ空配列)
- impact: 自社視点で緊急貨物業務への影響(60字以内)
- 不確実な場合は match: false

# 【出力フォーマット(該当する場合)】
{"match":true,"category":"輸送異常","headline":"30字以内","summary":"120字以内","key_data":[],"routes":[],"keywords":[],"excerpt":"最重要の1文","impact":"60字以内","article_title":"","ocr_text":"抽出全文"}

# 【出力フォーマット(該当しない場合)】
{"match":false,"reason":"15字以内","ocr_text":"抽出全文"}

# 【出力厳守】JSONのみ。markdown・説明文禁止。
---
判断対象:"""


# ── Utilities ─────────────────────────────────────────────────────────────────
def get_today():
    return datetime.now(ZoneInfo('Asia/Tokyo')).strftime('%Y-%m-%d')


def extract_urls(text):
    if not text:
        return []
    cleaned = re.sub(r'<(https?://[^|>]+)\|[^>]+>', r'\1', text)
    cleaned = re.sub(r'<(https?://[^>]+)>', r'\1', cleaned)
    return re.findall(r'https?://[^\s<>"\'\[\]()]+', cleaned)


def is_article_url(url):
    try:
        from urllib.parse import urlparse
        return bool(re.search(r'/\d{4,}', urlparse(url).path))
    except Exception:
        return False


def fetch_url_content(urls, max_chars=6000):
    from bs4 import BeautifulSoup
    ua = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    collected = []
    per_url = max_chars // max(len(urls[:3]), 1)
    for url in urls[:3]:
        try:
            resp = http.get(url, headers={'User-Agent': ua, 'Accept-Language': 'ja,en;q=0.9'},
                            timeout=12)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, 'html.parser')
            for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside']):
                tag.decompose()
            content = ''
            for sel in ['article', 'main', '[class*="article"]', '[class*="content"]', 'body']:
                el = soup.select_one(sel)
                if el:
                    candidate = el.get_text(separator='\n', strip=True)
                    if len(candidate) > len(content):
                        content = candidate
                    if len(content) > 200:
                        break
            if content:
                collected.append(content[:per_url])
        except Exception as e:
            print(f'URL fetch failed {url[:60]}: {e}')
    return '\n\n'.join(collected)


def parse_json_loose(s):
    s = s.strip()
    if s.startswith('```'):
        m = re.search(r'```(?:json)?\s*(.+?)\s*```', s, re.DOTALL)
        if m:
            s = m.group(1).strip()
    try:
        parsed = json.loads(s)
        if isinstance(parsed, list):
            parsed = parsed[0] if parsed else {'match': False, 'reason': 'empty'}
        return parsed
    except json.JSONDecodeError:
        pass
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
        raise ValueError(f'JSON parse failed: {s[:200]}')
    matched = [o for o in objects if o.get('match')]
    return matched[0] if matched else objects[0]


# ── Supabase ──────────────────────────────────────────────────────────────────
def _sb_headers():
    return {
        'apikey': SUPABASE_KEY,
        'Authorization': f'Bearer {SUPABASE_KEY}',
        'Content-Type': 'application/json',
        'Prefer': 'return=representation',
    }


def dedup_check(slack_ts):
    resp = http.get(
        f'{SUPABASE_URL}/rest/v1/articles?slack_ts=eq.{url_quote(slack_ts)}&select=id',
        headers=_sb_headers(), timeout=10,
    )
    return resp.status_code == 200 and len(resp.json()) > 0


def save_raw(ev):
    row = {
        'slack_ts':    ev['ts'],
        'date':        get_today(),
        'raw_text':    ev.get('text', ''),
        'ocr_text':    '',
        'image_url':   '',
        'source_urls': extract_urls(ev.get('text', '')),
        'slack_files': ev.get('files', []),
        'matched':     False,
        'analysis':    {'status': 'processing'},
    }
    resp = http.post(
        f'{SUPABASE_URL}/rest/v1/articles',
        headers=_sb_headers(), json=row, timeout=15,
    )
    if resp.status_code == 201:
        data = resp.json()
        return data[0]['id'] if data else None
    print(f'Supabase insert failed: {resp.status_code} {resp.text[:200]}')
    return None


def sb_patch(row_id, data):
    http.patch(
        f'{SUPABASE_URL}/rest/v1/articles?id=eq.{row_id}',
        headers=_sb_headers(), json=data, timeout=15,
    )


def load_today_matches():
    today = get_today()
    resp = http.get(
        f'{SUPABASE_URL}/rest/v1/articles?date=eq.{today}&matched=eq.true&order=created_at.asc',
        headers=_sb_headers(), timeout=15,
    )
    return resp.json() if resp.status_code == 200 else []


def upload_to_supabase(file_bytes, filename, mime_type):
    today = get_today()
    path = f'{today}/{filename}'
    resp = http.post(
        f'{SUPABASE_URL}/storage/v1/object/article-images/{path}',
        headers={
            'apikey': SUPABASE_KEY,
            'Authorization': f'Bearer {SUPABASE_KEY}',
            'Content-Type': mime_type,
        },
        data=file_bytes, timeout=60,
    )
    if resp.status_code in (200, 201):
        return f'{SUPABASE_URL}/storage/v1/object/public/article-images/{path}'
    print(f'Storage upload failed: {resp.status_code} {resp.text[:200]}')
    return None


# ── Gemini ────────────────────────────────────────────────────────────────────
_gemini_client = None


def _get_gemini():
    global _gemini_client
    if _gemini_client is None:
        _gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    return _gemini_client


def analyze_with_gemini(text=None, image_bytes=None, mime=None):
    client = _get_gemini()
    parts = [types.Part.from_text(text=FILTER_PROMPT)]
    if image_bytes:
        parts.append(types.Part.from_bytes(data=image_bytes, mime_type=mime or 'image/png'))
    if text:
        parts.append(types.Part.from_text(text=text))

    schedule = [
        (GEMINI_MODELS[0],  5),
        (GEMINI_MODELS[1],  8),
        (GEMINI_MODELS[2], 12),
        (GEMINI_MODELS[0], 15),
        (GEMINI_MODELS[1], 20),
        (GEMINI_MODELS[2],  0),
    ]
    for model_name, wait in schedule:
        try:
            resp = client.models.generate_content(
                model=model_name,
                contents=[types.Content(role='user', parts=parts)],
                config=types.GenerateContentConfig(temperature=0.1),
            )
            print(f'Gemini OK ({model_name})')
            return parse_json_loose(resp.text)
        except Exception as e:
            err = str(e).lower()
            print(f'Gemini error ({model_name}): {str(e)[:100]}')
            if wait and '404' not in err:
                time.sleep(wait)
    raise RuntimeError('Gemini API 連続失敗')


# ── Slack ─────────────────────────────────────────────────────────────────────
def slack_post(channel, text, thread_ts=None):
    body = {'channel': channel, 'text': text, 'unfurl_links': False, 'unfurl_media': False}
    if thread_ts:
        body['thread_ts'] = thread_ts
    resp = http.post(
        'https://slack.com/api/chat.postMessage',
        headers={'Authorization': f'Bearer {SLACK_BOT_TOKEN}', 'Content-Type': 'application/json'},
        json=body, timeout=15,
    )
    result = resp.json()
    if not result.get('ok'):
        print(f'Slack post error: {result.get("error")}')
    return result


def download_slack_file(file_obj):
    resp = http.get(
        file_obj['url_private'],
        headers={'Authorization': f'Bearer {SLACK_BOT_TOKEN}'},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.content


# ── Daily Report ──────────────────────────────────────────────────────────────
def format_daily_report(matches):
    date_str = datetime.now(ZoneInfo('Asia/Tokyo')).strftime('%Y年%m月%d日')
    lines = [f'*📋 Daily Cargo 日報 — {date_str}*', '']
    grouped = {}
    for entry in matches:
        cat = (entry.get('analysis') or {}).get('category', 'その他')
        grouped.setdefault(cat, []).append(entry)
    seq = 1
    for cat in CATEGORY_ORDER:
        items = grouped.get(cat)
        if not items:
            continue
        emoji = CATEGORY_EMOJI.get(cat, '📊')
        lines.append(f'{emoji} *【{cat}】*（{len(items)}件）')
        lines.append('─────────────────')
        for entry in items:
            a = entry.get('analysis') or {}
            urls = entry.get('source_urls') or []
            num = KEYCAPS[seq - 1] if seq <= len(KEYCAPS) else f'[{seq}]'
            lines.append(f'{num} *{a.get("headline", "")}*')
            if a.get('summary'):
                lines.append(a['summary'])
            detail = []
            if a.get('key_data'):
                detail.append(f'📌 数値: {" / ".join(a["key_data"])}')
            if a.get('routes'):
                detail.append(f'✈️ 航路: {" / ".join(a["routes"])}')
            if a.get('impact'):
                detail.append(f'🎯 自社影響: {a["impact"]}')
            if detail:
                lines.append(' ｜ '.join(detail))
            lines.append(f'🔗 <{urls[0] if urls else DAILY_CARGO_URL}|Daily Cargo原文>')
            lines.append('')
            seq += 1
    return '\n'.join(lines)


# ── Core event processor ──────────────────────────────────────────────────────
def process_event(ev, row_id):
    ts      = ev['ts']
    channel = ev.get('channel', INBOX)
    text    = ev.get('text', '')
    files   = ev.get('files', [])
    source_urls = extract_urls(text)

    if text.strip() in REPORT_TRIGGERS:
        slack_post(channel, '📋 日報を生成中...', thread_ts=ts)
        try:
            matches = load_today_matches()
            if not matches:
                slack_post(channel, '⚠️ 本日は命中記事がありません', thread_ts=ts)
            else:
                report = format_daily_report(matches)
                slack_post(OUTPUT, report)
                slack_post(channel, f'✅ 日報を #{OUTPUT} へ送信しました（{len(matches)}件）', thread_ts=ts)
        except Exception as e:
            slack_post(channel, f'❌ 日報生成エラー: {str(e)[:200]}', thread_ts=ts)
        sb_patch(row_id, {'analysis': {'skipped': 'daily_report_trigger'}})
        return

    slack_post(channel, '📥 受信しました、処理中...', thread_ts=ts)

    image_files = [f for f in files if f.get('mimetype', '').startswith('image/')]
    pdf_files   = [f for f in files if f.get('mimetype') == 'application/pdf']
    all_files   = image_files + pdf_files

    fetched_text = ''
    if not all_files and source_urls:
        article_urls = [u for u in source_urls if is_article_url(u)]
        if article_urls:
            fetched_text = fetch_url_content(article_urls)

    if fetched_text and text.strip():
        analysis_text = f'{text}\n\n【URL本文】\n{fetched_text}'
    elif fetched_text:
        analysis_text = fetched_text
    else:
        analysis_text = text

    if not analysis_text.strip() and not all_files:
        slack_post(channel, '⚠️ テキストも画像もありません、スキップします', thread_ts=ts)
        sb_patch(row_id, {'analysis': {'skipped': 'no_content'}})
        return

    slack_post(channel, '🔍 AI 分析中...', thread_ts=ts)

    try:
        if all_files:
            f = all_files[0]
            file_bytes = download_slack_file(f)
            mime  = f.get('mimetype', 'image/png')
            fname = f.get('name') or f'{f["id"]}.{f.get("filetype", "bin")}'
            sb_url = upload_to_supabase(file_bytes, fname, mime)
            sb_patch(row_id, {'image_url': sb_url or '', 'ocr_text': fetched_text})
            result = analyze_with_gemini(image_bytes=file_bytes, mime=mime)
        else:
            sb_patch(row_id, {'ocr_text': fetched_text})
            result = analyze_with_gemini(text=analysis_text)
    except Exception as e:
        slack_post(channel, f'❌ 処理エラー: {str(e)[:200]}', thread_ts=ts)
        sb_patch(row_id, {'analysis': {'error': str(e)[:300]}})
        return

    sb_patch(row_id, {
        'analysis': result,
        'matched':  bool(result.get('match')),
        'category': result.get('category', ''),
        'headline': result.get('headline', ''),
        'ocr_text': result.get('ocr_text', ''),
    })

    if result.get('match'):
        n = len(load_today_matches())
        slack_post(channel, f'✅ 命中【{result["category"]}】（本日{n}件目）※「日報」で一括送信', thread_ts=ts)
    else:
        slack_post(channel, f'⏭️ 命中せず({result.get("reason", "—")})', thread_ts=ts)


# ── Flask route ───────────────────────────────────────────────────────────────
@app.route('/api/slack', methods=['POST'])
def slack_webhook():
    try:
        body = request.get_json(force=True)

        if body.get('type') == 'url_verification':
            return jsonify({'challenge': body['challenge']})

        if body.get('type') == 'event_callback':
            ev = body['event']
            skip = {'message_changed', 'message_deleted', 'channel_join', 'channel_leave'}
            if (ev.get('type') == 'message'
                    and ev.get('subtype') not in skip
                    and not ev.get('bot_id')):
                if not dedup_check(ev['ts']):
                    row_id = save_raw(ev)
                    if row_id:
                        process_event(ev, row_id)

    except Exception as e:
        print(f'Webhook error: {e}')

    return 'OK', 200


@app.route('/', methods=['GET'])
def health():
    return 'Daily Cargo Bot running', 200
