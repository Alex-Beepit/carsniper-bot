import os
import io
import asyncio
import re
import pandas as pd
from datetime import datetime
from aiohttp import web
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
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

LOCATION, PICKUP_DATE, DROP_DATE, CAR_TYPE = range(4)
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
PORT = int(os.getenv("PORT", 10000))

LOC_MAP = {
    "Ηράκλειο (HER)": "Crete - Heraklion Airport",
    "Χανιά (CHQ)": "Crete - Chania Airport",
    "Σαντορίνη (JTR)": "Santorini Airport",
    "Αθήνα (ATH)": "Athens Airport"
}

# Dummy Web Server για να περνάει το Health Check του Render Free Tier
async def start_dummy_server():
    async def handle_ping(request):
        return web.Response(text="Bot is running!")

    server = web.Application()
    server.router.add_get("/", handle_ping)
    server.router.add_get("/healthz", handle_ping)
    runner = web.AppRunner(server)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

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
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            viewport={"width": 1366, "height": 768}
        )
        page = await context.new_page()

        try:
            await page.goto("https://www.doyouspain.com/", wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(2000)

            loc_input = page.locator("#place_origen_des, input[name='lugar_entrega']").first
            if await loc_input.count() > 0:
                await loc_input.fill(params.get("location", "Crete - Heraklion Airport"))
                await page.wait_for_timeout(1000)
                await page.keyboard.press("ArrowDown")
                await page.keyboard.press("Enter")

            search_btn = page.locator("#btnBuscar, button[type='submit']").first
            if await search_btn.count() > 0:
                await search_btn.click()

            await page.wait_for_selector("a:has-text('View deal'), div[class*='deal']", timeout=35000)
            await page.wait_for_timeout(3000)

            deals = await page.locator("xpath=//a[contains(., 'View deal')]/ancestor::div[contains(@class, 'deal') or contains(@class, 'card') or position()=1]").all()
            if not deals:
                deals = await page.locator("div[class*='deal'], div[class*='result']").all()

            rank = 1
            seen_vehicles = set()

            for deal in deals:
                if rank > 10:
                    break
                text = await deal.inner_text()
                
                prices = re.findall(r'(\d+[\.,]\d{2})\s*€', text)
                if not price_matches:
                    continue

                price_main = float(prices[0].replace(",", "."))
                price_day = float(prices[1].replace(",", ".")) if len(prices) > 1 else price_main

                car_name = "Car Model"
                lines = [line.strip() for line in text.split("\n") if line.strip()]
                for l in lines:
                    if "or similar" in l.lower() or any(b in l.lower() for b in ["fiat", "citroen", "vw", "toyota", "hyundai", "peugeot", "opel", "renault", "skoda"]):
                        car_name = l
                        break

                deal_key = f"{car_name}_{price_main}"
                if deal_key in seen_vehicles:
                    continue
                seen_vehicles.add(deal_key)

                supplier = "Partner"
                for s_name in ["Surprice", "Abbycar", "addCar", "AutoUnion", "Autocar", "Avance", "Avis", "Beepit", "Caldera", "CarRental2Greece", "Carwiz", "Centauro", "Cretamotor", "Enterprise", "Europcar", "Exer", "Flex", "Goldcar", "Green Motion", "Hertz", "Sixt"]:
                    if s_name.lower() in text.lower():
                        supplier = s_name
                        break

                results.append({
                    "Rank": rank,
                    "Vehicle": car_name,
                    "Supplier": supplier,
                    "Total_EUR": price_main,
                    "Per_Day_EUR": price_day
                })
                rank += 1

        except Exception as e:
            print(f"Scraping exception: {e}")
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
            f"• Τιμή: **{row['Total_EUR']}€** ({row['Per_Day_EUR']}€/ημέρα)\n"
        )
    await update.message.reply_text("\n".join(msg_lines), parse_mode="Markdown")

    # Δημιουργία και στυλιζάρισμα Excel
    df = pd.DataFrame(data)
    df.columns = ["Κατάταξη", "Όχημα / Μοντέλο", "Εταιρεία", "Συνολική Τιμή (€)", "Τιμή ανά Ημέρα (€)"]
    
    excel_buffer = io.BytesIO()
    with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="DoYouSpain_Results")
        worksheet = writer.sheets["DoYouSpain_Results"]

        header_fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
        header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
        center_align = Alignment(horizontal="center", vertical="center")
        thin_border = Border(
            left=Side(style='thin', color='D9D9D9'),
            right=Side(style='thin', color='D9D9D9'),
            top=Side(style='thin', color='D9D9D9'),
            bottom=Side(style='thin', color='D9D9D9')
        )

        for col_num in range(1, len(df.columns) + 1):
            cell = worksheet.cell(row=1, column=col_num)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = center_align

        for row in worksheet.iter_rows(min_row=2, max_row=len(df)+1, min_col=1, max_col=len(df.columns)):
            for cell in row:
                cell.border = thin_border
                if cell.column in [4, 5]:
                    cell.number_format = '#,##0.00 €'
                    cell.alignment = Alignment(horizontal="right")

        for col in worksheet.columns:
            max_len = max(len(str(cell.value or '')) for cell in col)
            col_letter = get_column_letter(col[0].column)
            worksheet.column_dimensions[col_letter].width = max(max_len + 4, 14)

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

async def main_async():
    await start_dummy_server()
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
    
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    
    # Διατήρηση της εφαρμογής ανοιχτής
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main_async())
