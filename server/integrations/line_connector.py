"""
LINE Connector ??MCP_Server Integration Module
===============================================
Integrates LINE Messaging API into the existing FastAPI + UMA architecture.

Architecture:
  Inbound  ??POST /api/line/webhook   (signature verify + parse events)
  Session  ??core.session.SessionManager  (session_id = "line_{user_id}")
  Outbound ??OpenAIAdapter.chat() ??reply_message / push_message fallback

Design Principle:
  -立即?? 200 OK (? LINE Webhook 1~2 ?Timeout ?制)
  - BackgroundTasks ??步執?LLM ????Tool Calling
  - 完全?用?? SessionManager + MEMORY.md ??????
"""
import logging
import os
import threading
from contextlib import contextmanager

from fastapi import APIRouter, Request, BackgroundTasks, HTTPException
import httpx
try:
    import redis
except ImportError:  # Optional dependency for distributed locks
    redis = None

logger = logging.getLogger("MCP_Server.LINE")
router = APIRouter()

# ?? Lazy-initialized LINE SDK components ??????????????????????????????????????
# 延遲????確?缺? key ?伺?器仍可??（?級模式?
_line_handler = None
_line_api = None


def _get_line_components():
    """
    Lazily initialize LINE SDK WebhookHandler and MessagingApi.
    Raises KeyError if LINE_CHANNEL_SECRET or LINE_CHANNEL_ACCESS_TOKEN is missing.
    """
    global _line_handler, _line_api
    if _line_handler is None:
        from linebot.v3 import WebhookHandler
        from linebot.v3.messaging import Configuration, ApiClient, MessagingApi

        secret = os.environ.get("LINE_CHANNEL_SECRET", "").strip()
        token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip()

        if not secret or not token:
            raise KeyError(
                "LINE_CHANNEL_SECRET and LINE_CHANNEL_ACCESS_TOKEN must be set in .env"
            )

        cfg = Configuration(access_token=token)
        _line_api = MessagingApi(ApiClient(cfg))
        _line_handler = WebhookHandler(secret)
        logger.info("[LINE] SDK initialized successfully.")

    return _line_handler, _line_api


# ?? LINE-specific system prompt ????????????????????????????????????????????????
def _get_dynamic_system_prompt() -> str:
    from datetime import datetime
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return (
        "You are the LINE AI assistant for MCP Agent Console.\n"
        f"Current time: {now_str}\n"
        "Respond in Traditional Chinese with concise, clear answers.\n"
        "If tools or lookups are needed, execute them directly and report the result.\n"
        "Keep replies under 3000 characters and focused."
    )

# ?? LINE ??訊息字?上? ???????????????????????????????????????????????????????
_LINE_MAX_CHARS = 4900


# ?? Webhook Endpoint ??????????????????????????????????????????????????????????

@router.post("/api/line/webhook", tags=["Integration"])
async def line_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    LINE Messaging API Webhook ?收端???

    流??
    A. 驗? X-Line-Signature（防?造?求?
    B. ?? LINE Events
    C. TextMessage ??丟入 BackgroundTasks（解??LLM 延遲?
    D. 立即?? 200 OK（解?Timeout ?頸?
    """
    # A. ??並??Signature
    try:
        handler, line_api = _get_line_components()
    except KeyError as e:
        logger.error(f"[LINE] Missing configuration: {e}")
        raise HTTPException(status_code=500, detail=f"LINE configuration error: {e}")

    signature = request.headers.get("X-Line-Signature", "")
    body_bytes = await request.body()
    body_text = body_bytes.decode("utf-8")

    # B. ??事件
    from linebot.v3.exceptions import InvalidSignatureError
    from linebot.v3.webhooks import MessageEvent, TextMessageContent

    try:
        events = handler.parser.parse(body_text, signature)
    except InvalidSignatureError:
        logger.warning("[LINE] Webhook rejected ??invalid X-Line-Signature")
        raise HTTPException(status_code=400, detail="Invalid signature")
    except Exception as e:
        logger.error(f"[LINE] Event parse error: {e}")
        raise HTTPException(status_code=400, detail=f"Event parse error: {e}")

    # C. ???? TextMessage Event
    for event in events:
        if isinstance(event, MessageEvent) and isinstance(event.message, TextMessageContent):
            # ?? session_id：?來?類?決?（user / group / room?
            source = event.source
            if hasattr(source, "group_id") and source.group_id:
                session_id = f"line_group_{source.group_id}"
                chat_id = source.group_id
            elif hasattr(source, "room_id") and source.room_id:
                session_id = f"line_room_{source.room_id}"
                chat_id = source.room_id
            else:
                session_id = f"line_{source.user_id}"
                chat_id = source.user_id

            background_tasks.add_task(
                _process_line_message,
                line_api=line_api,
                reply_token=event.reply_token,
                user_id=source.user_id,
                chat_id=chat_id,
                session_id=session_id,
                user_input=event.message.text,
            )
            logger.info(
                f"[LINE] Queued background task: session={session_id}, "
                f"input='{event.message.text[:40]}...'"
            )

    # D. 立即?? 200 OK ??不??LLM 完?
    return "OK"


# ?? Session Locking & UX ??????????????????????????????????????????????????????

_local_locks = {}
_local_lock_mutex = threading.Lock()
_last_request_time = {}  # 紀????session ??後?????(Debounce ??
_redis_client = None

try:
    if redis is not None:
        _redis_client = redis.Redis(host="localhost", port=6379, db=0, socket_connect_timeout=1)
        _redis_client.ping()
        logger.info("[LINE] Redis connected for distributed locking.")
    else:
        logger.info("[LINE] Redis package not installed. Falling back to in-memory locks.")
except Exception as e:
    logger.info(f"[LINE] Redis not available ({e}). Falling back to in-memory locks.")
    _redis_client = None

@contextmanager
def _acquire_session_lock(session_id: str):
    """
    Acquire a lock for the session_id to prevent concurrent LLM requests.
    Uses Redis if available, else falls back to in-memory threading.Lock.
    """
    lock_key = f"lock:{session_id}"
    redis_lock = None
    local_lock = None

    if _redis_client:
        try:
            redis_lock = _redis_client.lock(lock_key, timeout=60)
            acquired = redis_lock.acquire(blocking=False)
            if not acquired:
                yield False
                return
        except Exception as e:
            logger.warning(f"[LINE] Redis lock failed, falling back to local: {e}")

    if not redis_lock:
        with _local_lock_mutex:
            if lock_key not in _local_locks:
                _local_locks[lock_key] = threading.Lock()
            local_lock = _local_locks[lock_key]
        
        acquired = local_lock.acquire(blocking=False)
        if not acquired:
            yield False
            return

    try:
        yield True
    finally:
        if redis_lock:
            try:
                redis_lock.release()
            except Exception:
                pass
        if local_lock:
            local_lock.release()

def _send_loading_animation(line_api, chat_id: str):
    """?叫 LINE Loading Animation API (使用官方 SDK)"""
    from linebot.v3.messaging import ShowLoadingAnimationRequest

    try:
        req = ShowLoadingAnimationRequest(chatId=chat_id, loadingSeconds=20)
        line_api.show_loading_animation(req)
        logger.info(f"[LINE] Loading animation started for chat={chat_id}")
    except Exception as e:
        logger.warning(f"[LINE] Exception starting loading animation: {e}")


# ?? Background Processing Function ????????????????????????????????????????????

def _process_line_message(
    line_api,
    reply_token: str,
    user_id: str,
    chat_id: str,
    session_id: str,
    user_input: str,
):
    """
    ?景?數：LLM ?? ??Tool ?? ??組??? ???? LINE??

    ?用???件?
    - server.dependencies.session.get_session_manager() (SessionManager)
    - OpenAIAdapter.chat() (full Tool Calling + RAG)
    - MEMORY.md ????append_message ??觸發?
    """
    from server.dependencies.uma import get_uma_instance
    from server.adapters.openai_adapter import OpenAIAdapter
    from server.dependencies.session import get_session_manager
    import time
    _session_mgr = get_session_manager()

    logger.info(f"[LINE BG] Start processing: session={session_id}")

    # 0. ???機制 (Debounce)：??2 秒內??觸發???
    current_time = time.time()
    last_time = _last_request_time.get(session_id, 0)
    if current_time - last_time < 2.0:
        logger.warning(f"[LINE BG] Debounced input for session={session_id} (Too fast)")
        return
    _last_request_time[session_id] = current_time

    with _acquire_session_lock(session_id) as acquired:
        if not acquired:
            logger.warning(f"[LINE BG] Session {session_id} is locked. Ignoring concurrent input.")
            return

        # 0.5 顯示 loading ?畫 (安撫使用??待焦??，??傳??chat_id
        _send_loading_animation(line_api, chat_id)

        try:
            # 1. ???建?Session（?次建立?注入 LINE 專屬 system prompt?
            # 注?：為了解決日?幻覺?每次對話??保???????，? session ??後????system prompt
            # ?以??在每次對話??強制?新 System Prompt
            _session_mgr.get_or_create_conversation(session_id, _get_dynamic_system_prompt())
            _session_mgr._update_system_prompt(session_id, _get_dynamic_system_prompt())
    
            # 2. 追?使用???至 Session
            _session_mgr.append_message(session_id, "user", user_input)

            # 3. ???能??令?綴?????模?
            actual_input, execute_mode = _parse_command_prefix(user_input)

            # 4. ????Adapter（使???model，可依?求選 Gemini/Claude?
            uma = get_uma_instance()
            adapter = OpenAIAdapter(uma=uma)

            if not adapter.is_available:
                final_reply = "AI service is not available. Please verify OPENAI_API_KEY is configured."
            else:
                # ?????景??對話導致 OpenAI 429 Too Many Requests (Token Limit)
                # 強制?擷??System Prompt + ??5 輪??(10 條???
                # ?為?們? _update_system_prompt，???得???history
                history = _session_mgr.get_or_create_conversation(session_id)
                system_msgs = [m for m in history if m.get("role") == "system"]
                recent_msgs = [m for m in history if m.get("role") != "system"][-10:]
                truncated_history = system_msgs + recent_msgs

                # ?入?斷??history ?本，避??generator 消費?中 list 被??修??
                result_gen = adapter.chat(
                    messages=truncated_history,
                    user_query=actual_input,
                    session_id=session_id,
                )

                # 5. 消費?步 Generator，?裝???覆??
                final_reply = _collect_generator(result_gen)

            # 6. ?斷??LINE 字?上?
            if len(final_reply) > _LINE_MAX_CHARS:
                final_reply = final_reply[:_LINE_MAX_CHARS] + "\n\nReply truncated to fit LINE limits."

            # 7. 寫入 Session 記憶（觸??MEMORY.md ????
            _session_mgr.append_message(session_id, "assistant", final_reply)

            # 8. ?傳 LINE（reply_token ??，逾?後? push_message?
            _send_line_reply(line_api, reply_token, chat_id, final_reply)

        except Exception as e:
            logger.error(
                f"[LINE BG] Unhandled error for session={session_id}: {e}", exc_info=True
            )
            _send_error_push(line_api, chat_id)


def _parse_command_prefix(user_input: str) -> tuple[str, bool]:
    """
    ?? LINE 訊息?綴?令，??決定執行模式?

    /tool <msg>  ??Agent 模?（強??Tool Calling?
    /chat <msg>  ??純?話模?
    ??         ???設 Agent 模?
    """
    if user_input.startswith("/tool "):
        return user_input[6:].strip(), True
    elif user_input.startswith("/chat "):
        return user_input[6:].strip(), False
    return user_input, True  # ?設?用 Tool Calling


def _collect_generator(result_gen) -> str:
    """
    消費 adapter.chat() ???generator，?裝???覆?字?

    Generator ??chunk ???
    - {"status": "streaming", "content": "<partial text>"}
    - {"status": "success",   "content": "<full text>"}
    - {"status": "error",     "message": "<error msg>"}
    """
    accumulated = ""
    for chunk in result_gen:
        status = chunk.get("status")
        if status == "streaming":
            accumulated += chunk.get("content", "")
        elif status == "success":
            # success chunk ?含完整?終內?
            final = chunk.get("content", "")
            return final if final else accumulated
        elif status == "error":
            err_msg = chunk.get("message", "?知?誤")
            logger.error(f"[LINE BG] Adapter error: {err_msg}")
            return f"?????誤：{err_msg}"
        elif status == "requires_approval":
            tool_name = chunk.get("tool_name", "?知工具")
            return (
                f"?? 工具 `{tool_name}` ?要人工確認??能???\n"
                "Please use the web console to review and approve this action."
            )

    return accumulated if accumulated else "The AI did not return a reply. Please try again later."


def _send_line_reply(line_api, reply_token: str, chat_id: str, text: str):
    """
    ?送?覆至 LINE??
    ??使用 reply_token（? 30 秒???，逾?後???push_message??
    """
    from linebot.v3.messaging import TextMessage, ReplyMessageRequest, PushMessageRequest

    try:
        line_api.reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=text)],
            )
        )
        logger.info(f"[LINE] Reply sent via reply_token ??chat={chat_id}")
    except Exception as reply_err:
        # reply_token 已???失?，改??push_message 主???
        logger.warning(
            f"[LINE] reply_token expired/failed ({reply_err}), "
            f"falling back to push_message ??chat={chat_id}"
        )
        try:
            line_api.push_message(
                PushMessageRequest(
                    to=chat_id,
                    messages=[TextMessage(text=text)],
                )
            )
            logger.info(f"[LINE] Reply sent via push_message ??chat={chat_id}")
        except Exception as push_err:
            logger.error(f"[LINE] push_message also failed: {push_err}")


def _send_error_push(line_api, chat_id: str):
    """Send a generic error notification to the LINE user."""
    try:
        from linebot.v3.messaging import TextMessage, PushMessageRequest

        line_api.push_message(
            PushMessageRequest(
                to=chat_id,
                messages=[
                    TextMessage(text="System error. Please try again later or contact the administrator.")
                ],
            )
        )
    except Exception as e:
        logger.error(f"[LINE] Failed to send error notification to {chat_id}: {e}")

