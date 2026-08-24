import os
import json
import requests
import google.generativeai as genai

# Configuration & Environment Variables
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

CACHE_FILE = "channels.json"
MAX_RESULTS = 10
OUTLIER_THRESHOLD = 3.0  # Videos performing 3x better than channel average

# کلمات کلیدی جامع برای کشف خودکار کانال‌های فارسی در تمامی دسته‌بندی‌ها
SEARCH_KEYWORDS = [
    # گیمینگ و فورتنایت
    "فورتنایت فارسی", "Fortnite فارسی", "گیم پلی فورتنایت", "گیم پلی فارسی", "واکثرو فارسی",
    # تکنولوژی و موبایل
    "بررسی موبایل", "تکنولوژی فارسی", "انباکس فارسی", "ترفند گوشی",
    # ولاگ و سبک زندگی
    "ولاگ جدید", "دیلی ولاگ", "زندگی در", "ولاگ فارسی",
    # سرگرمی و چالش
    "چالش جدید", "دوربین مخفی فارسی", "سعی کن نخندی", "فان فارسی",
    # آموزشی و علم
    "آموزش پایتون", "هوش مصنوعی", "دانستنی ها", "کسب درآمد دلاری",
    # موزیک و هنر
    "موزیک ویدیو جدید", "اهنگ جدید فارسی", "رپ فارسی", "کاور موزیک",
    # اخبار، سیاست و تحلیلی
    "تحلیل سیاسی", "اخبار ایران", "مستند فارسی", "تحلیل روز"
]

def analyze_with_gemini(prompt):
    if not GEMINI_API_KEY:
        return None
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel("gemini-1.5-flash")
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"[Gemini Error]: {e}")
        return None

def analyze_with_openrouter(prompt, model_name):
    if not OPENROUTER_API_KEY:
        return None
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": prompt}]
    }
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=15)
        if response.status_code == 200:
            return response.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[OpenRouter {model_name} Error]: {e}")
    return None

def analyze_video(title, description):
    prompt = f"""
    به عنوان یک متخصص و آنالیزور یوتیوب، علت موفقیت و ویرال شدن این ویدیو را در ۲ تا ۳ جمله کوتاه و دقیق به فارسی تحلیل کن:
    عنوان ویدیو: {title}
    توضیحات: {description}
    """

    analysis = analyze_with_gemini(prompt)
    if analysis:
        return analysis

    analysis = analyze_with_openrouter(prompt, "deepseek/deepseek-chat:free")
    if analysis:
        return analysis

    analysis = analyze_with_openrouter(prompt, "qwen/qwen-2.5-72b-instruct:free")
    if analysis:
        return analysis

    return "تحلیل هوش مصنوعی در حال حاضر در دسترس نیست."

def send_telegram_message(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"[Telegram Exception]: {e}")

def load_cache():
    try:
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print(f"[Cache Load Error]: {e}")
    return {}

def save_cache(cache):
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[Cache Save Error]: {e}")

def discover_auto_channels():
    """کشف خودکار کانال‌های فعال فارسی در موضوعات گوناگون"""
    discovered_channels = {}
    for kw in SEARCH_KEYWORDS:
        try:
            url = f"https://www.googleapis.com/youtube/v3/search?key={YOUTUBE_API_KEY}&q={kw}&type=video&part=snippet&maxResults=5&order=date"
            res = requests.get(url, timeout=10).json()
            for item in res.get("items", []):
                snippet = item.get("snippet", {})
                ch_id = snippet.get("channelId")
                ch_title = snippet.get("channelTitle")
                if ch_id and ch_title:
                    discovered_channels[ch_id] = ch_title
        except Exception as e:
            print(f"[Auto-Discovery Error for keyword {kw}]: {e}")
    return discovered_channels

def get_channel_videos(channel_id):
    try:
        url = f"https://www.googleapis.com/youtube/v3/search?key={YOUTUBE_API_KEY}&channelId={channel_id}&part=snippet,id&order=date&maxResults={MAX_RESULTS}&type=video"
        response = requests.get(url, timeout=10).json()
        
        video_ids = [item["id"]["videoId"] for item in response.get("items", []) if "id" in item and "videoId" in item["id"]]
        if not video_ids:
            return []

        stats_url = f"https://www.googleapis.com/youtube/v3/videos?key={YOUTUBE_API_KEY}&id={','.join(video_ids)}&part=snippet,statistics"
        stats_response = requests.get(stats_url, timeout=10).json()
        
        videos = []
        for item in stats_response.get("items", []):
            videos.append({
                "id": item["id"],
                "title": item["snippet"]["title"],
                "description": item["snippet"]["description"],
                "views": int(item["statistics"].get("viewCount", 0)),
                "url": f"https://www.youtube.com/watch?v={item['id']}"
            })
        return videos
    except Exception as e:
        print(f"[YouTube API Error for Channel {channel_id}]: {e}")
        return []

def process_pipeline():
    cache = load_cache()
    
    # کشف خودکار کانال‌ها از تمامی حوزه‌ها
    channels_to_process = discover_auto_channels()
    print(f"Discovered {len(channels_to_process)} unique active channels across all categories.")

    for channel_id, channel_name in channels_to_process.items():
        try:
            videos = get_channel_videos(channel_id)
            if not videos:
                continue
                
            total_views = sum(v["views"] for v in videos)
            avg_views = total_views / len(videos) if videos else 1
            
            if channel_id not in cache:
                cache[channel_id] = []
                
            for video in videos:
                try:
                    video_id = video["id"]
                    views = video["views"]
                    
                    if views >= (avg_views * OUTLIER_THRESHOLD) and video_id not in cache[channel_id]:
                        performance_multiplier = round(views / avg_views, 1) if avg_views > 0 else 1
                        
                        ai_analysis = analyze_video(video["title"], video["description"])
                        
                        msg = (
                            f"🔥 <b>ویدیوی استثنایی (Outlier) جدید!</b>\n\n"
                            f"📺 <b>کانال:</b> {channel_name}\n"
                            f"📌 <b>عنوان:</b> {video['title']}\n"
                            f"📊 <b>عملکرد:</b> {performance_multiplier}x برابر میانگین ({views:,} بازدید)\n"
                            f"🔗 <a href='{video['url']}'>مشاهده ویدیو</a>\n\n"
                            f"🧠 <b>تحلیل هوش مصنوعی (علت موفقیت):</b>\n{ai_analysis}"
                        )
                        
                        send_telegram_message(msg)
                        cache[channel_id].append(video_id)
                except Exception as video_err:
                    print(f"[Error processing video {video.get('id')}]: {video_err}")
                    continue

        except Exception as channel_err:
            print(f"[Error processing channel {channel_name}]: {channel_err}")
            continue
            
    save_cache(cache)

if __name__ == "__main__":
    process_pipeline()
