import os
import io
import asyncio
import re
import urllib.parse
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

# Χαρτογράφηση γνωστών τοποθεσιών
LOC_MAP = {
    "Ηράκλειο (HER)": "Crete - Heraklion Airport",
    "Χανιά (CHQ)": "Crete - Chania Airport",
    "Σαντορίνη (JTR)": "Santorini Airport",
    "Αθήνα (ATH)": "Athens Airport"
}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reply_keyboard = [["Ηράκλειο (HER)", "Χανιά (CHQ)"], ["Σαντορίνη (JTR)", "Αθήνα (ATH)"]]
    await update.message.reply_text(
        "🚗 **Carsniper Bot**\nΕπιλέξτε τοποθεσία ή πληκτρολογήστε σημείο:",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True),
        parse_mode="Markdown"
    )
    return LOCATION

async def location_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    loc_input = update.message.text
    context.user_data["location"] = LOC_MAP.get(loc_input, loc_input)
    await update.message.reply_text(
        "📅 Δώστε ημερομηνία **Παραλαβής** (π.χ. `27/08/2026`):",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="Markdown"
    )
    return PICKUP_DATE

async def pickup_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["pickup"] = update.message.text
    await update.message.reply_text(
        "📅 Δώστε ημερομηνία **Παράδοσης** (π.χ. `29/08/2026`):",
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
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled"
            ]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            viewport={"width": 1366, "height": 768},
            locale="en-GB"
        )
        page = await context.new_page()

        try:
            # Μετάβαση στο DoYouSpain
            await page.goto("https://www.doyouspain.com/", wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(2000)

            # Εισαγωγή τοποθεσίας
            loc_input = page.locator("#place_origen_des, input[name='lugar_entrega'], input[id*='origen']").first
            if await loc_input.count() > 0:
                await loc_input.click()
                await loc_input.fill(params.get("location", "Crete - Heraklion Airport"))
                await page.wait_for_timeout(1000)
                await page.keyboard.press("ArrowDown")
                await page.keyboard.press("Enter")

            # Κλικ αναζήτησης
            search_btn = page.locator("#btnBuscar, button[type='submit'], input[type='submit']").first
            if await search_btn.count() > 0:
                await search_btn.click()

            # Αναμονή για τη σελίδα αποτελεσμάτων
            await page.wait_for_selector("a:has-text('View deal'), button:has-text('View deal'), div:has-text('Price for')", timeout=35000)
            await page.wait_for_timeout(4000)

            # Εντοπισμός όλων των προσφορών
            # Το DoYouSpain ομαδοποιεί τα deals γύρω από το κουμπί "View deal"
            deals = await page.locator("xpath=//a[contains(., 'View deal')]/ancestor::div[contains(@class, 'deal') or contains(@class, 'box') or contains(@class, 'card') or position()=1]").all()

            if not deals:
                # Εναλλακτικός τρόπος εντοπισμού καρτών
                deals = await page.locator("div[class*='deal'], div[class*='result'], div[class*='car-']").all()

            rank = 1
            seen_vehicles = set()

            for deal in deals:
                if rank > 10:
                    break
                text = await deal.inner_text()
                
                # Αναζήτηση τιμής (π.χ. 16,11 €)
                price_matches = re.findall(r'(\d+[\.,]\d{2})\s*€', text)
                if not price_matches:
                    continue

                price_main = float(price_matches[0].replace(",", "."))
                price_day = float(price_matches[1].replace(",", ".")) if len(price_matches) > 1 else price_main

                # Εξαγωγή ονόματος οχήματος (π.χ. Fiat 500 or similar)
                car_name = "Car"
                lines = [line.strip() for line in text.split("\n") if line.strip()]
                for l in lines:
                    if "or similar" in l.lower() or any(brand in l.lower() for brand in ["fiat", "citroen", "vw", "toyota", "hyundai", "peugeot", "opel", "renault", "skoda", "nissan", "kia"]):
                        car_name = l
                        break

                # Αποφυγή διπλότυπων καρτών
                deal_key = f"{car_name}_{price_main}"
                if deal_key in seen_vehicles:
                    continue
                seen_vehicles.add(deal_key)

                # Εξαγωγή εταιρείας (Surprice, Abbycar, Beepit, etc)
                supplier = "Partner Supplier"
                for s_name in ["Surprice", "Abbycar", "addCar", "AutoUnion", "Autocar", "Avance", "Avis", "Beepit", "Caldera", "CarRental2Greece", "Carwiz", "Centauro", "Cretamotor", "Enterprise", "EuroCar", "Europcar", "Exer", "Flex", "Goldcar", "Green Motion", "Grentals", "Hertz", "OK Mobility", "Record go", "Rhodium", "Sixt", "Thrifty", "Wheego", "Windy"]:
                    if s_name.lower() in text.lower():
                        supplier = s_name
                        break

                results.append({
                    "Rank": rank,
                    "Vehicle": car_name,
                    "Supplier": supplier,
                    "Price_Total_EUR": price_main,
                    "Price_Per_Day_EUR": price_day
                })
                rank += 1

        except Exception as e:
            print(f"Extraction Error: {e}")
        finally:
            await browser.close()

    return results

async def car_type_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["car_type"] = update.message.text
    await update.message.reply_text("⏳ Αναζήτηση ζωντανών τιμών στο DoYouSpain... Παρακαλώ περιμένετε 10-15 δευτερόλεπτα.")

    data = await scrape_doyouspain(context.user_data)

    if not data:
        await update.message.reply_text("❌ Δεν ήταν δυνατή η ανάκτηση των τιμών. Δοκιμάστε ξανά.")
        return ConversationHandler.END

    msg_lines = ["🏆 **Top 10 Αποτελέσματα (DoYouSpain Live):**\n"]
    for row in data:
        msg_lines.append(
            f"**{row['Rank']}. {row['Vehicle']}**\n"
            f"• Εταιρεία: `{row['Supplier']}`\n"
            f"• Τιμή: **{row['Price_Total_EUR']}€**\n"
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
