import os
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters
import yt_dlp
import subprocess

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

TOKEN = os.environ.get("TELEGRAM_TOKEN")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "أهلاً بك يا محمد! بوت استخراج الشورتس متصل وجاهز للعمل 🎬\nأرسل لي الآن رابط يوتيوب لنبدأ التجربة!"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    if "youtube.com" in url or "youtu.be" in url:
        await update.message.reply_text("⏳ تم استلام رابط يوتيوب بنجاح! جاري معالجة الفيديو...")
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
    application.run_polling()

if __name__ == '__main__':
    main()
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
  
