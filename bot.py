import os
import logging
import subprocess
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters
import yt_dlp

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

TOKEN = os.environ.get("TELEGRAM_TOKEN")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("أهلاً بك يا محمد! بوت قص وتحويل فيديوهات اليوتيوب إلى شورتس جاهز 🎬\nأرسل لي رابط أي فيديو لنبدأ!")

def download_and_cut_video(youtube_url, output_filename="short_output.mp4"):
    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]',
        'outtmpl': 'full_video.mp4',
        'max_filesize': 100 * 1024 * 1024,
    }
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([youtube_url])

    # قص 30 ثانية (من الثانية 10 إلى 40) وتحويله إلى مقاس الشورتس العمودي 9:16
    command = [
        'ffmpeg', '-y', '-i', 'full_video.mp4',
        '-ss', '10', '-to', '40',
        '-vf', "crop=ih*(9/16):ih,scale=1080:1920",
        '-c:v', 'libx264', '-c:a', 'aac',
        output_filename
    ]
    subprocess.run(command, check=True)
    
    if os.path.exists('full_video.mp4'):
        os.remove('full_video.mp4')
        
    return output_filename

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    if "youtube.com" in url or "youtu.be" in url:
        msg = await update.message.reply_text("⏳ جاري تحميل وقص الفيديو، انتظر قليلاً...")
        
        try:
            output_file = download_and_cut_video(url)
            
            # إرسال الفيديو القصير الناتج للمستخدم
            await update.message.reply_video(video=open(output_file, 'rb'))
            
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
    
