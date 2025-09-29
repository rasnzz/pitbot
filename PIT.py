import logging
import os
import sys
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters,
    CallbackQueryHandler
)
import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv
from datetime import datetime

# Загружаем переменные окружения
load_dotenv()

# Настройки
BOT_TOKEN = os.getenv('BOT_TOKEN')
CHANNEL_USERNAME = os.getenv('CHANNEL_USERNAME')
ADMIN_CHAT_ID = os.getenv('ADMIN_CHAT_ID')
SPREADSHEET_URL = os.getenv('SPREADSHEET_URL')

# Пути к изображениям
WELCOME_IMAGE = "images/welcome.jpg"
COUPON_IMAGE = "images/coupon.jpg"

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

class GoogleSheetsManager:
    def __init__(self):
        self.client = None
        self.sheet = None
        self.setup_gsheets()
    
    def setup_gsheets(self):
        """Настройка подключения к Google Sheets"""
        try:
            # ПРАВИЛЬНЫЕ SCOPE
            scope = [
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive.file"
            ]
            
            # Проверяем существование файла
            if not os.path.exists('credentials.json'):
                logging.error("❌ Файл credentials.json не найден")
                return False
                
            creds = Credentials.from_service_account_file('credentials.json', scopes=scope)
            self.client = gspread.authorize(creds)
            
            # Проверяем переменную окружения
            spreadsheet_url = os.getenv('SPREADSHEET_URL')
            if not spreadsheet_url:
                logging.error("❌ SPREADSHEET_URL не найден в .env")
                return False
                
            self.sheet = self.client.open_by_url(spreadsheet_url).sheet1
            
            # Создаем заголовки если их нет
            if not self.sheet.get_all_records():
                headers = ["Дата", "Имя", "Фамилия", "Телефон", "Username", "User ID", "Купон"]
                self.sheet.append_row(headers)
                
            logging.info("✅ Google Sheets подключен успешно")
            return True
            
        except Exception as e:
            logging.error(f"❌ Ошибка подключения к Google Sheets: {e}")
            return False
    
    def add_lead(self, data):
        """Добавление лида в таблицу"""
        if not self.sheet:
            logging.error("❌ Google Sheets не инициализирован")
            return False
            
        try:
            row = [
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                data.get('first_name', ''),
                data.get('last_name', ''),
                data.get('phone', ''),
                data.get('username', ''),
                data.get('user_id', ''),
                data.get('coupon', '')
            ]
            self.sheet.append_row(row)
            logging.info(f"✅ Данные добавлены в таблицу: {data['first_name']} - {data['phone']}")
            return True
        except Exception as e:
            logging.error(f"❌ Ошибка добавления в таблицу: {e}")
            return False

# Инициализация менеджера Google Sheets
gsheets_manager = GoogleSheetsManager()

async def send_photo_with_caption(chat_id, context, image_path, caption, reply_markup=None):
    """Универсальная функция отправки фото с текстом"""
    try:
        if os.path.exists(image_path):
            with open(image_path, 'rb') as photo:
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=photo,
                    caption=caption,
                    reply_markup=reply_markup,
                    parse_mode="HTML"
                )
            return True
        else:
            await context.bot.send_message(
                chat_id=chat_id,
                text=caption,
                reply_markup=reply_markup,
                parse_mode="HTML"
            )
            logging.warning(f"⚠️ Изображение не найдено: {image_path}")
            return False
    except Exception as e:
        logging.error(f"❌ Ошибка отправки фото: {e}")
        await context.bot.send_message(
            chat_id=chat_id,
            text=caption,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
        return False

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user = update.effective_user
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Подписаться на канал", url=f"https://t.me/{CHANNEL_USERNAME[1:]}")],
        [InlineKeyboardButton("✅ Я подписался", callback_data="check_subscription")]
    ])
    
    caption = (
        "🛠️ Добро пожаловать в <b>P.I.T Tools Оренбургs</b>!\n\n"
        "🎁 <b>Получите какой-то подарок, еще не придумали</b>\n\n"
        "Для участия в акции необходимо:\n"
        "1️⃣ Подписаться на наш канал\n"
        "2️⃣ Поделиться номером телефона\n\n"
        "После этого вы получите персональный купон для использования в нашем магазине!"
    )
    
    await send_photo_with_caption(
        update.effective_chat.id,
        context,
        WELCOME_IMAGE,
        caption,
        keyboard
    )

async def check_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверка подписки на канал"""
    query = update.callback_query
    await query.answer()
    
    try:
        user_channel_status = await context.bot.get_chat_member(
            chat_id=CHANNEL_USERNAME,
            user_id=query.from_user.id
        )
        
        if user_channel_status.status in ["member", "administrator", "creator"]:
            keyboard = ReplyKeyboardMarkup(
                [[KeyboardButton("📞 Поделиться номером", request_contact=True)]],
                resize_keyboard=True,
                one_time_keyboard=True
            )
            
            await query.edit_message_caption(
                caption="✅ <b>Отлично! Вы подписаны на канал!</b>\n\nТеперь поделитесь своим номером телефона с помощью кнопки ниже 👇"
            )
            
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="Нажмите на кнопку ниже, чтобы поделиться номером телефона:",
                reply_markup=keyboard
            )
        else:
            await query.edit_message_caption(
                caption="❌ <b>Вы еще не подписались на канал!</b>\n\nПожалуйста, подпишитесь и нажмите проверку снова."
            )
            
    except Exception as e:
        logging.error(f"❌ Ошибка проверки подписки: {e}")
        await query.edit_message_caption(
            caption="⚠️ <b>Произошла ошибка при проверке подписки.</b>\n\nПожалуйста, попробуйте позже."
        )

async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка полученного контакта"""
    contact = update.message.contact
    user = update.message.from_user
    
    if contact.user_id == user.id:
        # Форматирование номера телефона
        phone_number = contact.phone_number
        if not phone_number.startswith('+'):
            phone_number = f"+{phone_number}"
        
        # Генерация купона
        coupon_code = f"PIT-{user.id % 10000:04d}-15"
        
        # Сохранение в Google Sheets
        user_data = {
            'first_name': contact.first_name,
            'last_name': contact.last_name or '',
            'phone': phone_number,
            'username': user.username or '',
            'user_id': user.id,
            'coupon': coupon_code
        }
        
        save_success = gsheets_manager.add_lead(user_data)
        
        # Сообщение с купоном
        caption = (
            "🎉 <b>Благодарим за участие!</b>\n\n"
            f"🏷️ <b>Ваш купон на что-то</b> <code>{coupon_code}</code>\n\n"
            "🎁 <b>Что вы получаете:</b>\n"
            "• Ничего\n"
            "• И тут ничего\n"
            "• Бесплатную консультацию специалиста\n\n"
            "🏪 <b>Адрес магазина:</b>\n"
            "г. Оренбург, ул. Монтажников 37/3\n\n"
            "📞 <b>Телефон для связи:</b> +77777777\n\n"
            "<i>Купон действует в течение 15 дней</i>"
        )
        
        await send_photo_with_caption(
            update.effective_chat.id,
            context,
            COUPON_IMAGE,
            caption
        )
        
        # Уведомление для администратора
        admin_message = (
            "📱 <b>Новый лид!</b>\n"
            f"👤 Имя: {contact.first_name}\n"
            f"📞 Телефон: {phone_number}\n"
            f"🔗 Username: @{user.username}\n" if user.username else "🔗 Username: Не указан\n"
            f"🆔 User ID: {user.id}\n"
            f"🏷️ Купон: {coupon_code}\n"
            f"💾 В таблицу: {'✅' if save_success else '❌'}"
        )
        
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=admin_message,
            parse_mode="HTML"
        )
        
    else:
        await update.message.reply_text("❌ Пожалуйста, поделитесь своим номером телефона.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текстовых сообщений"""
    if update.message.text and update.message.text != "/start":
        await update.message.reply_text(
            "🤖 Пожалуйста, используйте кнопки для взаимодействия с ботом.\n"
            "Или введите /start для начала работы."
        )

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ошибок"""
    logging.error(f"❌ Ошибка: {context.error}")

def main():
    """Основная функция запуска бота"""
    # Проверяем обязательные переменные
    required_vars = ['BOT_TOKEN', 'CHANNEL_USERNAME', 'ADMIN_CHAT_ID', 'SPREADSHEET_URL']
    for var in required_vars:
        if not os.getenv(var):
            logging.error(f"❌ Отсутствует обязательная переменная: {var}")
            return
    
    # Создаем приложение
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(check_subscription, pattern="check_subscription"))
    application.add_handler(MessageHandler(filters.CONTACT, handle_contact))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Обработчик ошибок
    application.add_error_handler(error_handler)
    
    # Запуск бота
    logging.info("🚀 Бот запущен...")
    application.run_polling()

if __name__ == "__main__":
    main()


