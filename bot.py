import os
import io
import asyncio
import re
import json
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
from curl_cffi.requests import AsyncSession
from bs4 import BeautifulSoup
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
PORT = int(os.getenv("PORT", 10000))

LOCATIONS = {
    "loc_HER": ("Ηράκλειο (HER)", "Crete - Heraklion Airport"),
    "loc_CHQ": ("Χανιά (CHQ)", "Crete - Chania Airport"),
    "loc_JTR": ("Σαντορίνη (JTR)", "Santorini - Airport"),
    "loc_ATH": ("Αθήνα (ATH)", "Athens - Airport")
}

async def start_dummy_server():
    async def handle_ping(request):
        return web.Response(text="Carsniper 100% Live Engine Active")
    server = web.Application()
    server.router.add_get("/", handle_ping)
    server.router.add_get("/healthz", handle_ping)
    runner = web.AppRunner(server)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📍 Ηράκλειο (HER)", callback_data="loc_HER"), InlineKeyboardButton("📍 Χανιά (CHQ)", callback_data="loc_CHQ")],
        [InlineKeyboardButton("📍 Σαντορίνη (JTR)", callback_data="loc_JTR"), InlineKeyboardButton("📍 Αθήνα (ATH)", callback_data="loc_ATH")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    msg = "🚗 **Carsniper STRICT Live Engine**\nΑυστηρή ανάκτηση πραγματικών τιμών. Επιλέξτε:"
    if update.message:
        await update.message.reply_text(msg, reply_markup=reply_markup, parse_mode="Markdown")
    elif update.callback_query:
        await update.callback_query.message.reply_text(msg, reply_markup=reply_markup, parse_mode="Markdown")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("loc_"):
        loc_code = data.replace("loc_", "")
        loc_name, loc_query = LOCATIONS[data]
        context.user_data["loc_name"] = loc_name
        context.user_data["loc_query"] = loc_query

        keyboard = [
            [InlineKeyboardButton("1 Ημέρα (Αύριο)", callback_data="dur_1")],
            [InlineKeyboardButton("3 Ημέρες", callback_data="dur_3")],
            [InlineKeyboardButton("7 Ημέρες (1 Εβδομάδα)", callback_data="dur_7")]
        ]
        await query.edit_message_text(
            f"📍 Τοποθεσία: **{loc_name}**\nΕπιλέξτε διάρκεια:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

    elif data.startswith("dur_"):
        days = int(data.replace("dur_", ""))
        loc_name = context.user_data.get("loc_name", "Ηράκλειο (HER)")
        loc_query = context.user_data.get("loc_query", "Crete - Heraklion Airport")

        now = datetime.now()
        p_date = now + timedelta(days=1)
        d_date = p_date + timedelta(days=days)
        p_str = p_date.strftime("%d/%m/%Y")
        d_str = d_date.strftime("%d/%m/%Y")

        await query.edit_message_text(f"⏳ **Σύνδεση στο DoYouSpain...**\nΓίνεται προσπάθεια παράκαμψης ασφαλείας και άντλησης LIVE δεδομένων για {loc_name}.", parse_mode="Markdown")

        results = await fetch_live_doyouspain(loc_query, p_str, d_str, days)

        if results == "BLOCKED":
            await query.message.reply_text("🚨 **Αποτυχία:** Το Cloudflare μπλόκαρε την IP του server μας. Δεν επεστράφησαν δεδομένα. Δοκιμάστε ξανά αργότερα.")
            return
        elif not results:
            await query.message.reply_text("❌ Η αναζήτηση ολοκληρώθηκε αλλά δεν βρέθηκαν διαθέσιμα αυτοκίνητα για αυτές τις ημερομηνίες.")
            return

        msg_lines = [f"🏆 **100% LIVE Αποτελέσματα ({loc_name}):**\n📅 {p_str} ➔ {d_str}\n"]
        for row in results[:10]:
            msg_lines.append(
                f"**{row['Rank']}. {row['Vehicle']}**\n"
                f"• Εταιρεία: `{row['Supplier']}`\n"
                f"• Τιμή: **{row['Total_EUR']:.2f}€** ({row['Per_Day_EUR']:.2f}€/ημ.)\n"
            )
        await query.message.reply_text("\n".join(msg_lines), parse_mode="Markdown")

        # Create Excel
        df = pd.DataFrame(results)
        df.columns = ["Κατάταξη", "Όχημα / Μοντέλο", "Εταιρεία", "Συνολική Τιμή (€)", "Τιμή ανά Ημέρα (€)"]

        excel_buffer = io.BytesIO()
        with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="DoYouSpain_Live")
            worksheet = writer.sheets["DoYouSpain_Live"]

            header_fill = PatternFill(start_color="8B0000", end_color="8B0000", fill_type="solid")
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
        filename = f"STRICT_LIVE_{loc_name[:3]}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        await context.bot.send_document(
            chat_id=query.message.chat_id,
            document=excel_buffer,
            filename=filename,
            caption="📊 Το αρχείο περιέχει αποκλειστικά πραγματικές τιμές από το DoYouSpain."
        )

async def fetch_live_doyouspain(loc_query, p_str, d_str, days):
    results = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,el;q=0.8",
        "Origin": "https://www.doyouspain.com",
        "Referer": "https://www.doyouspain.com/index.htm",
        "Content-Type": "application/x-www-form-urlencoded"
    }

    try:
        async with AsyncSession(impersonate="chrome120") as session:
            # 1. Παίρνουμε τα αρχικά cookies
            await session.get("https://www.doyouspain.com/index.htm", headers=headers, timeout=15)

            # 2. Προσομοιώνουμε ΑΚΡΙΒΩΣ το POST request της φόρμας
            payload = {
                "lugar_entrega": loc_query,
                "lugar_recogida": loc_query,
                "fecha_recogida": p_str,
                "hora_recogida": "10",
                "minutos_recogida": "00",
                "fecha_devolucion": d_str,
                "hora_devolucion": "10",
                "minutos_devolucion": "00",
                "age": "30",
                "residente": "0"
            }

            resp = await session.post(
                "https://www.doyouspain.com/do/search",
                data=payload,
                headers=headers,
                allow_redirects=True,
                timeout=30
            )

            html = resp.text

            # 3. Έλεγχος για Cloudflare Block
            if "Just a moment..." in html or "cloudflare" in html.lower():
                return "BLOCKED"

            # 4. Εξόρυξη των Live Δεδομένων (Αναζήτηση κρυφού JSON αν υπάρχει)
            json_match = re.search(r'var\s+(?:cars|resultsData|data)\s*=\s*(\[.*?\]);', html, re.DOTALL)
            if json_match:
                try:
                    cars_data = json.loads(json_match.group(1))
                    for car in cars_data:
                        c_name = car.get("name") or car.get("car_name") or car.get("model")
                        s_name = car.get("supplier") or car.get("vendor_name") or car.get("company")
                        price = float(car.get("price") or car.get("total_price") or 0)
                        
                        if c_name and price > 0:
                            results.append({
                                "Vehicle": str(c_name).strip(),
                                "Supplier": str(s_name).strip() if s_name else "Partner",
                                "Total_EUR": price,
                                "Per_Day_EUR": round(price / days, 2)
                            })
                except Exception:
                    pass

            # 5. Αν δεν υπάρχει JSON, κάνουμε scraping στα HTML tags της σελίδας
            if not results:
                soup = BeautifulSoup(html, "html.parser")
                blocks = soup.find_all("div", class_=re.compile(r"(booking-item|car-box|result-item|deal)"))
                
                suppliers = ["Surprice", "Abbycar", "addCar", "AutoUnion", "Autocar", "Avance", "Avis", "Beepit", "Caldera", "CarRental2Greece", "Carwiz", "Centauro", "Cretamotor", "Enterprise", "Europcar", "Exer", "Flex", "Goldcar", "Green Motion", "Hertz", "Sixt", "OK Mobility", "Budget", "Thrifty", "Dollar"]

                for block in blocks:
                    text = block.get_text(separator=" ")
                    
                    price_match = re.search(r'(\d+[\.,]\d{2})\s*€', text)
                    if not price_match: continue
                    price = float(price_match.group(1).replace(",", "."))
                    
                    car_name = "Economy Car or similar"
                    lines = text.splitlines()
                    for line in lines:
                        l = line.strip()
                        if "or similar" in l.lower() or any(brand in l.lower() for brand in ["fiat", "citroen", "vw", "toyota", "hyundai", "peugeot", "opel", "kia", "suzuki", "nissan", "renault", "skoda", "seat"]):
                            car_name = l
                            break

                    supplier = "Partner"
                    for s in suppliers:
                        if s.lower() in text.lower():
                            supplier = s
                            break

                    results.append({
                        "Vehicle": car_name,
                        "Supplier": supplier,
                        "Total_EUR": price,
                        "Per_Day_EUR": round(price / days, 2)
                    })

            # 6. Καθαρισμός διπλότυπων, ταξινόμηση και Ranking
            seen = set()
            unique_results = []
            for r in results:
                key = f"{r['Vehicle']}_{r['Total_EUR']}"
                if key not in seen:
                    seen.add(key)
                    unique_results.append(r)
            
            unique_results.sort(key=lambda x: x["Total_EUR"])
            
            for idx, r in enumerate(unique_results, 1):
                r["Rank"] = idx

            return unique_results[:50] # Επιστρέφει τα κορυφαία 50 πραγματικά αποτελέσματα

    except Exception as e:
        print(f"Scraper Engine Failed: {e}")
        return None

def main():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(start_dummy_server())

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))

    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
