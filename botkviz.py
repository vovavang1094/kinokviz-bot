# bot.py - основной код бота (с исправленной передачей кода)

import asyncio
import logging
import json
import uuid
import random
import string
import os
from datetime import datetime
from typing import Dict, List, Optional, Set
from collections import defaultdict

from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup,
    InlineKeyboardButton, WebAppInfo
)
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage

# Импортируем конфигурацию
from config import BOT_TOKEN, LOCAL_PORT, MINI_APP_PORT, QUESTIONS, MAX_PLAYERS

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Инициализация бота
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()
dp.include_router(router)

# =========== ХРАНИЛИЩЕ ДАННЫХ ===========
class GameSession:
    def __init__(self, game_id: str, creator_id: int, creator_name: str):
        self.game_id = game_id
        self.creator_id = creator_id
        self.creator_name = creator_name
        self.players: Dict[int, Dict] = {}
        self.scores: Dict[int, int] = {}
        self.answers: Dict[int, List] = {}
        self.current_question = 0
        self.started = False
        self.finished = False
        self.created_at = datetime.now()
        self.answered_players: Set[int] = set()  # Игроки, ответившие на текущий вопрос
        self.waiting_for_next = asyncio.Event()  # Событие для ожидания всех игроков
        
        # Добавляем создателя
        self.add_player(creator_id, creator_name)
    
    def add_player(self, user_id: int, username: str) -> bool:
        """Добавление игрока в игру"""
        if user_id not in self.players and len(self.players) < MAX_PLAYERS:
            self.players[user_id] = {
                "username": username,
                "ready": False
            }
            self.scores[user_id] = 0
            self.answers[user_id] = []
            return True
        return False
    
    def get_player_count(self) -> int:
        return len(self.players)
    
    def get_players_list(self) -> List[Dict]:
        """Список игроков для отображения"""
        return [
            {
                "id": uid,
                "username": data["username"],
                "score": self.scores.get(uid, 0),
                "answered": uid in self.answered_players
            }
            for uid, data in self.players.items()
        ]
    
    def submit_answer(self, user_id: int, question_id: int, answer: str, is_correct: bool):
        """Сохранение ответа игрока"""
        self.answers[user_id].append({
            "question_id": question_id,
            "answer": answer,
            "correct": is_correct,
            "timestamp": datetime.now()
        })
        if is_correct:
            self.scores[user_id] += 1
    
    def all_players_answered(self) -> bool:
        """Проверка, ответили ли все игроки на текущий вопрос"""
        return len(self.answered_players) == len(self.players)
    
    def reset_for_next_question(self):
        """Сброс для следующего вопроса"""
        self.answered_players.clear()
        self.waiting_for_next.clear()

class GameManager:
    def __init__(self):
        self.games: Dict[str, GameSession] = {}
        self.user_games: Dict[int, str] = {}  # user_id: game_id
        self.used_codes: Set[str] = set()  # Уже использованные коды
    
    def generate_game_code(self) -> str:
        """Генерация уникального 6-значного кода игры"""
        characters = string.ascii_uppercase + string.digits
        
        while True:
            # Генерируем случайный код из 6 символов
            code = ''.join(random.choices(characters, k=6))
            
            # Проверяем, что код уникален и не используется
            if code not in self.games and code not in self.used_codes:
                self.used_codes.add(code)
                logger.info(f"Generated new game code: {code}")
                return code
    
    def create_game(self, creator_id: int, creator_name: str) -> Dict:
        """Создание новой игры с возвратом полной информации"""
        game_id = self.generate_game_code()
        self.games[game_id] = GameSession(game_id, creator_id, creator_name)
        self.user_games[creator_id] = game_id
        
        game_info = self.get_game_info(game_id)
        logger.info(f"Game created: {game_id} by {creator_name}")
        
        return {
            "game_id": game_id,
            "game_info": game_info
        }
    
    def join_game(self, game_id: str, user_id: int, username: str) -> Dict:
        """Присоединение к игре с возвратом результата"""
        game_id = game_id.upper().strip()
        
        if game_id in self.games:
            game = self.games[game_id]
            if not game.started and game.get_player_count() < MAX_PLAYERS:
                success = game.add_player(user_id, username)
                if success:
                    self.user_games[user_id] = game_id
                    game_info = self.get_game_info(game_id)
                    logger.info(f"Player {username} joined game {game_id}")
                    
                    return {
                        "success": True,
                        "game_id": game_id,
                        "game_info": game_info
                    }
        
        return {"success": False, "message": "Не удалось присоединиться"}
    
    async def start_game(self, game_id: str, user_id: int) -> bool:
        """Начало игры"""
        if game_id in self.games and self.games[game_id].creator_id == user_id:
            game = self.games[game_id]
            
            # Проверяем минимальное количество игроков
            if len(game.players) < 2:
                return False
            
            game.started = True
            
            # Уведомляем всех игроков о начале игры
            for player_id in game.players:
                try:
                    await bot.send_message(
                        player_id,
                        "🎮 *Игра началась!*\n\n"
                        "Вернитесь в мини-приложение для участия!",
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    logger.error(f"Failed to notify player {player_id}: {e}")
            
            logger.info(f"Game {game_id} started with {len(game.players)} players")
            return True
        return False
    
    def get_game_info(self, game_id: str) -> Optional[Dict]:
        """Получение информации об игре"""
        game_id = game_id.upper().strip()
        
        if game_id in self.games:
            game = self.games[game_id]
            return {
                "game_id": game.game_id,
                "creator_id": game.creator_id,
                "creator": game.creator_name,
                "players": game.get_players_list(),
                "player_count": game.get_player_count(),
                "started": game.started,
                "finished": game.finished,
                "current_question": game.current_question
            }
        return None
    
    async def submit_answer(self, game_id: str, user_id: int, question_id: int, 
                          answer_index: int) -> Dict:
        """Обработка ответа игрока"""
        game_id = game_id.upper().strip()
        
        if game_id in self.games:
            game = self.games[game_id]
            
            # Проверяем, что игра начата
            if not game.started:
                return {"error": "Game not started", "status": "error"}
            
            # Проверяем, что это текущий вопрос
            if question_id != game.current_question:
                return {"error": "Wrong question", "status": "error"}
            
            # Проверяем, что игрок еще не ответил
            if user_id in game.answered_players:
                return {"error": "Already answered", "status": "error"}
            
            question = QUESTIONS[question_id]
            is_correct = (answer_index == question["correct"])
            
            game.submit_answer(user_id, question_id, 
                             question["options"][answer_index], is_correct)
            game.answered_players.add(user_id)
            
            logger.info(f"Player {user_id} answered question {question_id} in game {game_id}. Correct: {is_correct}")
            
            # Проверяем, ответили ли все
            all_answered = game.all_players_answered()
            if all_answered:
                game.waiting_for_next.set()
                logger.info(f"All players answered question {question_id} in game {game_id}")
            
            return {
                "status": "success",
                "correct": is_correct,
                "correct_answer": question["options"][question["correct"]],
                "score": game.scores[user_id],
                "all_answered": all_answered,
                "answered_count": len(game.answered_players),
                "total_players": len(game.players)
            }
        return {"error": "Game not found", "status": "error"}
    
    async def wait_for_all_players(self, game_id: str, timeout: int = 30) -> Dict:
        """Ожидание ответов всех игроков"""
        game_id = game_id.upper().strip()
        
        if game_id in self.games:
            game = self.games[game_id]
            
            # Если все уже ответили, сразу возвращаем успех
            if game.all_players_answered():
                return {"status": "success", "all_answered": True, "timeout": False}
            
            try:
                # Ждем события или таймаута
                await asyncio.wait_for(game.waiting_for_next.wait(), timeout=timeout)
                return {"status": "success", "all_answered": True, "timeout": False}
            except asyncio.TimeoutError:
                # Время вышло, продолжаем без всех игроков
                logger.warning(f"Timeout waiting for players in game {game_id}")
                return {"status": "success", "all_answered": False, "timeout": True}
        return {"error": "Game not found", "status": "error"}
    
    def next_question(self, game_id: str) -> bool:
        """Переход к следующему вопросу"""
        game_id = game_id.upper().strip()
        
        if game_id in self.games:
            game = self.games[game_id]
            
            # Проверяем, что игра начата
            if not game.started:
                return False
            
            game.current_question += 1
            game.reset_for_next_question()
            
            # Проверяем, закончилась ли игра
            if game.current_question >= len(QUESTIONS):
                game.finished = True
            
            logger.info(f"Game {game_id} moved to question {game.current_question}")
            return True
        return False
    
    def get_results(self, game_id: str) -> Optional[List]:
        """Получение результатов игры"""
        game_id = game_id.upper().strip()
        
        if game_id in self.games:
            game = self.games[game_id]
            results = []
            for user_id, score in game.scores.items():
                username = game.players[user_id]["username"]
                results.append({
                    "username": username,
                    "score": score,
                    "total": len(QUESTIONS)
                })
            # Сортируем по очкам
            results.sort(key=lambda x: x["score"], reverse=True)
            return results
        return None
    
    def end_game(self, game_id: str):
        """Завершение игры"""
        game_id = game_id.upper().strip()
        
        if game_id in self.games:
            # Удаляем связи пользователей с игрой
            game = self.games[game_id]
            for player_id in game.players:
                if player_id in self.user_games:
                    del self.user_games[player_id]
            
            # Удаляем игру и освобождаем код
            del self.games[game_id]
            self.used_codes.discard(game_id)
            logger.info(f"Game {game_id} ended and removed")
    
    def cleanup_old_games(self):
        """Очистка старых неактивных игр"""
        current_time = datetime.now()
        games_to_remove = []
        
        for game_id, game in self.games.items():
            # Удаляем игры, созданные более 2 часов назад
            if (current_time - game.created_at).total_seconds() > 7200:  # 2 часа
                games_to_remove.append(game_id)
        
        for game_id in games_to_remove:
            self.end_game(game_id)
            logger.info(f"Cleaned up old game: {game_id}")

# Инициализация менеджера игр
game_manager = GameManager()

# =========== ОБРАБОТЧИКИ КОМАНД ===========
@router.message(Command("start"))
async def cmd_start(message: Message):
    """Обработчик команды /start"""
    # Очищаем старые игры при старте
    game_manager.cleanup_old_games()
    
    # Проверяем наличие параметра (кода игры) в команде start
    args = message.text.split()
    game_code = None
    
    if len(args) > 1:
        game_code = args[1].upper()
        logger.info(f"User {message.from_user.id} started with game code: {game_code}")
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🎮 Открыть Кино-Квиз",
            web_app=WebAppInfo(url=f"https://red-cougars-smoke.loca.lt")
        )]
    ])
    
    start_text = "🎬 *Добро пожаловать в Кино-Квиз!*\n\n"
    start_text += "Сыграйте в увлекательную викторину по фильмам с друзьями.\n\n"
    
    if game_code:
        start_text += f"🔍 *Код игры:* `{game_code}`\n\n"
        start_text += "Нажмите кнопку ниже, чтобы присоединиться к игре!\n"
        start_text += "В мини-приложении введите код: " + game_code
    else:
        start_text += "✨ *Как играть:*\n"
        start_text += "1. Создайте игру\n"
        start_text += "2. Пригласите до 5 друзей по коду\n"
        start_text += "3. Отвечайте на 10 вопросов о кино\n"
        start_text += "4. Соревнуйтесь за первое место!\n\n"
        start_text += "Нажмите кнопку ниже, чтобы начать:"
    
    await message.answer(
        start_text,
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

@router.message(Command("game"))
async def cmd_game(message: Message):
    """Быстрое создание игры через команду"""
    username = message.from_user.username or message.from_user.first_name
    
    result = game_manager.create_game(message.from_user.id, username)
    game_id = result["game_id"]
    game_info = result["game_info"]
    
    bot_username = (await bot.me()).username
    
    await message.answer(
        f"🎮 *Игра создана!*\n\n"
        f"🔑 *Код игры:* `{game_id}`\n"
        f"👥 *Игроков:* 1/{MAX_PLAYERS}\n\n"
        f"*Отправьте этот код друзьям:*\n"
        f"`/join {game_id}`\n\n"
        f"*Или поделитесь ссылкой:*\n"
        f"`https://t.me/{bot_username}?start={game_id}`\n\n"
        f"Когда все присоединятся, начните игру в мини-приложении.",
        parse_mode="Markdown"
    )

@router.message(Command("join"))
async def cmd_join(message: Message):
    """Присоединение к игре через команду"""
    args = message.text.split()
    if len(args) < 2:
        await message.answer("❌ *Используйте:* `/join КОД_ИГРЫ`", parse_mode="Markdown")
        return
    
    game_id = args[1].upper()
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name
    
    result = game_manager.join_game(game_id, user_id, username)
    
    if result["success"]:
        game_info = result["game_info"]
        
        # Уведомляем создателя
        try:
            creator_msg = (
                f"👤 *Новый игрок!*\n"
                f"`{username}` присоединился к игре `{game_id}`\n"
                f"Всего игроков: {game_info['player_count']}/{MAX_PLAYERS}"
            )
            await bot.send_message(
                game_info['creator_id'],
                creator_msg,
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Failed to notify creator: {e}")
        
        await message.answer(
            f"✅ *Вы присоединились!*\n\n"
            f"🎮 *Код игры:* `{game_id}`\n"
            f"👥 *Игроков:* {game_info['player_count']}/{MAX_PLAYERS}\n"
            f"👑 *Создатель:* {game_info['creator']}\n\n"
            f"Ожидайте начала игры...",
            parse_mode="Markdown"
        )
    else:
        await message.answer(
            "❌ *Не удалось присоединиться*\n\n"
            "Возможные причины:\n"
            "• Игра не найдена\n"
            "• Игра уже началась\n"
            "• Достигнут лимит игроков (6)\n"
            "• Вы уже в игре",
            parse_mode="Markdown"
        )

@router.message(Command("players"))
async def cmd_players(message: Message):
    """Показать игроков в текущей игре"""
    user_id = message.from_user.id
    
    if user_id in game_manager.user_games:
        game_id = game_manager.user_games[user_id]
        game_info = game_manager.get_game_info(game_id)
        
        if game_info:
            players_text = "👥 *Игроки в вашей игре:*\n\n"
            for i, player in enumerate(game_info['players'], 1):
                players_text += f"{i}. {player['username']}"
                if player['id'] == user_id:
                    players_text += " 👈 (Вы)"
                if player.get('answered'):
                    players_text += " ✅"
                players_text += "\n"
            
            players_text += f"\nВсего: {game_info['player_count']}/{MAX_PLAYERS} игроков"
            
            if game_info['started']:
                if game_info['finished']:
                    players_text += "\n\n🏁 *Игра завершена!*\nИспользуйте /mygame для результатов"
                else:
                    players_text += f"\n\n🎮 *Игра идет!*\nВопрос: {game_info['current_question'] + 1}/{len(QUESTIONS)}"
            else:
                players_text += "\n\n⏳ Ожидание начала..."
            
            await message.answer(players_text, parse_mode="Markdown")
            return
    
    await message.answer(
        "ℹ️ *Вы не в игре*\n\n"
        "Создайте игру командой `/game`\n"
        "Или присоединитесь командой `/join КОД`",
        parse_mode="Markdown"
    )

@router.message(Command("help"))
async def cmd_help(message: Message):
    """Справка по командам"""
    help_text = (
        "📚 *Справка по командам:*\n\n"
        "• `/start` - Начать работу с ботом\n"
        "• `/game` - Быстро создать игру\n"
        "• `/join КОД` - Присоединиться к игре\n"
        "• `/players` - Показать игроков в вашей игре\n"
        "• `/mygame` - Информация о вашей текущей игре\n"
        "• `/help` - Эта справка\n\n"
        "✨ *Основной способ игры:*\n"
        "1. Нажмите кнопку 'Открыть Кино-Квиз'\n"
        "2. В мини-приложении создайте игру\n"
        "3. Пригласите друзей по коду\n"
        "4. Начните игру!\n\n"
        "🎯 *Правила:*\n"
        "• Максимум 6 игроков\n"
        "• Минимум 2 игрока для начала\n"
        "• 10 вопросов о кино\n"
        "• За правильный ответ - 1 балл\n"
        "• Игра ждет всех игроков перед следующим вопросом\n"
        "• Побеждает игрок с наибольшим счетом"
    )
    await message.answer(help_text, parse_mode="Markdown")

@router.message(Command("mygame"))
async def cmd_mygame(message: Message):
    """Информация о текущей игре пользователя"""
    user_id = message.from_user.id
    
    if user_id in game_manager.user_games:
        game_id = game_manager.user_games[user_id]
        game_info = game_manager.get_game_info(game_id)
        
        if game_info:
            text = f"🎮 *Информация об игре:*\n\n"
            text += f"🔑 *Код:* `{game_id}`\n"
            text += f"👑 *Создатель:* {game_info['creator']}\n"
            text += f"👥 *Игроков:* {game_info['player_count']}/{MAX_PLAYERS}\n"
            
            if game_info['started']:
                if game_info['finished']:
                    text += "🏁 *Статус:* Завершена\n"
                    
                    # Показываем результаты
                    results = game_manager.get_results(game_id)
                    if results:
                        text += "\n🏆 *Результаты:*\n"
                        for i, result in enumerate(results[:3], 1):
                            medal = ["🥇", "🥈", "🥉"][i-1]
                            text += f"{medal} {result['username']} - {result['score']}/{result['total']}\n"
                else:
                    text += f"▶️ *Статус:* Идет\n"
                    text += f"📝 *Вопрос:* {game_info['current_question'] + 1}/{len(QUESTIONS)}\n"
                    
                    # Показываем, кто ответил на текущий вопрос
                    answered_players = [p for p in game_info['players'] if p.get('answered')]
                    text += f"✅ *Ответили:* {len(answered_players)}/{game_info['player_count']}\n"
            else:
                text += "⏳ *Статус:* Ожидание начала\n"
                text += f"👥 *Игроков готово:* {game_info['player_count']}/2 для старта\n"
            
            text += f"\n*Пригласить друзей:*\n`/join {game_id}`\n"
            text += f"Или отправьте им код: `{game_id}`"
            
            await message.answer(text, parse_mode="Markdown")
        else:
            await message.answer("❌ Игра не найдена", parse_mode="Markdown")
    else:
        await message.answer("ℹ️ Вы не участвуете в игре", parse_mode="Markdown")

@router.message(Command("cleanup"))
async def cmd_cleanup(message: Message):
    """Очистка старых игр (админ команда)"""
    # Можно добавить проверку на админа
    old_count = len(game_manager.games)
    game_manager.cleanup_old_games()
    new_count = len(game_manager.games)
    
    await message.answer(
        f"🧹 *Очистка завершена*\n\n"
        f"Удалено игр: {old_count - new_count}\n"
        f"Осталось игр: {new_count}",
        parse_mode="Markdown"
    )

@router.message(F.web_app_data)
async def handle_webapp_data(message: Message):
    """Обработка данных из мини-приложения"""
    try:
        data = json.loads(message.web_app_data.data)
        action = data.get('action')
        user_id = message.from_user.id
        username = message.from_user.username or message.from_user.first_name
        
        logger.info(f"WebApp action: {action}, user: {user_id}")
        
        if action == 'create_game':
            # Создание новой игры
            result = game_manager.create_game(user_id, username)
            game_id = result["game_id"]
            game_info = result["game_info"]
            
            response = {
                'status': 'success',
                'action': 'game_created',
                'game_id': game_id,
                'players': game_info['players'],
                'player_count': game_info['player_count'],
                'creator': game_info['creator']
            }
            
            logger.info(f"Created game with code: {game_id}")
            await message.answer(json.dumps(response, ensure_ascii=False))
            
        elif action == 'join_game':
            # Присоединение к игре
            game_id = data.get('game_id', '').upper().strip()
            result = game_manager.join_game(game_id, user_id, username)
            
            if result["success"]:
                game_info = result["game_info"]
                response = {
                    'status': 'success',
                    'action': 'joined',
                    'game_id': game_id,
                    'players': game_info['players'],
                    'player_count': game_info['player_count'],
                    'creator': game_info['creator'],
                    'started': game_info['started']
                }
                
                # Уведомляем создателя
                try:
                    creator_notification = (
                        f"👤 *Новый игрок!*\n"
                        f"{username} присоединился к игре {game_id}\n"
                        f"Всего игроков: {game_info['player_count']}/{MAX_PLAYERS}"
                    )
                    await bot.send_message(
                        game_info['creator_id'],
                        creator_notification,
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    logger.error(f"Failed to notify creator: {e}")
                
            else:
                response = {
                    'status': 'error',
                    'message': 'Не удалось присоединиться. Проверьте код игры.'
                }
            
            await message.answer(json.dumps(response, ensure_ascii=False))
        
        elif action == 'get_game_info':
            # Получение информации об игре
            game_id = data.get('game_id', '').upper().strip()
            game_info = game_manager.get_game_info(game_id)
            
            if game_info:
                response = {
                    'status': 'success',
                    'game_info': game_info
                }
            else:
                response = {'status': 'error', 'message': 'Игра не найдена'}
            
            await message.answer(json.dumps(response, ensure_ascii=False))
        
        elif action == 'start_game':
            # Начало игры
            game_id = data.get('game_id', '').upper().strip()
            success = await game_manager.start_game(game_id, user_id)
            
            if success:
                response = {'status': 'success', 'action': 'started'}
            else:
                response = {
                    'status': 'error', 
                    'message': 'Не удалось начать игру. Нужно минимум 2 игрока.'
                }
            
            await message.answer(json.dumps(response, ensure_ascii=False))
        
        elif action == 'get_questions':
            # Получение вопросов
            response = {
                'status': 'success',
                'questions': QUESTIONS,
                'total_questions': len(QUESTIONS)
            }
            await message.answer(json.dumps(response, ensure_ascii=False))
        
        elif action == 'submit_answer':
            # Отправка ответа
            game_id = data.get('game_id', '').upper().strip()
            question_id = data.get('question_id')
            answer_index = data.get('answer_index')
            
            result = await game_manager.submit_answer(game_id, user_id, question_id, answer_index)
            await message.answer(json.dumps(result, ensure_ascii=False))
        
        elif action == 'wait_for_all':
            # Ожидание ответов всех игроков
            game_id = data.get('game_id', '').upper().strip()
            result = await game_manager.wait_for_all_players(game_id)
            await message.answer(json.dumps(result, ensure_ascii=False))
        
        elif action == 'next_question':
            # Переход к следующему вопросу
            game_id = data.get('game_id', '').upper().strip()
            if game_manager.next_question(game_id):
                game_info = game_manager.get_game_info(game_id)
                response = {
                    'status': 'success',
                    'current_question': game_info['current_question'],
                    'finished': game_info['finished']
                }
            else:
                response = {'status': 'error', 'message': 'Ошибка перехода к следующему вопросу'}
            
            await message.answer(json.dumps(response, ensure_ascii=False))
        
        elif action == 'get_results':
            # Получение результатов
            game_id = data.get('game_id', '').upper().strip()
            results = game_manager.get_results(game_id)
            
            if results:
                response = {
                    'status': 'success',
                    'results': results
                }
                
                # Формируем красивое сообщение для чата
                results_text = "🏆 *Результаты игры:*\n\n"
                for i, result in enumerate(results, 1):
                    medal = ["🥇", "🥈", "🥉"][i-1] if i <= 3 else f"{i}."
                    results_text += f"{medal} *{result['username']}* - {result['score']}/{result['total']}\n"
                
                # Отправляем результаты всем игрокам
                game_info = game_manager.get_game_info(game_id)
                if game_info:
                    for player in game_info['players']:
                        try:
                            await bot.send_message(
                                player['id'],
                                results_text,
                                parse_mode="Markdown"
                            )
                        except Exception as e:
                            logger.error(f"Failed to send results to {player['id']}: {e}")
                
                # Завершаем игру
                game_manager.end_game(game_id)
            else:
                response = {'status': 'error', 'message': 'Игра не найдена'}
            
            await message.answer(json.dumps(response, ensure_ascii=False))
        
        elif action == 'leave_game':
            # Выход из игры
            game_id = data.get('game_id', '').upper().strip()
            user_id = message.from_user.id
            
            if user_id in game_manager.user_games:
                # Находим игру
                for gid, game in game_manager.games.items():
                    if user_id in game.players:
                        # Удаляем игрока из игры
                        del game.players[user_id]
                        if user_id in game.scores:
                            del game.scores[user_id]
                        if user_id in game.answers:
                            del game.answers[user_id]
                        if user_id in game.answered_players:
                            game.answered_players.remove(user_id)
                        
                        # Удаляем связь пользователь-игра
                        del game_manager.user_games[user_id]
                        
                        # Уведомляем создателя
                        try:
                            if gid == game_id and game.creator_id != user_id:
                                await bot.send_message(
                                    game.creator_id,
                                    f"👋 Игрок {username} покинул игру {game_id}",
                                    parse_mode="Markdown"
                                )
                        except Exception as e:
                            logger.error(f"Failed to notify creator: {e}")
                        
                        # Если игра пустая, удаляем ее
                        if len(game.players) == 0:
                            game_manager.end_game(game_id)
                        
                        break
                
                response = {'status': 'success', 'message': 'Вы вышли из игры'}
            else:
                response = {'status': 'error', 'message': 'Вы не в игре'}
            
            await message.answer(json.dumps(response, ensure_ascii=False))
    
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error: {e}")
        await message.answer(json.dumps({
            'status': 'error',
            'message': 'Неверный формат данных'
        }, ensure_ascii=False))
    except Exception as e:
        logger.error(f"Error processing WebApp data: {e}")
        await message.answer(json.dumps({
            'status': 'error',
            'message': str(e)
        }, ensure_ascii=False))

@router.message()
async def handle_other_messages(message: Message):
    """Обработка всех остальных сообщений"""
    text = message.text.strip()
    
    # Если пользователь отправил 6-значный код, предлагаем присоединиться
    if len(text) == 6 and text.isalnum():
        code = text.upper()
        await message.answer(
            f"🔍 *Найден код игры:* `{code}`\n\n"
            f"Хотите присоединиться к этой игре?\n"
            f"Используйте команду: `/join {code}`\n\n"
            f"Или нажмите кнопку 'Открыть Кино-Квиз' и введите код в мини-приложении.",
            parse_mode="Markdown"
        )
    else:
        # Простое эхо для тестирования
        await message.answer(
            "🎬 *Кино-Квиз Бот*\n\n"
            "Используйте команду /start для начала игры\n"
            "Или /help для справки по командам",
            parse_mode="Markdown"
        )

# =========== ОСНОВНАЯ ФУНКЦИЯ ЗАПУСКА ===========
async def main():
    """Основная функция запуска бота"""
    logger.info("🚀 Запуск Кино-Квиз бота...")
    
    # Удаляем вебхук если был (для чистого запуска в polling режиме)
    await bot.delete_webhook(drop_pending_updates=True)
    
    # Запускаем polling
    logger.info("✅ Бот запущен в режиме polling")
    logger.info(f"📝 Количество вопросов в игре: {len(QUESTIONS)}")
    logger.info(f"👥 Максимальное количество игроков: {MAX_PLAYERS}")
    
    bot_info = await bot.me()
    logger.info(f"🤖 Имя бота: @{bot_info.username}")
    logger.info(f"🆔 ID бота: {bot_info.id}")
    
    logger.info("\n🔗 Доступные команды:")
    logger.info("   /start - начало работы")
    logger.info("   /game - создать игру")
    logger.info("   /join КОД - присоединиться к игре")
    logger.info("   /players - показать игроков")
    logger.info("   /mygame - информация о вашей игре")
    logger.info("   /help - справка")
    
    try:
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"❌ Ошибка при polling: {e}")
    finally:
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"❌ Ошибка запуска бота: {e}")