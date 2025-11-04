import os
import threading
import time
import re
from flask import Flask, redirect, request, session
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
import random
import asyncio
from datetime import datetime, timedelta

# Render.com specific configuration
if 'RENDER' in os.environ:
    BASE_URL = f"https://{os.environ.get('RENDER_SERVICE_NAME')}.onrender.com"
else:
    BASE_URL = f"https://{os.getenv('REPLIT_DEV_DOMAIN', 'localhost:5000')}"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OAUTH_CLIENT_SECRETS_FILE = os.getenv("OAUTH_CLIENT_SECRETS_FILE", "client_secret.json")
FLASK_SECRET_KEY = os.getenv("SESSION_SECRET", "render-secret-key-change-in-production")
POLL_INTERVAL_SECONDS = 15

EMAIL_BASE = "TeleGramerKajkOrboeiTADIyeoKK"
DEFAULT_DOMAIN = "gmail.com"
OTP_REGEX = re.compile(r"\b(\d{4,8})\b")

SCOPES = [
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
    "https://www.googleapis.com/auth/gmail.modify",
]

app = Flask(__name__)
app.secret_key = FLASK_SECRET_KEY

USERS = {}

def random_mixed_case(s):
    return ''.join(c.upper() if random.choice([True, False]) else c.lower() for c in s)

def generate_email():
    local = random_mixed_case(EMAIL_BASE)
    domain = random_mixed_case(DEFAULT_DOMAIN)
    return f"{local}@{domain}"

def generate_mixed_case_variation(email):
    if '@' in email:
        local, domain = email.split('@', 1)
        local_mixed = random_mixed_case(local)
        domain_mixed = random_mixed_case(domain)
        return f"{local_mixed}@{domain_mixed}"
    return email

def creds_to_dict(creds: Credentials):
    return {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": creds.scopes
    }

def creds_from_dict(d: dict) -> Credentials:
    return Credentials(
        token=d["token"],
        refresh_token=d["refresh_token"],
        token_uri=d["token_uri"],
        client_id=d["client_id"],
        client_secret=d["client_secret"],
        scopes=d["scopes"]
    )

def extract_otps(text):
    return OTP_REGEX.findall(text or "")

def get_user_by_chat_id(chat_id):
    for email, data in USERS.items():
        if str(data["chat_id"]) == str(chat_id):
            return email, data
    return None, None

async def schedule_auto_delete(chat_id, message_id, delay_seconds=60):
    await asyncio.sleep(delay_seconds)
    try:
        await telegram_app.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        print(f"Auto-delete failed: {e}")

async def send_auto_delete_message(chat_id, text, parse_mode=None, reply_markup=None, delete_after=60):
    message = await telegram_app.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=parse_mode,
        reply_markup=reply_markup
    )
    asyncio.create_task(schedule_auto_delete(chat_id, message.message_id, delete_after))
    return message

async def fetch_latest_otp(chat_id):
    email, data = get_user_by_chat_id(chat_id)
    if not email or not data:
        return None, None, None, None
    
    try:
        creds = creds_from_dict(data["creds"])
        if not creds.valid and creds.refresh_token:
            creds.refresh(Request())
            data["creds"] = creds_to_dict(creds)
        
        service = build("gmail", "v1", credentials=creds)
        
        after_timestamp = int((datetime.now() - timedelta(hours=1)).timestamp() * 1000)
        query = f"is:unread after:{after_timestamp}"
        
        msgs = service.users().messages().list(
            userId="me", 
            q=query,
            maxResults=5
        ).execute().get("messages", [])
        
        latest_otp = None
        latest_timestamp = 0
        latest_sender = ""
        latest_subject = ""
        
        for m in msgs:
            mid = m["id"]
            msg = service.users().messages().get(userId="me", id=mid, format='full').execute()
            
            timestamp = int(msg.get("internalDate", 0))
            
            headers = msg.get("payload", {}).get("headers", [])
            subject = ""
            sender = ""
            
            for header in headers:
                if header.get("name", "").lower() == "subject":
                    subject = header.get("value", "")
                if header.get("name", "").lower() == "from":
                    sender = header.get("value", "")
            
            snippet = msg.get("snippet", "")
            otps = extract_otps(snippet)
            
            if otps and timestamp > latest_timestamp:
                latest_otp = otps[0]
                latest_timestamp = timestamp
                latest_sender = sender
                latest_subject = subject
                
                try:
                    service.users().messages().modify(
                        userId="me", 
                        id=mid, 
                        body={"removeLabelIds": ["UNREAD"]}
                    ).execute()
                except:
                    pass
        
        if latest_otp:
            return email, latest_otp, latest_sender, latest_subject
        else:
            return email, None, None, None
        
    except Exception as e:
        print(f"Error fetching OTP: {e}")
        return None, None, None, None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    
    asyncio.create_task(schedule_auto_delete(chat_id, update.message.message_id, 30))
    
    email, data = get_user_by_chat_id(chat_id)
    
    if email:
        keyboard = [
            [InlineKeyboardButton("🔄 Generate New Email", callback_data="generate_connected")],
            [InlineKeyboardButton("🔍 Check Latest OTP", callback_data="refresh_otp")],
            [InlineKeyboardButton("📊 Stats", callback_data="stats")],
            [InlineKeyboardButton("🚪 Logout", callback_data="logout")]
        ]
        await send_auto_delete_message(
            chat_id,
            f"🔥 Welcome Back!\n\n📧 Connected Gmail: `{email}`\n\n✅ Render.com - 24/7 Online",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
            delete_after=120
        )
    else:
        keyboard = [
            [InlineKeyboardButton("🔗 Connect Google Account", url=f"{BASE_URL}/start_oauth/{chat_id}")],
            [InlineKeyboardButton("ℹ️ How to Use", callback_data="help")]
        ]
        await send_auto_delete_message(
            chat_id,
            "🚀 Gmail OTP Bot - Render.com\n\n✨ 24/7 Online Service\n• Auto OTP Detection\n• Mixed-case Email Generation\n• Real-time Notifications\n• Auto-clean System\n\nConnect your Gmail to start!",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
            delete_after=120
        )

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    
    chat_id = q.message.chat.id
    
    if q.data == "generate_connected":
        email, data = get_user_by_chat_id(chat_id)
        
        if email and data:
            mixed_email = generate_mixed_case_variation(email)
            keyboard = [
                [InlineKeyboardButton("🔄 Generate New Email", callback_data="generate_connected")],
                [InlineKeyboardButton("🔍 Check Latest OTP", callback_data="refresh_otp")],
                [InlineKeyboardButton("📊 Stats", callback_data="stats")]
            ]
            await q.edit_message_text(
                f"🎯 Email Generated!\n\n📧 Original: `{email}`\n🔄 Mixed Case: `{mixed_email}`\n\n💡 Use this for registrations!",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await q.edit_message_text("❌ No Gmail account connected. Use /start to connect.")
    
    elif q.data == "refresh_otp":
        await q.edit_message_text("🔍 Scanning for latest OTP...", parse_mode="Markdown")
        email, otp, sender, subject = await fetch_latest_otp(chat_id)
        
        if email and otp:
            keyboard = [
                [InlineKeyboardButton("🔄 Generate New Email", callback_data="generate_connected")],
                [InlineKeyboardButton("🔍 Check Latest OTP", callback_data="refresh_otp")],
                [InlineKeyboardButton("📊 Stats", callback_data="stats")]
            ]
            
            sender_info = f"\n📨 From: {sender}" if sender else ""
            subject_info = f"\n📝 Subject: {subject}" if subject else ""
            
            await q.edit_message_text(
                f"✅ OTP Found!\n\n🔢 Your Code: `{otp}`{sender_info}{subject_info}\n\n⏰ Auto-deletes in 2 minutes",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            
        elif email:
            keyboard = [
                [InlineKeyboardButton("🔄 Generate New Email", callback_data="generate_connected")],
                [InlineKeyboardButton("🔍 Check Latest OTP", callback_data="refresh_otp")],
                [InlineKeyboardButton("📊 Stats", callback_data="stats")]
            ]
            mixed_email = generate_mixed_case_variation(email)
            await q.edit_message_text(
                f"📧 Account Status\n\n✅ Connected: `{email}`\n🔄 Mixed Case: `{mixed_email}`\n\n❌ No new OTPs found in last hour.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await q.edit_message_text("❌ No Gmail account connected. Use /start to connect.")
    
    elif q.data == "stats":
        email, data = get_user_by_chat_id(chat_id)
        if email:
            otp_count = data.get("otp_count", 0)
            keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="back_main")]]
            await q.edit_message_text(
                f"📊 Account Statistics\n\n📧 Email: `{email}`\n🔢 OTPs Found: `{otp_count}`\n🕒 Status: `24/7 Online`",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await q.edit_message_text("❌ No account data found.")
    
    elif q.data == "logout":
        email, data = get_user_by_chat_id(chat_id)
        if email:
            del USERS[email]
            keyboard = [[InlineKeyboardButton("🔗 Connect New Account", url=f"{BASE_URL}/start_oauth/{chat_id}")]]
            await q.edit_message_text(
                "✅ Logout Successful!\n\nYour Gmail account has been disconnected.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await q.edit_message_text("❌ No account to logout from.")
    
    elif q.data == "help":
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="back_main")]]
        await q.edit_message_text(
            "📖 How to Use:\n\n1. 🔗 Connect your Gmail\n2. 🎯 Generate email variations\n3. 🔍 Get OTPs automatically\n4. ⚡ Instant notifications\n5. 🧹 Auto-clean system\n\n✨ 24/7 Online on Render.com!",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    elif q.data == "back_main":
        email, data = get_user_by_chat_id(chat_id)
        if email:
            keyboard = [
                [InlineKeyboardButton("🔄 Generate New Email", callback_data="generate_connected")],
                [InlineKeyboardButton("🔍 Check Latest OTP", callback_data="refresh_otp")],
                [InlineKeyboardButton("📊 Stats", callback_data="stats")],
                [InlineKeyboardButton("🚪 Logout", callback_data="logout")]
            ]
            await q.edit_message_text(
                f"🔥 Main Menu\n\n📧 Connected: `{email}`\n\n✅ Render.com - 24/7 Online",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            keyboard = [
                [InlineKeyboardButton("🔗 Connect Google Account", url=f"{BASE_URL}/start_oauth/{chat_id}")]
            ]
            await q.edit_message_text(
                "🚀 Gmail OTP Bot - 24/7 Online\n\nConnect your Gmail to start!",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if update.message and not update.message.text.startswith('/'):
        asyncio.create_task(schedule_auto_delete(chat_id, update.message.message_id, 30))

@app.route("/")
def home():
    return "Gmail OTP Bot - 24/7 Online on Render.com"

@app.route("/start_oauth/<chat_id>")
def start_oauth(chat_id):
    if not os.path.exists(OAUTH_CLIENT_SECRETS_FILE):
        return "Configuration Required - Please upload client_secret.json"
    
    try:
        session["chat_id"] = chat_id
        flow = Flow.from_client_secrets_file(
            OAUTH_CLIENT_SECRETS_FILE, 
            scopes=SCOPES, 
            redirect_uri=f"{BASE_URL}/oauth2callback"
        )
        auth_url, state = flow.authorization_url(
            access_type="offline", 
            include_granted_scopes="true", 
            prompt="consent"
        )
        session["state"] = state
        return redirect(auth_url)
    except Exception as e:
        return f"Error: {str(e)}"

@app.route("/oauth2callback")
def oauth2callback():
    try:
        state = session.get("state")
        flow = Flow.from_client_secrets_file(
            OAUTH_CLIENT_SECRETS_FILE, 
            scopes=SCOPES, 
            state=state, 
            redirect_uri=f"{BASE_URL}/oauth2callback"
        )
        flow.fetch_token(authorization_response=request.url)
        creds = flow.credentials
        userinfo = build('oauth2', 'v2', credentials=creds).userinfo().get().execute()
        email = userinfo.get("email")
        chat_id = session.pop("chat_id", None)
        
        if not chat_id:
            return "Session expired. Please try again from Telegram."
        
        USERS[email] = {"chat_id": chat_id, "creds": creds_to_dict(creds), "seen": set(), "otp_count": 0}
        
        if telegram_app and telegram_loop:
            keyboard = [
                [InlineKeyboardButton("🔄 Generate New Email", callback_data="generate_connected")],
                [InlineKeyboardButton("🔍 Check Latest OTP", callback_data="refresh_otp")],
                [InlineKeyboardButton("📊 Stats", callback_data="stats")]
            ]
            asyncio.run_coroutine_threadsafe(
                telegram_app.bot.send_message(
                    chat_id=chat_id, 
                    text=f"✅ Connected Successfully!\n\n📧 Email: `{email}`\n\n✨ 24/7 Online on Render.com!",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                ),
                telegram_loop
            )
        
        return "Successfully connected! Return to Telegram."
    except Exception as e:
        return f"Authentication failed: {str(e)}"

def poll():
    print("Starting Gmail polling service on Render.com...")
    while True:
        try:
            for email, data in list(USERS.items()):
                try:
                    creds = creds_from_dict(data["creds"])
                    if not creds.valid and creds.refresh_token:
                        creds.refresh(Request())
                        data["creds"] = creds_to_dict(creds)
                    
                    service = build("gmail", "v1", credentials=creds)
                    
                    after_timestamp = int((datetime.now() - timedelta(hours=1)).timestamp() * 1000)
                    query = f"is:unread after:{after_timestamp}"
                    
                    msgs = service.users().messages().list(
                        userId="me", 
                        q=query,
                        maxResults=3
                    ).execute().get("messages", [])
                    
                    for m in msgs:
                        mid = m["id"]
                        if mid in data["seen"]:
                            continue
                        
                        msg = service.users().messages().get(userId="me", id=mid, format='full').execute()
                        
                        headers = msg.get("payload", {}).get("headers", [])
                        subject = ""
                        sender = ""
                        
                        for header in headers:
                            if header.get("name", "").lower() == "subject":
                                subject = header.get("value", "")
                            if header.get("name", "").lower() == "from":
                                sender = header.get("value", "")
                        
                        snippet = msg.get("snippet", "")
                        otps = extract_otps(snippet)
                        
                        if otps and telegram_app and telegram_loop:
                            otp = otps[0]
                            data["otp_count"] = data.get("otp_count", 0) + 1
                            
                            sender_info = f"\n📨 From: {sender}" if sender else ""
                            subject_info = f"\n📝 Subject: {subject}" if subject else ""
                            
                            asyncio.run_coroutine_threadsafe(
                                telegram_app.bot.send_message(
                                    chat_id=int(data["chat_id"]), 
                                    text=f"🚨 New OTP Received!\n\n🔢 Code: `{otp}`{sender_info}{subject_info}\n\n⏰ Auto-deletes in 2 minutes",
                                    parse_mode="Markdown"
                                ),
                                telegram_loop
                            )
                            
                            try:
                                service.users().messages().modify(
                                    userId="me", 
                                    id=mid, 
                                    body={"removeLabelIds": ["UNREAD"]}
                                ).execute()
                            except:
                                pass
                            
                            data["seen"].add(mid)
                            
                except Exception as e:
                    print(f"Polling error for {email}: {e}")
            
            time.sleep(POLL_INTERVAL_SECONDS)
        except Exception as e:
            print(f"Polling loop error: {e}")
            time.sleep(POLL_INTERVAL_SECONDS)

def start_poll():
    threading.Thread(target=poll, daemon=True).start()

telegram_bot = None
telegram_loop = None
telegram_app = None

def main():
    global telegram_bot, telegram_loop, telegram_app
    
    if not TELEGRAM_BOT_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN not set!")
        return
    
    try:
        from telegram.ext import Application
        
        telegram_bot = Bot(token=TELEGRAM_BOT_TOKEN)
        print("✅ Telegram bot initialized")
        
        start_poll()
        
        telegram_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(telegram_loop)
        
        telegram_app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
        telegram_app.add_handler(CommandHandler("start", start))
        telegram_app.add_handler(CallbackQueryHandler(button))
        telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        async def start_bot():
            await telegram_app.initialize()
            await telegram_app.start()
            await telegram_app.updater.start_polling()
            print("🤖 Bot started on Render.com - 24/7 Online!")
        
        telegram_loop.create_task(start_bot())
        
        def run_async_loop():
            asyncio.set_event_loop(telegram_loop)
            telegram_loop.run_forever()
        
        threading.Thread(target=run_async_loop, daemon=True).start()
        
        print("🚀 Starting Flask server on Render.com...")
        app.run(host="0.0.0.0", port=5000, debug=False)
        
    except Exception as e:
        print(f"❌ Error starting: {e}")

if __name__ == "__main__":
    main()
