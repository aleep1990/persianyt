import os
import json
import requests
from google import genai

# Configuration & Environment Variables
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

CACHE_FILE = "channels_cache.json"
MAX_VIDEOS_PER_CHANNEL = 10
OUTLIER_THRESHOLD = 2.5  # ویدیوهایی که حداقل ۲.۵ برابر میانگین بازدید داشته‌اند
TARGET_CHANNEL_COUNT = 1000

# لیست جامع کلمات کلیدی برای کشف ۱۰۰۰ کانال فارسی
SEARCH_KEYWORDS = [
    "فورتنایت فارسی", "گیم پلی فارسی", "اموزش پایتون", "تکنولوژی فارسی",
    "انباکس فارسی", "ولاگ جدید", "دیلی ولاگ", "زندگی در", "چالش جدید",
    "دوربین مخفی فارسی", "سعی کن نخندی", "هوش مصنوعی", "دانستنی ها",
    "کسب درامد دلاری", "رپ فارسی", "موزیک ویدیو جدید", "تحلیل سیاسی",
    "اخبار ایران", "مستند فارسی", "تحلیل روز", "پادکست فارسی", "یوتیوبر فارسی"
]

def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[Cache Load Error]: {e}")
    return {"channels": {}, "processed_videos": []}

def save_cache(cache):
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[Cache Save Error]: {e}")

def discover_persian_channels(existing_channels):
    """کشف انبوه کانال‌های فارسی تا رسیدن به سقف ۱۰۰۰ کانال"""
    if not YOUTUBE_API_KEY:
        print("[CRITICAL]: YOUTUBE_API_KEY is missing!")
        return existing_channels

    channels = dict(existing_channels)
    print(f"Starting discovery... Currently cached: {len(channels)} channels.")

    for kw in SEARCH_KEYWORDS:
        if len(channels) >= TARGET_CHANNEL_COUNT:
            break

        url = "https://www.googleapis.com/youtube/v3/search"
        params = {
            "key": YOUTUBE_API_KEY,
            "q": kw,
            "type": "channel",
            "relevanceLanguage": "fa",
            "part": "snippet",
            "maxResults": 50
        }

        try:
            res = requests.get(url, params=params, timeout=10).json()
            if "error" in res:
                print(f"[YouTube Search Error for '{kw}']: {res['error'].get('message')}")
                continue

            for item in res.get("items", []):
                ch_id = item["snippet"]["channelId"]
                ch_title = item["snippet"]["channelTitle"]
                channels[ch_id] = ch_title

                if len(channels) >= TARGET_CHANNEL_COUNT:
                    break

        except Exception as e:
            print(f"[Discovery Exception for '{kw}']: {e}")

    print(f"Discovery completed. Total channels tracked: {len(channels)}")
    return channels

def get_channel_videos_and_stats(channel_id):
    """دریافت ویدیوها و محاسبه میانگین واقعی بازدید کانال"""
    if not YOUTUBE_API_KEY:
        return []

    try:
        # ۱. دریافت آخرین ویدیوهای کانال
        search_url = f"https://www.googleapis.com/youtube/v3/search?key={YOUTUBE_API_KEY}&channelId={channel_id}&part=snippet,id&order=date&maxResults={MAX_VIDEOS_PER_CHANNEL}&type=video"
        res = requests.get(search_url, timeout=10).json()

        video_ids = [item["id"]["videoId"] for item in res.get("items", []) if "id" in item and "videoId" in item["id"]]
        if not video_ids:
            return []

        # ۲. دریافت آمار دقیق بازدید ویدیوها
        stats_url = f"https://www.googleapis.com/youtube/v3/videos?key={YOUTUBE_API_KEY}&id={','.join(video_ids)}&part=snippet,statistics"
        stats_res = requests.get(stats_url, timeout=10).json()

        videos = []
        for item in stats_res.get("items", []):
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

def analyze_with_ai(title, description):
    """تحلیل هوشمند علت وایرال شدن ویدیو با Gemini یا OpenRouter"""
    prompt = f"""
    به عنوان یک استراتژیست ارشد یوتیوب، علت موفقیت و وایرال شدن این ویدیوی فارسی را در ۲ تا ۳ جمله کوتاه، کاربردی و دقیق تحلیل کن:
    عنوان ویدیو: {title}
    توضیحات: {description}
    """

    # اولویت اول: Gemini 2.5 Flash
    if GEMINI_API_KEY:
        try:
            client = genai.Client(api_key=GEMINI_API_KEY)
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
            )
            if response.text:
                return response.text.strip()
        except Exception as e:
            print(f"[Gemini Error]: {e}")

    # پشتیبان: OpenRouter DeepSeek
    if OPENROUTER_API_KEY:
        try:
            url = "https://openrouter.ai/ai/v1/chat/completions"
            headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"}
            payload = {
                "model": "deepseek/deepseek-chat:free",
                "messages": [{"role": "user", "content": prompt}]
            }
            res = requests.post(url, headers=headers, json=payload, timeout=15)
            if res.status_code == 200:
                return res.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            print(f"[OpenRouter Error]: {e}")

    return "تحلیل هوش مصنوعی به دلیل محدودیت API در دسترس نیست."

def send_telegram_message(message):
    """ارسال گزارش متنی آماده به تلگرام"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[Telegram Alert]: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing!")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }
    try:
        res = requests.post(url, json=payload, timeout=10)
        if res.status_code != 200:
            print(f"[Telegram Send Error {res.status_code}]: {res.text}")
    except Exception as e:
        print(f"[Telegram Exception]: {e}")

def process_pipeline():
    cache = load_cache()

    # ۱. کشف و به روزرسانی فهرست ۱۰۰۰ کانال
    cache["channels"] = discover_persian_channels(cache.get("channels", {}))
    save_cache(cache)

    processed_videos = set(cache.get("processed_videos", []))
    outliers_found = 0

    print(f"Processing videos for {len(cache['channels'])} channels...")

    # ۲. آنالیز ویدیوهای کانال‌ها
    for channel_id, channel_name in cache["channels"].items():
        videos = get_channel_videos_and_stats(channel_id)
        if not videos:
            continue

        # محاسبه میانگین بازدید کانال
        total_views = sum(v["views"] for v in videos)
        avg_views = total_views / len(videos) if len(videos) > 0 else 1

        for video in videos:
            v_id = video["id"]
            views = video["views"]

            # بررسی شرط موفق بودن (Outlier) و عدم ارسال تکراری
            if views >= (avg_views * OUTLIER_THRESHOLD) and v_id not in processed_videos:
                multiplier = round(views / avg_views, 1)

                print(f"🔥 Outlier Detected: {video['title']} ({multiplier}x avg)")

                # ۳. تحلیل هوش مصنوعی
                ai_analysis = analyze_with_ai(video["title"], video["description"])

                # ۴. ساخت گزارش و ارسال به تلگرام
                report = (
                    f"🔥 <b>ویدیوی استثنایی (Outlier) جدید!</b>\n\n"
                    f"📺 <b>کانال:</b> {channel_name}\n"
                    f"📌 <b>عنوان:</b> {video['title']}\n"
                    f"📊 <b>عملکرد:</b> {multiplier} برابر میانگین کانال ({views:,} بازدید)\n"
                    f"🔗 <a href='{video['url']}'>مشاهده ویدیو در یوتیوب</a>\n\n"
                    f"🧠 <b>تحلیل علت موفقیت (AI):</b>\n{ai_analysis}"
                )

                send_telegram_message(report)
                processed_videos.add(v_id)
                outliers_found += 1

    # به‌روزرسانی کش برای جلوگیری از ارسال مجدد ویدیوهای تکراری
    cache["processed_videos"] = list(processed_videos)
    save_cache(cache)
    print(f"Execution finished. {outliers_found} new reports sent to Telegram.")

if __name__ == "__main__":
    process_pipeline()
