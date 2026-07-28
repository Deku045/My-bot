import os
import logging
import subprocess
import json
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters
import yt_dlp

# إعداد الـ Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

TOKEN = os.environ.get("TELEGRAM_TOKEN")

# ==== إعدادات قابلة للتعديل ====
NUM_CLIPS = 5          # عدد الشورتات المطلوب إنتاجها من كل فيديو
CLIP_DURATION = 30     # مدة كل مقطع بالثواني
EDGE_MARGIN = 5         # هامش بالثواني نتجنبه من أول وآخر الفيديو (انترو/آوترو غالباً)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "أهلاً بك يا محمد! بوت قص الفيديوهات جاهز 🎬\n"
        f"أرسل رابط يوتيوب وهقصّلك {NUM_CLIPS} شورتات منه!"
    )


def get_video_duration(filepath):
    """يرجع مدة الفيديو بالثواني باستخدام ffprobe"""
    command = [
        'ffprobe', '-v', 'error',
        '-show_entries', 'format=duration',
        '-of', 'json', filepath
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    data = json.loads(result.stdout)
    return float(data['format']['duration'])


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


def download_video(youtube_url, output_path="full_video.mp4"):
    ydl_opts = {
        'format': 'mp4/best',
        'outtmpl': output_path,
        'max_filesize': 200 * 1024 * 1024,
        'extractor_args': {'youtube': {'player_client': ['android', 'web']}},
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([youtube_url])
    return output_path


def cut_clip(source_path, start_time, duration, output_filename):
    command = [
        'ffmpeg', '-y', '-i', source_path,
        '-ss', str(start_time), '-t', str(duration),
        '-vf', "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280",
        '-c:v', 'libx264', '-preset', 'ultrafast', '-c:a', 'aac',
        output_filename
    ]
    subprocess.run(command, check=True)
    return output_filename


def download_and_cut_videos(youtube_url):
    """يحمّل الفيديو ويقصّه لعدة شورتات، ويرجع قائمة بأسماء الملفات الناتجة"""
    source_path = download_video(youtube_url)
    total_duration = get_video_duration(source_path)

    start_times = calculate_clip_start_times(
        total_duration, NUM_CLIPS, CLIP_DURATION, EDGE_MARGIN
    )

    output_files = []
    for idx, start_time in enumerate(start_times, start=1):
        # لو الفيديو قصير، خلي مدة القص متناسبة معاه
        actual_duration = min(CLIP_DURATION, max(1, total_duration - start_time))
        output_filename = f"short_{idx}.mp4"
        cut_clip(source_path, start_time, actual_duration, output_filename)
        output_files.append(output_filename)

    if os.path.exists(source_path):
        os.remove(source_path)

    return output_files


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    if "youtube.com" in url or "youtu.be" in url:
        msg = await update.message.reply_text(
            f"⏳ جاري تحميل الفيديو وقص {NUM_CLIPS} شورتات منه، انتظر شوية..."
        )

        output_files = []
        try:
            output_files = download_and_cut_videos(url)

            for idx, output_file in enumerate(output_files, start=1):
                with open(output_file, 'rb') as video_file:
                    await update.message.reply_video(
                        video=video_file,
                        caption=f"Short {idx}/{len(output_files)} 🎬"
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
    application = ApplicationBuilder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))

    print("Bot is starting...")
    application.run_polling(drop_pending_updates=True)


if __name__ == '__main__':
    main()
