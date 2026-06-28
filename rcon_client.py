import socket
import struct
import time
from typing import Optional, Tuple

class RconClient:
    def __init__(self, host: str, port: int, password: str):
        """
        Инициализация RCON клиента
        
        Args:
            host: IP-адрес или hostname Minecraft сервера
            port: Порт RCON (обычно 25575)
            password: Пароль RCON, указанный в server.properties
        """
        self.host = host
        self.port = port
        self.password = password
        self.socket = None
        self.auth = False
        self.debug = True  # Включает подробное логирование
    
    def log(self, message):
        """Логирование с метками времени, если включен режим отладки"""
        if self.debug:
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
            print(f"[RCON {timestamp}] {message}")
    
    def connect(self, max_attempts=3) -> bool:
        """
        Подключиться к RCON серверу с несколькими попытками
        
        Args:
            max_attempts: Максимальное количество попыток подключения
            
        Returns:
            bool: True если подключение успешно, иначе False
        """
        for attempt in range(max_attempts):
            try:
                self.log(f"Попытка подключения к RCON ({attempt+1}/{max_attempts}): {self.host}:{self.port}")
                self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.socket.settimeout(5)  # Таймаут 5 секунд
                self.socket.connect((self.host, self.port))
                self.auth = self._authenticate()
                if self.auth:
                    self.log(f"RCON подключение успешно установлено")
                    return True
                self.log(f"Аутентификация не удалась (попытка {attempt+1}/{max_attempts})")
                # Если аутентификация не удалась, закрываем соединение и повторяем
                self.disconnect()
                if attempt < max_attempts - 1:  # Не ждем после последней попытки
                    time.sleep(1)  # Ждем секунду перед повторной попыткой
            except (socket.error, ConnectionRefusedError, TimeoutError) as e:
                self.log(f"Ошибка подключения: {e} (попытка {attempt+1}/{max_attempts})")
                self.disconnect()
                if attempt < max_attempts - 1:  # Не ждем после последней попытки
                    time.sleep(1)  # Ждем секунду перед повторной попыткой
        
        self.log(f"Не удалось подключиться после {max_attempts} попыток")
        return False
    
    def disconnect(self):
        """
        Закрыть соединение с RCON сервером
        """
        if self.socket:
            try:
                self.socket.close()
                self.log("Соединение закрыто")
            except Exception as e:
                self.log(f"Ошибка при закрытии соединения: {e}")
            finally:
                self.socket = None
        self.auth = False
    
    def send_command(self, command: str, max_attempts=2) -> Optional[str]:
        """
        Отправить команду на RCON сервер с повторными попытками
        
        Args:
            command: Команда для отправки на сервер
            max_attempts: Максимальное количество попыток
            
        Returns:
            Optional[str]: Ответ сервера или None в случае ошибки
        """
        for attempt in range(max_attempts):
            if not self.auth or not self.socket:
                self.log("Переподключение перед отправкой команды...")
                if not self.connect():
                    self.log(f"Не удалось подключиться для отправки команды: {command}")
                    return None
            
            try:
                self.log(f"Отправка команды: {command}")
                self._send_packet(2, command)
                response_type, response_id, response_body = self._receive_packet()
                
                self.log(f"Получен ответ: [{response_type}] {response_body}")
                
                if response_type == 0:
                    return response_body
                else:
                    self.log(f"Неожиданный тип ответа: {response_type}")
                    self.disconnect()
                    if attempt < max_attempts - 1:
                        time.sleep(0.5)
                    continue
            except Exception as e:
                self.log(f"Ошибка при отправке команды: {e}")
                self.disconnect()
                if attempt < max_attempts - 1:
                    time.sleep(0.5)
                continue
        
        return None
    
    def _authenticate(self) -> bool:
        """
        Аутентификация на RCON сервере
        
        Returns:
            bool: True если аутентификация успешна, иначе False
        """
        if not self.socket:
            self.log("Попытка аутентификации без подключения")
            return False
        
        try:
            self.log(f"Отправка запроса аутентификации")
            # Send auth packet with password
            self._send_packet(3, self.password)
            response_type, response_id, response_body = self._receive_packet()
            
            success = response_id != -1
            self.log(f"Аутентификация {'успешна' if success else 'не удалась'}")
            return success
        except Exception as e:
            self.log(f"Ошибка аутентификации: {e}")
            return False
    
    def _send_packet(self, packet_type: int, packet_body: str) -> None:
        """
        Отправить RCON пакет
        
        Args:
            packet_type: Тип пакета (2=команда, 3=аутентификация)
            packet_body: Содержимое пакета (команда или пароль)
        """
        # Packet structure: ID (4) + Type (4) + Body + null terminator (1) + null terminator (1)
        packet_id = 0
        packet = struct.pack('<ii', packet_id, packet_type) + packet_body.encode('utf8') + b'\x00\x00'
        packet_length = len(packet)
        
        # Отправляем длину пакета, затем сам пакет
        try:
            self.socket.sendall(struct.pack('<i', packet_length) + packet)
        except Exception as e:
            self.log(f"Ошибка при отправке пакета: {e}")
            raise
    
    def _receive_packet(self) -> Tuple[int, int, str]:
        """
        Получить ответный RCON пакет
        
        Returns:
            Tuple[int, int, str]: (тип_пакета, id_пакета, содержимое)
        """
        # Read packet length
        packet_length_data = self._receive_all(4)
        if not packet_length_data:
            self.log("Не удалось получить длину пакета")
            raise ConnectionError("RCON соединение потеряно при получении длины пакета")
        
        packet_length = struct.unpack('<i', packet_length_data)[0]
        self.log(f"Получен пакет длиной {packet_length} байт")
        
        # Read packet content
        packet_data = self._receive_all(packet_length)
        if not packet_data:
            self.log("Не удалось получить данные пакета")
            raise ConnectionError("RCON соединение потеряно при получении данных пакета")
        
        # Extract packet components
        packet_id = struct.unpack('<i', packet_data[0:4])[0]
        packet_type = struct.unpack('<i', packet_data[4:8])[0]
        
        # Extract body (remove the two null terminators)
        packet_body = packet_data[8:-2].decode('utf8')
        
        return packet_type, packet_id, packet_body
    
    def _receive_all(self, length: int) -> Optional[bytes]:
        """
        Получить указанное количество байт из сокета
        
        Args:
            length: Количество байт для чтения
            
        Returns:
            Optional[bytes]: Прочитанные данные или None в случае ошибки
        """
        data = b''
        start_time = time.time()
        timeout = 10  # 10 секунд максимальное время чтения
        
        while len(data) < length:
            if time.time() - start_time > timeout:
                self.log(f"Превышено время ожидания при получении данных")
                return None
                
            try:
                self.socket.settimeout(timeout - (time.time() - start_time))
                packet = self.socket.recv(length - len(data))
                if not packet:
                    self.log("Соединение закрыто сервером при получении данных")
                    return None
                data += packet
            except socket.timeout:
                self.log("Таймаут при получении данных")
                return None
            except Exception as e:
                self.log(f"Ошибка при получении данных: {e}")
                return None
                
        return data

    def add_to_whitelist(self, minecraft_nickname: str) -> bool:
        """
        Добавить игрока в белый список через команду noblewl

        Args:
            minecraft_nickname: Ник игрока для добавления

        Returns:
            bool: True если игрок успешно добавлен, иначе False
        """
        # Проверяем соединение перед отправкой команды
        if not self.auth or not self.socket:
            self.log(f"Переподключение перед добавлением в whitelist...")
            if not self.connect():
                self.log(f"Не удалось подключиться для добавления в whitelist")
                return False

        # Формируем и отправляем команду noblewl add name
        command = f'noblewl add name "{minecraft_nickname}"'
        self.log(f"Добавление игрока в whitelist: {minecraft_nickname}")
        self.log(f"Отправка команды: {command}")

        response = self.send_command(command)
        self.log(f"Ответ на noblewl add: {response}")

        if response is None:
            self.log("Нет ответа от сервера")
            return False

        # Проверяем различные варианты успешных ответов
        success_phrases = [
            "added",
            "успешно",
            "добавлен",
            "success",
            "whitelist",
        ]

        for phrase in success_phrases:
            if phrase.lower() in response.lower():
                self.log(f"Игрок {minecraft_nickname} успешно добавлен в whitelist")
                return True

        # Если команда выполнилась без ошибок (пустой ответ или без ошибок)
        if "error" not in response.lower() and "fail" not in response.lower():
            self.log(f"Игрок {minecraft_nickname} успешно добавлен в whitelist")
            return True

        self.log(f"Неожиданный ответ при добавлении в whitelist: {response}")
        return False
    
    def test_connection(self) -> bool:
        """
        Проверить соединение с RCON сервером
        
        Returns:
            bool: True если соединение работает, иначе False
        """
        self.log("Тестирование RCON соединения...")
        
        # Сначала проверяем, можем ли мы подключиться
        if not self.connect():
            self.log("Тест соединения: не удалось подключиться")
            return False
            
        # Затем пробуем простую команду
        response = self.send_command("list")
        success = response is not None
        
        self.log(f"Тест соединения: {'успешно' if success else 'не удалось'}")
        if success:
            self.log(f"Ответ сервера: {response}")
            
        return success
