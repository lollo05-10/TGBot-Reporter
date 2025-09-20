import os
import base64
import smtplib
from email.message import EmailMessage
from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton, InputFile
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, filters,
    ConversationHandler, CallbackQueryHandler, ContextTypes
)

# Stati conversazione
(
    ASK_EMAIL, ASK_SMTP_PASS, ASK_SUBJECT, ASK_BODY,
    ASK_RECIPIENT, ASK_ATTACHMENT, CONFIRM_SEND
) = range(7)

# Limiti allegati
ALLOWED_MIME_TYPES = [
    "image/png", "image/jpeg", "application/pdf",
    "text/plain", "application/zip"
]
MAX_ATTACHMENTS = 10
MAX_TOTAL_SIZE_MB = 25

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Benvenuto! Usa /report per inviare una segnalazione a Telegram."
    )

async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Inserisci la tua email (mittente):")
    return ASK_EMAIL

async def ask_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    email = update.message.text.strip()
    if "@" not in email:
        await update.message.reply_text("Email non valida, riprova:")
        return ASK_EMAIL
    context.user_data['sender_email'] = email
    await update.message.reply_text(
        "Inserisci la password SMTP (non verrà salvata):"
    )
    return ASK_SMTP_PASS

async def ask_smtp_pass(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['smtp_password'] = update.message.text.strip()
    await update.message.reply_text("Inserisci l'oggetto della email:")
    return ASK_SUBJECT

async def ask_subject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['subject'] = update.message.text.strip()
    await update.message.reply_text("Inserisci il testo del messaggio:")
    return ASK_BODY

async def ask_body(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['body_text'] = update.message.text.strip()
    keyboard = [
        [InlineKeyboardButton("abuse@telegram.org", callback_data="abuse")],
        [InlineKeyboardButton("stopCA@telegram.org", callback_data="stopCA")]
    ]
    await update.message.reply_text(
        "Seleziona il destinatario:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return ASK_RECIPIENT

async def ask_recipient(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    choice = query.data
    recipient = "abuse@telegram.org" if choice == "abuse" else "stopCA@telegram.org"
    context.user_data['recipient_choice'] = recipient
    await query.edit_message_text(
        f"Hai scelto: {recipient}\nOra puoi inviare fino a {MAX_ATTACHMENTS} allegati. "
        "Invia /done quando hai finito o non vuoi allegati."
    )
    context.user_data['attachments'] = []
    return ASK_ATTACHMENT

async def handle_attachment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.document:
        await update.message.reply_text("Invia un file valido o /done per terminare.")
        return ASK_ATTACHMENT
    doc = update.message.document
    if doc.mime_type not in ALLOWED_MIME_TYPES:
        await update.message.reply_text("Tipo di file non consentito.")
        return ASK_ATTACHMENT
    if len(context.user_data['attachments']) >= MAX_ATTACHMENTS:
        await update.message.reply_text("Numero massimo di allegati raggiunto.")
        return ASK_ATTACHMENT
    file = await doc.get_file()
    file_bytes = await file.download_as_bytearray()
    total_size_mb = sum(len(a['content_bytes']) for a in context.user_data['attachments'])/1e6
    total_size_mb += len(file_bytes)/1e6
    if total_size_mb > MAX_TOTAL_SIZE_MB:
        await update.message.reply_text("Limite totale allegati superato.")
        return ASK_ATTACHMENT
    context.user_data['attachments'].append({
        'filename': doc.file_name,
        'mime_type': doc.mime_type,
        'content_bytes': file_bytes
    })
    await update.message.reply_text(f"Allegato {doc.file_name} aggiunto.")
    return ASK_ATTACHMENT

async def done_attachments(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = context.user_data
    n = len(data['attachments'])
    await update.message.reply_text(
        f"Riepilogo:\nMittente: {data['sender_email']}\nDestinatario: {data['recipient_choice']}\n"
        f"Oggetto: {data['subject']}\nAllegati: {n}\nConfermi invio? (Sì/No)"
    )
    return CONFIRM_SEND

async def confirm_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().lower()
    if text not in ["sì", "si"]:
        await update.message.reply_text("Invio annullato.")
        return ConversationHandler.END

    data = context.user_data
    try:
        msg = EmailMessage()
        msg['From'] = data['sender_email']
        msg['To'] = data['recipient_choice']
        msg['Subject'] = data['subject']
        msg.set_content(data['body_text'])
        for a in data['attachments']:
            msg.add_attachment(a['content_bytes'], maintype=a['mime_type'].split('/')[0],
                               subtype=a['mime_type'].split('/')[1], filename=a['filename'])

        smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
        smtp_port = int(os.getenv("SMTP_PORT", 587))
        use_tls = os.getenv("SMTP_USE_TLS", "true").lower() == "true"

        if use_tls:
            server = smtplib.SMTP(smtp_host, smtp_port)
            server.starttls()
        else:
            server = smtplib.SMTP_SSL(smtp_host, smtp_port)
        server.login(data['sender_email'], data['smtp_password'])
        server.send_message(msg)
        server.quit()
        await update.message.reply_text(
            f"Segnalazione inviata con successo a {data['recipient_choice']}."
        )
    except smtplib.SMTPAuthenticationError:
        await update.message.reply_text(
            "Autenticazione SMTP fallita: controlla email e app-password."
        )
    except Exception as e:
        await update.message.reply_text(f"Invio fallito: {e}")
    return ConversationHandler.END

def main():
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    if not BOT_TOKEN:
        print("Errore: BOT_TOKEN non impostato come variabile d'ambiente.")
        return

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('report', report)],
        states={
            ASK_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_email)],
            ASK_SMTP_PASS: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_smtp_pass)],
            ASK_SUBJECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_subject)],
            ASK_BODY: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_body)],
            ASK_RECIPIENT: [CallbackQueryHandler(ask_recipient)],
            ASK_ATTACHMENT: [
                MessageHandler(filters.Document.ALL, handle_attachment),
                CommandHandler('done', done_attachments)
            ],
            CONFIRM_SEND: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_send)],
        },
        fallbacks=[CommandHandler('start', start)],
        allow_reentry=True
    )

    app.add_handler(CommandHandler('start', start))
    app.add_handler(conv_handler)
    app.run_polling()

if __name__ == "__main__":
    main()
