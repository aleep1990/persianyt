import os
import json
import re
import requests
from datetime import datetime, timedelta
from collections import Counter
from google import genai

# ============================================================
# Configuration
# ============================================================
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

CACHE_FILE = "channels_cache.json"

# ---------- Outlier thresholds ----------
DAYS_AGO = 14                    # پنجره زمانی برای ویدیوهای اخیر
MAX_SUBS = 100_000               # حداکثر سابسکرایبر کانال (کانال بزرگ = فرمت غیرقابل تکرار)
MIN_VIEWS_TO_SUBS = 5.0          # حداقل نسبت بازدید به سابسکرایبر
MIN_OUTLIER_FACTOR = 3.0         # حداقل نسبت بازدید ویدیو به میانه کانال
MIN_VIEWS = 10_000               # حداقل بازدید مطلق
BASELINE_VIDEOS = 15             # تعداد ویدیو برای محاسبه میانه کانال

# ---------- Quota management ----------
MAX_SEARCH_CALLS_PER_RUN = 40    # هر search = ۱۰۰ واحد → ۴۰۰۰ واحد
MAX_VERIFY_PER_RUN = 150         # حداکثر ویدیو برای تأیید در هر اجرا
MAX_DEEP_DIVE = 15               # حداکثر Outlier برای تحلیل عمیق (کامنت + AI)

# ============================================================
# Niche sweep keywords — برای کشف نیچ‌ها و ساب‌نیچ‌های جدید
# ============================================================
NICHE_SWEEP_KEYWORDS = [
    # تکنولوژی و هوش مصنوعی
    "هوش مصنوعی", "چت جی پی تی", "اموزش پایتون", "برنامه نویسی",
    "طراحی سایت", "دولوپر", "هک اخلاقی", "دیتا ساینس",
    # مالی و کسب و کار
    "کسب درامد دلاری", "ارز دیجیتال", "بورس ایران", "استارتاپ",
    "دیجیتال مارکتینگ", "فریلنسری", "پسیو اینکام", "ترید",
    # سلامت و روان
    "سلامت روان", "روانشناسی", "افسردگی", "اضطراب", "مدیتیشن",
    "بهره وری", "عادت سازی", "توسعه فردی", "خودشناسی",
    # سبک زندگی
    "ولاگ زندگی", "روزمرگی", "مینیمالیسم", "سفر ارزان", "ولاگ سفر",
    "اشپزی سالم", "دکوراسیون", "مد و فشن", "مراقبت پوست",
    # ورزش و تناسب اندام
    "تناسب اندام", "بدنسازی", "یوگا", "پیلاتس", "لاغری شکم",
    # آموزش
    "اموزش زبان انگلیسی", "اموزش زبان المانی", "اموزش طراحی", "گرافیک",
    "اموزش موسیقی", "گیتار", "پیانو",
    # سرگرمی و فرهنگ
    "تحلیل فیلم", "نقد فیلم", "سینما", "سریال", "انیمه",
    "تاریخ ایران", "تاریخ جهان", "فلسفه", "علمی", "فضا", "فیزیک",
    # پادکست و گفتگو
    "پادکست فارسی", "گفتگو", "مصاحبه", "استندآپ کمدی",
    # خانواده و کودکان
    "تربیت کودک", "روانشناسی کودک", "کارتون فارسی", "شعر کودکانه",
    # خودرو
    "ریویو خودرو", "انباکس ماشین", "ماشین برقی",
    # خبر و تحلیل
    "تحلیل سیاسی", "خبر فوری", "تحلیل اقتصادی",
]

# ============================================================
# Cache helpers
# ============================================================
def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                # سازگاری با نسخه‌های قبلی
                data.setdefault("processed_videos", [])
                data.setdefault("discovered_niches", {})
                data.setdefault("seen_outliers", {})
                return data
        except Exception as e:
            print(f"[Cache Load Error]: {e}")
    return {"processed_videos": [], "discovered_niches": {}, "seen_outliers": {}}


def save_cache(cache):
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[Cache Save Error]: {e}")


# ============================================================
# YouTube API helpers
# ============================================================
def yt_get(endpoint, params, timeout=15):
    """فراخوانی امن YouTube API با هندل خطا"""
    if not YOUTUBE_API_KEY:
        print("[CRITICAL]: YOUTUBE_API_KEY is missing!")
        return None
    params["key"] = YOUTUBE_API_KEY
    url = f"https://www.googleapis.com/youtube/v3/{endpoint}"
    try:
        res = requests.get(url, params=params, timeout=timeout).json()
        if "error" in res:
            print(f"[YT Error {endpoint}]: {res['error'].get('message')}")
            return None
        return res
    except Exception as e:
        print(f"[YT Exception {endpoint}]: {e}")
        return None


def search_breakout_videos(keyword, days_ago=DAYS_AGO, max_results=50):
    """
    جستجوی ویدیوهای بریک‌اوت اخیر.
    ترکیب order=viewCount + publishedAfter = ویدیوهای اخیر با بالاترین بازدید.
    """
    published_after = (datetime.utcnow() - timedelta(days=days_ago)).isoformat("T") + "Z"

    res = yt_get("search", {
        "q": keyword,
        "type": "video",
        "order": "viewCount",
        "publishedAfter": published_after,
        "part": "snippet",
        "maxResults": max_results,
        "relevanceLanguage": "fa",
    })
    if not res:
        return []

    videos = []
    for item in res.get("items", []):
        vid = item.get("id", {})
        if "videoId" not in vid:
            continue
        videos.append({
            "video_id": vid["videoId"],
            "title": item["snippet"]["title"],
            "channel_id": item["snippet"]["channelId"],
            "channel_title": item["snippet"]["channelTitle"],
            "published_at": item["snippet"]["publishedAt"],
            "keyword": keyword,
        })
    return videos


def get_video_stats(video_ids):
    """دریافت آمار دسته‌ای ویدیوها (۱ واحد برای هر دسته‌ی ۵۰تایی)"""
    if not video_ids:
        return {}
    res = yt_get("videos", {
        "id": ",".join(video_ids[:50]),
        "part": "statistics,snippet,contentDetails",
    })
    if not res:
        return {}

    out = {}
    for item in res.get("items", []):
        out[item["id"]] = {
            "views": int(item["statistics"].get("viewCount", 0)),
            "likes": int(item["statistics"].get("likeCount", 0)),
            "comments": int(item["statistics"].get("commentCount", 0)),
            "title": item["snippet"]["title"],
            "description": item["snippet"].get("description", ""),
            "duration": item["contentDetails"].get("duration", ""),
            "tags": item["snippet"].get("tags", []),
        }
    return out


def get_channel_stats(channel_ids):
    """دریافت آمار دسته‌ای کانال‌ها"""
    if not channel_ids:
        return {}
    res = yt_get("channels", {
        "id": ",".join(channel_ids[:50]),
        "part": "statistics,snippet",
    })
    if not res:
        return {}

    out = {}
    for item in res.get("items", []):
        out[item["id"]] = {
            "subscriber_count": int(item["statistics"].get("subscriberCount", 0)),
            "video_count": int(item["statistics"].get("videoCount", 0)),
            "title": item["snippet"]["title"],
            "description": item["snippet"].get("description", ""),
            "country": item["snippet"].get("country", ""),
        }
    return out


def get_channel_baseline(channel_id, max_videos=BASELINE_VIDEOS):
    """محاسبه میانه بازدید آخرین ویدیوهای کانال"""
    res = yt_get("search", {
        "channelId": channel_id,
        "part": "id",
        "order": "date",
        "maxResults": max_videos,
        "type": "video",
    })
    if not res:
        return None

    video_ids = [i["id"]["videoId"] for i in res.get("items", [])
                 if "id" in i and "videoId" in i["id"]]
    if not video_ids:
        return None

    stats = get_video_stats(video_ids)
    views = [stats[v]["views"] for v in video_ids if v in stats]
    if not views:
        return None

    views.sort()
    median = views[len(views) // 2]
    return {"median_views": median, "sample_size": len(views)}


def get_comment_signals(video_id, max_comments=30):
    """استخراج سیگنال تقاضا از کامنت‌ها"""
    res = yt_get("commentThreads", {
        "videoId": video_id,
        "part": "snippet",
        "order": "relevance",
        "maxResults": max_comments,
    })
    if not res:
        return {"demand_ratio": 0, "demand_comments": [], "total": 0}

    demand_phrases = [
        "part 2", "پارت ۲", "پارت 2", "بیشتر", "ادامه بده", "باز هم",
        "لطفا", "لطفاً", "چطور", "آموزش", "اموزش", "tutorial",
        "how did", "چی شد", "دقیقا", "کامل تر",
    ]

    demand = []
    items = res.get("items", [])
    for item in items:
        text = item["snippet"]["topLevelComment"]["snippet"].get("textDisplay", "")
        text_l = text.lower()
        for phrase in demand_phrases:
            if phrase.lower() in text_l:
                demand.append({
                    "text": re.sub(r"<[^>]+>", "", text)[:200],
                    "likes": item["snippet"]["topLevelComment"]["snippet"].get("likeCount", 0),
                })
                break

    return {
        "total": len(items),
        "demand_comments": demand[:5],
        "demand_ratio": round(len(demand) / max(len(items), 1) * 100, 1),
    }


# ============================================================
# Outlier verification
# ============================================================
def verify_outlier(video, channel_stats, baseline):
    """
    بررسی می‌کند که آیا ویدیو یک Outlier واقعی است یا نه.
    سه معیار: اندازه کانال، نسبت بازدید به سابسکرایبر، ضریب Outlier.
    """
    if not channel_stats or not baseline:
        return None

    subs = channel_stats["subscriber_count"]
    views = video["views"]

    # شرط ۱: کانال نباید بزرگ باشه
    if subs == 0 or subs > MAX_SUBS:
        return None

    # شرط ۲: بازدید مطلق حداقل
    if views < MIN_VIEWS:
        return None

    # شرط ۳: نسبت بازدید به سابسکرایبر
    views_to_subs = views / subs
    if views_to_subs < MIN_VIEWS_TO_SUBS:
        return None

    # شرط ۴: ضریب Outlier (نسبت به میانه کانال)
    median = baseline["median_views"]
    if median == 0:
        return None
    outlier_factor = views / median
    if outlier_factor < MIN_OUTLIER_FACTOR:
        return None

    return {
        "outlier_factor": round(outlier_factor, 1),
        "views_to_subs": round(views_to_subs, 1),
        "subs": subs,
        "median_views": median,
        "age_days": (datetime.utcnow() - datetime.fromisoformat(
            video["published_at"].replace("Z", "+00:00")
        ).replace(tzinfo=None)).days,
    }


# ============================================================
# AI analysis
# ============================================================
def analyze_with_ai(title, description, keyword, outlier_meta):
    """تحلیل علت وایرال شدن + پیشنهاد زاویه برای کانال جدید"""
    prompt = f"""به عنوان یک استراتژیست ارشد یوتیوب، این ویدیوی فارسی را که در نیچ «{keyword}» وایرال شده تحلیل کن.

عنوان: {title}
توضیحات: {description[:500]}
آمار: {outlier_meta['outlier_factor']}x میانه کانال | {outlier_meta['views_to_subs']}x سابسکرایبر | {outlier_meta['subs']:,} سابسکرایبر | {outlier_meta['age_days']} روز پیش

در ۴ خط کوتاه و کاربردی پاسخ بده:
۱. چرا این ویدیو وایرال شد؟ (فرمت، قلاب، احساس)
۲. چه نیچ یا ساب‌نیچ جدیدی این موفقیت نشون می‌ده؟
۳. چه زاویه متفاوتی می‌شه روی همین موج گرفت که رقابت کمتری داشته باشه؟
۴. یک عنوان پیشنهادی برای کانال جدید در همین نیچ بده."""

    # اولویت ۱: Gemini
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

    # پشتیبان: OpenRouter
    if OPENROUTER_API_KEY:
        try:
            url = "https://openrouter.ai/api/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": "deepseek/deepseek-chat:free",
                "messages": [{"role": "user", "content": prompt}],
            }
            res = requests.post(url, headers=headers, json=payload, timeout=25)
            if res.status_code == 200:
                return res.json()["choices"][0]["message"]["content"].strip()
            else:
                print(f"[OpenRouter Error {res.status_code}]: {res.text[:200]}")
        except Exception as e:
            print(f"[OpenRouter Exception]: {e}")

    return "تحلیل AI در دسترس نیست."


# ============================================================
# Telegram
# ============================================================
def send_telegram_message(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[Telegram]: credentials missing!")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    try:
        res = requests.post(url, json=payload, timeout=15)
        if res.status_code != 200:
            print(f"[Telegram {res.status_code}]: {res.text[:200]}")
    except Exception as e:
        print(f"[Telegram Exception]: {e}")


# ============================================================
# Main pipeline
# ============================================================
def process_pipeline():
    if not YOUTUBE_API_KEY:
        print("[CRITICAL]: YOUTUBE_API_KEY is missing. Aborting.")
        return

    cache = load_cache()
    processed = set(cache.get("processed_videos", []))
    seen_outliers = cache.get("seen_outliers", {})
    niches = cache.get("discovered_niches", {})

    # ---------- ۱. Sweep روی نیچ‌ها برای پیدا کردن ویدیوهای بریک‌اوت ----------
    print(f"=== Phase 1: Breakout sweep on {len(NICHE_SWEEP_KEYWORDS)} niches ===")

    candidates = {}   # video_id -> video dict
    search_calls = 0
    keywords_run = 0

    for keyword in NICHE_SWEEP_KEYWORDS:
        if search_calls >= MAX_SEARCH_CALLS_PER_RUN:
            print(f"  Search quota cap reached ({search_calls} calls).")
            break

        print(f"  Sweeping: {keyword}")
        videos = search_breakout_videos(keyword)
        search_calls += 1
        keywords_run += 1

        for v in videos:
            if v["video_id"] not in processed and v["video_id"] not in candidates:
                candidates[v["video_id"]] = v

    print(f"Phase 1 done. {len(candidates)} unique candidates from {keywords_run} niches.")

    if not candidates:
        print("No candidates found. Exiting.")
        return

    # ---------- ۲. دریافت آمار ویدیوها و کانال‌ها (batch) ----------
    print(f"=== Phase 2: Verifying {min(len(candidates), MAX_VERIFY_PER_RUN)} candidates ===")

    candidate_ids = list(candidates.keys())[:MAX_VERIFY_PER_RUN]
    video_stats = get_video_stats(candidate_ids)

    channel_ids = list({candidates[v]["channel_id"] for v in candidate_ids if v in candidates})
    channel_stats_map = {}
    for i in range(0, len(channel_ids), 50):
        chunk = channel_ids[i:i+50]
        channel_stats_map.update(get_channel_stats(chunk))

    # ---------- ۳. تأیید Outlier + محاسبه baseline ----------
    outliers = []
    for vid, base_video in candidates.items():
        if vid not in video_stats:
            continue
        stats = video_stats[vid]
        ch_id = base_video["channel_id"]
        ch_stats = channel_stats_map.get(ch_id)

        if not ch_stats:
            continue

        # فیلتر سریع قبل از baseline (ذخیره سهمیه)
        if ch_stats["subscriber_count"] == 0 or ch_stats["subscriber_count"] > MAX_SUBS:
            continue
        if stats["views"] < MIN_VIEWS:
            continue
        if stats["views"] / ch_stats["subscriber_count"] < MIN_VIEWS_TO_SUBS:
            continue

        # محاسبه baseline (یک search call دیگه)
        baseline = get_channel_baseline(ch_id)
        if not baseline:
            continue

        video_with_stats = {**base_video, "views": stats["views"]}
        result = verify_outlier(video_with_stats, ch_stats, baseline)
        if result:
            outliers.append({
                **base_video,
                "views": stats["views"],
                "likes": stats["likes"],
                "comments": stats["comments"],
                "description": stats["description"],
                "channel_subs": ch_stats["subscriber_count"],
                **result,
            })

    print(f"Phase 2 done. {len(outliers)} confirmed outliers.")

    # ---------- ۴. رتبه‌بندی و انتخاب برترین‌ها ----------
    outliers.sort(key=lambda x: (x["views_to_subs"], x["outlier_factor"]), reverse=True)
    top_outliers = outliers[:MAX_DEEP_DIVE]

    # ---------- ۵. تحلیل عمیق + ارسال به تلگرام ----------
    print(f"=== Phase 3: Deep dive on top {len(top_outliers)} outliers ===")

    report_parts = [f"🔥 <b>گزارش Outlier — {len(top_outliers)} مورد برتر</b>\n"]

    for idx, o in enumerate(top_outliers, 1):
        if o["video_id"] in processed:
            continue

        print(f"  [{idx}] {o['title'][:60]} | {o['outlier_factor']}x | {o['views_to_subs']}x")

        # کامنت‌ها
        signals = get_comment_signals(o["video_id"])

        # تحلیل AI
        ai = analyze_with_ai(
            o["title"], o.get("description", ""), o["keyword"],
            {
                "outlier_factor": o["outlier_factor"],
                "views_to_subs": o["views_to_subs"],
                "subs": o["subs"],
                "age_days": o["age_days"],
            }
        )

        report = (
            f"🔥 <b>Outlier #{idx}</b>\n\n"
            f"📺 <b>کانال:</b> {o['channel_title']} ({o['channel_subs']:,} سابسکرایبر)\n"
            f"🎯 <b>نیچ:</b> {o['keyword']}\n"
            f"📌 <b>عنوان:</b> {o['title']}\n"
            f"🔗 <a href='https://www.youtube.com/watch?v={o['video_id']}'>مشاهده ویدیو</a>\n\n"
            f"📊 <b>آمار:</b>\n"
            f"  • بازدید: {o['views']:,}\n"
            f"  • میانه کانال: {o['median_views']:,}\n"
            f"  • ضریب Outlier: <b>{o['outlier_factor']}x</b>\n"
            f"  • نسبت به سابسکرایبر: <b>{o['views_to_subs']}x</b>\n"
            f"  • سن ویدیو: {o['age_days']} روز\n\n"
            f"💬 <b>سیگنال تقاضا:</b> {signals['demand_ratio']}% کامنت‌ها درخواست ادامه دارن\n"
        )

        if signals["demand_comments"]:
            top_c = signals["demand_comments"][0]
            report += f"  └ «{top_c['text'][:100]}»\n\n"

        report += f"🧠 <b>تحلیل AI:</b>\n{ai}\n"

        report_parts.append(report)
        processed.add(o["video_id"])
        seen_outliers[o["video_id"]] = {
            "title": o["title"],
            "keyword": o["keyword"],
            "outlier_factor": o["outlier_factor"],
            "detected_at": datetime.utcnow().isoformat(),
        }

        # آپدیت آمار نیچ
        niches.setdefault(o["keyword"], {"count": 0, "total_factor": 0})
        niches[o["keyword"]]["count"] += 1
        niches[o["keyword"]]["total_factor"] += o["outlier_factor"]

    # ---------- ۶. خلاصه نیچ‌ها ----------
    if niches:
        top_niches = sorted(
            [(k, v["count"], v["total_factor"] / v["count"]) for k, v in niches.items() if v["count"] > 0],
            key=lambda x: x[1], reverse=True
        )[:10]

        summary = "\n\n📈 <b>نیچ‌های داغ این دور:</b>\n"
        for name, count, avg_factor in top_niches:
            summary += f"  • <b>{name}</b> — {count} Outlier | میانگین {avg_factor:.1f}x\n"
        report_parts.append(summary)

    # ---------- ۷. ارسال به تلگرام ----------
    if len(report_parts) > 1:
        # تلگرام محدودیت ۴۰۹۶ کاراکتر داره — تکه‌تکه بفرست
        full_text = "\n\n".join(report_parts)
        for chunk in [full_text[i:i+3800] for i in range(0, len(full_text), 3800)]:
            send_telegram_message(chunk)
    else:
        print("No new outliers to report.")

    # ---------- ۸. ذخیره کش ----------
    cache["processed_videos"] = list(processed)[-5000:]   # سقف ۵۰۰۰ برای حجم فایل
    cache["seen_outliers"] = seen_outliers
    cache["discovered_niches"] = niches
    save_cache(cache)

    print(f"Pipeline finished. {len(top_outliers)} outliers reported. "
          f"Search calls used: {search_calls}.")


if __name__ == "__main__":
    process_pipeline()
