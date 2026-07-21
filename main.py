import os
import re
import socket
import struct
import time
import sqlite3
import asyncio
import datetime
from typing import Optional, List, Tuple, Dict
from dataclasses import dataclass
from dotenv import load_dotenv

import discord
from discord import app_commands, Embed, Colour
from discord.ui import Button, View, Modal, TextInput

# ==============================================================================
# КОНФИГУРАЦИЯ
# ==============================================================================

load_dotenv()

class Config:
    BOT_TOKEN = os.getenv('BOT_TOKEN')
    GUILD_ID = int(os.getenv('GUILD_ID', 0))
    APPLICATION_CHANNEL_ID = int(os.getenv('APPLICATION_CHANNEL_ID', 0))
    REVIEW_CHANNEL_ID = int(os.getenv('REVIEW_CHANNEL_ID', 0))
    LOGS_CHANNEL_ID = int(os.getenv('LOGS_CHANNEL_ID', 0))
    PLAYER_ROLE_ID = int(os.getenv('PLAYER_ROLE_ID', 0))
    ADMIN_ROLES = [int(r) for r in os.getenv('ADMIN_ROLES', '').split(',') if r.strip().isdigit()]

    RCON_HOST = os.getenv('RCON_HOST')
    RCON_PORT = int(os.getenv('RCON_PORT', 25575))
    RCON_PASSWORD = os.getenv('RCON_PASSWORD')

    DB_PATH = os.getenv('DB_PATH', 'applications.db')

    APPLICATION_COOLDOWN_DAYS = 14
    MIN_AGE = 14

config = Config()

# ==============================================================================
# УТИЛИТЫ
# ==============================================================================

def validate_minecraft_nickname(nickname: str) -> bool:
    return bool(re.compile(r'^[a-zA-Z0-9_]{3,16}$').match(nickname))

def validate_age(age: str) -> bool:
    try:
        return int(age) >= 14
    except ValueError:
        return False

def create_application_embed(application, user):
    embed = Embed(
        title=f"Заявка от {user.display_name}",
        description=f"Discord ID: {user.id}",
        color=Colour.blue(),
        timestamp=datetime.datetime.now()
    )
    embed.add_field(name="Ник в Minecraft", value=application.minecraft_nickname, inline=True)
    embed.add_field(name="Возраст", value=application.age, inline=True)
    embed.add_field(name="Микрофон", value=application.has_microphone, inline=True)
    embed.add_field(name="Опыт игры", value=application.experience, inline=False)
    embed.add_field(name="Мотивация и планы", value=application.motivation, inline=False)
    embed.add_field(name="Согласие с правилами", value="Подтверждено", inline=True)
    embed.add_field(name="Заполнено самостоятельно", value="Подтверждено", inline=True)
    embed.add_field(name="Дата подачи", value=application.created_at, inline=True)
    embed.set_footer(text=f"Заявка #{application.application_id}")
    if user.avatar:
        embed.set_thumbnail(url=user.avatar.url)
    return embed

# ==============================================================================
# RCON КЛИЕНТ
# ==============================================================================

class RconClient:
    def __init__(self, host: str, port: int, password: str):
        self.host = host
        self.port = port
        self.password = password
        self.socket = None
        self.auth = False
        self.debug = True

    def log(self, message):
        if self.debug:
            print(f"[RCON {time.strftime('%Y-%m-%d %H:%M:%S')}] {message}")

    def connect(self, max_attempts=3) -> bool:
        for attempt in range(max_attempts):
            try:
                self.log(f"Попытка подключения ({attempt+1}/{max_attempts}): {self.host}:{self.port}")
                self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.socket.settimeout(5)
                self.socket.connect((self.host, self.port))
                self.auth = self._authenticate()
                if self.auth:
                    self.log("RCON подключение успешно")
                    return True
                self.disconnect()
                if attempt < max_attempts - 1:
                    time.sleep(1)
            except (socket.error, ConnectionRefusedError, TimeoutError) as e:
                self.log(f"Ошибка подключения: {e}")
                self.disconnect()
                if attempt < max_attempts - 1:
                    time.sleep(1)
        return False

    def disconnect(self):
        if self.socket:
            try:
                self.socket.close()
            except Exception:
                pass
            finally:
                self.socket = None
        self.auth = False

    def send_command(self, command: str, max_attempts=2) -> Optional[str]:
        for attempt in range(max_attempts):
            if not self.auth or not self.socket:
                if not self.connect():
                    return None
            try:
                self._send_packet(2, command)
                response_type, response_id, response_body = self._receive_packet()
                if response_type == 0:
                    return response_body
                self.disconnect()
                if attempt < max_attempts - 1:
                    time.sleep(0.5)
            except Exception as e:
                self.log(f"Ошибка команды: {e}")
                self.disconnect()
                if attempt < max_attempts - 1:
                    time.sleep(0.5)
        return None

    def _authenticate(self) -> bool:
        if not self.socket:
            return False
        try:
            self._send_packet(3, self.password)
            _, response_id, _ = self._receive_packet()
            return response_id != -1
        except Exception:
            return False

    def _send_packet(self, packet_type: int, packet_body: str):
        packet = struct.pack('<ii', 0, packet_type) + packet_body.encode('utf8') + b'\x00\x00'
        self.socket.sendall(struct.pack('<i', len(packet)) + packet)

    def _receive_packet(self) -> Tuple[int, int, str]:
        length_data = self._receive_all(4)
        if not length_data:
            raise ConnectionError("Потеряно соединение")
        length = struct.unpack('<i', length_data)[0]
        data = self._receive_all(length)
        if not data:
            raise ConnectionError("Потеряно соединение")
        return struct.unpack('<i', data[4:8])[0], struct.unpack('<i', data[0:4])[0], data[8:-2].decode('utf8')

    def _receive_all(self, length: int) -> Optional[bytes]:
        data = b''
        start = time.time()
        while len(data) < length:
            if time.time() - start > 10:
                return None
            try:
                self.socket.settimeout(10 - (time.time() - start))
                chunk = self.socket.recv(length - len(data))
                if not chunk:
                    return None
                data += chunk
            except Exception:
                return None
        return data

    def add_to_whitelist(self, minecraft_nickname: str) -> bool:
        if not self.auth or not self.socket:
            if not self.connect():
                return False
        response = self.send_command(f'noblewl add name "{minecraft_nickname}"')
        if response is None:
            return False
        for phrase in ["added", "успешно", "добавлен", "success", "whitelist"]:
            if phrase.lower() in response.lower():
                return True
        return "error" not in response.lower() and "fail" not in response.lower()

# ==============================================================================
# БАЗА ДАННЫХ
# ==============================================================================

@dataclass
class Application:
    user_id: int
    username: str
    minecraft_nickname: str
    age: int
    experience: str
    has_microphone: str
    motivation: str
    plans: str
    agreed_rules: bool
    filled_manually: bool
    status: str
    created_at: str
    processed_at: Optional[str] = None
    processed_by: Optional[int] = None
    application_id: Optional[int] = None

class Database:
    def __init__(self, db_path: str):
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self):
        self.conn.execute('''
        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            minecraft_nickname TEXT NOT NULL,
            age INTEGER NOT NULL,
            experience TEXT NOT NULL,
            has_microphone TEXT NOT NULL,
            motivation TEXT NOT NULL,
            plans TEXT NOT NULL,
            agreed_rules BOOLEAN NOT NULL,
            filled_manually BOOLEAN NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            processed_at TEXT,
            processed_by INTEGER
        )''')
        self.conn.commit()

    def create_application(self, app: Application) -> int:
        cur = self.conn.execute('''
        INSERT INTO applications
            (user_id, username, minecraft_nickname, age, experience,
             has_microphone, motivation, plans, agreed_rules, filled_manually, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (app.user_id, app.username, app.minecraft_nickname, app.age,
              app.experience, app.has_microphone, app.motivation, app.plans,
              app.agreed_rules, app.filled_manually, app.status, app.created_at))
        self.conn.commit()
        return cur.lastrowid

    def get_application_by_id(self, application_id: int) -> Optional[Application]:
        row = self.conn.execute('SELECT * FROM applications WHERE id = ?', (application_id,)).fetchone()
        if not row:
            return None
        return Application(
            application_id=row['id'], user_id=row['user_id'], username=row['username'],
            minecraft_nickname=row['minecraft_nickname'], age=row['age'],
            experience=row['experience'], has_microphone=row['has_microphone'],
            motivation=row['motivation'], plans=row['plans'],
            agreed_rules=bool(row['agreed_rules']), filled_manually=bool(row['filled_manually']),
            status=row['status'], created_at=row['created_at'],
            processed_at=row['processed_at'], processed_by=row['processed_by']
        )

    def update_application_status(self, application_id: int, status: str, processed_by: int):
        self.conn.execute(
            'UPDATE applications SET status = ?, processed_at = ?, processed_by = ? WHERE id = ?',
            (status, datetime.datetime.now().isoformat(), processed_by, application_id)
        )
        self.conn.commit()

    def can_submit_new_application(self, user_id: int) -> Tuple[bool, Optional[str]]:
        if self.conn.execute(
            'SELECT 1 FROM applications WHERE user_id = ? AND status = ?', (user_id, 'pending')
        ).fetchone():
            return False, "У вас уже есть активная заявка на рассмотрении."
        row = self.conn.execute(
            'SELECT processed_at FROM applications WHERE user_id = ? AND status = ? ORDER BY processed_at DESC LIMIT 1',
            (user_id, 'rejected')
        ).fetchone()
        if row:
            cooldown_end = datetime.datetime.fromisoformat(row[0]) + datetime.timedelta(days=config.APPLICATION_COOLDOWN_DAYS)
            if cooldown_end > datetime.datetime.now():
                return False, f"Вы можете подать новую заявку через {(cooldown_end - datetime.datetime.now()).days + 1} дней."
        return True, None

    def close(self):
        self.conn.close()

# ==============================================================================
# БОТ
# ==============================================================================

db = Database(config.DB_PATH)
rcon = RconClient(config.RCON_HOST, config.RCON_PORT, config.RCON_PASSWORD)

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)


class RulesConfirmationView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="Я согласен с правилами", style=discord.ButtonStyle.primary)
    async def confirm_rules_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ApplicationModal())

    @discord.ui.button(label="Отмена", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Операция отменена.", ephemeral=True)


class ApplicationModal(Modal, title="Заявка на сервер Minecraft"):
    minecraft_nickname = TextInput(
        label="Ник в Minecraft",
        placeholder="Введите ник (3-16 символов, латиница)",
        min_length=3,
        max_length=16,
        required=True
    )
    age = TextInput(
        label="Возраст",
        placeholder="Укажите возраст (минимум 14 лет)",
        min_length=1,
        max_length=3,
        required=True
    )
    experience = TextInput(
        label="Опыт игры",
        placeholder="Сколько играете и на каких серверах? (мин. 200 символов)",
        min_length=200,
        style=discord.TextStyle.paragraph,
        required=True
    )
    has_microphone = TextInput(
        label="Наличие микрофона",
        placeholder="Да / Нет / Планирую приобрести",
        min_length=2,
        max_length=20,
        required=True
    )
    motivation = TextInput(
        label="Мотивация и планы на сервере",
        placeholder="Почему хотите играть и ваши планы (мин. 200 символов)",
        min_length=200,
        style=discord.TextStyle.paragraph,
        required=True
    )

    def __init__(self):
        super().__init__(timeout=None)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        if not validate_minecraft_nickname(self.minecraft_nickname.value):
            await interaction.followup.send(
                "Ник должен содержать только латинские буквы, цифры и подчёркивание (3-16 символов).",
                ephemeral=True
            )
            return

        if not validate_age(self.age.value):
            await interaction.followup.send(
                "Минимальный возраст для игры на сервере — 14 лет.",
                ephemeral=True
            )
            return

        can_submit, error_message = db.can_submit_new_application(interaction.user.id)
        if not can_submit:
            await interaction.followup.send(error_message, ephemeral=True)
            return

        application = Application(
            user_id=interaction.user.id,
            username=interaction.user.name,
            minecraft_nickname=self.minecraft_nickname.value,
            age=int(self.age.value),
            experience=self.experience.value,
            has_microphone=self.has_microphone.value,
            motivation=self.motivation.value,
            plans="",
            agreed_rules=True,
            filled_manually=True,
            status="pending",
            created_at=datetime.datetime.now().isoformat()
        )

        application_id = db.create_application(application)
        application.application_id = application_id

        review_channel = client.get_channel(config.REVIEW_CHANNEL_ID)
        if not review_channel:
            await interaction.followup.send(
                "Ошибка: канал для рассмотрения заявок не найден.", ephemeral=True
            )
            return

        embed = create_application_embed(application, interaction.user)
        view = View(timeout=None)
        view.add_item(Button(style=discord.ButtonStyle.success, label="Одобрить", custom_id=f"approve_{application_id}"))
        view.add_item(Button(style=discord.ButtonStyle.danger, label="Отклонить", custom_id=f"reject_{application_id}"))

        # Ретраи отправки сообщения в канал
        sent = False
        for attempt in range(5):
            try:
                await review_channel.send(embed=embed, view=view)
                sent = True
                break
            except Exception:
                await asyncio.sleep(2)

        if not sent:
            await interaction.followup.send(
                "Не удалось отправить заявку в канал администрации. Попробуйте позже.", ephemeral=True
            )
            return

        await interaction.followup.send(
            "Ваша заявка успешно отправлена и будет рассмотрена администрацией. "
            "Вы получите уведомление о результате в личные сообщения.",
            ephemeral=True
        )


class ApplicationButton(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Подать заявку", style=discord.ButtonStyle.primary, custom_id="application_button")
    async def application_button_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        can_submit, error_message = db.can_submit_new_application(interaction.user.id)
        if not can_submit:
            await interaction.response.send_message(error_message, ephemeral=True)
            return
        embed = discord.Embed(
            title="Подтверждение правил",
            description=(
                "Перед подачей заявки ознакомьтесь с пунктами:\n\n"
                "1. Я прочитал(а) и согласен(на) с правилами сервера.\n"
                "2. Я обязуюсь заполнить заявку самостоятельно без ИИ.\n"
                "3. Заявки, написанные с помощью ИИ, будут отклонены.\n\n"
                "Нажмите «Я согласен с правилами», чтобы продолжить."
            ),
            color=discord.Color.blue()
        )
        await interaction.response.send_message(embed=embed, view=RulesConfirmationView(), ephemeral=True)


@client.event
async def on_ready():
    print(f'Logged in as {client.user}')
    await tree.sync(guild=discord.Object(id=config.GUILD_ID))
    channel = client.get_channel(config.APPLICATION_CHANNEL_ID)
    if channel:
        async for msg in channel.history(limit=50):
            if msg.author == client.user and any(
                hasattr(c, 'custom_id') and c.custom_id == "application_button"
                for row in msg.components for c in row.children
            ):
                print("Application button already exists")
                return
        await channel.send(
            embed=discord.Embed(
                title="Подать заявку на сервер Minecraft",
                description="Нажмите кнопку ниже, чтобы подать заявку.",
                color=discord.Color.blue()
            ),
            view=ApplicationButton()
        )
        print("Created application button")


@client.event
async def on_interaction(interaction: discord.Interaction):
    if interaction.type != discord.InteractionType.component:
        return
    custom_id = interaction.data.get('custom_id', '')
    if not (custom_id.startswith('approve_') or custom_id.startswith('reject_')):
        return

    has_perm = interaction.user.guild_permissions.administrator or any(
        r.id in config.ADMIN_ROLES for r in interaction.user.roles
    )
    if not has_perm:
        await interaction.response.send_message("Нет прав.", ephemeral=True)
        return

    application_id = int(custom_id.split('_')[1])
    application = db.get_application_by_id(application_id)
    if not application:
        await interaction.response.send_message("Заявка не найдена.", ephemeral=True)
        return
    if application.status != 'pending':
        await interaction.response.send_message("Заявка уже обработана.", ephemeral=True)
        return

    if custom_id.startswith('approve_'):
        if not rcon.add_to_whitelist(application.minecraft_nickname):
            await interaction.response.send_message("Ошибка RCON. Проверьте соединение.", ephemeral=True)
            return
        db.update_application_status(application_id, 'approved', interaction.user.id)
        guild = interaction.guild
        if guild:
            member = guild.get_member(application.user_id)
            role = guild.get_role(config.PLAYER_ROLE_ID)
            if member and role:
                try:
                    await member.add_roles(role)
                except discord.Forbidden:
                    pass
        try:
            user = await client.fetch_user(application.user_id)
            await user.send("Ваша заявка одобрена! Вы добавлены в whitelist. Используйте /discord link на сервере.")
        except Exception:
            pass
        logs = client.get_channel(config.LOGS_CHANNEL_ID)
        if logs:
            await logs.send(embed=discord.Embed(
                title="Заявка одобрена",
                description=f"#{application_id} от <@{application.user_id}>",
                color=discord.Color.green(), timestamp=datetime.datetime.now()
            ).add_field(name="Ник", value=application.minecraft_nickname)
             .add_field(name="Одобрил", value=f"<@{interaction.user.id}>"))
        emb = interaction.message.embeds[0]
        emb.color = discord.Color.green()
        emb.title = f"{emb.title} (ОДОБРЕНА)"
        await interaction.message.edit(embed=emb, view=None)
        await interaction.response.send_message(
            f"Заявка {application.username} ({application.minecraft_nickname}) одобрена.", ephemeral=True)

    elif custom_id.startswith('reject_'):
        db.update_application_status(application_id, 'rejected', interaction.user.id)
        try:
            user = await client.fetch_user(application.user_id)
            await user.send("Ваша заявка отклонена. Повторная подача через 14 дней.")
        except Exception:
            pass
        logs = client.get_channel(config.LOGS_CHANNEL_ID)
        if logs:
            await logs.send(embed=discord.Embed(
                title="Заявка отклонена",
                description=f"#{application_id} от <@{application.user_id}>",
                color=discord.Color.red(), timestamp=datetime.datetime.now()
            ).add_field(name="Ник", value=application.minecraft_nickname)
             .add_field(name="Отклонил", value=f"<@{interaction.user.id}>"))
        emb = interaction.message.embeds[0]
        emb.color = discord.Color.red()
        emb.title = f"{emb.title} (ОТКЛОНЕНА)"
        await interaction.message.edit(embed=emb, view=None)
        await interaction.response.send_message(
            f"Заявка {application.username} ({application.minecraft_nickname}) отклонена.", ephemeral=True)


@tree.command(
    name="create_application_button",
    description="Создать кнопку подачи заявки",
    guild=discord.Object(id=config.GUILD_ID)
)
@app_commands.checks.has_permissions(administrator=True)
async def create_application_button_cmd(interaction: discord.Interaction, channel: discord.TextChannel = None):
    target = channel or interaction.channel
    await target.send(
        embed=discord.Embed(
            title="Подать заявку на сервер Minecraft",
            description="Нажмите кнопку ниже, чтобы подать заявку.",
            color=discord.Color.blue()
        ),
        view=ApplicationButton()
    )
    await interaction.response.send_message(f"Кнопка создана в {target.mention}", ephemeral=True)


if __name__ == '__main__':
    if not config.BOT_TOKEN:
        print("Ошибка: BOT_TOKEN не задан.")
    else:
        client.run(config.BOT_TOKEN)