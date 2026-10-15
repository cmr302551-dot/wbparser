"""
WB Automation — FastAPI Web Server

Запуск: python run_web.py
Открыть: http://127.0.0.1:8000
"""

import asyncio
import json
import os
import random
import shutil
import sys
import uuid
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# Пути
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.core.database import Database
from src.telegram.auth import TelegramAuth
from src.telegram.proxy import ProxyConfig
from src.wb.client import WBClientManager
from src.wb.collector import WBCollector, _sellers_file_lock
from src.wb.probiv import probiv_seller
from src.whatsapp.client import WhatsAppClientManager, WA_PROFILES_DIR

DATA_DIR = ROOT / 'data' / 'wb'
TASKS_FILE = DATA_DIR / 'tasks.json'
SELLERS_FILE = DATA_DIR / 'sellers.json'
ACCOUNTS_FILE = DATA_DIR / 'accounts.json'
BROADCAST_MEDIA_DIR = DATA_DIR / 'broadcast_media'
STATIC_DIR = ROOT / 'web' / 'static'

DATA_DIR.mkdir(parents=True, exist_ok=True)
BROADCAST_MEDIA_DIR.mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────
# Глобальное состояние
#
# Несколько задач могут работать одновременно — каждая в своей вкладке (Page)
# одного общего браузерного контекста (один профиль = один браузер на всех).
# ─────────────────────────────────────────────

class TaskRunner:
    """Состояние одной запущенной задачи сбора."""

    def __init__(self, task_id: str, name: str):
        self.task_id = task_id
        self.name = name
        self.stop_event: asyncio.Event = asyncio.Event()
        self.log_buffer: deque = deque(maxlen=500)
        self.collector: Optional[WBCollector] = None
        self.page = None
        self.async_task: Optional[asyncio.Task] = None


class RunnerState:
    def __init__(self):
        self.running: Dict[str, TaskRunner] = {}
        self.ws_queues: List[asyncio.Queue] = []
        # Общий браузер на все задачи (один профиль, разные вкладки)
        self.browser_client: Optional[WBClientManager] = None
        self.browser_lock: asyncio.Lock = asyncio.Lock()
        # Активные пробивы по задачам: task_id -> {"stop_event", "remaining", "workers"}
        self.probiv_runners: Dict[str, dict] = {}
        # Активные рассылки WhatsApp по задачам (та же структура, что у пробива)
        self.broadcast_runners: Dict[str, dict] = {}

state = RunnerState()

# Лок на чтение/запись общих JSON-файлов (sellers.json, tasks.json, accounts.json) —
# несколько задач могут писать в них одновременно
_json_file_lock = asyncio.Lock()

# База данных
db: Optional[Database] = None

# Ожидающие авторизации {session_id: {"auth": TelegramAuth, "phone": str, "proxy": ProxyConfig|None}}
_pending_auth: Dict[str, dict] = {}

# Открытые окна WhatsApp Web: account_id -> WhatsAppClientManager
_wa_windows: Dict[int, WhatsAppClientManager] = {}
_wa_lock: asyncio.Lock = asyncio.Lock()


# ─────────────────────────────────────────────
# Helpers: чтение/запись JSON
# ─────────────────────────────────────────────

def _read_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            pass
    return default

def _write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

# ─────────────────────────────────────────────
# Логи и статусы (рассылка через WebSocket с привязкой к task_id)
# ─────────────────────────────────────────────

def _push_log(msg: str, task_id: str, source: str = 'parse'):
    runner = state.running.get(task_id)
    if runner:
        runner.log_buffer.append((source, msg))
    payload = json.dumps({"type": "log", "task_id": task_id, "text": msg, "source": source})
    for q in list(state.ws_queues):
        q.put_nowait(payload)

def _push_status(task_id: str, status: str):
    payload = json.dumps({"type": "status", "task_id": task_id, "status": status})
    for q in list(state.ws_queues):
        q.put_nowait(payload)

def _push_probiv_status(task_id: str, status: str):
    payload = json.dumps({"type": "probiv_status", "task_id": task_id, "status": status})
    for q in list(state.ws_queues):
        q.put_nowait(payload)

def _push_broadcast_status(task_id: str, status: str):
    payload = json.dumps({"type": "broadcast_status", "task_id": task_id, "status": status})
    for q in list(state.ws_queues):
        q.put_nowait(payload)


# ─────────────────────────────────────────────
# Общий браузер: лениво запускается при первой задаче,
# закрывается, когда не осталось работающих задач
# ─────────────────────────────────────────────

async def _get_browser_client() -> WBClientManager:
    async with state.browser_lock:
        if state.browser_client is None:
            client = WBClientManager()
            await client.connect()
            state.browser_client = client
        return state.browser_client

async def _maybe_close_browser():
    async with state.browser_lock:
        if state.browser_client and not state.running:
            try:
                await state.browser_client.disconnect()
            except Exception:
                pass
            state.browser_client = None


# ─────────────────────────────────────────────
# Запуск коллектора как асинхронной задачи (своя вкладка в общем браузере)
# ─────────────────────────────────────────────

async def _run_collector_async(task: dict, runner: TaskRunner):
    task_id = task['id']
    try:
        _push_log(f"=== Запуск задачи: {task['name']} ===", task_id)

        client = await _get_browser_client()
        page = await client.new_page()
        runner.page = page

        collector = WBCollector(
            page=page,
            task_id=task_id,
            log_callback=_push_log
        )
        runner.collector = collector

        async def _watch_stop():
            await runner.stop_event.wait()
            collector.stop_requested = True

        watcher = asyncio.create_task(_watch_stop())

        query_configs = [
            {
                'query': q['query'],
                'kolvo_for_query': q.get('kolvo_for_query', 20),
                'stroke_position': q.get('stroke_position', 1),
                'proverka': q.get('proverka', None),
            }
            for q in task.get('queries', [])
        ]

        await collector.collect(query_configs=query_configs)

        watcher.cancel()
        try:
            await page.close()
        except Exception:
            pass

        _push_log("=== Сбор завершён ===", task_id)

    except Exception as e:
        _push_log(f"❌ Ошибка: {e}", task_id)
        import traceback
        _push_log(traceback.format_exc(), task_id)
    finally:
        state.running.pop(task_id, None)
        _push_status(task_id, 'idle')
        await _maybe_close_browser()


# ─────────────────────────────────────────────
# Lifespan
# ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global db
    db = Database()
    await db.connect()
    try:
        yield
    finally:
        # Останавливаем все работающие задачи и закрываем браузер
        for runner in list(state.running.values()):
            runner.stop_event.set()
        for runner in list(state.running.values()):
            if runner.async_task:
                try:
                    await runner.async_task
                except Exception:
                    pass
        if state.browser_client:
            try:
                await asyncio.wait_for(state.browser_client.disconnect(), timeout=10)
            except Exception:
                pass
            state.browser_client = None
        # Закрываем все открытые окна WhatsApp (с таймаутом-бэкстопом, чтобы
        # зависший на мёртвом прокси браузер не блокировал остановку сервера)
        for manager in list(_wa_windows.values()):
            try:
                await asyncio.wait_for(manager.disconnect(), timeout=12)
            except Exception:
                pass
        _wa_windows.clear()
        try:
            await db.close()
        except Exception:
            pass

app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ─────────────────────────────────────────────
# Serve index.html
# ─────────────────────────────────────────────

@app.get("/")
async def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


# ─────────────────────────────────────────────
# WebSocket — стрим логов
# ─────────────────────────────────────────────

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    q: asyncio.Queue = asyncio.Queue()
    state.ws_queues.append(q)

    # Отправляем буфер логов и статус "running" для каждой работающей задачи
    for runner in list(state.running.values()):
        for msg_source, msg in list(runner.log_buffer):
            await ws.send_text(json.dumps({"type": "log", "task_id": runner.task_id, "text": msg, "source": msg_source}))
        await ws.send_text(json.dumps({"type": "status", "task_id": runner.task_id, "status": "running"}))

    try:
        while True:
            payload = await q.get()
            await ws.send_text(payload)
    except (WebSocketDisconnect, Exception, BaseException):
        pass
    finally:
        if q in state.ws_queues:
            state.ws_queues.remove(q)


# ─────────────────────────────────────────────
# Tasks API
# ─────────────────────────────────────────────

@app.get("/api/tasks")
async def get_tasks():
    tasks = _read_json(TASKS_FILE, [])
    # Добавляем количество продавцов в каждую задачу
    sellers = _read_json(SELLERS_FILE, [])
    counts: Dict[str, int] = {}
    for s in sellers:
        tid = s.get('task_id', '')
        counts[tid] = counts.get(tid, 0) + 1
    for t in tasks:
        t['sellers_count'] = counts.get(t['id'], 0)
    return tasks


@app.post("/api/tasks")
async def create_task(body: dict):
    task = {
        "id": str(uuid.uuid4())[:8],
        "name": body.get("name", "Новая задача"),
        "queries": body.get("queries", []),
        "probiv_account_ids": body.get("probiv_account_ids", []),
        "broadcast_account_ids": body.get("broadcast_account_ids", []),
        "broadcast_message": body.get("broadcast_message", ""),
        "broadcast_media": body.get("broadcast_media", ""),
        "broadcast_daily_limit": body.get("broadcast_daily_limit", 30),
        "broadcast_min_delay": body.get("broadcast_min_delay", 4),
        "broadcast_max_delay": body.get("broadcast_max_delay", 9),
        "created": datetime.now().strftime("%Y-%m-%d"),
    }
    async with _json_file_lock:
        tasks = _read_json(TASKS_FILE, [])
        tasks.append(task)
        _write_json(TASKS_FILE, tasks)
    return task


@app.put("/api/tasks/{task_id}")
async def update_task(task_id: str, body: dict):
    async with _json_file_lock:
        tasks = _read_json(TASKS_FILE, [])
        for t in tasks:
            if t["id"] == task_id:
                t["name"] = body.get("name", t["name"])
                t["queries"] = body.get("queries", t.get("queries", []))
                t["probiv_account_ids"] = body.get("probiv_account_ids", t.get("probiv_account_ids", []))
                t["broadcast_account_ids"] = body.get("broadcast_account_ids", t.get("broadcast_account_ids", []))
                t["broadcast_message"] = body.get("broadcast_message", t.get("broadcast_message", ""))
                t["broadcast_media"] = body.get("broadcast_media", t.get("broadcast_media", ""))
                t["broadcast_daily_limit"] = body.get("broadcast_daily_limit", t.get("broadcast_daily_limit", 30))
                t["broadcast_min_delay"] = body.get("broadcast_min_delay", t.get("broadcast_min_delay", 4))
                t["broadcast_max_delay"] = body.get("broadcast_max_delay", t.get("broadcast_max_delay", 9))
                _write_json(TASKS_FILE, tasks)
                return t
    return JSONResponse({"error": "Task not found"}, status_code=404)


@app.delete("/api/tasks/{task_id}")
async def delete_task(task_id: str):
    if task_id in state.running:
        return JSONResponse({"error": "Сначала остановите задачу"}, status_code=400)
    async with _json_file_lock:
        tasks = _read_json(TASKS_FILE, [])
        tasks = [t for t in tasks if t["id"] != task_id]
        _write_json(TASKS_FILE, tasks)
    return {"ok": True}


# ─────────────────────────────────────────────
# Start / Stop
# ─────────────────────────────────────────────

@app.post("/api/tasks/{task_id}/start")
async def start_task(task_id: str):
    if task_id in state.running:
        return JSONResponse({"error": "Эта задача уже запущена"}, status_code=400)

    tasks = _read_json(TASKS_FILE, [])
    task = next((t for t in tasks if t["id"] == task_id), None)
    if not task:
        return JSONResponse({"error": "Задача не найдена"}, status_code=404)
    if not task.get("queries"):
        return JSONResponse({"error": "Нет поисковых запросов"}, status_code=400)

    runner = TaskRunner(task_id, task['name'])
    state.running[task_id] = runner

    # Уведомляем WebSocket клиентов
    _push_status(task_id, 'running')

    runner.async_task = asyncio.create_task(_run_collector_async(task, runner))

    return {"ok": True, "status": "running"}


@app.post("/api/tasks/{task_id}/stop")
async def stop_task(task_id: str):
    runner = state.running.get(task_id)
    if not runner:
        return JSONResponse({"error": "Эта задача не запущена"}, status_code=400)
    runner.stop_event.set()
    _push_log("⏹ Остановка запрошена...", task_id)
    return {"ok": True}


@app.get("/api/status")
async def get_status():
    return {"running_tasks": list(state.running.keys())}


# ─────────────────────────────────────────────
# Sellers API
# ─────────────────────────────────────────────

SELLER_DEFAULTS = {
    "query": "", "task_id": "", "seller_url": "", "product_url": "",
    "date": "", "phone": "", "channel": "", "status": "В ожидании",
    "account": "", "last_name": "", "name": "", "surname": "", "patronymic": "", "ogrnip": "", "telegram": "",
}

def _normalize_seller(s: dict) -> dict:
    result = {**SELLER_DEFAULTS, **s}
    # Нормализуем регистр статуса (старые записи могут иметь "в ожидании")
    if result.get("status") == "в ожидании":
        result["status"] = "В ожидании"
    return result


@app.get("/api/sellers")
async def get_sellers(
    task_id: Optional[str] = None,
    query: Optional[str] = None,
    date: Optional[str] = None,
    status: Optional[str] = None,
):
    sellers = [_normalize_seller(s) for s in _read_json(SELLERS_FILE, [])]
    if task_id:
        sellers = [s for s in sellers if s.get('task_id') == task_id]
    if query:
        sellers = [s for s in sellers if s.get('query') == query]
    if date:
        sellers = [s for s in sellers if s.get('date') == date]
    if status:
        sellers = [s for s in sellers if (s.get('status') or '').lower() == status.lower()]
    return sellers


@app.patch("/api/sellers/{inn}")
async def update_seller(inn: str, body: dict):
    allowed = {'phone', 'status', 'account'}
    async with _sellers_file_lock:
        sellers = _read_json(SELLERS_FILE, [])
        updated = False
        for s in sellers:
            if s.get('inn') == inn:
                for key in allowed:
                    if key in body:
                        s[key] = body[key]
                updated = True
                break
        if not updated:
            return JSONResponse({"error": "Продавец не найден"}, status_code=404)
        _write_json(SELLERS_FILE, sellers)
    return {"ok": True}


@app.delete("/api/sellers/{inn}")
async def delete_seller(inn: str):
    async with _sellers_file_lock:
        sellers = _read_json(SELLERS_FILE, [])
        before = len(sellers)
        sellers = [s for s in sellers if s.get('inn') != inn]
        if len(sellers) == before:
            return JSONResponse({"error": "Продавец не найден"}, status_code=404)
        _write_json(SELLERS_FILE, sellers)
    return {"ok": True}


# ─────────────────────────────────────────────
# Accounts API (простые метки для дропдауна "с какого аккаунта связывался")
# ─────────────────────────────────────────────

@app.get("/api/accounts")
async def get_accounts():
    return _read_json(ACCOUNTS_FILE, [])


@app.post("/api/accounts")
async def add_account(body: dict):
    phone = body.get("phone", "").strip()
    if not phone:
        return JSONResponse({"error": "Номер не указан"}, status_code=400)
    async with _json_file_lock:
        accounts = _read_json(ACCOUNTS_FILE, [])
        if phone not in accounts:
            accounts.append(phone)
            _write_json(ACCOUNTS_FILE, accounts)
    return accounts


@app.delete("/api/accounts/{phone}")
async def delete_account(phone: str):
    async with _json_file_lock:
        accounts = _read_json(ACCOUNTS_FILE, [])
        accounts = [a for a in accounts if a != phone]
        _write_json(ACCOUNTS_FILE, accounts)
    return accounts


# ─────────────────────────────────────────────
# TG Accounts API — полное управление с авторизацией
# ─────────────────────────────────────────────

@app.get("/api/tg-accounts")
async def get_tg_accounts():
    if not db:
        return []
    rows = await db.list_accounts()
    result = []
    for acc in rows:
        proxy_str = None
        if acc.get('proxy_id'):
            proxy = await db.get_proxy(acc['proxy_id'])
            if proxy:
                proxy_str = f"{proxy['address']}:{proxy['port']}"
        result.append({
            'id': acc['id'],
            'phone': acc['phone'],
            'username': acc.get('username') or '',
            'first_name': acc.get('first_name') or '',
            'last_name': acc.get('last_name') or '',
            'is_active': bool(acc.get('is_active', 1)),
            'proxy': proxy_str,
        })
    return result


@app.post("/api/tg-accounts/start-auth")
async def tg_start_auth(body: dict):
    phone = body.get("phone", "").strip()
    if not phone:
        return JSONResponse({"error": "Номер телефона не указан"}, status_code=400)

    proxy = None
    proxy_address = (body.get("proxy_address") or "").strip()
    if proxy_address:
        try:
            proxy = ProxyConfig(
                address=proxy_address,
                port=int(body.get("proxy_port") or 0),
                username=body.get("proxy_username") or None,
                password=body.get("proxy_password") or None,
                proxy_type=body.get("proxy_type") or "socks5",
            )
        except Exception as e:
            return JSONResponse({"error": f"Ошибка настроек прокси: {e}"}, status_code=400)

    auth = TelegramAuth(phone, proxy)
    try:
        await asyncio.wait_for(auth.connect(), timeout=15)
    except asyncio.TimeoutError:
        try:
            await auth.disconnect()
        except Exception:
            pass
        return JSONResponse({"error": "Не удалось подключиться — проверьте прокси (таймаут 15 сек)"}, status_code=400)
    except Exception as e:
        try:
            await auth.disconnect()
        except Exception:
            pass
        return JSONResponse({"error": str(e)}, status_code=500)

    try:
        # Аккаунт уже авторизован (сессия жива)
        if await auth.is_authorized():
            me = await auth.client.get_me()
            proxy_id = None
            if proxy:
                proxy_id = await db.add_proxy(
                    address=proxy.address, port=proxy.port,
                    username=proxy.username, password=proxy.password,
                    proxy_type=proxy.proxy_type,
                )
            existing = await db.get_account_by_phone(phone)
            if existing:
                await db.update_account_info(existing['id'],
                                              username=me.username or '',
                                              first_name=me.first_name or '',
                                              last_name=me.last_name or '')
            else:
                await db.add_account(phone, proxy_id,
                                      username=me.username or '',
                                      first_name=me.first_name or '',
                                      last_name=me.last_name or '')
            await auth.disconnect()
            return {"status": "already_authorized",
                    "first_name": me.first_name or '',
                    "username": me.username or ''}

        # Отправляем SMS-код
        await auth.client.send_code_request(phone)
        session_id = str(uuid.uuid4())
        _pending_auth[session_id] = {"auth": auth, "phone": phone, "proxy": proxy}
        return {"status": "code_sent", "session_id": session_id}

    except Exception as e:
        try:
            await auth.disconnect()
        except Exception:
            pass
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/tg-accounts/confirm-code")
async def tg_confirm_code(body: dict):
    session_id = (body.get("session_id") or "").strip()
    code = (body.get("code") or "").strip()

    if session_id not in _pending_auth:
        return JSONResponse({"error": "Сессия не найдена или истекла"}, status_code=400)
    if not code:
        return JSONResponse({"error": "Код не указан"}, status_code=400)

    pending = _pending_auth[session_id]
    auth: TelegramAuth = pending["auth"]
    phone: str = pending["phone"]
    proxy: Optional[ProxyConfig] = pending["proxy"]

    try:
        from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError, PhoneCodeExpiredError

        await auth.client.sign_in(phone, code)

        # Успех — сохраняем в БД
        me = await auth.client.get_me()
        proxy_id = None
        if proxy:
            proxy_id = await db.add_proxy(
                address=proxy.address, port=proxy.port,
                username=proxy.username, password=proxy.password,
                proxy_type=proxy.proxy_type,
            )
        existing = await db.get_account_by_phone(phone)
        if existing:
            await db.update_account_info(existing['id'],
                                          username=me.username or '',
                                          first_name=me.first_name or '',
                                          last_name=me.last_name or '')
        else:
            await db.add_account(phone, proxy_id,
                                  username=me.username or '',
                                  first_name=me.first_name or '',
                                  last_name=me.last_name or '')
        del _pending_auth[session_id]
        await auth.disconnect()
        return {"status": "ok", "first_name": me.first_name or '', "username": me.username or ''}

    except SessionPasswordNeededError:
        # 2FA — не удаляем pending_auth, ждём пароль
        return {"status": "need_password"}

    except (PhoneCodeInvalidError,):
        return JSONResponse({"error": "Неверный код подтверждения"}, status_code=400)

    except Exception as e:
        error_msg = str(e)
        if 'PHONE_CODE_INVALID' in error_msg:
            return JSONResponse({"error": "Неверный код подтверждения"}, status_code=400)
        if 'PHONE_CODE_EXPIRED' in error_msg:
            return JSONResponse({"error": "Код истёк, запросите новый"}, status_code=400)
        return JSONResponse({"error": error_msg}, status_code=500)


@app.post("/api/tg-accounts/confirm-password")
async def tg_confirm_password(body: dict):
    session_id = (body.get("session_id") or "").strip()
    password = (body.get("password") or "").strip()

    if session_id not in _pending_auth:
        return JSONResponse({"error": "Сессия не найдена или истекла"}, status_code=400)
    if not password:
        return JSONResponse({"error": "Пароль не указан"}, status_code=400)

    pending = _pending_auth[session_id]
    auth: TelegramAuth = pending["auth"]
    phone: str = pending["phone"]
    proxy: Optional[ProxyConfig] = pending["proxy"]

    try:
        await auth.client.sign_in(password=password)

        me = await auth.client.get_me()
        proxy_id = None
        if proxy:
            proxy_id = await db.add_proxy(
                address=proxy.address, port=proxy.port,
                username=proxy.username, password=proxy.password,
                proxy_type=proxy.proxy_type,
            )
        existing = await db.get_account_by_phone(phone)
        if existing:
            await db.update_account_info(existing['id'],
                                          username=me.username or '',
                                          first_name=me.first_name or '',
                                          last_name=me.last_name or '')
        else:
            await db.add_account(phone, proxy_id,
                                  username=me.username or '',
                                  first_name=me.first_name or '',
                                  last_name=me.last_name or '')
        del _pending_auth[session_id]
        await auth.disconnect()
        return {"status": "ok", "first_name": me.first_name or '', "username": me.username or ''}

    except Exception as e:
        error_msg = str(e)
        if 'PASSWORD_HASH_INVALID' in error_msg or 'PasswordHashInvalidError' in error_msg:
            return JSONResponse({"error": "Неверный пароль"}, status_code=400)
        return JSONResponse({"error": error_msg}, status_code=500)


@app.patch("/api/tg-accounts/{account_id}/proxy")
async def update_tg_account_proxy(account_id: int, body: dict):
    if not db:
        return JSONResponse({"error": "БД не инициализирована"}, status_code=500)
    account = await db.get_account(account_id)
    if not account:
        return JSONResponse({"error": "Аккаунт не найден"}, status_code=404)

    proxy_address = (body.get("proxy_address") or "").strip()
    if not proxy_address:
        return JSONResponse({"error": "Адрес прокси не указан"}, status_code=400)

    try:
        proxy = ProxyConfig(
            address=proxy_address,
            port=int(body.get("proxy_port") or 0),
            username=body.get("proxy_username") or None,
            password=body.get("proxy_password") or None,
            proxy_type=body.get("proxy_type") or "socks5",
        )
    except Exception as e:
        return JSONResponse({"error": f"Ошибка настроек прокси: {e}"}, status_code=400)

    proxy_id = await db.add_proxy(
        address=proxy.address, port=proxy.port,
        username=proxy.username, password=proxy.password,
        proxy_type=proxy.proxy_type,
    )
    await db.update_account_proxy(account_id, proxy_id)
    return {"ok": True}


@app.delete("/api/tg-accounts/{account_id}")
async def delete_tg_account(account_id: int):
    if not db:
        return JSONResponse({"error": "БД не инициализирована"}, status_code=500)
    account = await db.get_account(account_id)
    if not account:
        return JSONResponse({"error": "Аккаунт не найден"}, status_code=404)

    # Удаляем файл сессии
    from src.core.config import Config
    session_path = Config.SESSIONS_DIR / f"{account['session_name']}.session"
    if session_path.exists():
        session_path.unlink()

    await db.delete_account(account_id)
    return {"ok": True}


# ─────────────────────────────────────────────
# WhatsApp Accounts API — отдельное окно браузера на аккаунт (профиль + прокси)
# ─────────────────────────────────────────────

def _wa_account_view(acc: dict, proxy_str: str, sent_today: int = 0) -> dict:
    return {
        'id': acc['id'],
        'name': acc.get('name') or '',
        'phone': acc.get('phone') or '',
        'proxy': proxy_str,
        'open': acc['id'] in _wa_windows,
        'logged_in': bool(acc.get('logged_in', 0)),
        'sent_today': sent_today,
    }


async def _open_wa_window(account_id: int) -> WhatsAppClientManager:
    """Открыть (или поднять) окно WhatsApp Web для аккаунта."""
    async with _wa_lock:
        existing = _wa_windows.get(account_id)
        if existing and existing.is_open:
            await existing.bring_to_front()
            return existing

        acc = await db.get_whatsapp_account(account_id)
        if not acc:
            raise RuntimeError("Аккаунт не найден")
        proxy = await db.get_proxy(acc['proxy_id'])
        if not proxy:
            raise RuntimeError("Прокси аккаунта не найден")

        manager = WhatsAppClientManager(
            profile_dir=acc['profile_dir'],
            proxy_address=proxy['address'], proxy_port=proxy['port'],
            proxy_username=proxy.get('username'), proxy_password=proxy.get('password'),
            proxy_type=proxy.get('proxy_type', 'socks5'),
        )

        def _on_close(aid=account_id):
            _wa_windows.pop(aid, None)

        await manager.open(on_close=_on_close)
        _wa_windows[account_id] = manager
        return manager


@app.get("/api/wa-accounts")
async def get_wa_accounts():
    if not db:
        return []
    rows = await db.list_whatsapp_accounts()
    sent_map = await db.wa_sent_today_all()
    result = []
    for acc in rows:
        proxy_str = ''
        if acc.get('proxy_id'):
            proxy = await db.get_proxy(acc['proxy_id'])
            if proxy:
                proxy_str = f"{proxy['address']}:{proxy['port']}"
        result.append(_wa_account_view(acc, proxy_str, sent_map.get(acc['id'], 0)))
    return result


@app.post("/api/wa-accounts/add")
async def add_wa_account(body: dict):
    if not db:
        return JSONResponse({"error": "БД не инициализирована"}, status_code=500)

    name = (body.get("name") or "").strip()
    proxy_address = (body.get("proxy_address") or "").strip()
    if not proxy_address:
        return JSONResponse({"error": "Прокси обязателен для аккаунта WhatsApp"}, status_code=400)
    try:
        proxy_port = int(body.get("proxy_port") or 0)
    except Exception:
        proxy_port = 0
    if not proxy_port:
        return JSONResponse({"error": "Укажите порт прокси"}, status_code=400)

    proxy_id = await db.add_proxy(
        address=proxy_address, port=proxy_port,
        username=body.get("proxy_username") or None,
        password=body.get("proxy_password") or None,
        proxy_type=body.get("proxy_type") or "socks5",
    )
    profile_dir = f"wa_{uuid.uuid4().hex[:12]}"
    account_id = await db.add_whatsapp_account(name=name, proxy_id=proxy_id, profile_dir=profile_dir)

    try:
        await _open_wa_window(account_id)
        return {"id": account_id, "open": True}
    except Exception as e:
        # Аккаунт сохранён — окно можно открыть позже кнопкой «Открыть»
        return {"id": account_id, "open": False, "warning": str(e)}


@app.post("/api/wa-accounts/{account_id}/open")
async def open_wa_account(account_id: int):
    if not db:
        return JSONResponse({"error": "БД не инициализирована"}, status_code=500)
    acc = await db.get_whatsapp_account(account_id)
    if not acc:
        return JSONResponse({"error": "Аккаунт не найден"}, status_code=404)
    try:
        await _open_wa_window(account_id)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    return {"ok": True}


@app.post("/api/wa-accounts/{account_id}/close")
async def close_wa_account(account_id: int):
    async with _wa_lock:
        manager = _wa_windows.pop(account_id, None)
    if manager:
        try:
            await manager.disconnect()
        except Exception:
            pass
    return {"ok": True}


@app.get("/api/wa-accounts/{account_id}/status")
async def wa_account_status(account_id: int):
    if not db:
        return {"open": False, "logged_in": False}
    manager = _wa_windows.get(account_id)
    is_open = manager is not None and manager.is_open

    if is_open:
        logged_in = await manager.is_logged_in(timeout_ms=1500)
        acc = await db.get_whatsapp_account(account_id)
        if acc and bool(acc.get('logged_in', 0)) != logged_in:
            await db.set_whatsapp_logged_in(account_id, logged_in)
        return {"open": True, "logged_in": logged_in}

    acc = await db.get_whatsapp_account(account_id)
    return {"open": False, "logged_in": bool(acc.get('logged_in', 0)) if acc else False}


@app.patch("/api/wa-accounts/{account_id}/proxy")
async def update_wa_account_proxy(account_id: int, body: dict):
    if not db:
        return JSONResponse({"error": "БД не инициализирована"}, status_code=500)
    acc = await db.get_whatsapp_account(account_id)
    if not acc:
        return JSONResponse({"error": "Аккаунт не найден"}, status_code=404)

    proxy_address = (body.get("proxy_address") or "").strip()
    if not proxy_address:
        return JSONResponse({"error": "Адрес прокси не указан"}, status_code=400)
    try:
        proxy_port = int(body.get("proxy_port") or 0)
    except Exception:
        proxy_port = 0
    if not proxy_port:
        return JSONResponse({"error": "Укажите порт прокси"}, status_code=400)

    proxy_id = await db.add_proxy(
        address=proxy_address, port=proxy_port,
        username=body.get("proxy_username") or None,
        password=body.get("proxy_password") or None,
        proxy_type=body.get("proxy_type") or "socks5",
    )
    await db.update_whatsapp_account_proxy(account_id, proxy_id)

    # Окно нужно перезапустить, чтобы применился новый прокси
    async with _wa_lock:
        manager = _wa_windows.pop(account_id, None)
    if manager:
        try:
            await manager.disconnect()
        except Exception:
            pass
    return {"ok": True}


@app.delete("/api/wa-accounts/{account_id}")
async def delete_wa_account(account_id: int):
    if not db:
        return JSONResponse({"error": "БД не инициализирована"}, status_code=500)
    acc = await db.get_whatsapp_account(account_id)
    if not acc:
        return JSONResponse({"error": "Аккаунт не найден"}, status_code=404)

    async with _wa_lock:
        manager = _wa_windows.pop(account_id, None)
    if manager:
        try:
            await manager.disconnect()
        except Exception:
            pass

    await db.delete_whatsapp_account(account_id)

    profile_path = WA_PROFILES_DIR / acc['profile_dir']
    if profile_path.exists():
        shutil.rmtree(profile_path, ignore_errors=True)

    return {"ok": True}


# ─────────────────────────────────────────────
# Unique query values for filters
# ─────────────────────────────────────────────

@app.get("/api/sellers/queries")
async def get_seller_queries(task_id: Optional[str] = None):
    sellers = _read_json(SELLERS_FILE, [])
    if task_id:
        sellers = [s for s in sellers if s.get('task_id') == task_id]
    queries = sorted({s.get('query', '') for s in sellers if s.get('query')})
    return queries


@app.get("/api/sellers/dates")
async def get_seller_dates(task_id: Optional[str] = None):
    sellers = _read_json(SELLERS_FILE, [])
    if task_id:
        sellers = [s for s in sellers if s.get('task_id') == task_id]
    dates = sorted({s.get('date', '') for s in sellers if s.get('date')}, reverse=True)
    return dates


# ─────────────────────────────────────────────
# Пробив селлеров через Telegram-бота
# ─────────────────────────────────────────────

def _split_round_robin(items: list, n: int) -> List[list]:
    chunks = [[] for _ in range(n)]
    for i, item in enumerate(items):
        chunks[i % n].append(item)
    return chunks


def _push_seller_update(inn: str, fields: dict):
    payload = json.dumps({"type": "seller_update", "inn": inn, "fields": fields})
    for q in list(state.ws_queues):
        q.put_nowait(payload)


async def _update_seller_fields(inn: str, fields: dict):
    async with _sellers_file_lock:
        sellers = _read_json(SELLERS_FILE, [])
        for s in sellers:
            if s.get('inn') == inn:
                s.update(fields)
                break
        _write_json(SELLERS_FILE, sellers)
    _push_seller_update(inn, fields)


async def _mark_inns_status(inns: List[str], status: str):
    for inn in inns:
        await _update_seller_fields(inn, {'status': status})


@app.post("/api/sellers/probiv/start")
async def start_probiv(body: dict):
    task_id = body.get("task_id")
    inns = body.get("inns", [])
    if not inns:
        return JSONResponse({"error": "Не выбраны продавцы"}, status_code=400)

    if task_id in state.probiv_runners:
        return JSONResponse({"error": "Пробив для этой задачи уже запущен"}, status_code=400)

    tasks = _read_json(TASKS_FILE, [])
    task = next((t for t in tasks if t["id"] == task_id), None)
    if not task:
        return JSONResponse({"error": "Задача не найдена"}, status_code=404)

    account_ids = task.get("probiv_account_ids", [])
    if not account_ids:
        return JSONResponse({"error": "Не выбраны TG-аккаунты для пробива"}, status_code=400)

    if not db:
        return JSONResponse({"error": "БД не инициализирована"}, status_code=500)

    await _mark_inns_status(inns, 'Пробивается')

    queue: asyncio.Queue = asyncio.Queue()
    for inn in inns:
        queue.put_nowait(inn)

    state.probiv_runners[task_id] = {
        "stop_event": asyncio.Event(),
        "remaining": set(inns),
        "workers": len(account_ids),
        "queue": queue,
    }
    _push_probiv_status(task_id, 'running')

    for account_id in account_ids:
        asyncio.create_task(_run_probiv_account(task_id, account_id))

    return {"ok": True, "count": len(inns)}


@app.post("/api/sellers/probiv/add")
async def add_to_probiv(body: dict):
    task_id = body.get("task_id")
    inns = body.get("inns", [])
    if not inns:
        return JSONResponse({"error": "Не выбраны продавцы"}, status_code=400)
    runner = state.probiv_runners.get(task_id)
    if not runner:
        return JSONResponse({"error": "Пробив не запущен"}, status_code=400)
    new_inns = [inn for inn in inns if inn not in runner["remaining"]]
    if not new_inns:
        return {"ok": True, "added": 0}
    await _mark_inns_status(new_inns, 'Пробивается')
    for inn in new_inns:
        runner["remaining"].add(inn)
        runner["queue"].put_nowait(inn)
    _push_log(f"➕ Добавлено в пробив: {len(new_inns)} продавцов", task_id, source='probiv')
    return {"ok": True, "added": len(new_inns)}


@app.post("/api/sellers/probiv/stop")
async def stop_probiv(body: dict):
    task_id = body.get("task_id")
    runner = state.probiv_runners.get(task_id)
    if runner:
        runner["stop_event"].set()
        _push_log("⏹ Остановка пробива запрошена...", task_id, source='probiv')
    return {"ok": True}


async def _finish_probiv_worker(task_id: str):
    runner = state.probiv_runners.get(task_id)
    if not runner:
        return
    runner["workers"] -= 1
    if runner["workers"] > 0:
        return
    # Последний воркер — вернуть необработанных продавцов в "В ожидании"
    for inn in list(runner["remaining"]):
        await _update_seller_fields(inn, {'status': 'В ожидании'})
    del state.probiv_runners[task_id]
    _push_probiv_status(task_id, 'idle')


async def _run_probiv_account(task_id: str, account_id: int):
    runner = state.probiv_runners.get(task_id)
    stop_event = runner["stop_event"] if runner else None
    auth = None
    try:
        account = await db.get_account(account_id)
        if not account:
            _push_log(f"❌ Пробив: аккаунт {account_id} не найден", task_id, source='probiv')
            return
        if not account.get('proxy_id'):
            _push_log(f"❌ Пробив: у аккаунта {account['phone']} не настроен прокси", task_id, source='probiv')
            return

        proxy_row = await db.get_proxy(account['proxy_id'])
        proxy = ProxyConfig(
            address=proxy_row['address'], port=proxy_row['port'],
            username=proxy_row.get('username'), password=proxy_row.get('password'),
            proxy_type=proxy_row.get('proxy_type', 'socks5'),
        )

        auth = TelegramAuth(account['phone'], proxy)
        await auth.connect(log_callback=lambda msg: _push_log(f"{account['phone']}: {msg}", task_id, source='probiv'))
        if not await auth.is_authorized():
            _push_log(f"❌ Пробив: аккаунт {account['phone']} не авторизован", task_id, source='probiv')
            return

        client = auth.client
        sellers_data = {s['inn']: s for s in _read_json(SELLERS_FILE, []) if s.get('inn')}

        while True:
            if stop_event and stop_event.is_set():
                break
            try:
                inn = await asyncio.wait_for(runner["queue"].get(), timeout=10.0)
            except asyncio.TimeoutError:
                break

            try:
                _push_log(f"🔎 {account['phone']}: пробив ИНН {inn}", task_id, source='probiv')
                seller = sellers_data.get(inn, {})
                last_name = (seller.get('last_name') or '').strip()
                first_name = (seller.get('name') or '').strip()
                patronymic = (seller.get('patronymic') or seller.get('surname') or '').strip()
                name_query = ' '.join(p for p in [last_name, first_name, patronymic] if p) or None
                result = await probiv_seller(
                    client, inn,
                    name_query=name_query,
                    log_callback=lambda msg: _push_log(msg, task_id, source='probiv'),
                )
                if result.get('bot_down'):
                    _push_log(f"⚠️ {account['phone']}: бот недоступен (тех. работы) — пробив остановлен", task_id, source='probiv')
                    await _update_seller_fields(inn, {'status': 'В ожидании'})
                    if stop_event:
                        stop_event.set()
                    break
                elif result['found']:
                    phones = result['phones']
                    await _update_seller_fields(inn, {'phone': ', '.join(phones), 'status': 'Пробит'})
                    _push_log(f"✅ {account['phone']}: ИНН {inn} → найдено {len(phones)} номер(ов)", task_id, source='probiv')
                else:
                    await _update_seller_fields(inn, {'status': 'Не найден'})
                    _push_log(f"⏭ {account['phone']}: ИНН {inn} → не найдено", task_id, source='probiv')
            except Exception as e:
                _push_log(f"❌ {account['phone']}: ИНН {inn} → ошибка: {e}", task_id, source='probiv')
                await _update_seller_fields(inn, {'status': 'Неполадка'})
            finally:
                if runner:
                    runner["remaining"].discard(inn)

            if stop_event and stop_event.is_set():
                break
            delay = random.uniform(3, 8)
            _push_log(f"⏳ {account['phone']}: пауза {delay:.1f} сек перед следующим запросом", task_id, source='probiv')
            await asyncio.sleep(delay)
    finally:
        if auth:
            try:
                await auth.disconnect()
            except Exception:
                pass
        await _finish_probiv_worker(task_id)


# ─────────────────────────────────────────────
# Рассылка WhatsApp (по аналогии с пробивом)
# ─────────────────────────────────────────────

@app.post("/api/broadcast/media")
async def upload_broadcast_media(request: Request, filename: str = "media"):
    """Сохранить медиафайл рассылки на диск, вернуть путь к нему.

    Файл передаётся «сырым» телом запроса (без multipart, чтобы не тянуть
    python-multipart), имя — в query-параметре filename. Пишем потоково,
    чтобы большие видео не загружались целиком в память.
    """
    safe_name = os.path.basename(filename or "media") or "media"
    dest = BROADCAST_MEDIA_DIR / f"{uuid.uuid4().hex[:8]}_{safe_name}"
    with open(dest, "wb") as f:
        async for chunk in request.stream():
            f.write(chunk)
    return {"path": str(dest), "name": safe_name}


# Реальная отправка через WhatsApp Web (broadcaster.py). True — заглушка для отладки.
BROADCAST_STUB = False

# Переменные, доступные в тексте рассылки (поля продавца)
BROADCAST_VARS = [
    'name', 'last_name', 'patronymic', 'surname', 'shop', 'scu', 'query',
    'inn', 'ogrnip', 'phone', 'seller_url', 'product_url', 'date',
]


def _render_template(text: str, seller: dict) -> str:
    """Подставить {переменные} продавца в текст. Неизвестные {…} не трогаем."""
    for key in BROADCAST_VARS:
        token = '{' + key + '}'
        if token in text:
            text = text.replace(token, str(seller.get(key, '') or ''))
    return text


def _parse_phones(raw: str) -> List[str]:
    """Разобрать поле phone в список нац. номеров (10 цифр, без 7/8), без дублей."""
    if not raw:
        return []
    seen, result = set(), []
    for token in str(raw).replace('\n', ',').replace(';', ',').split(','):
        digits = ''.join(ch for ch in token if ch.isdigit())
        if len(digits) == 11 and digits[0] in '78':
            digits = digits[1:]
        if len(digits) >= 10 and digits not in seen:
            seen.add(digits)
            result.append(digits)
    return result


class _StubBroadcaster:
    """Заглушка: имитирует отправку, чтобы отладить оркестрацию без WhatsApp."""
    def __init__(self, page=None):
        self._page = page

    async def check_number(self, first, last, digits, nth):
        await asyncio.sleep(random.uniform(0.2, 0.6))
        return True

    async def send_to_number(self, first, last, digits, text, media_path, nth):
        await asyncio.sleep(random.uniform(0.3, 0.9))
        return {"registered": True, "sent": True}


@app.post("/api/sellers/broadcast/start")
async def start_broadcast(body: dict):
    task_id = body.get("task_id")
    inns = body.get("inns", [])
    if not inns:
        return JSONResponse({"error": "Не выбраны продавцы"}, status_code=400)
    if task_id in state.broadcast_runners:
        return JSONResponse({"error": "Рассылка для этой задачи уже запущена"}, status_code=400)

    tasks = _read_json(TASKS_FILE, [])
    task = next((t for t in tasks if t["id"] == task_id), None)
    if not task:
        return JSONResponse({"error": "Задача не найдена"}, status_code=404)

    account_ids = task.get("broadcast_account_ids", [])
    if not account_ids:
        return JSONResponse({"error": "Не выбраны WhatsApp-аккаунты для рассылки"}, status_code=400)
    message = (task.get("broadcast_message") or "").strip()
    if not message:
        return JSONResponse({"error": "Не задан текст сообщения рассылки"}, status_code=400)
    if not db:
        return JSONResponse({"error": "БД не инициализирована"}, status_code=500)

    try:
        daily_limit = int(task.get("broadcast_daily_limit") or 0)
    except Exception:
        daily_limit = 0

    # Случайная задержка между отправками (диапазон в секундах из настроек задачи)
    try:
        mn = task.get("broadcast_min_delay")
        mx = task.get("broadcast_max_delay")
        min_delay = float(mn if mn is not None else 4)
        max_delay = float(mx if mx is not None else 9)
    except Exception:
        min_delay, max_delay = 4.0, 9.0
    if min_delay < 0:
        min_delay = 0.0
    if max_delay < min_delay:
        max_delay = min_delay

    # Запоминаем исходный статус продавцов, чтобы вернуть его недоотправленным
    # при остановке/лимите (а не сбрасывать в «В ожидании»).
    sellers_now = {s.get('inn'): (s.get('status') or 'В ожидании')
                   for s in _read_json(SELLERS_FILE, []) if s.get('inn')}
    orig_status = {inn: sellers_now.get(inn, 'В ожидании') for inn in inns}

    await _mark_inns_status(inns, 'Рассылается')

    queue: asyncio.Queue = asyncio.Queue()
    for inn in inns:
        queue.put_nowait(inn)

    state.broadcast_runners[task_id] = {
        "stop_event": asyncio.Event(),
        "remaining": set(inns),
        "workers": len(account_ids),
        "queue": queue,
        "message": message,
        "media": task.get("broadcast_media") or "",
        "daily_limit": daily_limit,
        "min_delay": min_delay,
        "max_delay": max_delay,
        "orig_status": orig_status,
    }
    _push_broadcast_status(task_id, 'running')

    for account_id in account_ids:
        asyncio.create_task(_run_broadcast_account(task_id, account_id))

    return {"ok": True, "count": len(inns)}


@app.post("/api/sellers/broadcast/add")
async def add_to_broadcast(body: dict):
    task_id = body.get("task_id")
    inns = body.get("inns", [])
    if not inns:
        return JSONResponse({"error": "Не выбраны продавцы"}, status_code=400)
    runner = state.broadcast_runners.get(task_id)
    if not runner:
        return JSONResponse({"error": "Рассылка не запущена"}, status_code=400)
    new_inns = [inn for inn in inns if inn not in runner["remaining"]]
    if not new_inns:
        return {"ok": True, "added": 0}
    sellers_now = {s.get('inn'): (s.get('status') or 'В ожидании')
                   for s in _read_json(SELLERS_FILE, []) if s.get('inn')}
    for inn in new_inns:
        runner.setdefault("orig_status", {})[inn] = sellers_now.get(inn, 'В ожидании')
    await _mark_inns_status(new_inns, 'Рассылается')
    for inn in new_inns:
        runner["remaining"].add(inn)
        runner["queue"].put_nowait(inn)
    _push_log(f"➕ Добавлено в рассылку: {len(new_inns)} продавцов", task_id, source='broadcast')
    return {"ok": True, "added": len(new_inns)}


@app.post("/api/sellers/broadcast/stop")
async def stop_broadcast(body: dict):
    task_id = body.get("task_id")
    runner = state.broadcast_runners.get(task_id)
    if runner:
        runner["stop_event"].set()
        _push_log("⏹ Остановка рассылки запрошена...", task_id, source='broadcast')
    return {"ok": True}


async def _finish_broadcast_worker(task_id: str):
    runner = state.broadcast_runners.get(task_id)
    if not runner:
        return
    runner["workers"] -= 1
    if runner["workers"] > 0:
        return
    # Последний воркер — вернуть недоотправленным их исходный статус (например «Пробит»),
    # а не сбрасывать в «В ожидании».
    orig = runner.get("orig_status", {})
    for inn in list(runner["remaining"]):
        await _update_seller_fields(inn, {'status': orig.get(inn, 'В ожидании')})
    del state.broadcast_runners[task_id]
    _push_broadcast_status(task_id, 'idle')


async def _run_broadcast_account(task_id: str, account_id: int):
    runner = state.broadcast_runners.get(task_id)
    stop_event = runner["stop_event"] if runner else None
    manager = None
    try:
        acc = await db.get_whatsapp_account(account_id)
        if not acc:
            _push_log(f"❌ Рассылка: WhatsApp-аккаунт {account_id} не найден", task_id, source='broadcast')
            return
        label = acc.get('name') or acc.get('phone') or f"WA#{account_id}"
        if not acc.get('proxy_id'):
            _push_log(f"❌ Рассылка: у аккаунта {label} не настроен прокси", task_id, source='broadcast')
            return

        message = runner["message"]
        media = runner["media"]
        daily_limit = runner["daily_limit"]
        min_delay = runner.get("min_delay", 4)
        max_delay = runner.get("max_delay", 9)

        # Заглушка (Этап 2) не открывает окно; реальный режим (Этап 4) — открывает и проверяет вход
        if BROADCAST_STUB:
            broadcaster = _StubBroadcaster()
            _push_log(f"🧪 {label}: рассылка в режиме заглушки (без реального WhatsApp)", task_id, source='broadcast')
        else:
            manager = await _open_wa_window(account_id)
            # WhatsApp Web после открытия окна грузится не сразу (прокси + service
            # worker; вдобавок до восстановления сессии может кратковременно
            # мелькнуть экран QR). Поэтому ждём появления списка чатов до ~60с,
            # а не падаем по 5с — иначе залогиненный аккаунт ложно считается
            # неавторизованным.
            _push_log(f"⏳ {label}: жду загрузки WhatsApp Web...", task_id, source='broadcast')
            logged_in = False
            for _ in range(15):
                if stop_event and stop_event.is_set():
                    break
                if await manager.is_logged_in(timeout_ms=3000):
                    logged_in = True
                    break
                await asyncio.sleep(1)
            if not logged_in:
                _push_log(f"❌ Рассылка: аккаунт {label} — не дождались загрузки WhatsApp (нужен вход по QR?)", task_id, source='broadcast')
                return
            from src.whatsapp.broadcaster import WhatsAppBroadcaster
            broadcaster = WhatsAppBroadcaster(manager.page)
            _push_log(f"✅ {label}: WhatsApp загружен, начинаю рассылку", task_id, source='broadcast')

        sellers_data = {s['inn']: s for s in _read_json(SELLERS_FILE, []) if s.get('inn')}
        orig_status = runner.get("orig_status", {}) if runner else {}

        # Случайная пауза перед каждой операцией (проверкой/отправкой), кроме самой
        # первой — так задержка стоит и между номерами одного продавца, и между продавцами.
        _need_delay = [False]

        async def _pace():
            if _need_delay[0]:
                d = random.uniform(min_delay, max_delay)
                _push_log(f"⏳ {label}: пауза {d:.0f}с", task_id, source='broadcast')
                await asyncio.sleep(d)
            _need_delay[0] = True

        while True:
            if stop_event and stop_event.is_set():
                break
            if daily_limit and await db.wa_sent_today(account_id) >= daily_limit:
                _push_log(f"🛑 {label}: достигнут дневной лимит ({daily_limit}) — стоп", task_id, source='broadcast')
                break
            try:
                inn = await asyncio.wait_for(runner["queue"].get(), timeout=10.0)
            except asyncio.TimeoutError:
                break

            try:
                seller = sellers_data.get(inn, {})
                phones = _parse_phones(seller.get('phone', ''))
                first = (seller.get('name') or '').strip()
                last = (seller.get('last_name') or '').strip()
                text = _render_template(message, seller)

                if not phones:
                    await _update_seller_fields(inn, {'status': 'Нет WhatsApp'})
                    _push_log(f"⏭ {label}: ИНН {inn} → нет номеров", task_id, source='broadcast')
                    continue

                # Фаза 1: проверяем ВСЕ номера, собираем зарегистрированные (контакты не сохраняем)
                registered = []   # [(nth, digits), ...]
                for nth, digits in enumerate(phones):
                    if stop_event and stop_event.is_set():
                        break
                    await _pace()
                    reg = await broadcaster.check_number(first, last, digits, nth)
                    if reg is True:
                        registered.append((nth, digits))
                        _push_log(f"✓ {label}: ИНН {inn} → {digits} в WhatsApp", task_id, source='broadcast')
                    elif reg is False:
                        _push_log(f"➖ {label}: ИНН {inn} → {digits} не в WhatsApp", task_id, source='broadcast')
                    else:
                        _push_log(f"❓ {label}: ИНН {inn} → {digits} статус не определён", task_id, source='broadcast')

                if stop_event and stop_event.is_set():
                    await _update_seller_fields(inn, {'status': orig_status.get(inn, 'В ожидании')})
                    break

                # Решение по количеству зарегистрированных номеров
                if not registered:
                    await _update_seller_fields(inn, {'status': 'Нет WhatsApp'})
                    _push_log(f"⛔ {label}: ИНН {inn} → нет номеров в WhatsApp", task_id, source='broadcast')
                    continue
                if len(registered) > 2:
                    await _update_seller_fields(inn, {'status': 'Много номеров'})
                    _push_log(f"🔢 {label}: ИНН {inn} → номеров в WhatsApp {len(registered)} (>2) — не отправляем", task_id, source='broadcast')
                    continue

                # Фаза 2: отправляем 1–2 зарегистрированным номерам (с паузой между)
                any_sent = False
                for nth, digits in registered:
                    if stop_event and stop_event.is_set():
                        break
                    if daily_limit and await db.wa_sent_today(account_id) >= daily_limit:
                        _push_log(f"🛑 {label}: дневной лимит достигнут на номере {digits}", task_id, source='broadcast')
                        break
                    await _pace()
                    res = await broadcaster.send_to_number(first, last, digits, text, media, nth)
                    if res.get('sent'):
                        any_sent = True
                        await db.wa_log_sent(account_id, digits, inn)
                        _push_log(f"✅ {label}: ИНН {inn} → отправлено {digits}", task_id, source='broadcast')

                if any_sent:
                    await _update_seller_fields(inn, {'status': 'Отправлено', 'account': label})
                else:
                    # ни один номер не отправлен (остановка/лимит) — вернуть исходный статус
                    await _update_seller_fields(inn, {'status': orig_status.get(inn, 'В ожидании')})
            except Exception as e:
                _push_log(f"❌ {label}: ИНН {inn} → ошибка: {e}", task_id, source='broadcast')
                await _update_seller_fields(inn, {'status': 'Неполадка'})
            finally:
                if runner:
                    runner["remaining"].discard(inn)
    finally:
        await _finish_broadcast_worker(task_id)
