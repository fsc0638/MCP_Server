"""
LINE Connector — MCP_Server Integration Module
===============================================
Integrates LINE Messaging API into the existing FastAPI + UMA architecture.

Architecture:
  Inbound  → POST /api/line/webhook   (signature verify + parse events)
  Session  → core.session.SessionManager  (session_id = "line_{user_id}")
  Outbound → OpenAIAdapter.chat() → reply_message / push_message fallback

Design Principle:
  -立即回覆 200 OK (解決 LINE Webhook 1~2 秒 Timeout 限制)
  - BackgroundTasks 非同步執行 LLM 生成與 Tool Calling
  - 完全重用現有 SessionManager + MEMORY.md 持久化機制
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

# ── Lazy-initialized LINE SDK components ──────────────────────────────────────
# 延遲初始化，確保缺少 key 時伺服器仍可啟動（降級模式）
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


# ── LINE-specific system prompt ────────────────────────────────────────────────
def _get_dynamic_system_prompt() -> str:
    from datetime import datetime
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return (
        f"你是研發組 MCP Agent Console 的 LINE AI 助理。\n"
        f"現在時間是：{now_str}\n"
        f"請以繁體中文、簡潔有力地回覆使用者。\n"
        f"若需要執行技能工具，請直接執行並回報結果。\n"
        f"回覆請控制在 3000 字以內，保持清晰易讀。"
    )

# ── LINE 單則訊息字元上限 ───────────────────────────────────────────────────────
_LINE_MAX_CHARS = 4900


# ── Webhook Endpoint ──────────────────────────────────────────────────────────

@router.post("/api/line/webhook", tags=["Integration"])
async def line_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    LINE Messaging API Webhook 接收端點。

    流程：
    A. 驗證 X-Line-Signature（防偽造請求）
    B. 解析 LINE Events
    C. TextMessage → 丟入 BackgroundTasks（解耦 LLM 延遲）
    D. 立即回覆 200 OK（解決 Timeout 瓶頸）
    """
    # A. 取得並驗證 Signature
    try:
        handler, line_api = _get_line_components()
    except KeyError as e:
        logger.error(f"[LINE] Missing configuration: {e}")
        raise HTTPException(status_code=500, detail=f"LINE configuration error: {e}")

    signature = request.headers.get("X-Line-Signature", "")
    body_bytes = await request.body()
    body_text = body_bytes.decode("utf-8")

    # B. 解析事件
    from linebot.v3.exceptions import InvalidSignatureError
    from linebot.v3.webhooks import MessageEvent, TextMessageContent

    try:
        events = handler.parser.parse(body_text, signature)
    except InvalidSignatureError:
        logger.warning("[LINE] Webhook rejected — invalid X-Line-Signature")
        raise HTTPException(status_code=400, detail="Invalid signature")
    except Exception as e:
        logger.error(f"[LINE] Event parse error: {e}")
        raise HTTPException(status_code=400, detail=f"Event parse error: {e}")

    # C. 逐一處理 TextMessage Event
    for event in events:
        if isinstance(event, MessageEvent) and isinstance(event.message, TextMessageContent):
            # 解析 session_id：依來源類型決定（user / group / room）
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

    # D. 立即回覆 200 OK — 不等待 LLM 完成
    return "OK"


# ── Session Locking & UX ──────────────────────────────────────────────────────

_local_locks = {}
_local_lock_mutex = threading.Lock()
_last_request_time = {}  # 紀錄每個 session 的最後處理時間 (Debounce 用)
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
    """呼叫 LINE Loading Animation API (使用官方 SDK)"""
    from linebot.v3.messaging import ShowLoadingAnimationRequest

    try:
        req = ShowLoadingAnimationRequest(chatId=chat_id, loadingSeconds=20)
        line_api.show_loading_animation(req)
        logger.info(f"[LINE] Loading animation started for chat={chat_id}")
    except Exception as e:
        logger.warning(f"[LINE] Exception starting loading animation: {e}")


# ── Background Processing Function ────────────────────────────────────────────

def _process_line_message(
    line_api,
    reply_token: str,
    user_id: str,
    chat_id: str,
    session_id: str,
    user_input: str,
):
    """
    背景函數：LLM 生成 → Tool 執行 → 組裝回覆 → 送回 LINE。

    重用現有元件：
    - router._session_mgr  (SessionManager)
    - OpenAIAdapter.chat() (full Tool Calling + RAG)
    - MEMORY.md 持久化（append_message 自動觸發）
    """
    from server.dependencies.uma import get_uma_instance
    from server.adapters.openai_adapter import OpenAIAdapter
    from server.dependencies.session import get_session_manager
    import time
    _session_mgr = get_session_manager()

    logger.info(f"[LINE BG] Start processing: session={session_id}")

    # 0. 防連點機制 (Debounce)：過濾 2 秒內重複觸發的事件
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

        # 0.5 顯示 loading 動畫 (安撫使用者等待焦慮)，必須傳入 chat_id
        _send_loading_animation(line_api, chat_id)

        try:
            # 1. 取得或建立 Session（首次建立時注入 LINE 專屬 system prompt）
            # 注意：為了解決日期幻覺，每次對話都要保證時間是最新的，但 session 創立後不會重寫 system prompt
            # 所以我們在每次對話前，強制更新 System Prompt
            _session_mgr.get_or_create_conversation(session_id, _get_dynamic_system_prompt())
            _session_mgr._update_system_prompt(session_id, _get_dynamic_system_prompt())
    
            # 2. 追加使用者訊息至 Session
            _session_mgr.append_message(session_id, "user", user_input)

            # 3. 解析可能的指令前綴，動態切換模式
            actual_input, execute_mode = _parse_command_prefix(user_input)

            # 4. 初始化 Adapter（使用預設 model，可依需求選 Gemini/Claude）
            uma = get_uma_instance()
            adapter = OpenAIAdapter(uma=uma)

            if not adapter.is_available:
                final_reply = "⚠️ AI 服務暫時無法使用，請確認 OPENAI_API_KEY 設定。"
            else:
                # 為了避免背景長期對話導致 OpenAI 429 Too Many Requests (Token Limit)
                # 強制只擷取 System Prompt + 最近 5 輪對話 (10 條訊息)
                # 因為我們有 _update_system_prompt，重新取得最新 history
                history = _session_mgr.get_or_create_conversation(session_id)
                system_msgs = [m for m in history if m.get("role") == "system"]
                recent_msgs = [m for m in history if m.get("role") != "system"][-10:]
                truncated_history = system_msgs + recent_msgs

                # 傳入截斷的 history 副本，避免 generator 消費途中 list 被外部修改
                result_gen = adapter.chat(
                    messages=truncated_history,
                    user_query=actual_input,
                    session_id=session_id,
                )

                # 5. 消費同步 Generator，組裝完整回覆字串
                final_reply = _collect_generator(result_gen)

            # 6. 截斷至 LINE 字元上限
            if len(final_reply) > _LINE_MAX_CHARS:
                final_reply = final_reply[:_LINE_MAX_CHARS] + "\n\n…（回覆過長，已截斷）"

            # 7. 寫入 Session 記憶（觸發 MEMORY.md 持久化）
            _session_mgr.append_message(session_id, "assistant", final_reply)

            # 8. 回傳 LINE（reply_token 優先，逾時後備 push_message）
            _send_line_reply(line_api, reply_token, chat_id, final_reply)

        except Exception as e:
            logger.error(
                f"[LINE BG] Unhandled error for session={session_id}: {e}", exc_info=True
            )
            _send_error_push(line_api, chat_id)


def _parse_command_prefix(user_input: str) -> tuple[str, bool]:
    """
    解析 LINE 訊息前綴指令，動態決定執行模式。

    /tool <msg>  → Agent 模式（強制 Tool Calling）
    /chat <msg>  → 純對話模式
    其他         → 預設 Agent 模式
    """
    if user_input.startswith("/tool "):
        return user_input[6:].strip(), True
    elif user_input.startswith("/chat "):
        return user_input[6:].strip(), False
    return user_input, True  # 預設啟用 Tool Calling


def _collect_generator(result_gen) -> str:
    """
    消費 adapter.chat() 的同步 generator，組裝完整回覆文字。

    Generator 的 chunk 格式：
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
            # success chunk 包含完整最終內容
            final = chunk.get("content", "")
            return final if final else accumulated
        elif status == "error":
            err_msg = chunk.get("message", "未知錯誤")
            logger.error(f"[LINE BG] Adapter error: {err_msg}")
            return f"❌ 發生錯誤：{err_msg}"
        elif status == "requires_approval":
            tool_name = chunk.get("tool_name", "未知工具")
            return (
                f"⚠️ 工具 `{tool_name}` 需要人工確認後才能執行。\n"
                "請至 Web Console 處理此高風險操作。"
            )

    return accumulated if accumulated else "（AI 未產生回覆，請稍後再試）"


def _send_line_reply(line_api, reply_token: str, chat_id: str, text: str):
    """
    發送回覆至 LINE。
    優先使用 reply_token（限 30 秒有效），逾時後備為 push_message。
    """
    from linebot.v3.messaging import TextMessage, ReplyMessageRequest, PushMessageRequest

    try:
        line_api.reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=text)],
            )
        )
        logger.info(f"[LINE] Reply sent via reply_token → chat={chat_id}")
    except Exception as reply_err:
        # reply_token 已過期或失效，改用 push_message 主動推送
        logger.warning(
            f"[LINE] reply_token expired/failed ({reply_err}), "
            f"falling back to push_message → chat={chat_id}"
        )
        try:
            line_api.push_message(
                PushMessageRequest(
                    to=chat_id,
                    messages=[TextMessage(text=text)],
                )
            )
            logger.info(f"[LINE] Reply sent via push_message → chat={chat_id}")
        except Exception as push_err:
            logger.error(f"[LINE] push_message also failed: {push_err}")


def _send_error_push(line_api, chat_id: str):
    """發送通用錯誤通知至 LINE 使用者。"""
    try:
        from linebot.v3.messaging import TextMessage, PushMessageRequest

        line_api.push_message(
            PushMessageRequest(
                to=chat_id,
                messages=[
                    TextMessage(text="⚠️ 系統發生內部錯誤，請稍後再試或聯絡管理員。")
                ],
            )
        )
    except Exception as e:
        logger.error(f"[LINE] Failed to send error notification to {chat_id}: {e}")
