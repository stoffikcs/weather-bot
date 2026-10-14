import asyncio
import logging
import requests
import sqlite3

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder

from datetime import datetime, timezone, timedelta
from config import BOT_TOKEN,WEATHER_API_KEY
from apscheduler.schedulers.asyncio import AsyncIOScheduler

#______________________________________________

def init_db():
    conn=sqlite3.connect("bot.db")
    cursor=conn.cursor()
    
    cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            city TEXT NOT NULL,
            country TEXT,
            last_updated TEXT
            )
    """)
    
    conn.commit()
    conn.close()
    print("📁 База данных готова")

def save_user_city(user_id: int, city:str, country:str):
    conn=sqlite3.connect("bot.db")
    cursor=conn.cursor()
    
    cursor.execute("""
        INSERT INTO users (user_id, city, country, last_updated)
        VALUES (?, ?, ?, datetime('now'))
        ON CONFLICT(user_id) DO UPDATE SET
            city = excluded.city,
            country = excluded.country,
            last_updated = excluded.last_updated
    """, (user_id, city, country))
    conn.commit()
    conn.close()

def get_user_city(user_id:int):
    conn=sqlite3.connect("bot.db")
    cursor=conn.cursor()
    
    cursor.execute("SELECT city, country FROM users WHERE user_id = ?", (user_id,))
    row=cursor.fetchone()
    conn.close()
    
    if row:
        return {"city": row[0], "country":row[1]}
    return None

def get_all_users():
    conn=sqlite3.connect("bot.db")
    cursor=conn.cursor()
    cursor.execute("SELECT user_id, city FROM users")
    rows=cursor.fetchall()
    conn.close()
    return rows

async def notify_users_of_city(city: str, users: list, text:str):
    for user_id, user_city in users:
        if user_city==city:
            try:
                await bot.send_message(user_id, text, parse_mode="Markdown")
            except Exception as e:
                print(f"Не смог отправить {user_id}: {e}")

async def check_and_notify():
    print("🔍 Проверяю погоду для предупреждений...")
    users=get_all_users()
    
    unique_cities=set(city for _, city in users)
    
    for city in unique_cities:
        weather = get_weather(city)
        if weather is None:
            continue
        
        temp=weather["main"]["temp"]
        wind_speed=weather["wind"]["speed"]
        weather_id=weather["weather"][0]["id"]
        
        if city in weather_history:
            old=weather_history[city]
            
            temp_diff=temp - old["temp"]
            if abs(temp_diff)>=5:
                direction= "выросла" if temp_diff > 0 else "упала"
                alert_text=(
                    f"⚠️ *Резкая смена погоды!*\n\n"
                    f"📍 Город: *{city}*\n"
                    f"🌡 Температура {direction} на *{abs(temp_diff):.1f}°C* за час\n"
                    f"   Было: {old['temp']:.0f}°C → Стало: {temp:.0f}°C"
                )
                await notify_users_of_city(city, users, alert_text)
            
            if wind_speed >10 and old["wind_speed"]<=10:
                alert_text = (
                    f"⚠️ *Сильный ветер!*\n\n"
                    f"📍 Город: *{city}*\n"
                    f"💨 Ветер усилился до *{wind_speed} м/с*\n"
                    f"   Было: {old['wind_speed']} м/с"
                )
                await notify_users_of_city(city, users, alert_text)
        
        weather_history[city]={
            "temp": temp,
            "wind_speed": wind_speed,
            "weather_id": weather_id,
        }
    print(f"✅ Проверка завершена. Городов в истории: {len(weather_history)}")

#______________________________________________

logging.basicConfig(level=logging.INFO)

bot=Bot(token=BOT_TOKEN)
dp=Dispatcher()
weather_history={}
pending_cities={}

#______________________________________________

def main_menu():
    kb=ReplyKeyboardBuilder()
    kb.button(text="🌤 Узнать погоду")
    kb.button(text="📍 Мой город")
    kb.adjust(2)
    return kb.as_markup(resize_keyboard=True)

def weather_menu():
    kb=ReplyKeyboardBuilder()
    kb.button(text="🕐 Сейчас")
    kb.button(text="☀️ На сегодня")
    kb.button(text="🌅 На завтра")
    kb.button(text="📅 На 5 дней")
    kb.button(text="🔙 Назад")
    kb.adjust(2,2,1)
    return kb.as_markup(resize_keyboard=True)

def city_menu():
    kb=ReplyKeyboardBuilder()
    kb.button(text="🔍 Выбрать город")
    kb.button(text="🔙 Назад")
    kb.adjust(1,1)
    return kb.as_markup(resize_keyboard=True)

def get_weather(city: str)-> dict | None:
    url="https://api.openweathermap.org/data/2.5/weather"
    params={
        'q': city,
        'appid': WEATHER_API_KEY,
        'units':'metric',
        'lang':'ru'
    }
    try:
        response=requests.get(url,params=params, timeout=10)
        if response.status_code==200:
            return response.json()
        elif response.status_code==404:
            return None
        else:
            print(f"Ошибка API:{response.status_code}")
            return None
    except requests.RequestException as e:
        print(f"Ошибка запроса:{e}")
        return None

def find_cities(query: str, limit: int = 5) -> list:
    url = "https://nominatim.openstreetmap.org/search"
    params = {
        "q": query,
        "format": "json",
        "limit": limit,
        "accept-language": "ru",
        "addressdetails": 1,
    }
    headers = {
        "User-Agent": "WeatherBot/1.0 (my@email.com)"
    }
    try:
        response = requests.get(url, params=params, headers=headers, timeout=10)
        if response.status_code != 200:
            print(f"Ошибка Nominatim: {response.status_code}")
            return []
        
        raw = response.json()
        cities = []
        for item in raw:
            
            item_class=item.get("class", "")
            item_type=item.get("type", "")
            
            if item_class not in ["place", "boundary"]:
                continue
            if item_type not in ["city", "town", "administrative"]:
                continue
            
            display = item.get("display_name", "")
            name_ru = display.split(",")[0].strip()
            
            address = item.get("address", {})
            country_code = address.get("country_code", "").upper()
            
            name_en=item.get("name", name_ru)
            
            cities.append({
                "name": name_ru,
                "name_en": name_en,
                "country": country_code,
                "state": address.get("state", ""),
            })
        return cities
    except requests.RequestException as e:
        print(f"Ошибка запроса: {e}")
        return []

def get_forecast(city: str)->dict|None:
    url='https://api.openweathermap.org/data/2.5/forecast'
    params={
        'q':city,
        'appid':WEATHER_API_KEY,
        'units':"metric",
        'lang':"ru",
    }
    try:
        response=requests.get(url, params=params, timeout=10)
        if response.status_code==200:
            return response.json()
        elif response.status_code==404:
            return None
        else:
            print(f"Ошибка API: {response.status_code}")
            return None
    except requests.RequestException as e:
        print(f"Ошибка запроса: {e}")
        return None

def get_emoji(weather_id: int, is_night:bool)->str:
    if weather_id ==800:
        return "🌙" if is_night else "☀️"
    if 801 <= weather_id <= 804:
        if weather_id==801:
            return "🌤" if not is_night else "☁️"
        elif weather_id == 802:
            return "⛅️" if not is_night else "☁️"
        elif weather_id == 803:
            return "🌥"
        else:
            return "☁️"
    if 500 <= weather_id <= 531:
        return "🌧"
    if 600 <= weather_id <= 622:
        return "❄️"
    if 200 <= weather_id <= 232:
        return "⛈"
    if 701 <= weather_id <= 781:
        return "🌫"
    return "🌍"

def format_forecast_for_day(forecast_data: dict, day_offset:int=0, city_name: str="", country: str="")->str:
    from datetime import datetime, timezone, timedelta
    
    tz_offset=forecast_data["city"]["timezone"]
    now_local=datetime.now(timezone.utc)+timedelta(seconds=tz_offset)
    target_date=(now_local+timedelta(days=day_offset)).date()
    
    entries=[]
    for item in forecast_data["list"]:
        dt_local=datetime.fromtimestamp(item["dt"], tz=timezone.utc)+timedelta(seconds=tz_offset)
        if dt_local.date()!=target_date:
            continue
        
        if dt_local<now_local:
            continue
        
        time_str=dt_local.strftime("%H:%M")
        temp=item['main']['temp']
        desc=item['weather'][0]['description']
        weather_id=item['weather'][0]["id"]
        is_night=dt_local.hour>=21 or dt_local.hour<6
        emoji=get_emoji(weather_id, is_night)
        entries.append(f"{time_str} {emoji} {temp:+.0f}°C - {desc}")
    
    if not entries:
        return f"Нет данных на этот день."
    
    flag = ""
    if country:
        flag="".join(chr(ord(c)+127397) for c in country)
    
    header=f"📅 *Прогноз: {city_name}* {flag} — {target_date.strftime('%d.%m')}:\n\n"
    return header + "\n".join(entries[:8])

def cities_choice_menu(cities: list):
    kb=InlineKeyboardBuilder()
    for idx, city in enumerate(cities):
        name=city["name"]
        country=city["country"]
        flag="".join(chr(ord(c)+127397) for c in country) if country else""
        
        label=f"{flag} {name}".strip()
        kb.button(text=label, callback_data=f"city:{idx}")
        
    kb.button(text="🔍 Другой город", callback_data="another_city")
    kb.adjust(1)
    return kb.as_markup()

async def show_weather(message: Message, weather: dict):
    tz_offset = weather["timezone"]
    local_time = datetime.now(timezone.utc) + timedelta(seconds=tz_offset)
    local_time_str = local_time.strftime("%H:%M")
    
    temp = weather["main"]["temp"]
    feels_like = weather["main"]["feels_like"]
    humidity = weather["main"]["humidity"]
    wind_speed = weather["wind"]["speed"]
    description = weather["weather"][0]["description"]
    weather_id = weather["weather"][0]["id"]
    city_name = weather["name"]
    country = weather["sys"]["country"]
    
    flag = "".join(chr(ord(c) + 127397) for c in country)
    is_night = local_time.hour >= 21 or local_time.hour < 6
    emoji = get_emoji(weather_id, is_night)
    
    await message.answer(
        f"🌍 *{city_name}* {flag}\n\n"
        f"🌡 Температура: *{temp:.0f}°C*\n"
        f"🤔 Ощущается как: *{feels_like:.0f}°C*\n"
        f"{emoji} {description.capitalize()}\n"
        f"💧 Влажность: {humidity}%\n"
        f"💨 Ветер: {wind_speed} м/с\n"
        f"🕐 Местное время: *{local_time_str}*",
        parse_mode="Markdown",
        reply_markup=main_menu()
    )

#______________________________________________

@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        f"Привет, {message.from_user.first_name}!👋\n\n"
        "Я бот погоды. Умею показывать погоду на сейчас, сегодня, "
        "завтра и на 5 дней вперёд.\n\n"
        "Сначала выбери свой город через кнопку «📍 Мой город».",
        reply_markup=main_menu()
    )

@dp.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "🤖 *Что умеет этот бот:*\n\n"
        "🌤 Погода для любого города\n"
        "📅 Прогноз на сегодня, завтра и 5 дней\n"
        "🕐 Местное время\n"
        "🇵🇱 Флаг страны\n"
        "⚠️ Предупреждения о резких скачках\n\n"
        "Просто напиши название города!",
        parse_mode="Markdown"
    )

@dp.message(F.text == "🌤 Узнать погоду")
async def ask_weather(message: Message):
    user_id = message.from_user.id
    user=get_user_city(user_id)
    
    if user is None:
        await message.answer(
            "Сначала выбери город! Жми «📍 Мой город» → «🔍 Выбрать город».",
            reply_markup=main_menu()
        )
        return

    city=user["city"]
    await message.answer(
        f"Что показать для города *{city}*?",
        reply_markup=weather_menu(),
        parse_mode="Markdown"
    )

@dp.message(F.text=="📍 Мой город")
async def my_city(message: Message):
    await message.answer("Управление городом:", reply_markup=city_menu())

@dp.message(F.text=="🔍 Выбрать город")
async def choose_city(message: Message):
    await message.answer(
        "🌍 Напиши название города на русском или английском. \n"
        "Например: Гданьск или Gdansk\n\n",
        parse_mode="Markdown"
    )

@dp.message(F.text=="🔙 Назад")
async def go_back(message: Message):
    await message.answer("Главное меню:", reply_markup=main_menu())

@dp.message(F.text == "🕐 Сейчас")
async def weather_now(message: Message):
    user = get_user_city(message.from_user.id)
    if user is None:
        await message.answer("Сначала выбери город!", reply_markup=main_menu())
        return
    
    weather = get_weather(user["city"])
    if weather is None:
        await message.answer("Не смог получить погоду. Попробуй позже.")
        return
    
    await show_weather(message, weather)

@dp.message(F.text=="☀️ На сегодня")
async def weather_today(message: Message):
    user=get_user_city(message.from_user.id)
    if user is None:
        await message.answer("Сначала выберите город!", reply_markup=main_menu())
        return
    
    forecast=get_forecast(user["city"])
    if forecast is None:
        await message.answer("Не удалось получить прогноз погоды(\n попробуйте позже...")
        return
    
    text=format_forecast_for_day(forecast, day_offset=0, city_name=user["city"], country=user["country"])
    await message.answer(text, parse_mode="Markdown", reply_markup=weather_menu())

@dp.message(F.text=="🌅 На завтра")
async def weather_tomorrow(message: Message):
    user = get_user_city(message.from_user.id)
    if user is None:
        await message.answer("Сначала выберите город!", reply_markup=main_menu())
        return
    
    forecast = get_forecast(user["city"])
    if forecast is None:
        await message.answer("Не удалось получить прогноз погоды(\n попробуйте позже...")
        return
    
    text=format_forecast_for_day(forecast, day_offset=1, city_name=user["city"], country=user["country"])
    await message.answer(text, parse_mode="Markdown", reply_markup=weather_menu())

@dp.message(F.text=="📅 На 5 дней")
async def weather_5days(message: Message):
    user = get_user_city(message.from_user.id)
    if user is None:
        await message.answer("Сначала выбери город!", reply_markup=main_menu())
        return
    
    forecast = get_forecast(user['city'])
    if forecast is None:
        await message.answer("Не удалось получить прогноз погоды(\n попробуйте позже...")
        return
    
    from datetime import datetime, timezone, timedelta
    tz_offset=forecast["city"]["timezone"]
    now_local=datetime.now(timezone.utc)+timedelta(seconds=tz_offset)
    
    days_data={}
    for item in forecast["list"]:
        dt_local=datetime.fromtimestamp(item["dt"], tz=timezone.utc)+timedelta(seconds=tz_offset)
        
        if dt_local<now_local:
            continue
        
        date=dt_local.date()
        if date not in days_data:
            days_data[date]=[]
        days_data[date].append(item["main"]["temp"])
    
    flag="".join(chr(ord(c)+127397) for c in user["country"])
    text=f"📅 *Прогноз на 5 дней: {user['city']}* {flag}\n\n"
    for date, temps in list(days_data.items())[:5]:
        min_t=min(temps)
        max_t=max(temps)
        text+=f"*{date.strftime('%d.%m')}* — от {min_t:.0f}°C до {max_t:.0f}°C\n"
    
    await message.answer(text, parse_mode="Markdown", reply_markup=weather_menu())

@dp.callback_query(F.data=="another_city")
async def another_city(callback: CallbackQuery):
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer()
    await callback.message.answer(
        "🌍 Напишите новый город на русском или английском.\n"
        "Например: Гданьск или Gdansk",
        parse_mode="Markdown"
    )

@dp.callback_query(F.data.startswith("city:"))
async def city_chosen(callback: CallbackQuery):
    idx=int(callback.data.split(":", 1)[1])
    user_id = callback.from_user.id
    
    if user_id not in pending_cities:
        await callback.answer("Список устарел. Попробуй заново.")
        return
    
    city = pending_cities[user_id][idx]
    name_ru=city["name"]
    name_en=city["name_en"]
    country=city["country"]
    
    save_user_city(user_id, name_en, country)
    del pending_cities[user_id]
    
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer(f"Выбран город: {name_ru}")
    
    weather=get_weather(name_en)
    if weather is None:
        await callback.message.answer("Попробуй ввести другое название города")
        return
    await show_weather(callback.message, weather)

@dp.message(F.text)
async def handle_text(message: Message):
    
    buttons=["🌤 Узнать погоду", "📍 Мой город", "🔍 Выбрать город",
            "🔙 Назад", "🕐 Сейчас",
            "☀️ На сегодня", "🌅 На завтра", "📅 На 5 дней"]
    
    if message.text in buttons:
        return
    
    city=message.text.strip()
    user_id=message.from_user.id
    
    await message.answer(f"🔍 Ищу погоду для города *{city}*...",parse_mode="Markdown")
    weather = get_weather(city)
    
    if weather is None:
        cities=find_cities(city)
        
        if not cities:
            kb=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔍 Другой город", callback_data="another_city")
            ]])
            await message.answer(
                f"😕 Не нашёл город *{city}*.\n"
                "Попробуй написать иначе. Например: Гданьск или Gdansk.",
                parse_mode="Markdown",
                reply_markup=kb
            )
            return
        
        pending_cities[user_id]=cities
        await message.answer(
            "🤔 Не нашёл точного совпадения. Может, ты имел в виду:",
            reply_markup=cities_choice_menu(cities)
        )
        return
    
    city_name=weather['name']
    country=weather['sys']["country"]
    save_user_city(user_id, city_name, country)
    await show_weather(message, weather)

#______________________________________________

async def main():
    init_db()
    
    scheduler=AsyncIOScheduler()
    scheduler.add_job(check_and_notify, "interval", hours=1)
    scheduler.start()
    print("⏰ Планировщик запущен (проверка каждый час)")
    
    print("🚀 Бот запущен! Иди в Telegram и напиши ему /start")
    await dp.start_polling(bot)

if __name__=="__main__":
    asyncio.run(main())