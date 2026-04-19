# ════════════════════════════════════════════════════════════════
# YouTube → Whisper → Notion Transcription Bot
# ════════════════════════════════════════════════════════════════

import subprocess, sys, os

def install(pkg):
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", pkg])

print("📦 Installing dependencies...")
install("yt-dlp")
install("openai-whisper")
install("requests")
install("google-api-python-client")
subprocess.check_call(["sudo", "apt-get", "-qq", "install", "-y", "ffmpeg"])
print("✅ Dependencies ready\n")

import requests, re, tempfile
from datetime import datetime

# ── CONFIG ───────────────────────────────────────────────────────
NOTION_TOKEN       = "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"
NOTION_PAGE_ID     = "33bdc0ab7de98101af9af7242a4ba23e"
YOUTUBE_CHANNEL_ID = "UCsexetBmFzWSnDdzLT4H1yQ"
YOUTUBE_API_KEY    = "AIzaSyBa_ubaqOEoYTnMizttiOm-wqD3aT1SpBM"
WHISPER_MODEL      = "small"          # tiny/base/small/medium/large
MAX_VIDEOS_PER_RUN = 10
# ─────────────────────────────────────────────────────────────────

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28"
}

# ── Get last transcribed video ID from Notion page ───────────────
def get_all_block_text(page_id):
    """Recursively get all text from a Notion page's blocks."""
    url = f"https://api.notion.com/v1/blocks/{page_id}/children?page_size=100"
    full_text = ""
    while url:
        resp = requests.get(url, headers=NOTION_HEADERS).json()
        for block in resp.get("results", []):
            btype = block.get("type")
            rich  = block.get(btype, {}).get("rich_text", [])
            for rt in rich:
                full_text += rt.get("plain_text", "")
                href = rt.get("href") or ""
                full_text += " " + href
            full_text += "\n"
        cursor = resp.get("next_cursor")
        url = f"https://api.notion.com/v1/blocks/{page_id}/children?page_size=100&start_cursor={cursor}" if cursor else None
    return full_text

print("📄 Reading Notion page for last transcribed video...")
page_text = get_all_block_text(NOTION_PAGE_ID)
pattern   = r'(?:youtu\.be/|youtube\.com/(?:watch\?v=|shorts/))([A-Za-z0-9_-]{11})'
matches   = re.findall(pattern, page_text)
last_video_id = matches[-1] if matches else None

if last_video_id:
    print(f"🔖 Last transcribed video ID: {last_video_id}")
else:
    print("🔖 Page is empty — will transcribe latest videos")

# ── Fetch new videos from YouTube ────────────────────────────────
from googleapiclient.discovery import build as yt_build

print("\n🔍 Fetching channel videos from YouTube...")
yt = yt_build('youtube', 'v3', developerKey=YOUTUBE_API_KEY)

channel_resp         = yt.channels().list(part='contentDetails', id=YOUTUBE_CHANNEL_ID).execute()
uploads_playlist_id  = channel_resp['items'][0]['contentDetails']['relatedPlaylists']['uploads']

all_videos, next_page_token = [], None
while len(all_videos) < 200:
    resp = yt.playlistItems().list(
        part='snippet',
        playlistId=uploads_playlist_id,
        maxResults=50,
        pageToken=next_page_token
    ).execute()
    for item in resp.get('items', []):
        s = item['snippet']
        all_videos.append({
            'id':           s['resourceId']['videoId'],
            'title':        s['title'],
            'published_at': s['publishedAt'],
            'url':          f"https://www.youtube.com/watch?v={s['resourceId']['videoId']}"
        })
    next_page_token = resp.get('nextPageToken')
    if not next_page_token:
        break

# Slice to only new videos (oldest first so doc stays in order)
if last_video_id is None:
    new_videos = list(reversed(all_videos[:MAX_VIDEOS_PER_RUN]))
else:
    ids = [v['id'] for v in all_videos]
    if last_video_id not in ids:
        print("⚠️  Last video not in recent history. Processing latest batch.")
        new_videos = list(reversed(all_videos[:MAX_VIDEOS_PER_RUN]))
    else:
        idx        = ids.index(last_video_id)
        new_videos = list(reversed(all_videos[:idx][:MAX_VIDEOS_PER_RUN]))

if not new_videos:
    print("\n✅ No new videos. You are up to date!")
else:
    print(f"\n🎬 {len(new_videos)} new video(s) to transcribe:")
    for v in new_videos:
        print(f"   • {v['title']}")

# ── Transcribe + Save to Notion ───────────────────────────────────
if new_videos:
    import whisper, yt_dlp

    print(f"\n🧠 Loading Whisper '{WHISPER_MODEL}' model...")
    model = whisper.load_model(WHISPER_MODEL)
    print("✅ Whisper ready\n")

    def download_and_transcribe(url):
        with tempfile.TemporaryDirectory() as tmp:
            audio_base = os.path.join(tmp, 'audio')
            ydl_opts = {
                'format': 'bestaudio/best',
                'outtmpl': audio_base,
                'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '128'}],
                'quiet': True, 'no_warnings': True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
            result = model.transcribe(audio_base + '.mp3', verbose=False)
        return result['text'].strip()

    def format_date(iso):
        dt = datetime.fromisoformat(iso.replace('Z', '+00:00'))
        return dt.strftime('%B %d, %Y')

    def append_to_notion(video, transcription):
        blocks = [
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [
                        {"type": "text", "text": {"content": "Name - "}, "annotations": {"bold": True}},
                        {"type": "text", "text": {"content": video['title'], "link": {"url": video['url']}},
                         "annotations": {"bold": True, "color": "blue"}}
                    ]
                }
            },
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [
                        {"type": "text", "text": {"content": "Date - "}, "annotations": {"bold": True}},
                        {"type": "text", "text": {"content": format_date(video['published_at'])}}
                    ]
                }
            },
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [
                        {"type": "text", "text": {"content": "Transcribe - "}, "annotations": {"bold": True}},
                    ]
                }
            },
        ]

        chunk_size = 1900
        chunks = [transcription[i:i+chunk_size] for i in range(0, len(transcription), chunk_size)]
        for chunk in chunks:
            blocks.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": chunk}}]}
            })

        blocks.append({"object": "block", "type": "divider", "divider": {}})

        requests.patch(
            f"https://api.notion.com/v1/blocks/{NOTION_PAGE_ID}/children",
            headers=NOTION_HEADERS,
            json={"children": blocks}
        )

    # ── Main loop ─────────────────────────────────────────────────
    for i, video in enumerate(new_videos):
        print(f"[{i+1}/{len(new_videos)}] {video['title']}")
        try:
            print("   ⏳ Transcribing...")
            text = download_and_transcribe(video['url'])
            print(f"   ✅ Done ({len(text)} chars) — saving to Notion...")
            append_to_notion(video, text)
            print("   📝 Saved")
        except Exception as e:
            print(f"   ❌ Failed: {e}")

    print(f"\n🎉 All done! View your Notion page:")
    print(f"   https://www.notion.so/{NOTION_PAGE_ID}")
