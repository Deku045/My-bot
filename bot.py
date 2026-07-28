import os
import logging
import subprocess
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters
import yt_dlp

# إعداد الـ Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

TOKEN = os.environ.get("TELEGRAM_TOKEN")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("أهلاً بك يا محمد! بوت قص الفيديوهات جاهز 🎬\nأرسل رابط يوتيوب لنبدأ!")

def download_and_cut_video(youtube_url, output_filename="short_output.mp4"):
    # اختيار صيغة خفيفة وسريعة التحميل لتفادي التثاقل على السيرفر
    ydl_opts = {
        'format': 'mp4/best',
        'outtmpl': 'full_video.mp4',
        'max_filesize': 50 * 1024 * 1024,
        'extractor_args': {'youtube': {'player_client': ['android', 'web']}},
    }
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([youtube_url])

    # أمر FFmpeg لقص 30 ثانية (من الثانية 10 إلى 40) وتحويل المقاس
    command = [
        'ffmpeg', '-y', '-i', 'full_video.mp4',
        '-ss', '10', '-to', '40',
        '-vf', "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280",
        '-c:v', 'libx264', '-preset', 'ultrafast', '-c:a', 'aac',
        output_filename
    ]
    subprocess.run(command, check=True)
    
    # حذف الفيديو الأصلي بعد القص
    if os.path.exists('full_video.mp4'):
        os.remove('full_video.mp4')
        
    return output_filename

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    if "youtube.com" in url or "youtu.be" in url:
        msg = await update.message.reply_text("⏳ جاري تحميل وقص الفيديو، انتظر قليلاً...")
        
        try:
            output_file = download_and_cut_video(url)
            
            # إرسال الفيديو الناتج للمستخدم
            with open(output_file, 'rb') as video_file:
                await update.message.reply_video(video=video_file)
            
            # حذف الفيديو المقصوص بعد الإرسال
            if os.path.exists(output_file):
                os.remove(output_file)
                
        except Exception as e:
            await update.message.reply_text(f"❌ حدث خطأ أثناء معالجة الفيديو: {str(e)}")
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
