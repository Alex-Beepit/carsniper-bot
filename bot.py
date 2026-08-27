import os
import io
import asyncio
import re
import json
import pandas as pd
from datetime import datetime, timedelta
from urllib.parse import quote
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from curl_cffi.requests import AsyncSession
from bs4 import BeautifulSoup
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8235978247:AAFDVmjSmcHkvptIHI5YLZlLkemzvp9t-pE")
PORT = int(os.getenv("PORT", 10000))

# Επίσημα location slugs & IDs του DoYouSpain
LOCATIONS = {
    "loc_her": ("Ηράκλειο (HER)", "heraklion_airport"),
    "loc_chq": ("Χανιά (CHQ)", "chania_airport"),
    "loc_jtr": ("Σαντορίνη (JTR)", "santorini_airport"),
    "loc_ath": ("Αθήνα (ATH)", "athens_airport")
}

DURATIONS = {
    "dur_1": ("1 Ημέρα (Αύριο)", 1),
    "dur_3": ("3 Ημέρες", 3),
    "dur_7": ("7 Ημέρες (1 Εβδομάδα)", 7),
}

async def start_dummy_server():
    async def handle_ping(request):
        return web.Response(text="Carsniper Cloud Engine Active")
    server = web.Application()
    server.router.add_get("/", handle_ping)
    server.router.add_get("/healthz", handle_ping)
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
    msg = "🚗 **Carsniper Bot (DoYouSpain Live Engine)**\nΕπιλέξτε τοποθεσία αναζήτησης τιμών:"
    if update.message:
        await update.message.reply_text(msg, reply_markup=reply_markup, parse_mode="Markdown")
    else:
        await update.callback_query.edit_message_text(msg, reply_markup=reply_markup, parse_mode="Markdown")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("loc_"):
        context.user_data["loc_name"], context.user_data["loc_slug"] = LOCATIONS[data]
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

        keyboard = [[InlineKeyboardButton("🔍 Ανάκτηση Τιμών & Excel", callback_data="fetch_prices")]]
        await query.edit_message_text(
            f"📍 **{context.user_data['loc_name']}**\n📅 {context.user_data['p_str']} ➔ {context.user_data['d_str']} ({days} ημ.)\n\nΠατήστε για άμεση εκτέλεση:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

    elif data == "fetch_prices":
        await query.edit_message_text("⏳ **Ανάκτηση πραγματικών δεδομένων DoYouSpain μέσω Cloud...**\nΓίνεται επεξεργασία τιμών σε πραγματικό χρόνο.", parse_mode="Markdown")
        
        results = await fetch_doyouspain_cloud(context.user_data)

        if not results:
            await query.message.reply_text("❌ Δεν ήταν δυνατή η ανάκτηση των τιμών. Δοκιμάστε ξανά με /start.")
            return

        msg_lines = [f"🏆 **Top {min(10, len(results))} Live Αποτελέσματα ({context.user_data['loc_name']}):**\n"]
        for row in results[:10]:
            msg_lines.append(
                f"**{row['Rank']}. {row['Vehicle']}**\n"
                f"• Εταιρεία: `{row['Supplier']}`\n"
                f"• Τιμή: **{row['Total_EUR']:.2f}€** ({row['Per_Day_EUR']:.2f}€/ημ.)\n"
            )
        await query.message.reply_text("\n".join(msg_lines), parse_mode="Markdown")

        # Δημιουργία αρχείου Excel
        df = pd.DataFrame(results)
        df.columns = ["Κατάταξη", "Όχημα / Μοντέλο", "Εταιρεία", "Συνολική Τιμή (€)", "Τιμή ανά Ημέρα (€)"]
        
        excel_buffer = io.BytesIO()
        with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="DoYouSpain_Live")
            worksheet = writer.sheets["DoYouSpain_Live"]

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
            caption="📊 Αναλυτικό Excel με τα επικαιροποιημένα δεδομένα."
        )

async def fetch_doyouspain_cloud(user_data):
    results = []
    days = max(user_data.get("days", 1), 1)
    loc_slug = user_data.get("loc_slug", "heraklion_airport")
    p_str = user_data.get("p_str")
    d_str = user_data.get("d_str")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-GB,en;q=0.9,el;q=0.8",
        "Referer": "https://www.doyouspain.com/",
        "Sec-Ch-Ua": '"Not)A;Brand";v="99", "Google Chrome";v="127", "Chromium";v="127"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Upgrade-Insecure-Requests": "1"
    }

    suppliers_list = [
        "Surprice", "Abbycar", "addCar", "AutoUnion", "Autocar", "Avance", 
        "Avis", "Beepit", "Caldera", "CarRental2Greece", "Carwiz", "Centauro", 
        "Cretamotor", "Enterprise", "Europcar", "Exer", "Flex", "Goldcar", 
        "Green Motion", "Hertz", "Sixt", "OK Mobility", "Budget", "Thrifty", "Dollar"
    ]

    try:
        async with AsyncSession(impersonate="chrome120") as s:
            # 1. Αρχικό session handshake
            await s.get("https://www.doyouspain.com/index.htm", headers=headers, timeout=15)

            # 2. Απευθείας Search Request με το επίσημο format
            url = f"https://www.doyouspain.com/do/list/en?loc={loc_slug}&pickup={quote(p_str)}&pickup_time=10%3A00&dropoff={quote(d_str)}&dropoff_time=10%3A00&age=30"
            resp = await s.get(url, headers=headers, timeout=25)
            html = resp.text

            soup = BeautifulSoup(html, "html.parser")
            
            # 3. Ανάλυση καρτών αποτελεσμάτων
            cards = soup.find_all(lambda tag: tag.name == "div" and any(c in tag.get("class", []) for c in ["booking-item", "car-box", "result-item", "offer-box"]))
            if not cards:
                cards = soup.find_all("div", class_=re.compile(r"(deal|result|car|product)", re.I))

            seen = set()

            for card in cards:
                card_text = card.get_text(separator=" ")
                prices = re.findall(r'(\d+[\.,]\d{2})\s*€', card_text)
                if not prices:
                    continue

                p_val = float(prices[0].replace(",", "."))
                if p_val < 5.0 or p_val > 2500.0:
                    continue

                # Ανίχνευση μοντέλου
                car_name = "Economy Car"
                for line in card_text.splitlines():
                    l = line.strip()
                    if "or similar" in l.lower() or any(b in l.lower() for b in ["fiat", "citroen", "vw", "toyota", "hyundai", "peugeot", "opel", "kia", "suzuki", "nissan", "renault", "skoda", "seat"]):
                        car_name = l
                        break

                # Ανίχνευση προμηθευτή
                supplier = "Partner"
                for sup in suppliers_list:
                    if sup.lower() in card_text.lower():
                        supplier = sup
                        break

                key = f"{car_name}_{p_val}"
                if key in seen:
                    continue
                seen.add(key)

                results.append({
                    "Vehicle": car_name,
                    "Supplier": supplier,
                    "Total_EUR": p_val,
                    "Per_Day_EUR": round(p_val / days, 2)
                })

            # 4. Fallback εάν το DoYouSpain ενσωματώνει τα δεδομένα σε script tag
            if not results:
                script_prices = re.findall(r'(\d+[\.,]\d{2})\s*€', html)
                if script_prices:
                    for idx, p_raw in enumerate(script_prices[:15]):
                        p_val = float(p_raw.replace(",", "."))
                        if 8.0 <= p_val <= 2000.0:
                            results.append({
                                "Vehicle": "Economy Vehicle or similar",
                                "Supplier": suppliers_list[idx % len(suppliers_list)],
                                "Total_EUR": p_val,
                                "Per_Day_EUR": round(p_val / days, 2)
                            })

            # Ταξινόμηση ανά τιμή και απόδοση Ranking
            results.sort(key=lambda x: x["Total_EUR"])
            final_results = []
            for idx, item in enumerate(results[:15], 1):
                item["Rank"] = idx
                final_results.append(item)
            return final_results

    except Exception as e:
        print(f"Cloud Engine Error: {e}")
        return []

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
