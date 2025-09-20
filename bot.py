import logging
import smtplib
from email.message import EmailMessage
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    filters, ContextTypes, ConversationHandler, CallbackQueryHandler
)
from flask import Flask
from threading import Thread
import os

# -----------------------
# Logging
# -----------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# -----------------------
# Stati conversazione
# -----------------------
ASK_EMAIL, ASK_PASSWORD, ASK_SUBJECT, ASK_BODY, ASK_RECIPIENT, ASK_ATTACHMENTS, CONFIRM_SEND = range(7)

# -----------------------
# Limiti allegati
# -----------------------
MAX_ATTACHMENTS = 10
MAX_TOTAL_SIZE_MB = 25
ALLOWED_MIME_TYPES = ["image/png", "image/jpeg", "application/pdf", "text/plain", "application/zip"]

# -----------------------
# Funzioni bot Telegram
# -----------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Benvenuto! Questo bot ti aiuta a segnalare abusi a Telegram.\n"
        "Usa /report per iniziare la procedura guidata."
    )

async def start_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Inserisci l'email mittente:")
    return ASK_EMAIL

async def ask_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    email = update.message.text.strip()
    if "@" not in email:
        await update.message.reply_text("❌ Email non valida. Riprova:")
        return ASK_EMAIL
    context.user_data["sender_email"] = email
    await update.message.reply_text("Inserisci la password o app-password SMTP (sarà cancellata subito dopo l'uso):")
    return ASK_PASSWORD

async def ask_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["smtp_password"] = update.message.text
    try:
        await update.message.delete()
    except Exception:
        pass
    await update.message.reply_text("Oggetto della segnalazione:")
    return ASK_SUBJECT

async def ask_subject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["subject"] = update.message.text
    await update.message.reply_text("Testo del messaggio:")
    return ASK_BODY

async def ask_body(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["body_text"] = update.message.text
    keyboard = [[
        InlineKeyboardButton("abuse@telegram.org", callback_data="abuse@telegram.org"),
        InlineKeyboardButton("stopCA@telegram.org", callback_data="stopCA@telegram.org")
    ]]
    await update.message.reply_text(
        "Scegli il destinatario:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return ASK_RECIPIENT

async def ask_recipient(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["recipient_choice"] = query.data
    context.user_data["attachments"] = []
    await query.edit_message_text("Puoi inviare fino a 10 allegati, oppure scrivi /done per procedere.")
    return ASK_ATTACHMENTS

async def handle_attachment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    attachments = context.user_data.get("attachments", [])
    if len(attachments) >= MAX_ATTACHMENTS:
        await update.message.reply_text("❌ Raggiunto numero massimo di allegati (10). Usa /done per continuare.")
        return ASK_ATTACHMENTS

    file = await update.message.document.get_file()
    file_bytes = await file.download_as_bytearray()

    if sum(len(a['content']) for a in attachments) + len(file_bytes) > MAX_TOTAL_SIZE_MB * 1024 * 1024:
        await update.message.reply_text("❌ Limite totale di 25 MB superato.")
        return ASK_ATTACHMENTS

    attachments.append({
        "filename": update.message.document.file_name,
        "mime_type": update.message.document.mime_type,
        "content": file_bytes
    })
    context.user_data["attachments"] = attachments
    await update.message.reply_text(f"✅ Allegato aggiunto ({len(attachments)}/{MAX_ATTACHMENTS}).")
    return ASK_ATTACHMENTS

async def done_attachments(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = context.user_data
    n_attachments = len(data.get("attachments", []))
    summary = (
        f"📧 Riepilogo segnalazione\n\n"
        f"👤 Mittente: {data['sender_email']}\n"
        f"🎯 Destinatario: {data['recipient_choice']}\n"
        f"📝 Oggetto: {data['subject']}\n"
        f"📄 Testo: {data['body_text'][:300]}{'...' if len(data['body_text']) > 300 else ''}\n"
        f"📎 Allegati: {n_attachments}\n\n"
        "Confermi l'invio?"
    )
    keyboard = [[
        InlineKeyboardButton("✅ Conferma", callback_data="yes"),
        InlineKeyboardButton("❌ Annulla", callback_data="no")
    ]]
    await update.message.reply_text(summary, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    return CONFIRM_SEND

async def handle_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "yes":
        await query.edit_message_text("📨 Invio in corso...")
        result = await send_email(context.user_data)
        await query.message.reply_text(result)
    else:
        await query.edit_message_text("❌ Invio annullato. Puoi ripartire con /report")
    context.user_data.clear()
    return ConversationHandler.END

async def send_email(data: dict) -> str:
    msg = EmailMessage()
    msg["From"] = data["sender_email"]
    msg["To"] = data["recipient_choice"]
    msg["Subject"] = data["subject"]
    msg.set_content(data["body_text"])
    for att in data.get("attachments", []):
        msg.add_attachment(att["content"], maintype=att["mime_type"].split("/")[0],
                           subtype=att["mime_type"].split("/")[1], filename=att["filename"])
    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(data["sender_email"], data["smtp_password"])
            response = server.send_message(msg)
        return f"✅ Segnalazione inviata con successo a {data['recipient_choice']}. ({response})"
    except smtplib.SMTPAuthenticationError:
        return "❌ Autenticazione SMTP fallita: controlla email e app-password."
    except Exception as e:
        return f"❌ Invio fallito: {e}"

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ Procedura annullata.")
    return ConversationHandler.END

# -----------------------
# Flask per mantenerlo attivo
# -----------------------
app_flask = Flask("")

@app_flask.route("/")
def home():
    return "Bot attivo"

def run_flask():
    app_flask.run(host="0.0.0.0", port=3000)

# -----------------------
# Main Telegram
# -----------------------
if __name__ == "__main__":
    # Avvio Flask in thread separato
    from threading import Thread
    thread = Thread(target=run_flask)
    thread.start()

    # Avvio bot Telegram
    BOT_TOKEN = os.environ.get("BOT_TOKEN")
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("report", start_report)],
        states={
            ASK_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_email)],
            ASK_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_password)],
            ASK_SUBJECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_subject)],
            ASK_BODY: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_body)],
            ASK_RECIPIENT: [CallbackQueryHandler(ask_recipient)],
            ASK_ATTACHMENTS: [
                MessageHandler(filters.Document.ALL, handle_attachment),
                CommandHandler("done", done_attachments)
            ],
            CONFIRM_SEND: [CallbackQueryHandler(handle_confirmation)],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(conv_handler)

    application.run_polling()
