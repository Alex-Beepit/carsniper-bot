import os
import io
import asyncio
import re
import random
import pandas as pd
from datetime import datetime, timedelta
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from playwright.async_api import async_playwright
from playwright_stealth import stealth_async
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
PORT = int(os.getenv("PORT", 10000))

LOC_CODES = {
    "loc_her": ("Ηράκλειο (HER)", "heraklion_airport"),
    "loc_chq": ("Χανιά (CHQ)", "chania_airport"),
    "loc_jtr": ("Σαντορίνη (JTR)", "santorini_airport"),
    "loc_ath": ("Αθήνα (ATH)", "athens_airport")
}

DURATIONS = {
    "dur_1": ("1 Ημέρα", 1),
    "dur_3": ("3 Ημέρες", 3),
    "dur_7": ("7 Ημέρες", 7),
}

async def start_dummy_server():
    async def handle_ping(request):
        return web.Response(text="Carsniper Xvfb Live")
    server = web.Application()
    server.router.add_get("/", handle_ping)
    runner = web.AppRunner(server)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📍 Ηράκλειο (HER)", callback_data="loc_her"), InlineKeyboardButton("📍 Χανιά (CHQ)", callback_data="loc_chq")],
        [InlineKeyboardButton("📍 Σαντορίνη (JTR)", callback_data="loc_jtr"), InlineKeyboardButton("📍 Αθήνα (ATH)", callback_data="loc_ath")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    msg = "🚗 **Carsniper Bot**\nΕπιλέξτε τοποθεσία αναζήτησης:"
    if update.message:
        await update.message.reply_text(msg, reply_markup=reply_markup, parse_mode="Markdown")
    else:
        await update.callback_query.edit_message_text(msg, reply_markup=reply_markup, parse_mode="Markdown")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("loc_"):
        context.user_data["loc_name"], context.user_data["loc_slug"] = LOC_CODES[data]
        keyboard = [
            [InlineKeyboardButton("1 Ημέρα (Αύριο)", callback_data="dur_1")],
            [InlineKeyboardButton("3 Ημέρες", callback_data="dur_3")],
            [InlineKeyboardButton("7 Ημέρες (1 Εβδομάδα)", callback_data="dur_7")]
        ]
        await query.edit_message_text(
            f"📍 Τοποθεσία: **{context.user_data['loc_name']}**\nΕπιλέξτε διάρκεια:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

    elif data.startswith("dur_"):
        _, days = DURATIONS[data]
        now = datetime.now()
        p_date = now + timedelta(days=1)
        d_date = p_date + timedelta(days=days)

        context.user_data["days"] = days
        context.user_data["p_str"] = p_date.strftime("%d/%m/%Y")
        context.user_data["d_str"] = d_date.strftime("%d/%m/%Y")

        keyboard = [[InlineKeyboardButton("🔍 Αναζήτηση Πραγματικών Τιμών", callback_data="scrape_now")]]
        await query.edit_message_text(
            f"📍 **{context.user_data['loc_name']}**\n📅 {context.user_data['p_str']} ➔ {context.user_data['d_str']} ({days} ημ.)\n\nΠατήστε το κουμπί για live αναζήτηση:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

    elif data == "scrape_now":
        await query.edit_message_text("⏳ **Ανάκτηση μέσω εικονικής οθόνης (Bypassing Cloudflare)...** \nΠαρακαλώ περιμένετε 20-30 δευτερόλεπτα.", parse_mode="Markdown")
        
        results = await scrape_with_playwright(context.user_data)

        if not results:
            await query.message.reply_text("❌ Η αναζήτηση απέτυχε. Το σύστημα ασφαλείας είναι ενεργό. Δοκιμάστε ξανά σε 5 λεπτά με /start.")
            return

        msg_lines = [f"🏆 **Top 10 Αποτελέσματα ({context.user_data['loc_name']}):**\n"]
        for row in results[:10]:
            msg_lines.append(
                f"**{row['Rank']}. {row['Vehicle']}**\n"
                f"• Εταιρεία: `{row['Supplier']}`\n"
                f"• Τιμή: **{row['Total_EUR']}€** ({row['Per_Day_EUR']}€/ημ.)\n"
            )
        await query.message.reply_text("\n".join(msg_lines), parse_mode="Markdown")

        df = pd.DataFrame(results)
        df.columns = ["Κατάταξη", "Όχημα / Μοντέλο", "Εταιρεία", "Συνολική Τιμή (€)", "Τιμή ανά Ημέρα (€)"]
        
        excel_buffer = io.BytesIO()
        with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="DoYouSpain")
            worksheet = writer.sheets["DoYouSpain"]

            header_fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
            header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
            center_align = Alignment(horizontal="center", vertical="center")
            thin_border = Border(
                left=Side(style='thin', color='D9D9D9'), right=Side(style='thin', color='D9D9D9'),
                top=Side(style='thin', color='D9D9D9'), bottom=Side(style='thin', color='D9D9D9')
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
            chat_id=query.message.chat_id,
            document=excel_buffer,
            filename=filename,
            caption="📊 Αναλυτικά αποτελέσματα σε αρχείο Excel."
        )

async def scrape_with_playwright(user_data):
    results = []
    days = max(user_data.get("days", 1), 1)
    loc_slug = user_data.get("loc_slug", "heraklion_airport")
    p_str = user_data.get("p_str")
    d_str = user_data.get("d_str")
    
    async with async_playwright() as p:
        # ΤΟ ΑΠΟΛΥΤΟ ΜΥΣΤΙΚΟ ΕΙΝΑΙ ΕΔΩ: headless=False
        browser = await p.chromium.launch(
            headless=False,
            args=[
                "--no-sandbox", 
                "--disable-setuid-sandbox", 
                "--disable-blink-features=AutomationControlled",
                "--window-size=1920,1080"
            ]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="el-GR",
            timezone_id="Europe/Athens"
        )
        page = await context.new_page()
        
        await stealth_async(page)
        
        try:
            await asyncio.sleep(random.uniform(1.0, 2.5))
            
            url = f"https://www.doyouspain.com/do/list/en?loc={loc_slug}&pickup={p_str}&dropoff={d_str}"
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            
            # Έξτρα χρόνος αν εμφανιστεί ο κύκλος φόρτωσης του Cloudflare
            await page.wait_for_timeout(6000)
            await page.mouse.wheel(0, 700)
            await asyncio.sleep(random.uniform(2.0, 3.0))
            
            try:
                await page.wait_for_selector("text=View deal", timeout=30000)
            except Exception:
                await page.wait_for_timeout(5000)

            cards = await page.locator("div[class*='deal'], div[class*='result'], div[class*='car']").all()
            
            rank = 1
            seen = set()
            for card in cards:
                if rank > 10: break
                text = await card.inner_text()
                
                prices = re.findall(r'(\d+[\.,]\d{2})\s*€', text)
                if not prices: continue
                
                price_main = float(prices[0].replace(",", "."))
                price_day = float(prices[1].replace(",", ".")) if len(prices) > 1 else round(price_main/days, 2)
                
                car_name = "Economy Car"
                for l in text.split("\n"):
                    l_clean = l.strip()
                    if "or similar" in l_clean.lower() or any(b in l_clean.lower() for b in ["fiat", "citroen", "vw", "toyota", "hyundai", "peugeot", "opel", "kia", "suzuki", "nissan", "renault"]):
                        car_name = l_clean
                        break
                        
                key = f"{car_name}_{price_main}"
                if key in seen: continue
                seen.add(key)
                
                supplier = "Partner"
                for s in ["Surprice", "Abbycar", "addCar", "AutoUnion", "Autocar", "Avance", "Avis", "Beepit", "Caldera", "CarRental2Greece", "Carwiz", "Centauro", "Cretamotor", "Enterprise", "Europcar", "Exer", "Flex", "Goldcar", "Green Motion", "Hertz", "Sixt", "OK Mobility"]:
                    if s.lower() in text.lower():
                        supplier = s
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
            print(f"Playwright Xvfb Error: {e}")
        finally:
            await browser.close()
            
    return results

async def main_async():
    await start_dummy_server()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main_async())
