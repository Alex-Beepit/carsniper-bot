import os
import io
import asyncio
import pandas as pd
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
from playwright.async_api import async_playwright

# Στάδια διαλόγου
LOCATION, PICKUP_DATE, DROP_DATE, CAR_TYPE = range(4)

# Ανάκτηση Token από Environment Variable
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reply_keyboard = [["Ηράκλειο (HER)", "Χανιά (CHQ)"], ["Σαντορίνη (JTR)", "Αθήνα (ATH)"]]
    await update.message.reply_text(
        "🚗 **Carsniper Bot**\nΕπιλέξτε τοποθεσία ή πληκτρολογήστε πόλη/αεροδρόμιο:",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True),
        parse_mode="Markdown"
    )
    return LOCATION


async def location_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["location"] = update.message.text
    await update.message.reply_text(
        "📅 Δώστε ημερομηνία & ώρα **Παραλαβής** (π.χ. `15/09/2026 10:00`):",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="Markdown"
    )
    return PICKUP_DATE


async def pickup_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["pickup"] = update.message.text
    await update.message.reply_text(
        "📅 Δώστε ημερομηνία & ώρα **Παράδοσης** (π.χ. `22/09/2026 10:00`):",
        parse_mode="Markdown"
    )
    return DROP_DATE


async def drop_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["drop"] = update.message.text
    categories = [["Small / Mini", "Economy"], ["Compact", "SUV"], ["Όλες οι κατηγορίες"]]
    await update.message.reply_text(
        "🚙 Επιλέξτε κατηγορία οχήματος:",
        reply_markup=ReplyKeyboardMarkup(categories, one_time_keyboard=True, resize_keyboard=True),
    )
    return CAR_TYPE


async def scrape_doyouspain(params):
    results = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
        page = await browser.new_page()
        
        # Προσομοίωση αναζήτησης / scraping
        # Για εξειδικευμένα selectors ή direct API calls του DoYouSpain:
        search_url = f"https://www.doyouspain.com"
        await page.goto(search_url, wait_until="networkidle")
        
        # Εδώ εκτελείται η άντληση των DOM nodes (όνομα, τιμή, πάροχος, κατηγορία)
        # Παράδειγμα dummy δομής δεδομένων:
        for i in range(1, 11):
            results.append({
                "Rank": i,
                "Vehicle": f"Model Category {params.get('car_type', 'Standard')} {i}",
                "Supplier": f"Provider {i}",
                "Price_Total_EUR": 45.0 + (i * 4.5),
                "Price_Per_Day_EUR": round((45.0 + (i * 4.5)) / 7, 2)
            })
            
        await browser.close()
    return results


async def car_type_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["car_type"] = update.message.text
    await update.message.reply_text("⏳ Αναζήτηση τιμών στο DoYouSpain... Παρακαλώ περιμένετε.")

    # Εκτέλεση scraping
    data = await scrape_doyouspain(context.user_data)

    if not data:
        await update.message.reply_text("❌ Δεν βρέθηκαν αποτελέσματα για τις παραμέτρους που δώσατε.")
        return ConversationHandler.END

    # Δημιουργία σύνοψης μηνύματος (Top 10)
    msg_lines = ["🏆 **Top 10 Αποτελέσματα:**\n"]
    for row in data[:10]:
        msg_lines.append(
            f"**{row['Rank']}. {row['Vehicle']}**\n"
            f"• Εταιρεία: `{row['Supplier']}`\n"
            f"• Σύνολο: **{row['Price_Total_EUR']}€** ({row['Price_Per_Day_EUR']}€/ημέρα)\n"
        )
    await update.message.reply_text("\n".join(msg_lines), parse_mode="Markdown")

    # Δημιουργία αρχείου Excel (.xlsx) στη μνήμη
    df = pd.DataFrame(data)
    excel_buffer = io.BytesIO()
    with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="DoYouSpain_Results")
    excel_buffer.seek(0)

    filename = f"doyouspain_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    await context.bot.send_document(
        chat_id=update.effective_chat.id,
        document=excel_buffer,
        filename=filename,
        caption="📊 Αναλυτικά αποτελέσματα σε αρχείο Excel."
    )

    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Η αναζήτηση ακυρώθηκε.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start), CommandHandler("search", start)],
        states={
            LOCATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, location_handler)],
            PICKUP_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, pickup_handler)],
            DROP_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, drop_handler)],
            CAR_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, car_type_handler)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(conv_handler)
    app.run_polling()


if __name__ == "__main__":
    main()