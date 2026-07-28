import os
import logging
import subprocess
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters
import yt_dlp

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

TOKEN = os.environ.get("TELEGRAM_TOKEN")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hello! Send me a YouTube link, and I will process it into a short clip for you 🎬"
    )

def download_and_cut_video(youtube_url, output_filename="short_output.mp4"):
    # 1. Download a short segment or low-res version to save server space/time
    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]',
        'outtmpl': 'full_video.mp4',
        'max_filesize': 100 * 1024 * 1024, # Limit to 100MB for safety on free tiers
    }
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([youtube_url])

    # 2. Use FFmpeg to cut a 30-second clip (e.g., from second 10 to 40) and crop to vertical 9:16
    command = [
        'ffmpeg', '-y', '-i', 'full_video.mp4',
        '-ss', '10', '-to', '40',
        '-vf', "crop=ih*(9/16):ih,scale=1080:1920",
        '-c:v', 'libx264', '-c:a', 'aac',
        output_filename
    ]
    subprocess.run(command, check=True)
    
    # Clean up full video
    if os.path.exists('full_video.mp4'):
        os.remove('full_video.mp4')
        
    return output_filename

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    if "youtube.com" in url or "youtu.be" in url:
        msg = await update.message.reply_text("⏳ Downloading and processing your video, please wait...")
        
        try:
            output_file = download_and_cut_video(url)
            
            # Send the resulting short video back to the user
            await update.message.reply_video(video=open(output_file, 'rb'))
            
            # Clean up output file after sending
            if os.path.exists(output_file):
                os.remove(output_file)
                
        except Exception as e:
            await update.message.reply_text(f"❌ An error occurred while processing the video: {strId(e) if 'strId' in globals() else str(e)}")
    else:
        await update.message.reply_text("❌ Please send a valid YouTube link.")

if __name__ == '__main__':
    application = ApplicationBuilder().token(TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    
    print("Bot is running...")
    application.run_polling()
  
