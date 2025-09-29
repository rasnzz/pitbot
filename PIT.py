import logging
import os
import sys
import sqlite3
import asyncio
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InputMediaPhoto
)
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters,
    CallbackQueryHandler,
    ConversationHandler
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

# Состояния для рассылки
BROADCAST_TEXT, BROADCAST_PHOTO = range(2)

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

class UserManager:
    def __init__(self):
        self.db_path = '/root/pitbot/PIT/users.db'
        self.setup_database()
    
    def get_connection(self):
        """Создание соединения с базой данных"""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("PRAGMA foreign_keys = ON")
            return conn
        except Exception as e:
            logging.error(f"❌ Ошибка подключения к БД: {e}")
            return None
    
    def setup_database(self):
        """Создание базы данных для отслеживания пользователей"""
        try:
            conn = self.get_connection()
            if conn is None:
                return
            
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    phone TEXT UNIQUE,
                    username TEXT,
                    first_name TEXT,
                    registered_at TIMESTAMP,
                    coupon_code TEXT
                )
            ''')
            conn.commit()
            conn.close()
            logging.info("✅ База данных пользователей инициализирована")
        except Exception as e:
            logging.error(f"❌ Ошибка инициализации базы данных: {e}")
    
    def is_user_registered(self, user_id):
        """Проверка, регистрировался ли пользователь ранее"""
        try:
            conn = self.get_connection()
            if conn is None:
                return False
                
            cursor = conn.cursor()
            cursor.execute('SELECT user_id FROM users WHERE user_id = ?', (user_id,))
            result = cursor.fetchone() is not None
            conn.close()
            return result
        except Exception as e:
            logging.error(f"❌ Ошибка проверки пользователя {user_id}: {e}")
            return False
    
    def register_user(self, user_data):
        """Регистрация нового пользователя"""
        conn = None
        try:
            conn = self.get_connection()
            if conn is None:
                return False
                
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO users (user_id, phone, username, first_name, registered_at, coupon_code)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                user_data['user_id'],
                user_data['phone'],
                user_data.get('username', ''),
                user_data.get('first_name', ''),
                datetime.now(),
                user_data.get('coupon', '')
            ))
            conn.commit()
            conn.close()
            logging.info(f"✅ Пользователь {user_data['user_id']} зарегистрирован в локальной БД")
            return True
        except sqlite3.IntegrityError as e:
            logging.warning(f"⚠️ Ошибка целостности данных для пользователя {user_data['user_id']}: {e}")
            # Пробуем обновить существующую запись
            try:
                if conn:
                    cursor = conn.cursor()
                    cursor.execute('''
                        UPDATE users SET 
                        phone = ?, username = ?, first_name = ?, registered_at = ?, coupon_code = ?
                        WHERE user_id = ?
                    ''', (
                        user_data['phone'],
                        user_data.get('username', ''),
                        user_data.get('first_name', ''),
                        datetime.now(),
                        user_data.get('coupon', ''),
                        user_data['user_id']
                    ))
                    conn.commit()
                    conn.close()
                    logging.info(f"✅ Данные пользователя {user_data['user_id']} обновлены")
                    return True
            except Exception as update_error:
                logging.error(f"❌ Ошибка обновления пользователя {user_data['user_id']}: {update_error}")
                return False
        except Exception as e:
            logging.error(f"❌ Ошибка регистрации пользователя {user_data['user_id']}: {e}")
            if conn:
                conn.close()
            return False
    
    def get_user_coupon(self, user_id):
        """Получение купона пользователя"""
        try:
            conn = self.get_connection()
            if conn is None:
                return None
                
            cursor = conn.cursor()
            cursor.execute('SELECT coupon_code FROM users WHERE user_id = ?', (user_id,))
            result = cursor.fetchone()
            conn.close()
            return result[0] if result else None
        except Exception as e:
            logging.error(f"❌ Ошибка получения купона для {user_id}: {e}")
            return None
    
    def get_stats(self):
        """Получение статистики"""
        try:
            conn = self.get_connection()
            if conn is None:
                return 0
                
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) FROM users')
            total_users = cursor.fetchone()[0]
            conn.close()
            return total_users
        except Exception as e:
            logging.error(f"❌ Ошибка получения статистики: {e}")
            return 0
    
    def get_all_users(self):
        """Получение списка всех пользователей"""
        try:
            conn = self.get_connection()
            if conn is None:
                return []
                
            cursor = conn.cursor()
            cursor.execute('SELECT user_id FROM users')
            users = cursor.fetchall()
            conn.close()
            return [user[0] for user in users]
        except Exception as e:
            logging.error(f"❌ Ошибка получения списка пользователей: {e}")
            return []

class GoogleSheetsManager:
    def __init__(self):
        self.client = None
        self.sheet = None
        self.is_connected = False
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
            
            # ПРОВЕРКА И ИНИЦИАЛИЗАЦИЯ ТАБЛИЦЫ
            try:
                # Пробуем прочитать данные
                records = self.sheet.get_all_records()
                logging.info(f"✅ Google Sheets подключен. Записей в таблице: {len(records)}")
                
            except IndexError:
                # Если таблица пустая, создаем заголовки
                logging.info("⚠️ Таблица пустая, создаем заголовки...")
                headers = ["Дата", "Имя", "Фамилия", "Телефон", "Username", "User ID", "Купон"]
                self.sheet.append_row(headers)
                logging.info("✅ Заголовки таблицы созданы")
                records = []
                
            # Если таблица пустая (нет записей кроме заголовков)
            if len(records) == 0:
                logging.info("📝 Таблица готова к работе, записей пока нет")
                    
            self.is_connected = True
            return True
            
        except gspread.exceptions.SpreadsheetNotFound:
            logging.error("❌ Таблица не найдена. Проверьте SPREADSHEET_URL")
        except gspread.exceptions.APIError as e:
            logging.error(f"❌ Ошибка доступа к API: {e}")
            logging.error("Добавьте сервисный аккаунт в редакторы таблицы")
        except Exception as e:
            logging.error(f"❌ Ошибка подключения к Google Sheets: {e}")
            import traceback
            logging.error(traceback.format_exc())
        
        return False
    
    def add_lead(self, data):
        """Добавление лида в таблицу"""
        if not self.is_connected:
            logging.error("❌ Google Sheets не подключен, данные не сохранены")
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
            logging.error(f"❌ Ошибка при записи в таблицу: {e}")
            return False

# Инициализация менеджеров
user_manager = UserManager()
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

# ========== РАССЫЛКА СООБЩЕНИЙ ==========

async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало процесса рассылки"""
    user = update.effective_user
    if str(user.id) != ADMIN_CHAT_ID:
        await update.message.reply_text("❌ Эта команда только для администратора")
        return ConversationHandler.END

    keyboard = ReplyKeyboardMarkup([
        ["📢 Текстовая рассылка"],
        ["🖼️ Рассылка с фото"],
        ["❌ Отмена"]
    ], resize_keyboard=True, one_time_keyboard=True)

    await update.message.reply_text(
        "📢 <b>Панель рассылки</b>\n\n"
        "Выберите тип рассылки:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    return BROADCAST_TEXT

async def broadcast_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора типа рассылки"""
    choice = update.message.text
    
    if choice == "❌ Отмена":
        await update.message.reply_text(
            "❌ Рассылка отменена",
            reply_markup=ReplyKeyboardRemove()
        )
        return ConversationHandler.END
    
    elif choice == "📢 Текстовая рассылка":
        await update.message.reply_text(
            "✍️ <b>Текстовая рассылка</b>\n\n"
            "Введите текст для рассылки:",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="HTML"
        )
        context.user_data['broadcast_type'] = 'text'
        return BROADCAST_PHOTO
    
    elif choice == "🖼️ Рассылка с фото":
        await update.message.reply_text(
            "🖼️ <b>Рассылка с фото</b>\n\n"
            "Отправьте фото для рассылки:",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="HTML"
        )
        context.user_data['broadcast_type'] = 'photo'
        return BROADCAST_PHOTO
    
    else:
        await update.message.reply_text("❌ Неверный выбор")
        return BROADCAST_TEXT

async def broadcast_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка фото и текста для рассылки"""
    user = update.effective_user
    if str(user.id) != ADMIN_CHAT_ID:
        return ConversationHandler.END

    broadcast_type = context.user_data.get('broadcast_type', 'text')
    
    try:
        if broadcast_type == 'photo' and update.message.photo:
            # Сохраняем фото
            photo_file = await update.message.photo[-1].get_file()
            context.user_data['broadcast_photo'] = photo_file.file_id
            
            await update.message.reply_text(
                "✅ Фото получено! Теперь введите текст для рассылки:"
            )
            return BROADCAST_PHOTO
        
        elif update.message.text:
            # Сохраняем текст
            context.user_data['broadcast_text'] = update.message.text
            
            # Подтверждение рассылки
            user_count = len(user_manager.get_all_users())
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Начать рассылку", callback_data="confirm_broadcast")],
                [InlineKeyboardButton("❌ Отмена", callback_data="cancel_broadcast")]
            ])
            
            preview_text = (
                f"📢 <b>Предпросмотр рассылки</b>\n\n"
                f"Текст: {update.message.text}\n"
                f"Тип: {'Фото + текст' if broadcast_type == 'photo' else 'Только текст'}\n"
                f"Получателей: {user_count}\n\n"
                f"<i>Подтвердите начало рассылки:</i>"
            )
            
            if broadcast_type == 'photo' and 'broadcast_photo' in context.user_data:
                await update.message.reply_photo(
                    photo=context.user_data['broadcast_photo'],
                    caption=preview_text,
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
            else:
                await update.message.reply_text(
                    preview_text,
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
            
            return ConversationHandler.END
    
    except Exception as e:
        logging.error(f"❌ Ошибка в процессе рассылки: {e}")
        await update.message.reply_text("❌ Произошла ошибка")
        return ConversationHandler.END
    
    await update.message.reply_text("❌ Пожалуйста, отправьте текст")
    return BROADCAST_PHOTO

async def broadcast_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подтверждение и запуск рассылки"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "cancel_broadcast":
        await query.edit_message_caption(
            caption="❌ Рассылка отменена"
        )
        return ConversationHandler.END
    
    # Запуск рассылки
    await query.edit_message_caption(
        caption="🔄 <b>Начинаем рассылку...</b>",
        parse_mode="HTML"
    )
    
    broadcast_type = context.user_data.get('broadcast_type', 'text')
    broadcast_text = context.user_data.get('broadcast_text', '')
    broadcast_photo = context.user_data.get('broadcast_photo', None)
    
    users = user_manager.get_all_users()
    total_users = len(users)
    success_count = 0
    fail_count = 0
    
    # Статистика для админа
    progress_message = await context.bot.send_message(
        chat_id=ADMIN_CHAT_ID,
        text=f"📊 <b>Статистика рассылки</b>\n\n"
             f"▪️ Отправлено: 0/{total_users}\n"
             f"▪️ Успешно: 0\n"
             f"▪️ Ошибок: 0",
        parse_mode="HTML"
    )
    
    for i, user_id in enumerate(users, 1):
        try:
            if broadcast_type == 'photo' and broadcast_photo:
                await context.bot.send_photo(
                    chat_id=user_id,
                    photo=broadcast_photo,
                    caption=broadcast_text,
                    parse_mode="HTML"
                )
            else:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=broadcast_text,
                    parse_mode="HTML"
                )
            success_count += 1
            
            # Обновляем статистику каждые 10 сообщений
            if i % 10 == 0 or i == total_users:
                await context.bot.edit_message_text(
                    chat_id=ADMIN_CHAT_ID,
                    message_id=progress_message.message_id,
                    text=f"📊 <b>Статистика рассылки</b>\n\n"
                         f"▪️ Отправлено: {i}/{total_users}\n"
                         f"▪️ Успешно: {success_count}\n"
                         f"▪️ Ошибок: {fail_count}",
                    parse_mode="HTML"
                )
            
            # Задержка чтобы не превысить лимиты Telegram
            await asyncio.sleep(0.1)
            
        except Exception as e:
            fail_count += 1
            logging.error(f"❌ Ошибка отправки пользователю {user_id}: {e}")
    
    # Финальная статистика
    await context.bot.edit_message_text(
        chat_id=ADMIN_CHAT_ID,
        message_id=progress_message.message_id,
        text=f"✅ <b>Рассылка завершена!</b>\n\n"
             f"▪️ Всего получателей: {total_users}\n"
             f"▪️ Успешно: {success_count}\n"
             f"▪️ Ошибок: {fail_count}\n"
             f"▪️ Процент доставки: {round(success_count/total_users*100, 1) if total_users > 0 else 0}%",
        parse_mode="HTML"
    )
    
    # Очищаем данные рассылки
    context.user_data.clear()
    
    return ConversationHandler.END

async def broadcast_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена рассылки"""
    user = update.effective_user
    if str(user.id) != ADMIN_CHAT_ID:
        return ConversationHandler.END
    
    context.user_data.clear()
    await update.message.reply_text(
        "❌ Рассылка отменена",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

# ========== ОСНОВНЫЕ ФУНКЦИИ БОТА ==========

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user = update.effective_user
    
    # Проверяем, не регистрировался ли пользователь ранее
    if user_manager.is_user_registered(user.id):
        existing_coupon = user_manager.get_user_coupon(user.id)
        await update.message.reply_text(
            f"👋 Снова здравствуйте, {user.first_name}!\n\n"
            f"🎫 <b>Ваш купон:</b> <code>{existing_coupon}</code>\n\n"
            f"Один участник = один купон 🎫\n"
            f"Если вы потеряли купон, вот он 👆",
            parse_mode="HTML"
        )
        return

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Подписаться на канал", url=f"https://t.me/{CHANNEL_USERNAME[1:]}")],
        [InlineKeyboardButton("✅ Я подписался", callback_data="check_subscription")]
    ])
    
    caption = (
        "🛠️ Добро пожаловать в <b>P.I.T Tools</b>!\n\n"
        "🎁 <b>Получите специальный купон на скидку 15%!</b>\n\n"
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
    
    user = query.from_user
    
    # Проверяем, не регистрировался ли пользователь ранее
    if user_manager.is_user_registered(user.id):
        existing_coupon = user_manager.get_user_coupon(user.id)
        await query.edit_message_caption(
            caption=f"❌ Вы уже участвовали в акции!\n\nВаш купон: <b>{existing_coupon}</b>",
            parse_mode="HTML"
        )
        return
    
    try:
        user_channel_status = await context.bot.get_chat_member(
            chat_id=CHANNEL_USERNAME,
            user_id=user.id
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
    
    # Проверяем, не регистрировался ли пользователь ранее
    if user_manager.is_user_registered(user.id):
        existing_coupon = user_manager.get_user_coupon(user.id)
        await update.message.reply_text(
            f"❌ Вы уже участвовали в акции!\n\n"
            f"Ваш купон: <b>{existing_coupon}</b>\n\n"
            f"Один участник = один купон 🎫",
            parse_mode="HTML"
        )
        return
    
    if contact.user_id == user.id:
        # Форматирование номера телефона
        phone_number = contact.phone_number
        if not phone_number.startswith('+'):
            phone_number = f"+{phone_number}"
        
        # Генерация купона
        coupon_code = f"PIT-{user.id % 10000:04d}-15"
        
        # Регистрируем пользователя в локальной базе
        user_data = {
            'user_id': user.id,
            'first_name': contact.first_name,
            'last_name': contact.last_name or '',
            'phone': phone_number,
            'username': user.username or '',
            'coupon': coupon_code
        }
        
        local_registration = user_manager.register_user(user_data)
        
        # Сохранение в Google Sheets
        sheets_success = gsheets_manager.add_lead(user_data)
        
        if local_registration:
            # Сообщение с купоном
            caption = (
                "🎉 <b>Благодарим за участие!</b>\n\n"
                f"🏷️ <b>Ваш купон на скидку:</b> <code>{coupon_code}</code>\n\n"
                "🎁 <b>Что вы получаете:</b>\n"
                "• Скидку 15% на любой инструмент\n"
                "• Подарочный набор расходных материалов\n"
                "• Бесплатную консультацию специалиста\n\n"
                "🏪 <b>Адрес магазина:</b>\n"
                "г. Москва, ул. Инструментальная, д. 15\n\n"
                "📞 <b>Телефон для связи:</b> +7 (495) 123-45-67\n\n"
                "<i>Купон действует в течение 30 дней</i>"
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
                f"💾 В таблицу: {'✅' if sheets_success else '❌'}\n"
                f"📊 Всего участников: {user_manager.get_stats()}"
            )
            
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=admin_message,
                parse_mode="HTML"
            )
        else:
            await update.message.reply_text(
                "❌ Произошла ошибка при регистрации. Пожалуйста, попробуйте позже."
            )
    else:
        await update.message.reply_text("❌ Пожалуйста, поделитесь своим номером телефона.")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Статистика для администратора"""
    if str(update.effective_user.id) != ADMIN_CHAT_ID:
        await update.message.reply_text("❌ Эта команда только для администратора")
        return
    
    total_users = user_manager.get_stats()
    sheets_status = "✅" if gsheets_manager.is_connected else "❌"
    
    await update.message.reply_text(
        f"📊 <b>Статистика бота P.I.T Tools:</b>\n\n"
        f"• Всего участников: <b>{total_users}</b>\n"
        f"• Google Sheets: {sheets_status}\n"
        f"• Бот запущен: ✅\n\n"
        f"<i>Обновлено: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i>",
        parse_mode="HTML"
    )

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
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    if missing_vars:
        logging.error(f"❌ Отсутствуют обязательные переменные: {', '.join(missing_vars)}")
        return
    
    # Создаем приложение
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Обработчик рассылки
    broadcast_conv = ConversationHandler(
        entry_points=[CommandHandler('broadcast', broadcast_start)],
        states={
            BROADCAST_TEXT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, broadcast_text)
            ],
            BROADCAST_PHOTO: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, broadcast_photo),
                MessageHandler(filters.PHOTO, broadcast_photo)
            ],
        },
        fallbacks=[
            CommandHandler('cancel', broadcast_cancel),
            CallbackQueryHandler(broadcast_confirm, pattern='^(confirm_broadcast|cancel_broadcast)$')
        ]
    )
    
    # Обработчики
    application.add_handler(broadcast_conv)
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CallbackQueryHandler(check_subscription, pattern="check_subscription"))
    application.add_handler(CallbackQueryHandler(broadcast_confirm, pattern="^(confirm_broadcast|cancel_broadcast)$"))
    application.add_handler(MessageHandler(filters.CONTACT, handle_contact))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Обработчик ошибок
    application.add_error_handler(error_handler)
    
    # Запуск бота
    logging.info("🚀 Бот запущен...")
    application.run_polling()

if __name__ == "__main__":
    main()
