"""
LINE Connector ??MCP_Server Integration Module
===============================================
Integrates LINE Messaging API into the existing FastAPI + UMA architecture.

Architecture:
  Inbound  ??POST /api/line/webhook   (signature verify + parse events)
  Session  ??core.session.SessionManager  (session_id = "line_{user_id}")
  Outbound ??OpenAIAdapter.chat() ??reply_message / push_message fallback

Design Principle:
  -ç«‹å³?è? 200 OK (è§?±º LINE Webhook 1~2 ç§?Timeout ?åˆ¶)
  - BackgroundTasks ?å?æ­¥åŸ·è¡?LLM ?Ÿæ???Tool Calling
  - å®Œå…¨?ç”¨?¾æ? SessionManager + MEMORY.md ?ä??–æ???
"""
import logging
import os
import threading
from contextlib import contextmanager

from fastapi import APIRouter, Request, BackgroundTasks, HTTPException
import httpx
import redis

logger = logging.getLogger("MCP_Server.LINE")
router = APIRouter()

# ?€?€ Lazy-initialized LINE SDK components ?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€
# å»¶é²?å??–ï?ç¢ºä?ç¼ºå? key ?‚ä¼º?å™¨ä»å¯?Ÿå?ï¼ˆé?ç´šæ¨¡å¼ï?
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


# ?€?€ LINE-specific system prompt ?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€
def _get_dynamic_system_prompt() -> str:
    from datetime import datetime
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return (
        f"ä½ æ˜¯?”ç™¼çµ?MCP Agent Console ??LINE AI ?©ç??‚\n"
        f"?¾åœ¨?‚é??¯ï?{now_str}\n"
        f"è«‹ä»¥ç¹é?ä¸­æ??ç°¡æ½”æ??›åœ°?è?ä½¿ç”¨?…ã€‚\n"
        f"?¥é?è¦åŸ·è¡Œæ??½å·¥?·ï?è«‹ç›´?¥åŸ·è¡Œä¸¦?å ±çµæ??‚\n"
        f"?è?è«‹æ§?¶åœ¨ 3000 å­—ä»¥?§ï?ä¿æ?æ¸…æ™°?“è???
    )

# ?€?€ LINE ?®å?è¨Šæ¯å­—å?ä¸Šé? ?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€
_LINE_MAX_CHARS = 4900


# ?€?€ Webhook Endpoint ?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€

@router.post("/api/line/webhook", tags=["Integration"])
async def line_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    LINE Messaging API Webhook ?¥æ”¶ç«¯é???

    æµç?ï¼?
    A. é©—è? X-Line-Signatureï¼ˆé˜²?½é€ è?æ±‚ï?
    B. è§?? LINE Events
    C. TextMessage ??ä¸Ÿå…¥ BackgroundTasksï¼ˆè§£??LLM å»¶é²ï¼?
    D. ç«‹å³?è? 200 OKï¼ˆè§£æ±?Timeout ?¶é ¸ï¼?
    """
    # A. ?–å?ä¸¦é?è­?Signature
    try:
        handler, line_api = _get_line_components()
    except KeyError as e:
        logger.error(f"[LINE] Missing configuration: {e}")
        raise HTTPException(status_code=500, detail=f"LINE configuration error: {e}")

    signature = request.headers.get("X-Line-Signature", "")
    body_bytes = await request.body()
    body_text = body_bytes.decode("utf-8")

    # B. è§??äº‹ä»¶
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

    # C. ?ä??•ç? TextMessage Event
    for event in events:
        if isinstance(event, MessageEvent) and isinstance(event.message, TextMessageContent):
            # è§?? session_idï¼šä?ä¾†æ?é¡å?æ±ºå?ï¼ˆuser / group / roomï¼?
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

    # D. ç«‹å³?è? 200 OK ??ä¸ç?å¾?LLM å®Œæ?
    return "OK"


# ?€?€ Session Locking & UX ?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€

_local_locks = {}
_local_lock_mutex = threading.Lock()
_last_request_time = {}  # ç´€?„æ???session ?„æ?å¾Œè??†æ???(Debounce ??
_redis_client = None

try:
    _redis_client = redis.Redis(host="localhost", port=6379, db=0, socket_connect_timeout=1)
    _redis_client.ping()
    logger.info("[LINE] Redis connected for distributed locking.")
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
    """?¼å« LINE Loading Animation API (ä½¿ç”¨å®˜æ–¹ SDK)"""
    from linebot.v3.messaging import ShowLoadingAnimationRequest

    try:
        req = ShowLoadingAnimationRequest(chatId=chat_id, loadingSeconds=20)
        line_api.show_loading_animation(req)
        logger.info(f"[LINE] Loading animation started for chat={chat_id}")
    except Exception as e:
        logger.warning(f"[LINE] Exception starting loading animation: {e}")


# ?€?€ Background Processing Function ?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€

def _process_line_message(
    line_api,
    reply_token: str,
    user_id: str,
    chat_id: str,
    session_id: str,
    user_input: str,
):
    """
    ?Œæ™¯?½æ•¸ï¼šLLM ?Ÿæ? ??Tool ?·è? ??çµ„è??è? ???å? LINE??

    ?ç”¨?¾æ??ƒä»¶ï¼?
    - server.dependencies.session.get_session_manager() (SessionManager)
    - OpenAIAdapter.chat() (full Tool Calling + RAG)
    - MEMORY.md ?ä??–ï?append_message ?ªå?è§¸ç™¼ï¼?
    """
    from server.dependencies.uma import get_uma_instance
    from server.adapters.openai_adapter import OpenAIAdapter
    from server.dependencies.session import get_session_manager
    import time
    _session_mgr = get_session_manager()

    logger.info(f"[LINE BG] Start processing: session={session_id}")

    # 0. ?²é€??æ©Ÿåˆ¶ (Debounce)ï¼šé?æ¿?2 ç§’å…§?è?è§¸ç™¼?„ä?ä»?
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

        # 0.5 é¡¯ç¤º loading ?•ç•« (å®‰æ’«ä½¿ç”¨?…ç?å¾…ç„¦??ï¼Œå??ˆå‚³??chat_id
        _send_loading_animation(line_api, chat_id)

        try:
            # 1. ?–å??–å»ºç«?Sessionï¼ˆé?æ¬¡å»ºç«‹æ?æ³¨å…¥ LINE å°ˆå±¬ system promptï¼?
            # æ³¨æ?ï¼šç‚ºäº†è§£æ±ºæ—¥?Ÿå¹»è¦ºï?æ¯æ¬¡å°è©±?½è?ä¿è??‚é??¯æ??°ç?ï¼Œä? session ?µç?å¾Œä??ƒé?å¯?system prompt
            # ?€ä»¥æ??‘åœ¨æ¯æ¬¡å°è©±?ï?å¼·åˆ¶?´æ–° System Prompt
            _session_mgr.get_or_create_conversation(session_id, _get_dynamic_system_prompt())
            _session_mgr._update_system_prompt(session_id, _get_dynamic_system_prompt())
    
            # 2. è¿½å?ä½¿ç”¨?…è??¯è‡³ Session
            _session_mgr.append_message(session_id, "user", user_input)

            # 3. è§???¯èƒ½?„æ?ä»¤å?ç¶´ï??•æ??‡æ?æ¨¡å?
            actual_input, execute_mode = _parse_command_prefix(user_input)

            # 4. ?å???Adapterï¼ˆä½¿?¨é?è¨?modelï¼Œå¯ä¾é?æ±‚é¸ Gemini/Claudeï¼?
            uma = get_uma_instance()
            adapter = OpenAIAdapter(uma=uma)

            if not adapter.is_available:
                final_reply = "? ï? AI ?å??«æ??¡æ?ä½¿ç”¨ï¼Œè?ç¢ºè? OPENAI_API_KEY è¨­å???
            else:
                # ?ºä??¿å??Œæ™¯?·æ?å°è©±å°è‡´ OpenAI 429 Too Many Requests (Token Limit)
                # å¼·åˆ¶?ªæ“·??System Prompt + ?€è¿?5 è¼ªå?è©?(10 æ¢è???
                # ? ç‚º?‘å€‘æ? _update_system_promptï¼Œé??°å?å¾—æ???history
                history = _session_mgr.get_or_create_conversation(session_id)
                system_msgs = [m for m in history if m.get("role") == "system"]
                recent_msgs = [m for m in history if m.get("role") != "system"][-10:]
                truncated_history = system_msgs + recent_msgs

                # ?³å…¥?ªæ–·??history ?¯æœ¬ï¼Œé¿??generator æ¶ˆè²»?”ä¸­ list è¢«å??¨ä¿®??
                result_gen = adapter.chat(
                    messages=truncated_history,
                    user_query=actual_input,
                    session_id=session_id,
                )

                # 5. æ¶ˆè²»?Œæ­¥ Generatorï¼Œç?è£å??´å?è¦†å?ä¸?
                final_reply = _collect_generator(result_gen)

            # 6. ?ªæ–·??LINE å­—å?ä¸Šé?
            if len(final_reply) > _LINE_MAX_CHARS:
                final_reply = final_reply[:_LINE_MAX_CHARS] + "\n\n?¦ï??è??é•·ï¼Œå·²?ªæ–·ï¼?

            # 7. å¯«å…¥ Session è¨˜æ†¶ï¼ˆè§¸??MEMORY.md ?ä??–ï?
            _session_mgr.append_message(session_id, "assistant", final_reply)

            # 8. ?å‚³ LINEï¼ˆreply_token ?ªå?ï¼Œé€¾æ?å¾Œå? push_messageï¼?
            _send_line_reply(line_api, reply_token, chat_id, final_reply)

        except Exception as e:
            logger.error(
                f"[LINE BG] Unhandled error for session={session_id}: {e}", exc_info=True
            )
            _send_error_push(line_api, chat_id)


def _parse_command_prefix(user_input: str) -> tuple[str, bool]:
    """
    è§?? LINE è¨Šæ¯?ç¶´?‡ä»¤ï¼Œå??‹æ±ºå®šåŸ·è¡Œæ¨¡å¼ã€?

    /tool <msg>  ??Agent æ¨¡å?ï¼ˆå¼·??Tool Callingï¼?
    /chat <msg>  ??ç´”å?è©±æ¨¡å¼?
    ?¶ä?         ???è¨­ Agent æ¨¡å?
    """
    if user_input.startswith("/tool "):
        return user_input[6:].strip(), True
    elif user_input.startswith("/chat "):
        return user_input[6:].strip(), False
    return user_input, True  # ?è¨­?Ÿç”¨ Tool Calling


def _collect_generator(result_gen) -> str:
    """
    æ¶ˆè²» adapter.chat() ?„å?æ­?generatorï¼Œç?è£å??´å?è¦†æ?å­—ã€?

    Generator ??chunk ?¼å?ï¼?
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
            # success chunk ?…å«å®Œæ•´?€çµ‚å…§å®?
            final = chunk.get("content", "")
            return final if final else accumulated
        elif status == "error":
            err_msg = chunk.get("message", "?ªçŸ¥?¯èª¤")
            logger.error(f"[LINE BG] Adapter error: {err_msg}")
            return f"???¼ç??¯èª¤ï¼š{err_msg}"
        elif status == "requires_approval":
            tool_name = chunk.get("tool_name", "?ªçŸ¥å·¥å…·")
            return (
                f"? ï? å·¥å…· `{tool_name}` ?€è¦äººå·¥ç¢ºèªå??èƒ½?·è??‚\n"
                "è«‹è‡³ Web Console ?•ç?æ­¤é?é¢¨éšª?ä???
            )

    return accumulated if accumulated else "ï¼ˆAI ?ªç”¢?Ÿå?è¦†ï?è«‹ç?å¾Œå?è©¦ï?"


def _send_line_reply(line_api, reply_token: str, chat_id: str, text: str):
    """
    ?¼é€å?è¦†è‡³ LINE??
    ?ªå?ä½¿ç”¨ reply_tokenï¼ˆé? 30 ç§’æ??ˆï?ï¼Œé€¾æ?å¾Œå???push_message??
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
        # reply_token å·²é??Ÿæ?å¤±æ?ï¼Œæ”¹??push_message ä¸»å??¨é€?
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
    """?¼é€é€šç”¨?¯èª¤?šçŸ¥??LINE ä½¿ç”¨?…ã€?""
    try:
        from linebot.v3.messaging import TextMessage, PushMessageRequest

        line_api.push_message(
            PushMessageRequest(
                to=chat_id,
                messages=[
                    TextMessage(text="? ï? ç³»çµ±?¼ç??§éƒ¨?¯èª¤ï¼Œè?ç¨å??è©¦?–è¯çµ¡ç®¡?†å“¡??)
                ],
            )
        )
    except Exception as e:
        logger.error(f"[LINE] Failed to send error notification to {chat_id}: {e}")

