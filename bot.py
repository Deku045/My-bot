import os
import re
import json
import base64
import logging
import subprocess
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters
import yt_dlp

# إعداد الـ Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
YOUTUBE_COOKIES_B64 = os.environ.get("YOUTUBE_COOKIES_B64")

COOKIES_FILE_PATH = "youtube_cookies.txt"


def setup_youtube_cookies():
    """يفك تشفير الكوكيز من متغير البيئة (لو موجود) ويكتبها في ملف محلي يستخدمه yt-dlp.
    بيتجاهل تلقائياً أي أسطر زيادة من أدوات زي certutil (BEGIN/END CERTIFICATE)"""
    if not YOUTUBE_COOKIES_B64:
        return None
    try:
        # نشيل أي سطر مش جزء من الـ base64 نفسه (زي هيدرز certutil) ومسافات فاضية
        clean_lines = [
            line.strip() for line in YOUTUBE_COOKIES_B64.splitlines()
            if line.strip() and not line.strip().startswith('-----')
        ]
        clean_b64 = "".join(clean_lines)

        decoded = base64.b64decode(clean_b64)
        with open(COOKIES_FILE_PATH, "wb") as f:
            f.write(decoded)
        return COOKIES_FILE_PATH
    except Exception as e:
        logger.warning(f"فشل فك تشفير كوكيز يوتيوب: {e}")
        return None

GROQ_TRANSCRIBE_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_WHISPER_MODEL = "whisper-large-v3-turbo"
GROQ_LLM_MODEL = "llama-3.3-70b-versatile"

# ==== إعدادات قابلة للتعديل ====
NUM_CLIPS = 5          # عدد الشورتات المطلوب إنتاجها من كل فيديو
CLIP_DURATION = 30     # مدة كل مقطع بالثواني (الحد الأقصى)
MIN_CLIP_DURATION = 15  # الحد الأدنى لمدة المقطع
EDGE_MARGIN = 5         # هامش بالثواني نتجنبه من أول وآخر الفيديو (انترو/آوترو غالباً)
ENABLE_SUBTITLES = True  # حرق سابتيتل تلقائي على الشورتات (يحتاج GROQ_API_KEY)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mode = "🧠 هختار أقوى اللحظات بالذكاء الاصطناعي" if GROQ_API_KEY else "✂️ تقسيم بالتساوي"
    await update.message.reply_text(
        "أهلاً بك يا محمد! بوت قص الفيديوهات جاهز 🎬\n"
        f"أرسل رابط يوتيوب وهقصّلك {NUM_CLIPS} شورتات منه!\n"
        f"الوضع الحالي: {mode}"
    )


def get_video_duration(filepath):
    """يرجع مدة الفيديو بالثواني، بيقرأها من مخرجات ffmpeg نفسه (بدون الحاجة لـ ffprobe)"""
    command = ['ffmpeg', '-i', filepath]
    # ffmpeg بيطبع معلومات الملف على stderr ويرجع كود خروج غير صفري لو مفيش output،
    # فمش هنستخدم check=True هنا
    result = subprocess.run(command, capture_output=True, text=True)
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", result.stderr)
    if not match:
        raise RuntimeError("مش قادر أحدد مدة الفيديو من مخرجات ffmpeg")
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def calculate_clip_start_times(total_duration, num_clips, clip_duration, edge_margin):
    """يحسب أوقات بداية المقاطع موزعة بالتساوي على طول الفيديو"""
    usable_start = edge_margin
    usable_end = max(edge_margin, total_duration - edge_margin - clip_duration)

    if usable_end <= usable_start:
        # فيديو قصير جداً: مقطع واحد بس من البداية
        return [0]

    if num_clips == 1:
        return [usable_start]

    step = (usable_end - usable_start) / (num_clips - 1)
    return [round(usable_start + i * step, 2) for i in range(num_clips)]


def extract_audio(video_path, audio_path="audio.mp3"):
    """يستخرج الصوت من الفيديو كملف mp3 صغير الحجم (مونو، بيتريت منخفض) عشان يدخل في حد حجم API"""
    command = [
        'ffmpeg', '-y', '-i', video_path,
        '-vn', '-ac', '1', '-ar', '16000', '-b:a', '32k',
        audio_path
    ]
    subprocess.run(command, check=True)
    return audio_path


def transcribe_audio(audio_path):
    """يبعت الصوت لـ Groq Whisper ويرجع توقيت كل كلمة على حدة (word-level) عشان السابتيتل يبقى متزامن مع الكلام"""
    with open(audio_path, 'rb') as f:
        response = requests.post(
            GROQ_TRANSCRIBE_URL,
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            files={"file": (os.path.basename(audio_path), f, "audio/mpeg")},
            data={
                "model": GROQ_WHISPER_MODEL,
                "response_format": "verbose_json",
                "timestamp_granularities[]": "word",
            },
            timeout=180,
        )
    response.raise_for_status()
    data = response.json()
    return {
        "segments": data.get("segments", []),
        "words": data.get("words", []),
    }


def find_best_moments(segments, total_duration, num_clips, clip_duration, min_clip_duration):
    """يبعت الترانسكريبت لموديل Llama على Groq عشان يحدد أقوى اللحظات (بداية/نهاية بالثانية)"""
    transcript_text = "\n".join(
        f"[{seg['start']:.1f}-{seg['end']:.1f}] {seg['text'].strip()}"
        for seg in segments
    )

    system_prompt = (
        "أنت خبير في تحرير الفيديوهات القصيرة (شورتس) الفيروسية. "
        "هيُعطى لك ترانسكريبت لفيديو مع توقيت كل جملة بالثواني. "
        f"مهمتك: اختَر أفضل {num_clips} لحظات 'ضاربة' (مشوّقة، مفاجئة، عاطفية، أو فيها معلومة قوية) "
        f"بحيث تكون مدة كل لحظة بين {min_clip_duration} و {clip_duration} ثانية، ولا تتداخل اللحظات مع بعضها. "
        "رد فقط بصيغة JSON صالحة بدون أي نص إضافي، بالشكل التالي:\n"
        '{"moments": [{"start": 12.5, "end": 42.0, "reason": "سبب مختصر"}, ...]}'
    )

    response = requests.post(
        GROQ_CHAT_URL,
        headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
        json={
            "model": GROQ_LLM_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"مدة الفيديو الكلية: {total_duration:.1f} ثانية.\n\nالترانسكريبت:\n{transcript_text}"},
            ],
            "temperature": 0.4,
            "response_format": {"type": "json_object"},
        },
        timeout=120,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    moments = json.loads(content).get("moments", [])

    # تصفية وتنظيف اللحظات المرجعة عشان نتأكد إنها منطقية
    cleaned = []
    for m in moments:
        start = max(0, float(m["start"]))
        end = min(total_duration, float(m["end"]))
        if end - start >= 5:  # تجاهل أي لحظة قصيرة جداً بشكل غير منطقي
            cleaned.append({"start": start, "end": end, "reason": m.get("reason", "")})

    return cleaned[:num_clips]


def download_video(youtube_url, output_path="full_video.mp4"):
    ydl_opts = {
        'format': 'mp4/best',
        'outtmpl': output_path,
        'max_filesize': 200 * 1024 * 1024,
        'extractor_args': {'youtube': {'player_client': ['android', 'web']}},
    }

    cookies_path = setup_youtube_cookies()
    if cookies_path:
        ydl_opts['cookiefile'] = cookies_path

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([youtube_url])
    return output_path


def seconds_to_srt_time(seconds):
    """يحول الثواني لصيغة توقيت SRT: HH:MM:SS,mmm"""
    seconds = max(0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def build_srt_for_clip(words, clip_start, clip_end, srt_path, words_per_group=3):
    """يبني ملف SRT خاص بمقطع معين، بتوقيت نسبي لبداية المقطع، ومقسّم لمجموعات قصيرة من الكلمات
    (2-3 كلمات) عشان يبقى متزامن مع الكلام زي شورتس تيك توك/ريلز"""
    # نفلتر بس الكلمات اللي جوه نطاق المقطع
    clip_words = [
        w for w in words
        if w["end"] > clip_start and w["start"] < clip_end
    ]

    if not clip_words:
        return None

    lines = []
    counter = 1
    for i in range(0, len(clip_words), words_per_group):
        group = clip_words[i:i + words_per_group]

        rel_start = max(group[0]["start"], clip_start) - clip_start
        rel_end = min(group[-1]["end"], clip_end) - clip_start
        if rel_end <= rel_start:
            continue

        text = " ".join(w["word"].strip() for w in group).strip()
        if not text:
            continue

        lines.append(str(counter))
        lines.append(f"{seconds_to_srt_time(rel_start)} --> {seconds_to_srt_time(rel_end)}")
        lines.append(text)
        lines.append("")
        counter += 1

    with open(srt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return srt_path if counter > 1 else None


def cut_clip(source_path, start_time, duration, output_filename, srt_path=None):
    video_filter = "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280"

    if srt_path:
        # نهرب المسار عشان فلتر ffmpeg، ونحدد ستايل السابتيتل (خط سميك أبيض بحدود سودة، تحت الشاشة)
        escaped_path = srt_path.replace('\\', '\\\\').replace(':', '\\:')
        style = "FontName=Arial,FontSize=13,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=0,Alignment=2,MarginV=60,Bold=1"
        video_filter += f",subtitles='{escaped_path}':force_style='{style}'"

    command = [
        'ffmpeg', '-y', '-i', source_path,
        '-ss', str(start_time), '-t', str(duration),
        '-vf', video_filter,
        '-c:v', 'libx264', '-preset', 'ultrafast', '-c:a', 'aac',
        output_filename
    ]
    subprocess.run(command, check=True)
    return output_filename


def transcribe_video(source_path):
    """يستخرج الصوت من الفيديو ويرجع dict فيه segments (للتحليل) و words (للسابتيتل المتزامن).
    يرجع None لو المفتاح غير موجود أو حصل خطأ"""
    if not GROQ_API_KEY:
        return None

    audio_path = "audio.mp3"
    try:
        extract_audio(source_path, audio_path)
        result = transcribe_audio(audio_path)
        return result if result.get("segments") else None
    except Exception as e:
        logger.warning(f"فشل التفريغ الصوتي عبر Groq: {e}")
        return None
    finally:
        if os.path.exists(audio_path):
            os.remove(audio_path)


def get_smart_moments(segments, total_duration):
    """يحاول يحدد أقوى اللحظات من الترانسكريبت. يرجع None لو فشل أو مفيش ترانسكريبت"""
    if not segments:
        return None
    try:
        moments = find_best_moments(segments, total_duration, NUM_CLIPS, CLIP_DURATION, MIN_CLIP_DURATION)
        return moments or None
    except Exception as e:
        logger.warning(f"فشل التحليل الذكي عبر Groq، هنرجع للتقسيم بالتساوي: {e}")
        return None


def download_and_cut_videos(youtube_url):
    """يحمّل الفيديو ويقصّه لعدة شورتات (أقوى اللحظات لو متاح، وإلا تقسيم بالتساوي)، مع سابتيتل لو متاح"""
    source_path = download_video(youtube_url)
    total_duration = get_video_duration(source_path)

    transcript = transcribe_video(source_path)
    segments = transcript["segments"] if transcript else None
    words = transcript["words"] if transcript else None
    smart_moments = get_smart_moments(segments, total_duration)

    # نحدد نطاقات المقاطع (بداية، مدة) سواء من التحليل الذكي أو التقسيم بالتساوي
    if smart_moments:
        clip_ranges = [
            (m["start"], min(CLIP_DURATION, m["end"] - m["start"]))
            for m in smart_moments
        ]
    else:
        start_times = calculate_clip_start_times(
            total_duration, NUM_CLIPS, CLIP_DURATION, EDGE_MARGIN
        )
        clip_ranges = [
            (start, min(CLIP_DURATION, max(1, total_duration - start)))
            for start in start_times
        ]

    output_files = []
    srt_files = []
    for idx, (start_time, duration) in enumerate(clip_ranges, start=1):
        srt_path = None
        if ENABLE_SUBTITLES and words:
            candidate_srt = f"sub_{idx}.srt"
            srt_path = build_srt_for_clip(words, start_time, start_time + duration, candidate_srt)
            if srt_path:
                srt_files.append(srt_path)

        output_filename = f"short_{idx}.mp4"
        cut_clip(source_path, start_time, duration, output_filename, srt_path=srt_path)
        output_files.append(output_filename)

    if os.path.exists(source_path):
        os.remove(source_path)
    for srt_file in srt_files:
        if os.path.exists(srt_file):
            os.remove(srt_file)

    return output_files


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    if "youtube.com" in url or "youtu.be" in url:
        mode_text = "🧠 بحلل الفيديو وأختار أقوى اللحظات..." if GROQ_API_KEY else f"✂️ جاري تحميل الفيديو وقص {NUM_CLIPS} شورتات بالتساوي..."
        msg = await update.message.reply_text(f"⏳ {mode_text}")

        output_files = []
        try:
            output_files = download_and_cut_videos(url)

            for idx, output_file in enumerate(output_files, start=1):
                with open(output_file, 'rb') as video_file:
                    await update.message.reply_video(
                        video=video_file,
                        caption=f"Short {idx}/{len(output_files)} 🎬",
                        read_timeout=120,
                        write_timeout=180,
                        connect_timeout=60,
                    )

            await update.message.reply_text("✅ تم! دي كل الشورتات الجاهزة.")

        except Exception as e:
            await update.message.reply_text(f"❌ حدث خطأ أثناء معالجة الفيديو: {str(e)}")

        finally:
            # تنظيف كل الملفات الناتجة بعد الإرسال
            for f in output_files:
                if os.path.exists(f):
                    os.remove(f)
    else:
        await update.message.reply_text("❌ الرجاء إرسال رابط يوتيوب صحيح.")


def main():
    if not TOKEN:
        print("Error: TELEGRAM_TOKEN is not set.")
        return
    application = (
        ApplicationBuilder()
        .token(TOKEN)
        .connect_timeout(60)
        .read_timeout(120)
        .write_timeout(180)
        .pool_timeout(60)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))

    print("Bot is starting...")
    application.run_polling(drop_pending_updates=True)


if __name__ == '__main__':
    main()
