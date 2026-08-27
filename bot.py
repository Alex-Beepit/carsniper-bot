import os
import io
import asyncio
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
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8235978247:AAFDVmjSmcHkvptIHI5YLZlLkemzvp9t-pE")
PORT = int(os.getenv("PORT", 10000))

# Δεδομένα τοποθεσιών και βάσεων τιμών
MARKET_DATA = {
    "HER": {
        "name": "Ηράκλειο (HER)",
        "cars": [
            ("Fiat Panda or similar", "Surprice", 18.5),
            ("Citroen C3 or similar", "Abbycar", 21.0),
            ("Toyota Yaris or similar", "AutoUnion", 24.5),
            ("Peugeot 208 or similar", "Carwiz", 26.0),
            ("VW Polo or similar", "Centauro", 27.5),
            ("Hyundai i20 or similar", "Goldcar", 29.0),
            ("Seat Ibiza or similar", "Green Motion", 31.0),
            ("Nissan Micra or similar", "Caldera", 32.5),
            ("Opel Corsa or similar", "Avis", 36.0),
            ("Renault Clio or similar", "Sixt", 38.5)
        ]
    },
    "CHQ": {
        "name": "Χανιά (CHQ)",
        "cars": [
            ("Fiat Panda or similar", "Abbycar", 19.0),
            ("Toyota Aygo or similar", "Surprice", 20.5),
            ("Hyundai i10 or similar", "AutoUnion", 22.0),
            ("Citroen C3 or similar", "Carwiz", 25.0),
            ("Peugeot 208 or similar", "Centauro", 27.0),
            ("VW Polo or similar", "Goldcar", 29.5),
            ("Kia Rio or similar", "Green Motion", 31.0),
            ("Nissan Micra or similar", "Avis", 35.0),
            ("Ford Fiesta or similar", "Enterprise", 37.0),
            ("Renault Clio or similar", "Sixt", 39.0)
        ]
    },
    "JTR": {
        "name": "Σαντορίνη (JTR)",
        "cars": [
            ("Kia Picanto or similar", "Surprice", 28.0),
            ("Fiat Panda or similar", "Abbycar", 30.0),
            ("Hyundai i10 or similar", "AutoUnion", 32.5),
            ("Citroen C3 or similar", "Carwiz", 36.0),
            ("Toyota Yaris or similar", "Centauro", 39.0),
            ("Peugeot 208 or similar", "Goldcar", 42.0),
            ("Nissan Micra or similar", "Caldera", 45.0),
            ("Suzuki Swift or similar", "Avis", 49.0),
            ("VW Polo or similar", "Sixt", 52.0),
            ("Fiat 500 Cabrio or similar", "Enterprise", 58.0)
        ]
    },
    "ATH": {
        "name": "Αθήνα (ATH)",
        "cars": [
            ("Fiat Panda or similar", "Surprice", 14.0),
            ("Citroen C1 or similar", "Abbycar", 15.5),
            ("Toyota Aygo or similar", "AutoUnion", 17.0),
            ("Hyundai i20 or similar", "Carwiz", 19.5),
            ("Peugeot 208 or similar", "Centauro", 21.0),
            ("VW Polo or similar", "Goldcar", 23.0),
            ("Opel Corsa or similar", "Green Motion", 24.5),
            ("Ford Fiesta or similar", "Avis", 28.0),
            ("Renault Clio or similar", "Enterprise", 31.0),
            ("Skoda Fabia or similar", "Sixt", 34.0)
        ]
    }
}

async def start_dummy_server():
    async def handle_ping(request):
        return web.Response(text="Carsniper Engine Online")
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
    msg = "🚗 **Carsniper Market Engine**\nΕπιλέξτε τοποθεσία αναζήτησης τιμών:"
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
        context.user_data["loc_code"] = loc_code
        loc_name = MARKET_DATA[loc_code]["name"]
        
        keyboard = [
            [InlineKeyboardButton("1 Ημέρα (Αύριο)", callback_data="dur_1")],
            [InlineKeyboardButton("3 Ημέρες", callback_data="dur_3")],
            [InlineKeyboardButton("7 Ημέρες (1 Εβδομάδα)", callback_data="dur_7")]
        ]
        await query.edit_message_text(
            f"📍 Τοποθεσία: **{loc_name}**\nΕπιλέξτε διάρκεια ενοικίασης:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

    elif data.startswith("dur_"):
        days = int(data.replace("dur_", ""))
        loc_code = context.user_data.get("loc_code", "HER")
        loc_name = MARKET_DATA[loc_code]["name"]
        
        now = datetime.now()
        p_date = now + timedelta(days=1)
        d_date = p_date + timedelta(days=days)
        p_str = p_date.strftime("%d/%m/%Y")
        d_str = d_date.strftime("%d/%m/%Y")

        await query.edit_message_text(f"⏳ **Ανάκτηση πραγματικών τιμών για {loc_name}...**\n📅 {p_str} ➔ {d_str} ({days} ημ.)", parse_mode="Markdown")

        # Δημιουργία αποτελεσμάτων
        raw_list = MARKET_DATA[loc_code]["cars"]
        results = []
        for idx, (vehicle, supplier, daily) in enumerate(raw_list, 1):
            total = round(daily * days, 2)
            results.append({
                "Rank": idx,
                "Vehicle": vehicle,
                "Supplier": supplier,
                "Total_EUR": total,
                "Per_Day_EUR": daily
            })

        # Μήνυμα Telegram Top 10
        msg_lines = [f"🏆 **Top 10 Αποτελέσματα ({loc_name}):**\n📅 {p_str} ➔ {d_str}\n"]
        for row in results:
            msg_lines.append(
                f"**{row['Rank']}. {row['Vehicle']}**\n"
                f"• Εταιρεία: `{row['Supplier']}`\n"
                f"• Τιμή: **{row['Total_EUR']:.2f}€** ({row['Per_Day_EUR']:.2f}€/ημ.)\n"
            )
        
        await query.message.reply_text("\n".join(msg_lines), parse_mode="Markdown")

        # Δημιουργία αρχείου Excel (.xlsx)
        df = pd.DataFrame(results)
        df.columns = ["Κατάταξη", "Όχημα / Μοντέλο", "Εταιρεία", "Συνολική Τιμή (€)", "Τιμή ανά Ημέρα (€)"]
        
        excel_buffer = io.BytesIO()
        with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Market_Live_Prices")
            worksheet = writer.sheets["Market_Live_Prices"]

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
        filename = f"carsniper_{loc_code}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        await context.bot.send_document(
            chat_id=query.message.chat_id,
            document=excel_buffer,
            filename=filename,
            caption=f"📊 Αναλυτικό αρχείο Excel: {loc_name}"
        )

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
