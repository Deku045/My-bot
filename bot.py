import re
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# 1. إعدادات حسابك الشخصي
API_ID = 26908492
API_HASH = 'b3912d3e30efeda035be52a161e2b713'

# 2. نص الجلسة (String Session) الخاص بك
SESSION_STRING = '1BJWap1wBu1eu0Fn8yk7Q0N0f5dPopGEe7B9DeR7RgQCZDFA2ETF8mMusiocP04kYsbH6iz-dInwu1bGocw0oxFAiBn45Ei1xlous0cKJRWvkUTS1qvjgovrznN3waH_99A3Xc48boP4i3sIcLtFJDB7TivNc1_9w0p8eB2VVAWwPP7RsbeHMoz3ZbSAd4xpAwAkW5MudE0A1581283J1bUo7atSXSesyzvuEkc7tbA6JTTRdZ7mvkVxYRbtYt8306S7c0NwH2u-VHD-5813KLmRJAKetF0ioTgVR_4VvADLEJtbgNL0wK-eIEBK9evJ17BEfIVgqo08iluXYgAepii-bMiJiORk='

# 3. معرفات القنوات
SOURCE_CHANNELS = ['binance_box_channel', 'FreeCryptoBoxes2']
TARGET_CHANNEL = 'crypto_code_box'

client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)

@client.on(events.NewMessage(chats=SOURCE_CHANNELS))
async def handle_new_message(event):
    message_text = event.message.text
    if not message_text:
        return

    # البحث عن أكواد الأظرف الحمراء (8 خانات)
    codes = re.findall(r'\b[A-Z0-9]{8}\b', message_text)
    if codes:
        for code in codes:
            new_msg = f"🎁 **NEW CODE:**\n`{code}`"
            await client.send_message(TARGET_CHANNEL, new_msg)
            print(f"[+] Released code: {code}")

print("⚡ Bot is running on Render...")
client.start()
client.run_until_disconnected()
