import asyncio
from aiogram import types, Router, F
from aiogram.filters import Command, CommandObject
from aiogram.types import FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import ReplyKeyboardBuilder
import aiosqlite
import os
import pandas as pd
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from rapidfuzz import process, fuzz # Импортируем process и fuzz из rapidfuzz

router = Router()

DB_PATH = 'app/films.db'
ADMIN_IDS = {307631283}  # Замените на реальные Telegram ID админов
SEARCH_THRESHOLD = 60 # Порог сходства для нечеткого поиска (можно настроить)

# --- Инициализация базы данных ---
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''CREATE TABLE IF NOT EXISTS films (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            file_id TEXT NOT NULL,
            user_id INTEGER NOT NULL
        )''')
        # Таблицы для сериалов
        await db.execute('''CREATE TABLE IF NOT EXISTS series (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL
        )''')
        # Проверяем наличие колонки user_id в таблице series и добавляем, если отсутствует
        cursor = await db.execute("PRAGMA table_info(series)")
        columns = await cursor.fetchall()
        column_names = [col[1] for col in columns]
        if 'user_id' not in column_names:
            await db.execute('ALTER TABLE series ADD COLUMN user_id INTEGER') # Добавляем колонку user_id
            await db.commit()
        await db.execute('''CREATE TABLE IF NOT EXISTS seasons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            series_id INTEGER NOT NULL,
            season_number INTEGER NOT NULL,
            FOREIGN KEY (series_id) REFERENCES series(id)
        )''')
        await db.execute('''CREATE TABLE IF NOT EXISTS episodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            season_id INTEGER NOT NULL,
            episode_number INTEGER NOT NULL,
            file_id TEXT NOT NULL,
            FOREIGN KEY (season_id) REFERENCES seasons(id)
        )''')
        await db.commit()

# --- Состояния FSM ---
class UploadFilm(StatesGroup):
    waiting_for_title = State()
    waiting_for_find_title = State()
    waiting_for_send_fileid = State()
    waiting_for_video = State()
    waiting_for_delete_title = State()
    waiting_for_delete_selection = State()
    waiting_for_find_selection = State()
    # Состояния для добавления сериала
    waiting_for_series_title = State()
    waiting_for_series_title_action = State() # Ожидание выбора действия при существующем названии
    waiting_for_season_to_add_episode = State() # Ожидание выбора сезона для добавления эпизода
    waiting_for_existing_episode_details = State() # Ожидание номера эпизода и file_id для существующего сериала
    waiting_for_number_of_seasons = State()
    waiting_for_number_of_episodes = State() # Для текущего сезона
    waiting_for_episode_file_id = State() # Для текущего эпизода
    waiting_for_add_series_confirm = State() # Возможно, для подтверждения
    # Состояние для поиска сериала
    waiting_for_find_series_title = State()
    waiting_for_series_action = State() # Выбор действия после нахождения сериала
    waiting_for_season_selection = State() # Ожидание выбора сезона
    # Состояние для выбора эпизода
    waiting_for_episode_selection = State()

# --- FSM обработчики для текстового ввода (перемещены выше) ---
@router.message(UploadFilm.waiting_for_title, F.text)
async def handle_title(message: types.Message, state: FSMContext):
    print(f"[ТЕСТ] Получен текстовый ввод названия фильма в FSM: {message.text}")
    data = await state.get_data()
    title = message.text.strip()
    file_id = data.get('file_id')
    user_id = data.get('user_id')
    if not file_id or not title:
        await message.answer('Ошибка. Попробуйте загрузить видео заново.')
        await state.clear()
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('CREATE TABLE IF NOT EXISTS films (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, file_id TEXT NOT NULL, user_id INTEGER NOT NULL)')
        await db.execute('INSERT INTO films (title, file_id, user_id) VALUES (?, ?, ?)', (title, file_id, user_id))
        await db.commit()
    await message.answer(f'Фильм "{title}" успешно добавлен!')
    await state.clear()
    is_admin = int(message.from_user.id) in ADMIN_IDS
    await message.answer('Выберите действие:', reply_markup=get_main_menu(is_admin))

@router.message(UploadFilm.waiting_for_find_title, F.text)
async def process_find_title(message: types.Message, state: FSMContext):
    print(f"[ТЕСТ] Получен текстовый ввод для поиска фильма в FSM: {message.text}")
    title_to_find = message.text.strip()

    async with aiosqlite.connect(DB_PATH) as db:
        # Поиск по точному совпадению (нечувствительный к регистру)
        async with db.execute('SELECT file_id, title FROM films WHERE title = ? COLLATE NOCASE', (title_to_find,)) as cursor:
            exact_match = await cursor.fetchone()

    if exact_match:
        file_id_to_send = exact_match[0]
        found_title = exact_match[1]
        print(f'[ТЕСТ] Фильм "{found_title}" найден, пытаюсь отправить video с file_id: {file_id_to_send}') # Отладочное сообщение
        try:
            await message.answer_video(file_id_to_send)
            await message.answer(f'Отправляю фильм "{found_title}".')
        except Exception as e:
            await message.answer(f'[ТЕСТ] Ошибка при отправке видео: {e}')
        await state.clear()
        is_admin = int(message.from_user.id) in ADMIN_IDS
        await message.answer('Выберите действие:', reply_markup=get_main_menu(is_admin))
    else:
        # Если точное совпадение не найдено, ищем похожие названия с rapidfuzz
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute('SELECT title, file_id FROM films') as cursor:
                all_films = await cursor.fetchall()

        # Используем rapidfuzz для нечеткого поиска
        # results формат: [(найденное_название, score, index_в_all_films), ...]
        similar_results_with_scores = process.extract(title_to_find, [film[0] for film in all_films], scorer=fuzz.ratio, limit=10) # Используем fuzz.ratio и ограничиваем 10 результатами

        # Фильтруем результаты по порогу сходства и получаем уникальные названия
        similar_results = []
        seen_titles = set()
        for result, score, index in similar_results_with_scores:
            if score >= SEARCH_THRESHOLD and result not in seen_titles:
                similar_results.append(all_films[index]) # Сохраняем пару (title, file_id)
                seen_titles.add(result)

        print(f"[ТЕСТ] Результаты нечеткого поиска rapidfuzz для '{title_to_find}' (порог {SEARCH_THRESHOLD}): {similar_results}") # Отладочный вывод

        if similar_results:
            # Сохраняем найденные результаты для выбора в следующем состоянии
            await state.update_data(similar_find_results=similar_results)

            text = 'Фильм с таким названием не найден. Возможно, вы имели в виду:\n'
            for i, (title, file_id) in enumerate(similar_results):
                text += f'{i+1}. {title}\n'
            text += '\nВведите номер(а) фильма(ов) через запятую для отправки или любое другое сообщение для отмены.'
            await message.answer(text)
            await state.set_state(UploadFilm.waiting_for_find_selection)
        else:
            await message.answer(f'Фильмы с названием "{title_to_find}" не найдены.')
            await state.clear()
            is_admin = int(message.from_user.id) in ADMIN_IDS
            await message.answer('Выберите действие:', reply_markup=get_main_menu(is_admin))

@router.message(UploadFilm.waiting_for_send_fileid, F.text)
async def process_send_fileid(message: types.Message, state: FSMContext):
    print(f"[ТЕСТ] Получен текстовый ввод для отправки по file_id в FSM: {message.text}")
    uid = int(message.from_user.id)
    if uid not in ADMIN_IDS:
        await message.answer('Нет доступа.')
        await state.clear()
        is_admin = int(message.from_user.id) in ADMIN_IDS
        await message.answer('Выберите действие:', reply_markup=get_main_menu(is_admin))
        return

    file_id = message.text.strip()
    print(f'[ТЕСТ] Пытаюсь отправить video с file_id: {file_id}')
    try:
        await message.answer_video(file_id)
    except Exception as e:
        await message.answer(f'[ТЕСТ] Ошибка при отправке видео: {e}')
    await state.clear()
    is_admin = int(message.from_user.id) in ADMIN_IDS
    await message.answer('Выберите действие:', reply_markup=get_main_menu(is_admin))

# --- Видео и FSM для добавления (оставлен тут) ---
# Общий обработчик видео удален, видео обрабатывается только в состоянии waiting_for_video
# @router.message(F.video)
# async def handle_video_general(message: types.Message, state: FSMContext):
#     file_id = message.video.file_id
#     await message.answer(f'[ТЕСТ] file_id этого видео: {file_id}')
#     await message.answer('Пожалуйста, введите название фильма для этого видео:')
#     await state.set_state(UploadFilm.waiting_for_title)
#     await state.update_data(file_id=file_id, user_id=message.from_user.id)

# --- Поиск и отправка видео по названию ---
@router.message(F.text.startswith('/find'))
async def find_film(message: types.Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer('Укажите название фильма: /find <название>')
        return
    title = parts[1].strip()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT file_id FROM films WHERE title = ?', (title,)) as cursor:
            row = await cursor.fetchone()
    if row:
        await message.answer(f'[ТЕСТ] Фильм найден, file_id: {row[0]}')
        await message.answer_video(row[0])
    else:
        await message.answer(f'[ТЕСТ] Фильм с названием "{title}" не найден.')

# --- Отправка видео по file_id ---
@router.message(F.text.startswith('/send'))
async def send_video_by_file_id(message: types.Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer('Укажите file_id: /send <file_id>')
        return
    file_id = parts[1].strip()
    print(f'[ТЕСТ] Отправляю видео по file_id: {file_id}')
    await message.answer_video(file_id)

# --- Экспорт в Excel (только для админов) ---
@router.message(F.text.startswith('/export'))
async def export_db(message: types.Message):
    uid = int(message.from_user.id)
    await message.answer(f'[ТЕСТ] Ваш user_id: {uid}')
    if uid not in ADMIN_IDS:
        await message.answer('Нет доступа. Ваш user_id не в списке админов.')
        return
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT id, title, file_id, user_id FROM films') as cursor:
            rows = await cursor.fetchall()
    df = pd.DataFrame(rows, columns=['id', 'title', 'file_id', 'user_id'])
    excel_path = 'app/films_export.xlsx'
    df.to_excel(excel_path, index=False)
    await message.answer_document(types.FSInputFile(excel_path))
    os.remove(excel_path)

# --- Список фильмов (только для админов) ---
@router.message(F.text.startswith('/list'))
async def list_films(message: types.Message):
    uid = int(message.from_user.id)
    await message.answer(f'[ТЕСТ] Ваш user_id: {uid}')
    if uid not in ADMIN_IDS:
        await message.answer('Нет доступа. Ваш user_id не в списке админов.')
        return
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT id, title FROM films') as cursor:
            rows = await cursor.fetchall()
    if not rows:
        await message.answer('База пуста.')
        return
    text = '\n'.join([f'{r[0]}. {r[1]}' for r in rows])
    await message.answer(f'Список фильмов:\n{text}')

# --- Проверка содержимого базы (отладка) ---
@router.message(F.text.startswith('/check'))
async def check_db(message: types.Message):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT id, title, file_id, user_id FROM films') as cursor:
            rows = await cursor.fetchall()
    if not rows:
        await message.answer('[ТЕСТ] База пуста.')
    else:
        text = '\n'.join([f'id: {r[0]}, title: {r[1]}, file_id: {r[2]}, user_id: {r[3]}' for r in rows])
        await message.answer(f'[ТЕСТ] Содержимое базы:\n{text}')

# --- Инициализация при старте ---
async def on_startup():
    await init_db()

def get_main_menu(is_admin=False):
    print(f"[ТЕСТ] Построение меню. Пользователь админ: {is_admin}")
    all_buttons = [
        InlineKeyboardButton(text='Найти фильм', callback_data='find_film'),
        InlineKeyboardButton(text='Список фильмов', callback_data='list_films'),
        InlineKeyboardButton(text='Список сериалов', callback_data='list_series'),
        InlineKeyboardButton(text='Найти сериал', callback_data='find_series'),
    ]
    if is_admin:
        admin_buttons = [
            InlineKeyboardButton(text='Добавить сериал', callback_data='add_series'),
            InlineKeyboardButton(text='Добавить видео', callback_data='add_video'),
            InlineKeyboardButton(text='Отправить по file_id', callback_data='send_fileid'),
            InlineKeyboardButton(text='Экспорт в Excel', callback_data='export_db'),
            InlineKeyboardButton(text='Удалить фильм по названию', callback_data='delete_film_by_title'),
            InlineKeyboardButton(text='Проверить базу', callback_data='check_db'),
        ]
        all_buttons = admin_buttons + all_buttons

    # Группируем кнопки по две
    grouped_buttons = []
    for i in range(0, len(all_buttons), 2):
        grouped_buttons.append(all_buttons[i:i+2])

    return InlineKeyboardMarkup(inline_keyboard=grouped_buttons)

@router.message(F.text == '/start')
async def start_menu(message: types.Message):
    uid = int(message.from_user.id)
    is_admin = uid in ADMIN_IDS
    print(f"[ТЕСТ] Команда /start. Пользователь {uid} является админом: {is_admin}")
    await message.answer('Выберите действие:', reply_markup=get_main_menu(is_admin))

# --- Inline кнопки и их обработка ---
@router.callback_query(F.data == 'add_video')
async def cb_add_video(callback: types.CallbackQuery, state: FSMContext):
    uid = int(callback.from_user.id)
    is_admin = uid in ADMIN_IDS
    print(f"[ТЕСТ] Кнопка 'Добавить видео'. Пользователь {uid} является админом: {is_admin}")
    if not is_admin:
        await callback.message.answer('Нет доступа.')
        return
    await callback.message.answer('Пожалуйста, отправьте видеофайл.')
    await state.set_state(UploadFilm.waiting_for_video)

# Обработчик для получения видео после нажатия кнопки 'Добавить видео' (уже ограничен состоянием и фактически админами)
@router.message(UploadFilm.waiting_for_video, F.video)
async def handle_video_after_button(message: types.Message, state: FSMContext):
    file_id = message.video.file_id
    await message.answer(f'[ТЕСТ] file_id этого видео: {file_id}')
    await message.answer('Пожалуйста, введите название фильма для этого видео:')
    await state.set_state(UploadFilm.waiting_for_title)
    await state.update_data(file_id=file_id, user_id=message.from_user.id)

@router.callback_query(F.data == 'find_film')
async def cb_find_film(callback: types.CallbackQuery, state: FSMContext):
    print("[ТЕСТ] Нажата кнопка 'Найти фильм'")
    # Доступно всем пользователям, нет проверки на админа
    await callback.message.answer('Введите название фильма для поиска:')
    await state.set_state(UploadFilm.waiting_for_find_title)

@router.callback_query(F.data == 'send_fileid')
async def cb_send_fileid(callback: types.CallbackQuery, state: FSMContext):
    uid = int(callback.from_user.id)
    is_admin = uid in ADMIN_IDS
    print(f"[ТЕСТ] Кнопка 'Отправить по file_id'. Пользователь {uid} является админом: {is_admin}")
    if not is_admin:
        await callback.message.answer('Нет доступа.')
        return
    await callback.message.answer('Введите file_id для отправки видео:')
    await state.set_state(UploadFilm.waiting_for_send_fileid)

@router.callback_query(F.data == 'list_films')
async def cb_list_films(callback: types.CallbackQuery):
    print("[ТЕСТ] Нажата кнопка 'Список фильмов'")
    uid = int(callback.from_user.id)
    is_admin = uid in ADMIN_IDS
    print(f"[ТЕСТ] Кнопка 'Список фильмов'. Пользователь {uid} является админом: {is_admin}")
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT id, title FROM films') as cursor:
            rows = await cursor.fetchall()
    if not rows:
        await callback.message.answer('База пуста.')
    else:
        text = '\n'.join([f'{r[0]}. {r[1]}' for r in rows])
        await callback.message.answer(f'Список фильмов:\n{text}')
    await callback.message.answer('Выберите действие:', reply_markup=get_main_menu(is_admin))

@router.callback_query(F.data == 'list_series')
async def cb_list_series(callback: types.CallbackQuery):
    print("[ТЕСТ] Нажата кнопка 'Список сериалов'")
    uid = int(callback.from_user.id)
    is_admin = uid in ADMIN_IDS
    print(f"[ТЕСТ] Кнопка 'Список сериалов'. Пользователь {uid} является админом: {is_admin}")
    # Проверка на админа здесь не нужна, так как кнопка доступна всем
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT id, title FROM series') as cursor:
            rows = await cursor.fetchall()
    if not rows:
        await callback.message.answer('База сериалов пуста.')
    else:
        text = '\n'.join([f'{r[0]}. {r[1]}' for r in rows])
        await callback.message.answer(f'Список сериалов:\n{text}')
    await callback.message.answer('Выберите действие:', reply_markup=get_main_menu(is_admin))

@router.callback_query(F.data == 'find_series')
async def cb_find_series(callback: types.CallbackQuery, state: FSMContext):
    print("[ТЕСТ] Нажата кнопка 'Найти сериал'")
    # Доступно всем пользователям, нет проверки на админа
    await callback.message.answer('Введите название сериала для поиска:')
    await state.set_state(UploadFilm.waiting_for_find_series_title)

@router.message(UploadFilm.waiting_for_find_series_title, F.text)
async def process_find_series_title(message: types.Message, state: FSMContext):
    print(f"[ТЕСТ] Получен текстовый ввод для поиска сериала в FSM: {message.text}")
    series_title_to_find = message.text.strip()

    async with aiosqlite.connect(DB_PATH) as db:
        # Поиск по точному совпадению (нечувствительный к регистру)
        async with db.execute('SELECT id, title FROM series WHERE title = ? COLLATE NOCASE', (series_title_to_find,)) as cursor:
            exact_match = await cursor.fetchone()

    if exact_match:
        series_id = exact_match[0]
        found_title = exact_match[1]
        print(f'[ТЕСТ] Сериал "{found_title}" найден, id: {series_id}') # Отладочное сообщение
        # Сохраняем найденный сериал и предлагаем действия
        await state.update_data(found_series_id=series_id, found_series_title=found_title)
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='Начать просмотр', callback_data=f'view_series:{series_id}')],
            [InlineKeyboardButton(text='Подписаться на новые серии', callback_data=f'subscribe_series:{series_id}')],
            [InlineKeyboardButton(text='<< Назад к списку сериалов', callback_data='back_to_series_list')]
        ])
        await message.answer(f'Сериал "{found_title}" найден. Выберите действие:', reply_markup=keyboard)
        await state.set_state(UploadFilm.waiting_for_series_action)
    else:
        # TODO: Добавить нечеткий поиск по rapidfuzz, как для фильмов
        await message.answer(f'Сериал с названием "{series_title_to_find}" не найден.')
        await state.clear()
        is_admin = int(message.from_user.id) in ADMIN_IDS
        await message.answer('Выберите действие:', reply_markup=get_main_menu(is_admin))

@router.callback_query(F.data == 'export_db')
async def cb_export_db(callback: types.CallbackQuery):
    uid = int(callback.from_user.id)
    is_admin = uid in ADMIN_IDS
    print(f"[ТЕСТ] Кнопка 'Экспорт в Excel'. Пользователь {uid} является админом: {is_admin}")
    if not is_admin:
        await callback.message.answer('Нет доступа.')
        return
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT id, title, file_id, user_id FROM films') as cursor:
            rows = await cursor.fetchall()
    df = pd.DataFrame(rows, columns=['id', 'title', 'file_id', 'user_id'])
    excel_path = 'app/films_export.xlsx'
    df.to_excel(excel_path, index=False)
    await callback.message.answer_document(types.FSInputFile(excel_path))
    os.remove(excel_path)
    await callback.message.answer('Выберите действие:', reply_markup=get_main_menu(is_admin))

@router.callback_query(F.data == 'check_db')
async def cb_check_db(callback: types.CallbackQuery):
    print("[ТЕСТ] Нажата кнопка 'Проверить базу'")
    is_admin = int(callback.from_user.id) in ADMIN_IDS
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT id, title, file_id, user_id FROM films') as cursor:
            rows = await cursor.fetchall()
    if not rows:
        await callback.message.answer('[ТЕСТ] База пуста.')
    else:
        text = '\n'.join([f'id: {r[0]}, title: {r[1]}, file_id: {r[2]}, user_id: {r[3]}' for r in rows])
        await callback.message.answer(f'[ТЕСТ] Содержимое базы:\n{text}')
    await callback.message.answer('Выберите действие:', reply_markup=get_main_menu(is_admin))

@router.callback_query(F.data == 'delete_film_by_title')
async def cb_delete_film_by_title(callback: types.CallbackQuery, state: FSMContext):
    uid = int(callback.from_user.id)
    is_admin = uid in ADMIN_IDS
    print(f"[ТЕСТ] Кнопка 'Удалить фильм по названию'. Пользователь {uid} является админом: {is_admin}")
    if not is_admin:
        await callback.message.answer('Нет доступа.')
        return
    await callback.message.answer('Введите название фильма для удаления:')
    await state.set_state(UploadFilm.waiting_for_delete_title)

@router.message(UploadFilm.waiting_for_delete_title, F.text)
async def process_delete_title(message: types.Message, state: FSMContext):
    uid = int(message.from_user.id)
    is_admin = uid in ADMIN_IDS
    print(f"[ТЕСТ] Удаление фильма. Пользователь {uid} является админом: {is_admin}")
    if not is_admin:
        await message.answer('Нет доступа.')
        await state.clear()
        await message.answer('Выберите действие:', reply_markup=get_main_menu(is_admin))
        return

    title_to_delete = message.text.strip()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute('DELETE FROM films WHERE title = ?', (title_to_delete,))
        await db.commit()
        deleted_count = cursor.rowcount

    if deleted_count > 0:
        await message.answer(f'Удалено {deleted_count} фильм(а/ов) с названием "{title_to_delete}".')
        await state.clear()
        is_admin = int(message.from_user.id) in ADMIN_IDS
        await message.answer('Выберите действие:', reply_markup=get_main_menu(is_admin))
    else:
        # Если точное совпадение не найдено, ищем похожие названия
        search_term = f'%{title_to_delete}%'
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute('SELECT DISTINCT title FROM films WHERE title LIKE ?', (search_term,)) as cursor:
                similar_titles = [row[0] for row in await cursor.fetchall()]

        if similar_titles:
            text = 'Фильм с таким названием не найден. Возможно, вы имели в виду:\n'
            for i, title in enumerate(similar_titles):
                text += f'{i+1}. {title}\n'
            text += '\nВведите номера фильмов для удаления через запятую (например, 1,3) или отправьте любое другое сообщение для отмены.'
            await message.answer(text)
            await state.set_state(UploadFilm.waiting_for_delete_selection)
            await state.update_data(similar_titles=similar_titles)
        else:
            await message.answer(f'Фильмы с названием "{title_to_delete}" не найдены.')
            await state.clear()
            is_admin = int(message.from_user.id) in ADMIN_IDS
            await message.answer('Выберите действие:', reply_markup=get_main_menu(is_admin))

@router.message(UploadFilm.waiting_for_delete_selection, F.text)
async def process_delete_selection(message: types.Message, state: FSMContext):
    uid = int(message.from_user.id)
    is_admin = uid in ADMIN_IDS
    print(f"[ТЕСТ] Выбор фильмов для удаления. Пользователь {uid} является админом: {is_admin}")
    if not is_admin:
        await message.answer('Нет доступа.')
        await state.clear()
        await message.answer('Выберите действие:', reply_markup=get_main_menu(is_admin))
        return

    user_input = message.text.strip()
    # Получаем данные состояния корректно с await
    state_data = await state.get_data()
    similar_titles = state_data.get('similar_titles', [])

    if not similar_titles:
        await message.answer('Произошла ошибка. Попробуйте начать удаление заново.')
        await state.clear()
        is_admin = int(message.from_user.id) in ADMIN_IDS
        await message.answer('Выберите действие:', reply_markup=get_main_menu(is_admin))
        return

    # Обрабатываем ввод нескольких номеров через запятую
    selection_indices = []
    invalid_inputs = []

    for item in user_input.split(','):
        item = item.strip()
        if item.isdigit():
            index = int(item) - 1
            if 0 <= index < len(similar_titles):
                selection_indices.append(index)
            else:
                invalid_inputs.append(item)
        elif item:
             invalid_inputs.append(item)

    deleted_titles = []

    if selection_indices:
        async with aiosqlite.connect(DB_PATH) as db:
            for index in sorted(list(set(selection_indices))): # Удаляем дубликаты индексов и сортируем
                title_to_delete = similar_titles[index]
                cursor = await db.execute('DELETE FROM films WHERE title = ?', (title_to_delete,))
                await db.commit()
                if cursor.rowcount > 0:
                     deleted_titles.append(title_to_delete)

    response_text = ''
    if deleted_titles:
        response_text += 'Удалены фильмы:\n' + '\n'.join([f'- {title}' for title in deleted_titles])
    
    if invalid_inputs:
        if response_text:
            response_text += '\n\n'
        response_text += 'Некорректные номера или ввод:\n' + ', '.join(invalid_inputs)

    if not deleted_titles and not invalid_inputs:
         response_text = 'Не удалось удалить фильмы. Введены некорректные данные.'

    await message.answer(response_text)

    await state.clear()
    is_admin = int(message.from_user.id) in ADMIN_IDS
    await message.answer('Выберите действие:', reply_markup=get_main_menu(is_admin))

@router.message(UploadFilm.waiting_for_find_selection, F.text)
async def process_find_selection(message: types.Message, state: FSMContext):
    print(f"[ТЕСТ] Получен текстовый ввод для выбора фильма в FSM: {message.text}")
    user_input = message.text.strip()

    # Получаем данные состояния
    state_data = await state.get_data()
    similar_find_results = state_data.get('similar_find_results', [])

    if not similar_find_results:
        await message.answer('Произошла ошибка. Попробуйте начать поиск заново.')
        await state.clear()
        is_admin = int(message.from_user.id) in ADMIN_IDS
        await message.answer('Выберите действие:', reply_markup=get_main_menu(is_admin))
        return

    # Обрабатываем ввод нескольких номеров через запятую
    selection_indices = []
    invalid_inputs = []

    for item in user_input.split(','):
        item = item.strip()
        if item.isdigit():
            index = int(item) - 1
            if 0 <= index < len(similar_find_results):
                selection_indices.append(index)
            else:
                invalid_inputs.append(item)
        elif item:
             invalid_inputs.append(item)

    sent_titles = []

    if selection_indices:
        for index in sorted(list(set(selection_indices))): # Удаляем дубликаты индексов и сортируем
            title_to_send = similar_find_results[index][0]
            file_id_to_send = similar_find_results[index][1]
            try:
                await message.answer_video(file_id_to_send)
                await message.answer(f'Отправляю фильм "{title_to_send}".')
                sent_titles.append(title_to_send)
            except Exception as e:
                await message.answer(f'[ТЕСТ] Ошибка при отправке видео "{title_to_send}": {e}')

    response_text = ''
    if sent_titles:
        response_text += 'Отправлены фильмы:\n' + '\n'.join([f'- {title}' for title in sent_titles])

    if invalid_inputs:
        if response_text:
            response_text += '\n\n'
        response_text += 'Некорректные номера или ввод:\n' + ', '.join(invalid_inputs)

    if not sent_titles and not invalid_inputs:
         response_text = 'Не удалось найти или отправить фильмы. Введены некорректные данные или фильмы не найдены по номерам.'

    await message.answer(response_text)

    await state.clear()
    is_admin = int(message.from_user.id) in ADMIN_IDS
    await message.answer('Выберите действие:', reply_markup=get_main_menu(is_admin))

@router.callback_query(F.data == 'add_series')
async def cb_add_series(callback: types.CallbackQuery, state: FSMContext):
    print("[ТЕСТ] Нажата кнопка 'Добавить сериал'")
    uid = int(callback.from_user.id)
    if uid not in ADMIN_IDS:
        await callback.message.answer('Нет доступа.')
        return
    await callback.message.answer('Введите название сериала:')
    await state.set_state(UploadFilm.waiting_for_series_title)

@router.message(UploadFilm.waiting_for_series_title, F.text)
async def handle_series_title(message: types.Message, state: FSMContext):
    print(f"[ТЕСТ] Получен текстовый ввод названия сериала в FSM: {message.text}")
    series_title = message.text.strip()
    if not series_title:
        await message.answer('Название сериала не может быть пустым. Попробуйте снова.')
        return # Остаемся в текущем состоянии

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT id FROM series WHERE title = ? COLLATE NOCASE', (series_title,)) as cursor:
            existing_series = await cursor.fetchone()

    if existing_series:
        series_id = existing_series[0]
        await state.update_data(existing_series_id=series_id, series_title=series_title)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='Добавить серии к этому сериалу', callback_data='add_to_existing_series')],
            [InlineKeyboardButton(text='Ввести другое название', callback_data='enter_new_series_title')]
        ])
        await message.answer(f'Сериал с названием "{series_title}" уже существует. Что вы хотите сделать?', reply_markup=keyboard)
        await state.set_state(UploadFilm.waiting_for_series_title_action)
    else:
        await state.update_data(series_title=series_title)
        await message.answer(f'Название сериала: "{series_title}".\nТеперь введите общее количество сезонов:')
        await state.set_state(UploadFilm.waiting_for_number_of_seasons)

@router.message(UploadFilm.waiting_for_number_of_seasons, F.text)
async def handle_number_of_seasons(message: types.Message, state: FSMContext):
    print(f"[ТЕСТ] Получен текстовый ввод количества сезонов в FSM: {message.text}")
    try:
        total_seasons = int(message.text.strip())
        if total_seasons <= 0:
            raise ValueError("Количество сезонов должно быть больше 0")
    except ValueError:
        await message.answer('Пожалуйста, введите корректное число сезонов (целое число больше 0):')
        return # Остаемся в текущем состоянии

    await state.update_data(total_seasons=total_seasons, current_season=1, seasons_data={})
    data = await state.get_data()
    series_title = data.get('series_title')
    await message.answer(f'Для сериала "{series_title}", сезон 1: Введите количество эпизодов в этом сезоне:')
    await state.set_state(UploadFilm.waiting_for_number_of_episodes)

@router.message(UploadFilm.waiting_for_number_of_episodes, F.text)
async def handle_number_of_episodes(message: types.Message, state: FSMContext):
    print(f"[ТЕСТ] Получен текстовый ввод количества эпизодов в FSM: {message.text}")
    data = await state.get_data()
    current_season = data.get('current_season')
    series_title = data.get('series_title')

    try:
        total_episodes_in_season = int(message.text.strip())
        if total_episodes_in_season <= 0:
            raise ValueError("Количество эпизодов должно быть больше 0")
    except ValueError:
        await message.answer(f'Для сезона {current_season} сериала "{series_title}": Пожалуйста, введите корректное число эпизодов (целое число больше 0):')
        return # Остаемся в текущем состоянии

    # Инициализируем данные для текущего сезона
    seasons_data = data.get('seasons_data', {})
    seasons_data[current_season] = {'total_episodes': total_episodes_in_season, 'episodes': {}, 'current_episode': 1}
    await state.update_data(seasons_data=seasons_data)

    await message.answer(f'Для сериала "{series_title}", сезон {current_season}, эпизод 1: Отправьте file_id этого эпизода (или видео файл):')
    await state.set_state(UploadFilm.waiting_for_episode_file_id)

@router.message(UploadFilm.waiting_for_episode_file_id, F.text | F.video)
async def handle_episode_file_id(message: types.Message, state: FSMContext):
    print(f"[ТЕСТ] Получен ввод file_id/видео эпизода в FSM: {message.text or message.video.file_id}")
    data = await state.get_data()
    series_title = data.get('series_title')
    total_seasons = data.get('total_seasons')
    current_season = data.get('current_season')
    seasons_data = data.get('seasons_data', {})

    # Получаем file_id из текстового сообщения или из видео файла
    episode_file_id = message.text.strip() if message.text else message.video.file_id

    if not episode_file_id:
        await message.answer('Не удалось получить file_id эпизода. Попробуйте отправить еще раз.')
        return # Остаемся в текущем состоянии

    current_episode = seasons_data.get(current_season, {}).get('current_episode', 1)
    total_episodes_in_season = seasons_data.get(current_season, {}).get('total_episodes', 0)

    # Сохраняем file_id текущего эпизода во временной структуре
    seasons_data[current_season]['episodes'][current_episode] = episode_file_id
    await state.update_data(seasons_data=seasons_data)

    # Переходим к следующему эпизоду или сезону
    if current_episode < total_episodes_in_season:
        # Переход к следующему эпизоду в текущем сезоне
        seasons_data[current_season]['current_episode'] += 1
        await state.update_data(seasons_data=seasons_data)
        await message.answer(f'Для сериала "{series_title}", сезон {current_season}, эпизод {current_episode + 1}: Отправьте file_id этого эпизода (или видео файл):')
        await state.set_state(UploadFilm.waiting_for_episode_file_id)
    elif current_season < total_seasons:
        # Переход к следующему сезону
        next_season = current_season + 1
        await state.update_data(current_season=next_season)
        await message.answer(f'Для сериала "{series_title}", сезон {next_season}: Введите количество эпизодов в этом сезоне:')
        await state.set_state(UploadFilm.waiting_for_number_of_episodes)
    else:
        # Все сезоны и эпизоды введены
        print(f'[ТЕСТ] Все данные для сериала "{series_title}" собраны. Данные: {seasons_data}') # Выводим собранные данные для отладки
        # Сохранение в базу данных
        user_id = message.from_user.id
        async with aiosqlite.connect(DB_PATH) as db:
            # Вставляем запись о сериале
            cursor = await db.execute('INSERT INTO series (title, user_id) VALUES (?, ?)', (series_title, user_id))
            series_id = cursor.lastrowid
            await db.commit()

            # Вставляем записи о сезонах и эпизодах
            for season_number, season_data_item in seasons_data.items(): # Изменено название переменной
                cursor = await db.execute('INSERT INTO seasons (series_id, season_number) VALUES (?, ?)', (series_id, season_number))
                season_id = cursor.lastrowid
                await db.commit()

                for episode_number, file_id in season_data_item['episodes'].items(): # Изменено название переменной
                    await db.execute('INSERT INTO episodes (season_id, episode_number, file_id, user_id) VALUES (?, ?, ?, ?)', (season_id, episode_number, file_id, user_id))
                await db.commit()

        await message.answer(f'Сериал "{series_title}" и его эпизоды успешно добавлены в базу данных!')
        await state.clear()
        is_admin = int(message.from_user.id) in ADMIN_IDS
        await message.answer('Выберите действие:', reply_markup=get_main_menu(is_admin))

@router.message(UploadFilm.waiting_for_add_series_confirm, F.text)
async def handle_add_series_confirm(message: types.Message, state: FSMContext):
    print(f"[ТЕСТ] Получен текстовый ввод подтверждения добавления сериала в FSM: {message.text}")
    data = await state.get_data()
    confirm = message.text.strip()
    if confirm.lower() != 'да':
        await message.answer('Добавление сериала отменено.')
        await state.clear()
        return
    await message.answer('Сериал успешно добавлен!')
    await state.clear()
    is_admin = int(message.from_user.id) in ADMIN_IDS
    await message.answer('Выберите действие:', reply_markup=get_main_menu(is_admin)) 

@router.callback_query(F.data.startswith('view_series:'))
async def cb_view_series(callback: types.CallbackQuery, state: FSMContext):
    print("[ТЕСТ] Нажата кнопка 'Начать просмотр'")
    await callback.message.delete() # Удаляем предыдущее сообщение с кнопками действий сериала
    series_id = int(callback.data.split(':')[1])

    async with aiosqlite.connect(DB_PATH) as db:
        # Получаем название сериала (для отображения пользователю)
        async with db.execute('SELECT title FROM series WHERE id = ?', (series_id,)) as cursor:
            series_row = await cursor.fetchone()
            series_title = series_row[0] if series_row else 'Неизвестный сериал'

        # Получаем все сезоны для данного сериала
        async with db.execute('SELECT id, season_number FROM seasons WHERE series_id = ? ORDER BY season_number', (series_id,)) as cursor:
            seasons = await cursor.fetchall()

    if not seasons:
        await callback.message.answer(f'Для сериала "{series_title}" сезоны не найдены.')
        await state.clear()
        is_admin = int(callback.from_user.id) in ADMIN_IDS
        await callback.message.answer('Выберите действие:', reply_markup=get_main_menu(is_admin))
        return

    # Формируем кнопки для выбора сезона
    keyboard_buttons = []
    for season_id, season_number in seasons:
        keyboard_buttons.append([InlineKeyboardButton(text=f'{season_number}. Сезон', callback_data=f'select_season:{season_id}')])

    # Добавляем кнопку назад
    keyboard_buttons.append([InlineKeyboardButton(text='<< Назад', callback_data='back_to_series_actions')])

    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    await callback.message.answer(f'Вы выбрали:\nСериал: {series_title}\nТеперь выберите сезон из списка ниже:', reply_markup=keyboard)

    # Сохраняем ID сериала в состоянии для дальнейшего использования
    await state.update_data(current_series_id=series_id)

    await state.set_state(UploadFilm.waiting_for_season_selection)

@router.callback_query(F.data.startswith('subscribe_series:'))
async def cb_subscribe_series(callback: types.CallbackQuery, state: FSMContext):
    print("[ТЕСТ] Нажата кнопка 'Подписаться на новые серии'")
    await callback.message.delete() # Удаляем предыдущее сообщение с кнопками действий сериала
    series_id = int(callback.data.split(':')[1])
    # TODO: Реализовать логику подписки
    await callback.message.answer(f'[ТЕСТ] Логика подписки на сериал ID {series_id} пока не реализована.')
    await state.clear()
    is_admin = int(callback.from_user.id) in ADMIN_IDS
    await callback.message.answer('Выберите действие:', reply_markup=get_main_menu(is_admin))

@router.callback_query(F.data == 'back_to_series_actions')
async def cb_back_to_series_actions(callback: types.CallbackQuery, state: FSMContext):
    print("[ТЕСТ] Нажата кнопка '<< Назад к действиям с сериалом'")
    await callback.message.delete() # Удаляем текущее сообщение с кнопками сезонов

    # Получаем ID сериала из состояния, чтобы отобразить его название при возврате
    data = await state.get_data()
    series_id = data.get('current_series_id')
    print(f"[ТЕСТ] cb_back_to_series_actions: state_data={data}, series_id={series_id}") # Отладочное сообщение

    async with aiosqlite.connect(DB_PATH) as db:
        # Получаем название сериала (для отображения пользователю)
        async with db.execute('SELECT title FROM series WHERE id = ?', (series_id,)) as cursor:
            series_row = await cursor.fetchone()
            series_title = series_row[0] if series_row else 'Неизвестный сериал'

    # Восстанавливаем предыдущее меню действий с сериалом
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='Начать просмотр', callback_data=f'view_series:{series_id}')],
        [InlineKeyboardButton(text='Подписаться на новые серии', callback_data=f'subscribe_series:{series_id}')],
        [InlineKeyboardButton(text='<< Назад к списку сериалов', callback_data='back_to_series_list')]
    ])

    await callback.message.answer(f'Вы выбрали:\nСериал: {series_title}\nВыберите действие из списка ниже:', reply_markup=keyboard)
    await state.set_state(UploadFilm.waiting_for_series_action) # Возвращаемся в предыдущее состояние

@router.callback_query(F.data.startswith('select_season:'))
async def cb_select_season(callback: types.CallbackQuery, state: FSMContext):
    print("[ТЕСТ] Нажата кнопка выбора сезона")
    await callback.message.delete() # Удаляем предыдущее сообщение с кнопками сезонов

    # Извлекаем ID сезона из callback_data кнопки
    season_id_str = callback.data.split(':')[1]
    try:
        season_id = int(season_id_str)
    except ValueError:
        await callback.message.answer('[ТЕСТ] Ошибка обработки выбора сезона. Некорректный ID сезона.')
        await state.clear()
        is_admin = int(callback.from_user.id) in ADMIN_IDS
        await callback.message.answer('Выберите действие:', reply_markup=get_main_menu(is_admin))
        return

    async with aiosqlite.connect(DB_PATH) as db:
        # Получаем номер сезона и ID сериала
        async with db.execute('SELECT season_number, series_id FROM seasons WHERE id = ?', (season_id,)) as cursor:
            season_data = await cursor.fetchone()
            if not season_data:
                await callback.message.answer('Сезон не найден.')
                await state.clear()
                is_admin = int(callback.from_user.id) in ADMIN_IDS
                await callback.message.answer('Выберите действие:', reply_markup=get_main_menu(is_admin))
                return
            season_number, series_id = season_data

        # Получаем название сериала
        async with db.execute('SELECT title FROM series WHERE id = ?', (series_id,)) as cursor:
            series_row = await cursor.fetchone()
            series_title = series_row[0] if series_row else 'Неизвестный сериал'

        # Получаем все эпизоды для данного сезона
        async with db.execute('SELECT id, episode_number FROM episodes WHERE season_id = ? ORDER BY episode_number', (season_id,)) as cursor:
            episodes = await cursor.fetchall()

    if not episodes:
        await callback.message.answer(f'В {season_number} сезоне сериала "{series_title}" эпизоды не найдены.')
        await state.clear()
        is_admin = int(callback.from_user.id) in ADMIN_IDS
        await callback.message.answer('Выберите действие:', reply_markup=get_main_menu(is_admin))
        return

    # Формируем кнопки для выбора эпизода
    keyboard_buttons = []
    for episode_id, episode_number in episodes:
        keyboard_buttons.append([InlineKeyboardButton(text=f'{episode_number}. Серия', callback_data=f'select_episode:{episode_id}')])

    # Добавляем кнопку назад
    keyboard_buttons.append([InlineKeyboardButton(text='<< Назад к выбору сезона', callback_data='back_to_season_selection')])

    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    await callback.message.answer(f'Вы выбрали:\nСериал: {series_title}\nСезон: {season_number}\nТеперь выберите серию из списка ниже:', reply_markup=keyboard)

    # Сохраняем ID сезона и сериала в состоянии для дальнейшего использования
    await state.update_data(current_season_id=season_id, current_series_id=series_id)

    await state.set_state(UploadFilm.waiting_for_episode_selection)

@router.callback_query(F.data == 'back_to_season_selection')
async def cb_back_to_season_selection(callback: types.CallbackQuery, state: FSMContext):
    print("[ТЕСТ] Нажата кнопка '<< Назад к выбору сезона'")
    await callback.message.delete() # Удаляем текущее сообщение с кнопками эпизодов

    # Получаем ID сериала из состояния
    data = await state.get_data()
    series_id = data.get('current_series_id')

    if not series_id:
        await callback.message.answer('Произошла ошибка при возврате. Попробуйте начать сначала.')
        await state.clear()
        is_admin = int(callback.from_user.id) in ADMIN_IDS
        await callback.message.answer('Выберите действие:', reply_markup=get_main_menu(is_admin))
        return

    # Повторно вызываем логику отображения сезонов
    async with aiosqlite.connect(DB_PATH) as db:
        # Получаем название сериала
        async with db.execute('SELECT title FROM series WHERE id = ?', (series_id,)) as cursor:
            series_row = await cursor.fetchone()
            series_title = series_row[0] if series_row else 'Неизвестный сериал'

        # Получаем все сезоны для данного сериала
        async with db.execute('SELECT id, season_number FROM seasons WHERE series_id = ? ORDER BY season_number', (series_id,)) as cursor:
            seasons = await cursor.fetchall()

    if not seasons:
        await callback.message.answer(f'Для сериала "{series_title}" сезоны не найдены.')
        await state.clear()
        is_admin = int(callback.from_user.id) in ADMIN_IDS
        await callback.message.answer('Выберите действие:', reply_markup=get_main_menu(is_admin))
        return

    # Формируем кнопки для выбора сезона
    keyboard_buttons = []
    for season_id, season_number in seasons:
        keyboard_buttons.append([InlineKeyboardButton(text=f'{season_number}. Сезон', callback_data=f'select_season:{season_id}')])

    # Добавляем кнопку назад
    keyboard_buttons.append([InlineKeyboardButton(text='<< Назад', callback_data='back_to_series_actions')]) # Возвращаемся к действиям с сериалом

    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    await callback.message.answer(f'Вы выбрали:\nСериал: {series_title}\nТеперь выберите сезон из списка ниже:', reply_markup=keyboard)

    await state.set_state(UploadFilm.waiting_for_season_selection) # Возвращаемся в состояние выбора сезона

@router.callback_query(F.data.startswith('select_episode:'))
async def cb_select_episode(callback: types.CallbackQuery, state: FSMContext):
    print("[ТЕСТ] Нажата кнопка выбора серии")
    # Удаляем предыдущее сообщение только если это не первый вход в эту функцию (например, после навигации)
    data = await state.get_data()
    previous_message_id = data.get('last_episode_message_id')
    chat_id = callback.message.chat.id
    if previous_message_id:
        try:
            await callback.bot.delete_message(chat_id, previous_message_id)
        except Exception as e:
            pass # Игнорируем ошибку, если сообщение уже удалено или не найдено

    episode_id_str = callback.data.split(':')[1]
    try:
        episode_id = int(episode_id_str)
    except ValueError:
        await callback.message.answer('[ТЕСТ] Ошибка обработки выбора серии. Некорректный ID серии.')
        await state.clear()
        is_admin = int(callback.from_user.id) in ADMIN_IDS
        await callback.message.answer('Выберите действие:', reply_markup=get_main_menu(is_admin))
        return

    async with aiosqlite.connect(DB_PATH) as db:
        # Получаем file_id эпизода и его номер, а также данные сезона и сериала
        async with db.execute('''
            SELECT
                e.file_id,
                e.episode_number,
                s.season_number,
                ser.title,
                s.id, -- Получаем ID сезона
                ser.id -- Получаем ID сериала
            FROM episodes e
            JOIN seasons s ON e.season_id = s.id
            JOIN series ser ON s.series_id = ser.id
            WHERE e.id = ?
        ''', (episode_id,)) as cursor:
            episode_data = await cursor.fetchone()

    if not episode_data:
        await callback.message.answer('Эпизод не найден в базе данных.')
        await state.clear()
        is_admin = int(callback.from_user.id) in ADMIN_IDS
        await callback.message.answer('Выберите действие:', reply_markup=get_main_menu(is_admin))
        return

    file_id, episode_number, season_number, series_title, season_id, series_id = episode_data

    # Получаем общее количество эпизодов в сезоне
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT COUNT(*) FROM episodes WHERE season_id = ?', (season_id,)) as cursor:
            total_episodes_in_season = (await cursor.fetchone())[0]

    # Формируем текст сообщения
    message_text = f'Вы смотрите:\nСериал: {series_title}\nСезон: {season_number}\nСерия в сезоне: {episode_number} из {total_episodes_in_season}'

    # Определяем кнопки навигации
    buttons = []
    # Кнопка "Пред." или "К сезонам" слева
    if episode_number == 1:
        buttons.append(InlineKeyboardButton(text='<< К сезонам', callback_data='back_to_season_selection'))
    else:
        # Находим ID предыдущего эпизода
        async with aiosqlite.connect(DB_PATH) as db:
             async with db.execute('SELECT id FROM episodes WHERE season_id = ? AND episode_number = ?', (season_id, episode_number - 1)) as cursor:
                 prev_episode_row = await cursor.fetchone()
                 if prev_episode_row:
                     buttons.append(InlineKeyboardButton(text='<< Пред.', callback_data=f'prev_episode:{prev_episode_row[0]}'))

    # Кнопка "След." или "К сезонам" справа
    if episode_number < total_episodes_in_season:
        # Находим ID следующего эпизода
        async with aiosqlite.connect(DB_PATH) as db:
             async with db.execute('SELECT id FROM episodes WHERE season_id = ? AND episode_number = ?', (season_id, episode_number + 1)) as cursor:
                 next_episode_row = await cursor.fetchone()
                 if next_episode_row:
                     buttons.append(InlineKeyboardButton(text='След. >>', callback_data=f'next_episode:{next_episode_row[0]}'))

    # Добавляем кнопку "К сезонам" справа, если это последняя серия
    if episode_number == total_episodes_in_season:
         buttons.append(InlineKeyboardButton(text='К сезонам >>', callback_data='back_to_season_selection'))

    keyboard = InlineKeyboardMarkup(inline_keyboard=[buttons])

    # Отправляем сообщение с описанием и видео с кнопками
    try:
        await callback.message.answer(message_text)
        sent_message = await callback.message.answer_video(file_id, reply_markup=keyboard)
        # Сохраняем ID отправленного сообщения с видео для последующего удаления при навигации
        await state.update_data(last_episode_message_id=sent_message.message_id)
    except Exception as e:
        await callback.message.answer(f'[ТЕСТ] Ошибка при отправке видео эпизода или сообщения: {e}')

    # Сохраняем текущий episode_id, season_id и series_id в состоянии для навигации
    await state.update_data(current_episode_id=episode_id, current_season_id=season_id, current_series_id=series_id)

@router.callback_query(F.data.startswith('prev_episode:'))
async def cb_previous_episode(callback: types.CallbackQuery, state: FSMContext):
    print("[ТЕСТ] Нажата кнопка 'Пред.'")
    # Удаляем предыдущее сообщение с видео и кнопками
    # await callback.message.delete() # Убрано, так как cb_select_episode удаляет предыдущее сообщение
    
    # Получаем текущий episode_id из состояния
    data = await state.get_data()
    current_episode_id = data.get('current_episode_id')
    
    if not current_episode_id:
        await callback.message.answer('[ТЕСТ] Ошибка. Не могу определить текущий эпизод.')
        await state.clear()
        is_admin = int(callback.from_user.id) in ADMIN_IDS
        await callback.message.answer('Выберите действие:', reply_markup=get_main_menu(is_admin))
        return

    async with aiosqlite.connect(DB_PATH) as db:
        # Получаем номер текущего эпизода и ID сезона
        async with db.execute('SELECT episode_number, season_id FROM episodes WHERE id = ?', (current_episode_id,)) as cursor:
            current_episode_data = await cursor.fetchone()
            if not current_episode_data:
                await callback.message.answer('[ТЕСТ] Ошибка. Данные текущего эпизода не найдены.')
                await state.clear()
                is_admin = int(callback.from_user.id) in ADMIN_IDS
                await callback.message.answer('Выберите действие:', reply_markup=get_main_menu(is_admin))
                return
            current_episode_number, season_id = current_episode_data

        # Находим ID предыдущего эпизода в том же сезоне
        async with db.execute('SELECT id FROM episodes WHERE season_id = ? AND episode_number = ?', (season_id, current_episode_number - 1)) as cursor:
             prev_episode_row = await cursor.fetchone()

    if prev_episode_row:
        prev_episode_id = prev_episode_row[0]
        # Вызываем логику отображения эпизода с новым ID
        # Создаем фиктивный callback объект с нужными данными
        fake_callback = types.CallbackQuery(id=callback.id, from_user=callback.from_user, chat_instance=callback.chat_instance, data=f'select_episode:{prev_episode_id}', message=callback.message)
        await cb_select_episode(fake_callback, state)
    else:
        # Если предыдущего эпизода нет (текущий - первый), остаемся на первом или возвращаемся к сезонам
        # В текущей логике кнопка "Пред." не показывается для первого эпизода, но добавим обработку на всякий случай
        await callback.answer("Это первый эпизод сезона.") # Оставляем ответ на callback кнопку
        # Можно отправить текущий эпизод еще раз, если нужно
        # await cb_select_episode(types.CallbackQuery(id=callback.id, from_user=callback.from_user, chat_instance=callback.chat_instance, data=f'select_episode:{current_episode_id}', message=callback.message), state)

@router.callback_query(F.data.startswith('next_episode:'))
async def cb_next_episode(callback: types.CallbackQuery, state: FSMContext):
    print("[ТЕСТ] Нажата кнопка 'След.'")
    # Удаляем предыдущее сообщение с видео и кнопками
    # await callback.message.delete() # Убрано, так как cb_select_episode удаляет предыдущее сообщение
    
    # Получаем текущий episode_id из состояния
    data = await state.get_data()
    current_episode_id = data.get('current_episode_id')

    if not current_episode_id:
        await callback.message.answer('[ТЕСТ] Ошибка. Не могу определить текущий эпизод.')
        await state.clear()
        is_admin = int(callback.from_user.id) in ADMIN_IDS
        await callback.message.answer('Выберите действие:', reply_markup=get_main_menu(is_admin))
        return

    async with aiosqlite.connect(DB_PATH) as db:
        # Получаем номер текущего эпизода и ID сезона
        async with db.execute('SELECT episode_number, season_id FROM episodes WHERE id = ?', (current_episode_id,)) as cursor:
            current_episode_data = await cursor.fetchone()
            if not current_episode_data:
                await callback.message.answer('[ТЕСТ] Ошибка. Данные текущего эпизода не найдены.')
                await state.clear()
                is_admin = int(callback.from_user.id) in ADMIN_IDS
                await callback.message.answer('Выберите действие:', reply_markup=get_main_menu(is_admin))
                return
            current_episode_number, season_id = current_episode_data

        # Находим ID следующего эпизода в том же сезоне
        async with db.execute('SELECT id FROM episodes WHERE season_id = ? AND episode_number = ?', (season_id, current_episode_number + 1)) as cursor:
             next_episode_row = await cursor.fetchone()

    if next_episode_row:
        next_episode_id = next_episode_row[0]
        # Вызываем логику отображения эпизода с новым ID
        fake_callback = types.CallbackQuery(id=callback.id, from_user=callback.from_user, chat_instance=callback.chat_instance, data=f'select_episode:{next_episode_id}', message=callback.message)
        await cb_select_episode(fake_callback, state)
    else:
        # Если следующего эпизода нет (текущий - последний)
        await callback.answer("Это последний эпизод сезона.") # Оставляем ответ на callback кнопку
        # Кнопка "К сезонам >>" должна быть доступна для перехода
        # Можно отправить текущий эпизод еще раз, если нужно
        # await cb_select_episode(types.CallbackQuery(id=callback.id, from_user=callback.from_user, chat_instance=callback.chat_instance, data=f'select_episode:{current_episode_id}', message=callback.message), state)

@router.callback_query(F.data == 'back_to_series_list')
async def cb_back_to_series_list(callback: types.CallbackQuery, state: FSMContext):
    print("[ТЕСТ] Нажата кнопка '<< Назад к списку сериалов'")
    await callback.message.delete() # Удаляем текущее сообщение

    # Показываем список сериалов
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT id, title FROM series') as cursor:
            rows = await cursor.fetchall()
    if not rows:
        await callback.message.answer('База сериалов пуста.')
    else:
        text = '\n'.join([f'{r[0]}. {r[1]}' for r in rows])
        await callback.message.answer(f'Список сериалов:\n{text}')

    await state.clear() # Очищаем состояние FSM, связанное с поиском/просмотром сериала
    is_admin = int(callback.from_user.id) in ADMIN_IDS
    await callback.message.answer('Выберите действие:', reply_markup=get_main_menu(is_admin))
    await callback.answer() 