import os
import json
import requests
import googleapiclient.discovery
import numpy as np
from google import genai

# دریافت کلیدها
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

youtube = googleapiclient.discovery.build("youtube", "v3", developerKey=YOUTUBE_API_KEY)
ai_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

CACHE_FILE = "channels.json"

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
    requests.post(url, data=payload)

def load_known_channels():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_known_channels(channels):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(list(set(channels)), f, ensure_ascii=False, indent=2)

def discover_new_channels(keywords, current_channels, limit_per_kw=3):
    """کشف تدریجی کانال‌های جدید فارسی"""
    new_found = []
    for kw in keywords:
        try:
            res = youtube.search().list(
                q=kw,
                type="channel",
                relevanceLanguage="fa",
                part="id",
                maxResults=limit_per_kw
            ).execute()
            for item in res.get("items", []):
                ch_id = item["id"]["channelId"]
                if ch_id not in current_channels and ch_id not in new_found:
                    new_found.append(ch_id)
        except Exception as e:
            print(f"Error searching {kw}: {e}")
    return new_found

def analyze_outlier_with_gemini(video_title, channel_title, views, avg_views):
    """تحلیل هوشمند علت موفقیت ویدیو توسط Gemini"""
    if not ai_client:
        return "تحلیل هوش مصنوعی فعال نیست (کلید Gemini تنظیم نشده)."
    
    prompt = f"""
    تو یک متخصص تحلیل یوتیوب هستی. 
    ویدیویی با عنوان "{video_title}" از کانال "{channel_title}" به بازدید {views:,} رسیده که {round(views/avg_views, 1)} برابر میانگین بازدیدهای عادی این کانال ({int(avg_views):,}) است.
    
    بر اساس الگوی ویدیوهای ویروسی (مفهوم Outlier)، در ۳ الی ۴ جمله خلاصه و کاربردی تحلیل کن که:
    ۱. چرا تیتر و موضوع این ویدیو عملکرد فوق‌العاده‌ای داشته؟ (قلاب، کنجکاوی، هویت مخاطب یا فرمت شناخته‌شده)
    ۲. یوتیوبرهای فارسی چه نکته‌ای می‌توانند از ساختار این ویدیو یاد بگیرند؟
    """
    try:
        response = ai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        return response.text
    except Exception as e:
        return f"خطا در تحلیل هوش مصنوعی: {e}"

def process_channel(channel_id, multiplier=2.5, max_results=20):
    try:
        ch_res = youtube.channels().list(part="snippet,contentDetails", id=channel_id).execute()
        if not ch_res.get("items"):
            return
            
        channel_title = ch_res['items'][0]['snippet']['title']
        uploads_playlist_id = ch_res['items'][0]['contentDetails']['relatedPlaylists']['uploads']
        
        pl_res = youtube.playlistItems().list(
            part="snippet", playlistId=uploads_playlist_id, maxResults=max_results
        ).execute()
        
        video_ids = [item['snippet']['resourceId']['videoId'] for item in pl_res.get('items', [])]
        if not video_ids:
            return

        v_res = youtube.videos().list(part="statistics,snippet", id=",".join(video_ids)).execute()
        
        video_data = []
        views_list = []
        for item in v_res.get('items', []):
            title = item['snippet']['title']
            views = int(item['statistics'].get('viewCount', 0))
            url = f"https://www.youtube.com/watch?v={item['id']}"
            video_data.append({'title': title, 'views': views, 'url': url})
            views_list.append(views)
            
        if not views_list:
            return

        avg_views = np.mean(views_list)
        
        for v in video_data:
            if v['views'] >= avg_views * multiplier:
                ratio = round(v['views'] / avg_views, 1)
                analysis = analyze_outlier_with_gemini(v['title'], channel_title, v['views'], avg_views)
                
                msg = f"<b>🔥 ویدیوی استثنایی (Outlier) جدید!</b>\n\n"
                msg += f"📺 <b>کانال:</b> {channel_title}\n"
                msg += f"📌 <b>عنوان:</b> {v['title']}\n"
                msg += f"📊 <b>عملکرد:</b> {ratio}x برابر میانگین ({v['views']:,} بازدید)\n"
                msg += f"🔗 <a href='{v['url']}'>مشاهده ویدیو</a>\n\n"
                msg += f"🧠 <b>تحلیل هوش مصنوعی (علت موفقیت):</b>\n{analysis}"
                
                send_telegram_message(msg)

    except Exception as e:
        print(f"Error processing {channel_id}: {e}")

if __name__ == "__main__":
    known_channels = load_known_channels()
    
    # کلمات کلیدی برای کشف کانال‌های جدید
    keywords = ["آموزش فارسی", "بررسی گوشی فارسی", "گیم پلی فارسی", "پادکست فارسی", "فارسی یوتیوب"]
    new_channels = discover_new_channels(keywords, known_channels, limit_per_kw=2)
    
    all_channels = list(set(known_channels + new_channels))
    save_known_channels(all_channels)
    
    print(f"تعداد کل کانال‌های دیتابیس شما: {len(all_channels)}")
    
    # اسکن ۵ کانال در هر بار اجرای اکشنز برای عدم تجاوز از سهمیه API
    channels_to_scan = all_channels[:5]
    for ch in channels_to_scan:
        process_channel(ch)