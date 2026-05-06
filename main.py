from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from typing import Dict, List, Optional, Tuple
import json
import random
import sqlite3
import uuid
import hashlib
import asyncio
import os
from datetime import datetime, timedelta

app = FastAPI(title="Ludo Royale - Full Stack")

# ========== STATIC FILES SETUP ==========
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# ========== DATABASE SETUP ==========
DB_PATH = os.path.join(BASE_DIR, "ludo.db")
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
cursor = conn.cursor()

# Users table
cursor.execute('''CREATE TABLE IF NOT EXISTS users
             (id TEXT PRIMARY KEY, username TEXT UNIQUE, password_hash TEXT, created_at TIMESTAMP)''')

# Games table
cursor.execute('''CREATE TABLE IF NOT EXISTS games
             (id TEXT PRIMARY KEY, players TEXT, state TEXT, bet_amount INTEGER DEFAULT 0, created_at TIMESTAMP)''')

# Leaderboard table
cursor.execute('''CREATE TABLE IF NOT EXISTS leaderboard
             (player_id TEXT PRIMARY KEY, player_name TEXT, wins INTEGER DEFAULT 0, games INTEGER DEFAULT 0, created_at TIMESTAMP)''')

# Game history for replay
cursor.execute('''CREATE TABLE IF NOT EXISTS game_history
             (id TEXT PRIMARY KEY, game_id TEXT, move_data TEXT, timestamp TIMESTAMP)''')

# User wallet for coins
cursor.execute('''CREATE TABLE IF NOT EXISTS user_wallet
             (user_id TEXT PRIMARY KEY, coins INTEGER DEFAULT 1000, updated_at TIMESTAMP)''')

# Transactions log
cursor.execute('''CREATE TABLE IF NOT EXISTS transactions
             (id TEXT PRIMARY KEY, user_id TEXT, amount INTEGER, type TEXT, description TEXT, timestamp TIMESTAMP)''')

# Rooms table
cursor.execute('''CREATE TABLE IF NOT EXISTS rooms
             (id TEXT PRIMARY KEY, room_code TEXT UNIQUE, host_id TEXT, is_private INTEGER DEFAULT 0, password_hash TEXT, created_at TIMESTAMP)''')

# Tournaments table
cursor.execute('''CREATE TABLE IF NOT EXISTS tournaments
             (id TEXT PRIMARY KEY, name TEXT, max_players INTEGER, status TEXT, winner_id TEXT, created_at TIMESTAMP)''')

# Tournament matches
cursor.execute('''CREATE TABLE IF NOT EXISTS tournament_matches
             (id TEXT PRIMARY KEY, tournament_id TEXT, round INTEGER, player1_id TEXT, player2_id TEXT, winner_id TEXT, game_id TEXT)''')

# Daily rewards
cursor.execute('''CREATE TABLE IF NOT EXISTS daily_rewards
             (user_id TEXT PRIMARY KEY, last_claim TIMESTAMP, streak INTEGER DEFAULT 0)''')
conn.commit()

# ========== GAME CONSTANTS ==========
COLORS = ['red', 'green', 'yellow', 'blue']
COLOR_HEX = {'red': '#ef4444', 'green': '#10b981', 'yellow': '#f59e0b', 'blue': '#3b82f6'}
START_POSITIONS = {'red': 0, 'green': 13, 'yellow': 26, 'blue': 39}
SAFE_POSITIONS = [0, 8, 13, 21, 26, 34, 39, 47]
HOME_PATH_LENGTH = 6
TOTAL_PATH_LENGTH = 52
TOKENS_PER_PLAYER = 4

# 52-position main path coordinates for 15x15 board
MAIN_PATH = [
    (6,1),(6,2),(6,3),(6,4),(6,5),(5,6),(4,6),(3,6),(2,6),(1,6),(0,6),
    (0,7),(0,8),(1,8),(2,8),(3,8),(4,8),(5,8),(6,8),(6,9),(6,10),(6,11),(6,12),(6,13),(6,14),
    (7,14),(8,14),(8,13),(8,12),(8,11),(8,10),(8,9),(8,8),(9,8),(10,8),(11,8),(12,8),(13,8),(14,8),
    (14,7),(14,6),(13,6),(12,6),(11,6),(10,6),(9,6),(8,6),(8,5),(8,4),(8,3),(8,2),(8,1),(8,0),
    (7,0)
]

# Home paths for each color - 6 steps to reach center
HOME_PATHS = {
    'red': [(6,2),(6,3),(6,4),(6,5),(6,6),(7,6)],
    'green': [(2,8),(3,8),(4,8),(5,8),(6,8),(7,8)],
    'yellow': [(8,12),(8,11),(8,10),(8,9),(8,8),(7,8)],
    'blue': [(12,6),(11,6),(10,6),(9,6),(8,6),(7,6)]
}

# Home base token positions
HOME_BASES = {
    'red': [(1,1),(1,3),(3,1),(3,3)],
    'green': [(1,11),(1,13),(3,11),(3,13)],
    'yellow': [(11,1),(11,3),(13,1),(13,3)],
    'blue': [(11,11),(11,13),(13,11),(13,13)]
}

# ========== UTILITY FUNCTIONS ==========
def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def add_coins(user_id: str, amount: int, txn_type: str, desc: str):
    cursor.execute("INSERT OR IGNORE INTO user_wallet(user_id,coins,updated_at) VALUES(?,?,?)",
                  (user_id,1000,datetime.now()))
    cursor.execute("UPDATE user_wallet SET coins=coins+?,updated_at=? WHERE user_id=?",
                  (amount,datetime.now(),user_id))
    cursor.execute("INSERT INTO transactions(id,user_id,amount,type,description,timestamp) VALUES(?,?,?,?,?,?)",
                  (str(uuid.uuid4()),user_id,amount,txn_type,desc,datetime.now()))
    conn.commit()
    return get_balance(user_id)

def get_balance(user_id: str) -> int:
    cursor.execute("SELECT coins FROM user_wallet WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    return row[0] if row else 1000

def get_path_index(position: Tuple[int,int]) -> int:
    try:
        return MAIN_PATH.index(position)
    except ValueError:
        return -1

# ========== TOKEN CLASS ==========
class Token:
    def __init__(self, player_color: str, token_id: int):
        self.player_color = player_color
        self.token_id = token_id
        self.position = -1 # -1 = in home base
        self.home_path_position = -1 # -1 = not in home path
        self.is_finished = False

    def is_in_home(self) -> bool:
        return self.position == -1 and self.home_path_position == -1

    def is_in_home_path(self) -> bool:
        return self.home_path_position >= 0 and not self.is_finished

    def is_on_board(self) -> bool:
        return self.position >= 0 and not self.is_in_home_path() and not self.is_finished

    def get_current_position(self) -> Optional[Tuple[int,int]]:
        if self.is_finished:
            return None
        elif self.is_in_home_path():
            return HOME_PATHS[self.player_color][self.home_path_position]
        elif self.is_on_board():
            return MAIN_PATH[self.position]
        else:
            return HOME_BASES[self.player_color][self.token_id]

    def can_move(self, dice_value: int) -> bool:
        if self.is_finished:
            return False
        if self.is_in_home():
            return dice_value == 6
        if self.is_in_home_path():
            return self.home_path_position + dice_value <= HOME_PATH_LENGTH
        new_position = self.position + dice_value
        return new_position <= TOTAL_PATH_LENGTH + HOME_PATH_LENGTH

# ========== PLAYER CLASS ==========
class Player:
    def __init__(self, player_id: str, player_name: str, color: str):
        self.player_id = player_id
        self.player_name = player_name
        self.color = color
        self.tokens = [Token(color, i) for i in range(TOKENS_PER_PLAYER)]
        self.finished_tokens = 0
        self.is_turn = False

    def get_movable_tokens(self, dice_value: int) -> List[int]:
        return [i for i, token in enumerate(self.tokens) if token.can_move(dice_value)]

    def has_won(self) -> bool:
        return self.finished_tokens == TOKENS_PER_PLAYER

# ========== AI PLAYER CLASS ==========
class AIPlayer(Player):
    def __init__(self, color: str, difficulty='medium'):
        super().__init__(f"ai_{color}_{uuid.uuid4().hex[:4]}", f"AI {color.title()}", color)
        self.difficulty = difficulty

    def choose_token(self, dice_value: int, game_state: dict) -> Optional[int]:
        movable = self.get_movable_tokens(dice_value)
        if not movable:
            return None

        if self.difficulty == 'easy':
            return random.choice(movable)

        elif self.difficulty == 'medium':
            # Priority: 1. Kill opponent 2. Move token close to home 3. Bring token out
            for token_idx in movable:
                token = self.tokens[token_idx]
                if token.is_in_home() and dice_value == 6:
                    return token_idx
                if token.is_on_board():
                    new_pos = token.position + dice_value
                    if self.would_kill_opponent(new_pos, game_state):
                        return token_idx
            return random.choice(movable)

        elif self.difficulty == 'hard':
            # Priority: 1. Safe position 2. Kill 3. Move to home path 4. Advance
            for token_idx in movable:
                token = self.tokens[token_idx]
                if token.is_on_board():
                    new_pos = token.position + dice_value
                    if new_pos in SAFE_POSITIONS:
                        return token_idx
            return random.choice(movable)
        return random.choice(movable)

    def would_kill_opponent(self, position: int, game_state: dict) -> bool:
        if position in SAFE_POSITIONS:
            return False
        for player in game_state['players']:
            if player['color']!= self.color:
                for token in player['tokens']:
                    if token['position'] == position and not token['is_finished']:
                        return True
        return False

# ========== LUDO GAME ENGINE ==========
class LudoGame:
    def __init__(self, game_id: str, bet_amount: int = 0):
        self.game_id = game_id
        self.players: Dict[str, Player] = {}
        self.player_order: List[str] = []
        self.current_player_index = 0
        self.dice_value = 0
        self.game_state = 'waiting' # waiting, playing, finished
        self.winner = None
        self.move_history = []
        self.bet_amount = bet_amount
        self.created_at = datetime.now()

    def add_player(self, player_id: str, player_name: str, is_ai: bool = False, ai_difficulty: str = 'medium') -> Optional[str]:
        if len(self.players) >= 4 or self.game_state!= 'waiting':
            return None
        color = COLORS[len(self.players)]
        if is_ai:
            self.players[player_id] = AIPlayer(color, ai_difficulty)
        else:
            self.players[player_id] = Player(player_id, player_name, color)
        self.player_order.append(player_id)
        if len(self.players) >= 2:
            self.game_state = 'playing'
            self.players[self.player_order[0]].is_turn = True
        return color

    def remove_player(self, player_id: str):
        if player_id in self.players:
            del self.players[player_id]
            if player_id in self.player_order:
                self.player_order.remove(player_id)

    def get_current_player(self) -> Optional[Player]:
        if not self.player_order or self.current_player_index >= len(self.player_order):
            return None
        return self.players[self.player_order[self.current_player_index]]

    def start_bet(self) -> bool:
        if self.bet_amount <= 0:
            return True
        for player_id in self.players:
            if get_balance(player_id) < self.bet_amount:
                return False
            add_coins(player_id, -self.bet_amount, 'bet', f'Bet for game {self.game_id}')
        return True

    def payout_winner(self, winner_id: str):
        if self.bet_amount <= 0:
            return
        total_pot = self.bet_amount * len(self.players)
        add_coins(winner_id, total_pot, 'win', f'Won game {self.game_id}')

    def roll_dice(self, player_id: str) -> Optional[int]:
        current_player = self.get_current_player()
        if not current_player or current_player.player_id!= player_id or self.game_state!= 'playing':
            return None
        self.dice_value = random.randint(1, 6)
        self.log_move(player_id, 'roll_dice', {'dice_value': self.dice_value})
        return self.dice_value

    def move_token(self, player_id: str, token_index: int) -> bool:
        current_player = self.get_current_player()
        if not current_player or current_player.player_id!= player_id or self.game_state!= 'playing':
            return False
        if token_index < 0 or token_index >= TOKENS_PER_PLAYER:
            return False

        token = current_player.tokens[token_index]
        if not token.can_move(self.dice_value):
            return False

        old_position = token.get_current_position()

        # Move token logic
        if token.is_in_home():
            if self.dice_value == 6:
                token.position = START_POSITIONS[current_player.color]
        elif token.is_in_home_path():
            token.home_path_position += self.dice_value
            if token.home_path_position >= HOME_PATH_LENGTH:
                token.is_finished = True
                current_player.finished_tokens += 1
        else:
            new_position = token.position + self.dice_value
            if new_position >= TOTAL_PATH_LENGTH:
                token.home_path_position = new_position - TOTAL_PATH_LENGTH
                token.position = -1
            else:
                token.position = new_position

        # Check for killing opponent tokens
        if token.is_on_board():
            self.check_and_kill_opponents(token, current_player.color)

        # Check win condition
        if current_player.has_won():
            self.game_state = 'finished'
            self.winner = current_player.player_id
            self.payout_winner(current_player.player_id)
            self.update_leaderboard(current_player.player_name)

        # Log move
        self.log_move(player_id, 'move_token', {
            'token_index': token_index,
            'old_position': old_position,
            'new_position': token.get_current_position(),
            'dice_value': self.dice_value
        })

        # Next turn if dice!= 6
        if self.dice_value!= 6:
            self.next_turn()
        self.dice_value = 0
        return True

    def check_and_kill_opponents(self, moving_token: Token, moving_color: str):
        current_pos = moving_token.get_current_position()
        if not current_pos:
            return
        path_index = get_path_index(current_pos)
        if path_index in SAFE_POSITIONS:
            return # Safe position, no killing

        for player_id, player in self.players.items():
            if player.color == moving_color:
                continue
            for token in player.tokens:
                if token.get_current_position() == current_pos and token.is_on_board():
                    token.position = -1
                    token.home_path_position = -1
                    self.log_move(player_id, 'token_killed', {
                        'token_index': token.token_id,
                        'killed_by': moving_color
                    })

    def next_turn(self):
        current_player = self.get_current_player()
        if current_player:
            current_player.is_turn = False
        self.current_player_index = (self.current_player_index + 1) % len(self.player_order)
        if self.player_order:
            next_player = self.players[self.player_order[self.current_player_index]]
            next_player.is_turn = True
            # If next player is AI, auto play
            if isinstance(next_player, AIPlayer):
                asyncio.create_task(self.ai_auto_play(next_player))

    async def ai_auto_play(self, ai_player: AIPlayer):
        await asyncio.sleep(1.5)
        dice = self.roll_dice(ai_player.player_id)
        if dice:
            movable = ai_player.get_movable_tokens(dice)
            if movable:
                token_idx = ai_player.choose_token(dice, self.get_game_state())
                if token_idx is not None:
                    self.move_token(ai_player.player_id, token_idx)
                    await manager.broadcast(self.game_id, {
                        "type": "ai_move", "state": self.get_game_state()
                    })

    def log_move(self, player_id: str, move_type: str, move_data: dict):
        move_record = {
            'player_id': player_id,
            'move_type': move_type,
            'move_data': move_data,
            'timestamp': datetime.now().isoformat()
        }
        self.move_history.append(move_record)
        cursor.execute("INSERT INTO game_history(id,game_id,move_data,timestamp) VALUES(?,?,?,?)",
                      (str(uuid.uuid4()), self.game_id, json.dumps(move_record), datetime.now()))
        conn.commit()

    def update_leaderboard(self, winner_name: str):
        for player in self.players.values():
            cursor.execute("INSERT OR IGNORE INTO leaderboard(player_id,player_name,wins,games,created_at) VALUES(?,?,?,?,?)",
                          (player.player_id, player.player_name, 0, 0, datetime.now()))
            cursor.execute("UPDATE leaderboard SET games = games + 1 WHERE player_id =?",
                          (player.player_id,))
        cursor.execute("UPDATE leaderboard SET wins = wins + 1 WHERE player_name =?", (winner_name,))
        conn.commit()

    def get_game_state(self) -> dict:
        return {
            'game_id': self.game_id,
            'players': [{
                'player_id': p.player_id,
                'player_name': p.player_name,
                'color': p.color,
                'tokens': [{
                    'token_id': t.token_id,
                    'position': t.position,
                    'home_path_position': t.home_path_position,
                    'is_finished': t.is_finished,
                    'current_coord': t.get_current_position()
                } for t in p.tokens],
                'finished_tokens': p.finished_tokens,
                'is_turn': p.is_turn
            } for p in self.players.values()],
            'current_player': self.get_current_player().player_id if self.get_current_player() else None,
            'dice_value': self.dice_value,
            'game_state': self.game_state,
            'winner': self.winner,
            'bet_amount': self.bet_amount
        }

# ========== CONNECTION MANAGER ==========
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, List[WebSocket]] = {}
        self.chat_connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, game_id: str, websocket: WebSocket):
        await websocket.accept()
        if game_id not in self.active_connections:
            self.active_connections[game_id] = []
        self.active_connections[game_id].append(websocket)

    async def connect_chat(self, game_id: str, websocket: WebSocket):
        await websocket.accept()
        if game_id not in self.chat_connections:
            self.chat_connections[game_id] = []
        self.chat_connections[game_id].append(websocket)

    def disconnect(self, game_id: str, websocket: WebSocket):
        if game_id in self.active_connections and websocket in self.active_connections[game_id]:
            self.active_connections[game_id].remove(websocket)
        if game_id in self.chat_connections and websocket in self.chat_connections[game_id]:
            self.chat_connections[game_id].remove(websocket)

    async def broadcast(self, game_id: str, message: dict):
        if game_id in self.active_connections:
            disconnected = []
            for connection in self.active_connections[game_id]:
                try:
                    await connection.send_json(message)
                except:
                    disconnected.append(connection)
            for conn in disconnected:
                self.active_connections[game_id].remove(conn)

    async def broadcast_chat(self, game_id: str, message: dict):
        if game_id in self.chat_connections:
            for connection in self.chat_connections[game_id]:
                try:
                    await connection.send_json(message)
                except:
                    pass

manager = ConnectionManager()
games: Dict[str, LudoGame] = {}

# ========== AUTH API ROUTES ==========
@app.post("/api/register")
async def register(username: str, password: str):
    if len(password) < 4:
        return {"error": "Password must be at least 4 characters"}
    user_id = str(uuid.uuid4())
    try:
        cursor.execute("INSERT INTO users(id,username,password_hash,created_at) VALUES(?,?,?,?)",
                      (user_id, username, hash_password(password), datetime.now()))
        conn.commit()
        add_coins(user_id, 1000, 'signup', 'Welcome bonus')
        return {"user_id": user_id, "username": username}
    except sqlite3.IntegrityError:
        return {"error": "Username already exists"}

@app.post("/api/login")
async def login(username: str, password: str):
    cursor.execute("SELECT id,password_hash FROM users WHERE username=?", (username,))
    row = cursor.fetchone()
    if row and row[1] == hash_password(password):
        balance = get_balance(row[0])
        return {"user_id": row[0], "username": username, "coins": balance}
    return {"error": "Invalid credentials"}

@app.get("/api/wallet/{user_id}")
async def get_wallet(user_id: str):
    balance = get_balance(user_id)
    cursor.execute("SELECT amount,type,description,timestamp FROM transactions WHERE user_id=? ORDER BY timestamp DESC LIMIT 20", (user_id,))
    transactions = [{"amount":r[0],"type":r[1],"description":r[2],"timestamp":r[3]} for r in cursor.fetchall()]
    return {"coins": balance, "transactions": transactions}

# ========== ROOM API ROUTES ==========
@app.post("/api/create_room")
async def create_room(host_id: str, is_private: bool = False, password: str = ""):
    room_code = str(random.randint(100000, 999))
    password_hash = hash_password(password) if password else ""
    cursor.execute("INSERT INTO rooms(id,room_code,host_id,is_private,password_hash,created_at) VALUES(?,?,?,?,?,?)",
                  (str(uuid.uuid4()), room_code, host_id, 1 if is_private else 0, password_hash, datetime.now()))
    conn.commit()
    return {"room_code": room_code}

@app.post("/api/join_room/{room_code}")
async def join_room(room_code: str, password: str = ""):
    cursor.execute("SELECT id,is_private,password_hash FROM rooms WHERE room_code=?", (room_code,))
    row = cursor.fetchone()
    if not row:
        return {"error": "Room not found"}
    if row[1] and row[2]!= hash_password(password):
        return {"error": "Wrong password"}
    return {"room_id": row[0]}

# ========== GAME API ROUTES ==========
@app.post("/api/create_game")
async def create_game(bet_amount: int = 0, user_id: str = ""):
    if bet_amount > 0 and user_id:
        if get_balance(user_id) < bet_amount:
            return {"error": "Insufficient coins"}
    game_id = f"game_{uuid.uuid4().hex[:8]}"
    games[game_id] = LudoGame(game_id, bet_amount)
    if bet_amount > 0:
        games[game_id].start_bet()
    cursor.execute("INSERT INTO games(id,players,state,bet_amount,created_at) VALUES(?,?,?,?)",
                  (game_id, json.dumps([]), 'waiting', bet_amount, datetime.now()))
    conn.commit()
    return {"game_id": game_id, "status": "created"}

@app.post("/api/join_game/{game_id}")
async def join_game(game_id: str, player_name: str, user_id: str = "", is_ai: bool = False, ai_difficulty: str = 'medium'):
    if game_id not in games:
        return {"error": "Game not found"}
    player_id = user_id if user_id else str(uuid.uuid4())
    color = games[game_id].add_player(player_id, player_name, is_ai, ai_difficulty)
    if color:
        cursor.execute("UPDATE games SET players=? WHERE id=?",
                      (json.dumps(list(games[game_id].players.keys())), game_id))
        conn.commit()
        await manager.broadcast(game_id, {"type": "player_joined", "state": games[game_id].get_game_state()})
        return {"player_id": player_id, "color": color, "game_id": game_id}
    return {"error": "Game is full or already started"}

@app.get("/api/replay/{game_id}")
async def get_replay(game_id: str):
    cursor.execute("SELECT move_data FROM game_history WHERE game_id=? ORDER BY timestamp", (game_id,))
    moves = [json.loads(row[0]) for row in cursor.fetchall()]
    return {"moves": moves}

@app.get("/api/leaderboard")
async def get_leaderboard():
    cursor.execute("SELECT player_name,wins,games FROM leaderboard ORDER BY wins DESC LIMIT 20")
    return {"leaderboard": [{"name": row[0], "wins": row[1], "games": row[2]} for row in cursor.fetchall()]}

@app.post("/api/daily_reward/{user_id}")
async def claim_daily_reward(user_id: str):
    cursor.execute("SELECT last_claim,streak FROM daily_rewards WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    now = datetime.now()

    if row:
        last_claim = datetime.fromisoformat(row[0])
        if (now - last_claim).days == 1:
            streak = row[1] + 1
        elif (now - last_claim).days > 1:
            streak = 1
        else:
            return {"error": "Already claimed today"}
    else:
        streak = 1

    reward_coins = min(100 + streak*50, 1000)
    add_coins(user_id, reward_coins, 'daily_reward', f'Day {streak} streak')
    cursor.execute("INSERT OR REPLACE INTO daily_rewards(user_id,last_claim,streak) VALUES(?,?,?)",
                  (user_id,now.isoformat(),streak))
    conn.commit()
    return {"coins": reward_coins, "streak": streak}

@app.get("/api/admin/stats")
async def get_admin_stats():
    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM games WHERE created_at > datetime('now','-24 hours')")
    games_today = cursor.fetchone()[0]
    cursor.execute("SELECT SUM(amount) FROM transactions WHERE type='bet' AND timestamp > datetime('now','-24 hours')")
    revenue_today = cursor.fetchone()[0] or 0
    cursor.execute("SELECT COUNT(*) FROM games WHERE state='playing'")
    active_games = cursor.fetchone()[0]
    return {
        "total_users": total_users,
        "games_today": games_today,
        "revenue_today": revenue_today,
        "active_games": active_games
    }

# ========== TOURNAMENT API ROUTES ==========
@app.post("/api/create_tournament")
async def create_tournament(name: str, max_players: int = 8):
    tournament_id = str(uuid.uuid4())
    cursor.execute("INSERT INTO tournaments(id,name,max_players,status,created_at) VALUES(?,?,?,?,?)",
                  (tournament_id,name,max_players,'registration',datetime.now()))
    conn.commit()
    return {"tournament_id": tournament_id}

@app.post("/api/join_tournament/{tournament_id}")
async def join_tournament(tournament_id: str, user_id: str):
    cursor.execute("SELECT max_players,status FROM tournaments WHERE id=?", (tournament_id,))
    row = cursor.fetchone()
    if not row:
        return {"error": "Tournament not found"}
    if row[1]!= 'registration':
        return {"error": "Tournament registration closed"}
    # Add player to tournament - simplified
    return {"status": "joined"}

# ========== WEBSOCKET ROUTES ==========
@app.websocket("/ws/{game_id}/{player_id}")
async def websocket_endpoint(websocket: WebSocket, game_id: str, player_id: str):
    await manager.connect(game_id, websocket)
    try:
        while True:
            data = await websocket.receive_json()
            if game_id not in games:
                await websocket.send_json({"type": "error", "message": "Game not found"})
                continue

            game = games[game_id]
            action = data.get('action')

            if action == 'roll_dice':
                dice = game.roll_dice(player_id)
                if dice is not None:
                    await manager.broadcast(game_id, {
                        "type": "dice_rolled", "dice_value": dice, "state": game.get_game_state()
                    })

            elif action == 'move_token':
                token_index = data.get('token_index', -1)
                success = game.move_token(player_id, token_index)
                await manager.broadcast(game_id, {
                    "type": "token_moved", "success": success, "state": game.get_game_state()
                })

    except WebSocketDisconnect:
        manager.disconnect(game_id, websocket)
        if game_id in games:
            games[game_id].remove_player(player_id)
            await manager.broadcast(game_id, {"type": "player_left", "state": games[game_id].get_game_state()})

@app.websocket("/ws/{game_id}/{player_id}")
async def websocket_endpoint(websocket: WebSocket, game_id: str, player_id: str):
    await manager.connect(game_id, websocket)
    # SEND INITIAL STATE IMMEDIATELY
    if game_id in games:
        await websocket.send_json({"type": "game_state", "state": games[game_id].get_game_state()})

    try:
        while True:
            data = await websocket.receive_json()
            if game_id not in games:
                await websocket.send_json({"type": "error", "message": "Game not found"})
                continue

            game = games[game_id]
            action = data.get('action')

            if action == 'roll_dice':
                dice = game.roll_dice(player_id)
                if dice is not None:
                    await manager.broadcast(game_id, {
                        "type": "dice_rolled", "dice_value": dice, "state": game.get_game_state()
                    })
                else:
                    await websocket.send_json({"type": "error", "message": "Not your turn"})

            elif action == 'move_token':
                token_index = data.get('token_index', -1)
                success = game.move_token(player_id, token_index)
                await manager.broadcast(game_id, {
                    "type": "token_moved", "success": success, "state": game.get_game_state()
                })
                if not success:
                    await websocket.send_json({"type": "error", "message": "Invalid move"})

    except WebSocketDisconnect:
        manager.disconnect(game_id, websocket)
        if game_id in games:
            games[game_id].remove_player(player_id)
            await manager.broadcast(game_id, {"type": "player_left", "state": games[game_id].get_game_state()})

# ========== FRONTEND ROUTE ==========
@app.get("/", response_class=HTMLResponse)
async def read_root():
    html_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Ludo Royale</h1><p>Frontend not found. Please create static/index.html</p>")

if __name__ == "__main__":
    import uvicorn
    print("🎲 Ludo Royale Server running at http://localhost:8000")
    print("📊 Admin Dashboard: http://localhost:8000/admin")
    uvicorn.run(app, host="0.0.0.0", port=8000)