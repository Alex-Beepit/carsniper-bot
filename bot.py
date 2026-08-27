import os
import io
import asyncio
import re
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

LOCATION, PICKUP_DATE, DROP_DATE, CAR_TYPE = range(4)
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")

# Mapping βασικών τοποθεσιών για σωστή συμπλήρωση στο DoYouSpain
LOC_MAP = {
    "Ηράκλειο (HER)": "Crete, Heraklion Airport",
    "Χανιά (CHQ)": "Crete, Chania Airport",
    "Σαντορίνη (JTR)": "Santorini Airport",
    "Αθήνα (ATH)": "Athens Airport"
}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reply_keyboard = [["Ηράκλειο (HER)", "Χανιά (CHQ)"], ["Σαντορίνη (JTR)", "Αθήνα (ATH)"]]
    await update.message.reply_text(
        "🚗 **Carsniper Bot**\nΕπιλέξτε τοποθεσία ή πληκτρολογήστε σημείο παραλαβής:",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True),
        parse_mode="Markdown"
    )
    return LOCATION

async def location_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    loc_input = update.message.text
    context.user_data["location"] = LOC_MAP.get(loc_input, loc_input)
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
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800}
        )
        page = await context.new_page()

        try:
            # Μετάβαση στην κεντρική φόρμα αναζήτησης
            await page.goto("https://www.doyouspain.com/index.htm", wait_until="domcontentloaded", timeout=60000)

            # Συμπλήρωση τοποθεσίας
            loc_input = page.locator("#place_origen_des, input[name*='place'], input[id*='place']")
            if await loc_input.count() > 0:
                await loc_input.first.fill(params.get("location", "Crete, Heraklion Airport"))
                await page.keyboard.press("ArrowDown")
                await page.keyboard.press("Enter")
                await page.wait_for_timeout(1000)

            # Υποβολή φόρμας αναζήτησης
            search_btn = page.locator("#btnBuscar, input[type='submit'], button[type='submit']").first
            if await search_btn.count() > 0:
                await search_btn.click()

            # Αναμονή εμφάνισης αποτελεσμάτων
            await page.wait_for_selector(".results-car-item, .car-box, div[class*='deal'], div[class*='result'], div[class*='card']", timeout=40000)
            await page.wait_for_timeout(3000)

            car_cards = await page.locator("div[class*='deal-card'], div[class*='car-result'], div[class*='results-car-item'], div[class*='car-box']").all()

            rank = 1
            for card in car_cards:
                if rank > 10:
                    break
                text_content = await card.inner_text()
                
                # Εξαγωγή μοντέλου οχήματος
                title_elem = card.locator("h3, h4, .car-name, .model-name").first
                vehicle = await title_elem.inner_text() if await title_elem.count() > 0 else f"Vehicle Option {rank}"
                vehicle = vehicle.split("\n")[0].strip()

                # Εξαγωγή προμηθευτή
                supplier = "DoYouSpain Partner"
                supplier_img = card.locator("img[class*='supplier'], img[class*='logo']").first
                if await supplier_img.count() > 0:
                    alt_text = await supplier_img.get_attribute("alt")
                    if alt_text:
                        supplier = alt_text

                # Εξαγωγή πραγματικών τιμών με regex
                prices = re.findall(r'(\d+[\.,]\d{2})\s*€', text_content)
                if prices:
                    price_val = float(prices[0].replace(",", "."))
                    price_day = float(prices[1].replace(",", ".")) if len(prices) > 1 else price_val
                else:
                    price_val = 0.0
                    price_day = 0.0

                results.append({
                    "Rank": rank,
                    "Vehicle": vehicle,
                    "Supplier": supplier,
                    "Price_Total_EUR": price_val,
                    "Price_Per_Day_EUR": price_day
                })
                rank += 1

        except Exception as e:
            print(f"Scraping Error: {e}")
        finally:
            await browser.close()

    return results

async def car_type_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["car_type"] = update.message.text
    await update.message.reply_text("⏳ Αναζήτηση ζωντανών τιμών στο DoYouSpain... Παρακαλώ περιμένετε.")

    data = await scrape_doyouspain(context.user_data)

    if not data:
        await update.message.reply_text("❌ Δεν βρέθηκαν αποτελέσματα. Δοκιμάστε ξανά.")
        return ConversationHandler.END

    msg_lines = ["🏆 **Top 10 Αποτελέσματα (DoYouSpain Live):**\n"]
    for row in data:
        msg_lines.append(
            f"**{row['Rank']}. {row['Vehicle']}**\n"
            f"• Εταιρεία: `{row['Supplier']}`\n"
            f"• Σύνολο: **{row['Price_Total_EUR']}€** ({row['Price_Per_Day_EUR']}€/ημέρα)\n"
        )
    await update.message.reply_text("\n".join(msg_lines), parse_mode="Markdown")

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
