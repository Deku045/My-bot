import os
import re
import json
import base64
import sys
import logging
import platform
import subprocess
import traceback
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters
import yt_dlp

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)
sys.stderr = sys.stdout

def global_excepthook(exc_type, exc_value, exc_tb):
    logger.critical(f"UNHANDLED EXCEPTION: {exc_type.__name__}: {exc_value}")
    logger.critical("".join(traceback.format_exception(exc_type, exc_value, exc_tb)))
sys.excepthook = global_excepthook

logger.info(f"Python: {sys.version}")
logger.info(f"Platform: {platform.system()} {platform.release()}")

TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
YOUTUBE_COOKIES_B64 = os.environ.get("YOUTUBE_COOKIES_B64")

if GROQ_API_KEY:
    logger.info(f"GROQ_API_KEY is set (len={len(GROQ_API_KEY)})")
else:
    logger.warning("GROQ_API_KEY is NOT set - subtitles & AI features disabled")
if TOKEN:
    logger.info(f"TELEGRAM_TOKEN is set (len={len(TOKEN)})")

COOKIES_FILE_PATH = "youtube_cookies.txt"

MODE_TRENDING = "trending"
MODE_SPLIT = "split"

user_modes = {}

GROQ_TRANSCRIBE_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_WHISPER_MODEL = "whisper-large-v3-turbo"
GROQ_LLM_MODEL = "llama-3.3-70b-versatile"

NUM_CLIPS = 5
CLIP_DURATION = 30
MIN_CLIP_DURATION = 15
EDGE_MARGIN = 5
ENABLE_SUBTITLES = True


def setup_youtube_cookies():
    if not YOUTUBE_COOKIES_B64:
        return None
    try:
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
        logger.warning(f"YouTube cookies decode failed: {e}")
        return None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "أهلاً بك يا محمد! بوت قص الفيديوهات جاهز 🎬\n\n"
        "أرسل رابط يوتيوب لأبدأ العمل.\n\n"
        "الأوامر المتاحة:\n"
        "🔹 /trending  -  AI يختار أقوى اللحظات (افتراضي)\n"
        "🔹 /split     -  تقسيم الفيديو كاملاً إلى شورتات\n"
        "🔹 /set_clips 10  -  تغيير عدد الشورتات"
    )


async def set_trending_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_modes[update.effective_user.id] = MODE_TRENDING
    await update.message.reply_text("✅ وضع Trending: AI سيختار أقوى اللحظات!")


async def set_split_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_modes[update.effective_user.id] = MODE_SPLIT
    await update.message.reply_text("✅ وضع Split: سيتم تقسيم الفيديو كاملاً!")


async def set_clips(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        num = int(context.args[0])
        if 1 <= num <= 50:
            context.user_data["num_clips"] = num
            await update.message.reply_text(f"✅ تم تعيين عدد الشورتات إلى {num}")
        else:
            await update.message.reply_text("❌ الرجاء إدخال رقم بين 1 و 50")
    except (IndexError, ValueError):
        await update.message.reply_text("❌ الرجاء إدخال رقم صحيح. مثال: /set_clips 10")


def get_video_duration(filepath):
    command = ['ffmpeg', '-i', filepath]
    result = subprocess.run(command, capture_output=True, text=True)
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", result.stderr)
    if not match:
        raise RuntimeError("Could not parse video duration from ffmpeg output")
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def calculate_clip_start_times(total_duration, num_clips, clip_duration, edge_margin):
    usable_start = edge_margin
    usable_end = max(edge_margin, total_duration - edge_margin - clip_duration)

    if usable_end <= usable_start:
        return [usable_start]

    if num_clips == 1:
        return [usable_start]

    step = (usable_end - usable_start) / (num_clips - 1)
    return [round(usable_start + i * step, 2) for i in range(num_clips)]


def calculate_full_split(total_duration, clip_duration):
    clips = []
    start = EDGE_MARGIN
    while start < total_duration - EDGE_MARGIN:
        remaining = total_duration - start
        dur = min(clip_duration, remaining)
        if dur < MIN_CLIP_DURATION:
            break
        clips.append((start, dur))
        start += dur
    return clips


def extract_audio(video_path, audio_path="audio.mp3"):
    command = [
        'ffmpeg', '-y', '-i', video_path,
        '-vn', '-ac', '1', '-ar', '16000', '-b:a', '32k',
        audio_path
    ]
    subprocess.run(command, check=True, capture_output=True)
    return audio_path


def transcribe_audio(audio_path):
    with open(audio_path, 'rb') as f:
        response = requests.post(
            GROQ_TRANSCRIBE_URL,
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            files={"file": (os.path.basename(audio_path), f, "audio/mpeg")},
            data=[
                ("model", GROQ_WHISPER_MODEL),
                ("response_format", "verbose_json"),
                ("timestamp_granularities[]", "word"),
                ("timestamp_granularities[]", "segment"),
            ],
            timeout=180,
        )
    if response.status_code != 200:
        logger.error(f"Groq Transcribe API error: {response.status_code} - {response.text[:500]}")
        raise RuntimeError(f"Groq transcription failed: {response.status_code}")
    data = response.json()
    return {
        "segments": data.get("segments", []),
        "words": data.get("words", []),
    }


def find_best_moments(segments, total_duration, num_clips, clip_duration, min_clip_duration):
    formatted_lines = [
        f"[{seg['start']:.1f}-{seg['end']:.1f}] {seg['text'].strip()}"
        for seg in segments
    ]

    # اختصار النص لو كان أطول من اللازم لتفادي خطأ 413 (تجاوز حد TPM)
    if len(formatted_lines) > 200:
        step = len(formatted_lines) / 200
        formatted_lines = [formatted_lines[int(i * step)] for i in range(200)]

    transcript_text = "\n".join(formatted_lines)

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
        },
        timeout=120,
    )
    if response.status_code != 200:
        logger.error(f"Groq LLM API error: {response.status_code} - {response.text[:500]}")
        return []

    content = response.json()["choices"][0]["message"]["content"]

    try:
        moments = json.loads(content).get("moments", [])
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse Groq JSON response: {content[:200]}")
        return []

    cleaned = []
    for m in moments:
        start = max(0, float(m["start"]))
        end = min(total_duration, float(m["end"]))
        if end - start >= min_clip_duration:
            cleaned.append({"start": start, "end": end, "reason": m.get("reason", "")})

    return cleaned[:num_clips]


def download_video(youtube_url, output_path="full_video.mp4"):
    ydl_opts = {
        'format': 'mp4/best',
        'outtmpl': output_path,
        'max_filesize': 500 * 1024 * 1024,
        'extractor_args': {'youtube': {'player_client': ['android', 'web']}},
    }

    cookies_path = setup_youtube_cookies()
    if cookies_path:
        ydl_opts['cookiefile'] = cookies_path

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([youtube_url])
    return output_path


def seconds_to_srt_time(seconds):
    seconds = max(0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def build_srt_for_clip(words, clip_start, clip_end, srt_path, words_per_group=3):
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

    if counter <= 1:
        return None

    with open(srt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    logger.info(f"SRT created (words): {srt_path} - {counter - 1} entries")
    return srt_path


def build_srt_from_segments(segments, clip_start, clip_end, srt_path, max_words_per_group=3):
    clip_segments = [
        s for s in segments
        if s["end"] > clip_start and s["start"] < clip_end
    ]
    if not clip_segments:
        return None

    lines = []
    counter = 1

    for seg in clip_segments:
        text = seg["text"].strip()
        words = text.split()
        if not words:
            continue

        seg_start = seg["start"]
        seg_end = seg["end"]
        seg_dur = max(0.5, seg_end - seg_start)

        # نقسم الجملة إلى مجموعات صغيرة من 2-3 كلمات وتوزيع وقت الجملة عليها نسبياً
        groups = [words[i:i + max_words_per_group] for i in range(0, len(words), max_words_per_group)]
        group_dur = seg_dur / len(groups)

        for g_idx, group in enumerate(groups):
            g_start = seg_start + (g_idx * group_dur)
            g_end = seg_start + ((g_idx + 1) * group_dur)

            rel_start = max(g_start, clip_start) - clip_start
            rel_end = min(g_end, clip_end) - clip_start

            if rel_end <= rel_start:
                continue

            g_text = " ".join(group).strip()
            if not g_text:
                continue

            lines.append(str(counter))
            lines.append(f"{seconds_to_srt_time(rel_start)} --> {seconds_to_srt_time(rel_end)}")
            lines.append(g_text)
            lines.append("")
            counter += 1

    if counter <= 1:
        return None

    with open(srt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    logger.info(f"SRT created (smart segment chunks): {srt_path} - {counter - 1} entries")
    return srt_path


def cut_clip(source_path, start_time, duration, output_filename, srt_path=None):
    video_filter = "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280"

    if srt_path:
        if platform.system() == "Windows":
            escaped_path = srt_path.replace('\\', '/').replace(':', '\\:')
        else:
            escaped_path = srt_path
        style = "FontName=Arial,FontSize=14,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=1,Alignment=2,MarginV=60,Bold=1"
        video_filter += f",subtitles={escaped_path}:force_style='{style}'"

    command = [
        'ffmpeg', '-y', '-i', source_path,
        '-ss', str(start_time), '-t', str(duration),
        '-vf', video_filter,
        '-c:v', 'libx264', '-preset', 'ultrafast', '-c:a', 'aac',
        output_filename
    ]

    logger.info(f"Cutting clip: start={start_time}, duration={duration}, srt={srt_path}, filter={video_filter[:100]}")
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error(f"ffmpeg failed: {result.stderr[:1000]}")
        raise RuntimeError(f"ffmpeg error: {result.stderr[:200]}")
    if result.stderr:
        logger.debug(f"ffmpeg ok: {result.stderr[-300:]}")
    return output_filename


def transcribe_video(source_path):
    if not GROQ_API_KEY:
        logger.info("GROQ_API_KEY not set - subtitles disabled")
        return None
    audio_path = "audio.mp3"
    try:
        extract_audio(source_path, audio_path)
        result = transcribe_audio(audio_path)
        segments = result.get("segments", [])
        words = result.get("words", [])
        logger.info(f"Transcription: {len(segments)} segments, {len(words)} words")
        if segments:
            return result
        logger.warning("No segments returned from Groq")
        return None
    except Exception as e:
        logger.warning(f"Transcription failed via Groq: {e}")
        return None
    finally:
        if os.path.exists(audio_path):
            os.remove(audio_path)


def get_smart_moments(segments, total_duration, num_clips):
    if not segments:
        return None
    try:
        moments = find_best_moments(segments, total_duration, num_clips, CLIP_DURATION, MIN_CLIP_DURATION)
        return moments or None
    except Exception as e:
        logger.warning(f"Smart analysis failed, falling back to equal split: {e}")
        return None


def download_and_cut_videos(youtube_url, user_id=None, num_clips=NUM_CLIPS, mode=MODE_TRENDING):
    source_path = download_video(youtube_url)
    total_duration = get_video_duration(source_path)

    transcript = transcribe_video(source_path)
    segments = transcript["segments"] if transcript else None
    words = transcript["words"] if transcript else None
    logger.info(f"Transcript data: segments={'yes' if segments else 'no'}, words={'yes' if words else 'no'}")

    smart_moments = None
    if mode == MODE_TRENDING and segments:
        smart_moments = get_smart_moments(segments, total_duration, num_clips)

    if smart_moments:
        clip_ranges = [
            (m["start"], min(CLIP_DURATION, m["end"] - m["start"]))
            for m in smart_moments
        ]
    elif mode == MODE_SPLIT:
        clip_ranges = calculate_full_split(total_duration, CLIP_DURATION)
    else:
        start_times = calculate_clip_start_times(
            total_duration, num_clips, CLIP_DURATION, EDGE_MARGIN
        )
        clip_ranges = [
            (start, min(CLIP_DURATION, max(1, total_duration - start)))
            for start in start_times
        ]

    output_files = []
    srt_files = []
    logger.info(f"ENABLE_SUBTITLES={ENABLE_SUBTITLES}, has_words={bool(words)}, has_segments={bool(segments)}")
    for idx, (start_time, duration) in enumerate(clip_ranges, start=1):
        srt_path = None
        if ENABLE_SUBTITLES and (words or segments):
            candidate_srt = f"sub_{idx}.srt"
            if words:
                srt_path = build_srt_for_clip(words, start_time, start_time + duration, candidate_srt)
                logger.info(f"Clip {idx}: build_srt_for_clip returned {srt_path}")
            if not srt_path and segments:
                srt_path = build_srt_from_segments(segments, start_time, start_time + duration, candidate_srt)
                logger.info(f"Clip {idx}: build_srt_from_segments returned {srt_path}")
            if srt_path:
                logger.info(f"SRT file exists: {os.path.exists(srt_path)}, size: {os.path.getsize(srt_path) if os.path.exists(srt_path) else 0}")
                srt_files.append(srt_path)
            else:
                logger.warning(f"Clip {idx}: No SRT created")

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
        user_id = update.effective_user.id
        mode = user_modes.get(user_id, MODE_TRENDING)
        num_clips = context.user_data.get("num_clips", NUM_CLIPS)

        mode_text = "🧠 AI يختار أقوى اللحظات..." if mode == MODE_TRENDING else "✂️ تقسيم الفيديو كاملاً..."
        msg = await update.message.reply_text(f"⏳ جاري التحميل...\n{mode_text}")

        output_files = []
        try:
            output_files = download_and_cut_videos(url, user_id, num_clips, mode)

            for idx, output_file in enumerate(output_files, start=1):
                with open(output_file, 'rb') as video_file:
                    await update.message.reply_video(
                        video=video_file,
                        caption=f"Short {idx}/{len(output_files)} 🎬",
                        read_timeout=120,
                        write_timeout=180,
                        connect_timeout=60,
                    )

            await update.message.reply_text(f"✅ تم! {len(output_files)} شورتات جاهزة.")

        except Exception as e:
            logger.error(f"Error: {e}", exc_info=True)
            await update.message.reply_text(f"❌ حدث خطأ: {str(e)}")

        finally:
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
    application.add_handler(CommandHandler("trending", set_trending_mode))
    application.add_handler(CommandHandler("split", set_split_mode))
    application.add_handler(CommandHandler("set_clips", set_clips))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))

    print("Bot is starting...")
    application.run_polling(drop_pending_updates=True)


if __name__ == '__main__':
    main()
