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
try:
    import redis
except ImportError:
    redis = None

logger = logging.getLogger("MCP_Server.LINE")
router = APIRouter()

# ── Perpetual Session Date Tracker ───────────────────────────────────────────
# {session_id: YYYY-MM-DD}
_session_days = {}

# ── Lazy-initialized LINE SDK components ──────────────────────────────────────
# 延遲初始化，確保缺少 key 時伺服器仍可啟動（降級模式）
_line_handler = None
_line_api = None
_line_api_blob = None


def _get_line_components():
    """
    Lazily initialize LINE SDK WebhookHandler and MessagingApi.
    Raises KeyError if LINE_CHANNEL_SECRET or LINE_CHANNEL_ACCESS_TOKEN is missing.
    """
    global _line_handler, _line_api, _line_api_blob
    if _line_handler is None:
        from linebot.v3 import WebhookHandler
        from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, MessagingApiBlob

        secret = os.environ.get("LINE_CHANNEL_SECRET", "").strip()
        token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip()

        if not secret or not token:
            raise KeyError(
                "LINE_CHANNEL_SECRET and LINE_CHANNEL_ACCESS_TOKEN must be set in .env"
            )

        cfg = Configuration(access_token=token)
        _api_client = ApiClient(cfg)
        _line_api = MessagingApi(_api_client)
        _line_api_blob = MessagingApiBlob(_api_client)
        _line_handler = WebhookHandler(secret)
        logger.info("[LINE] SDK initialized successfully.")

    return _line_handler, _line_api, _line_api_blob


# ── LINE-specific system prompt (Moved to server/services/runtime.py) ─────────
from server.services.runtime import get_universal_system_prompt

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
        handler, line_api, line_api_blob = _get_line_components()
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

    # C. 逐一處理 TextMessage / ImageMessage / FileMessage / StickerMessage Event
    from linebot.v3.webhooks import MessageEvent, TextMessageContent, ImageMessageContent, FileMessageContent, StickerMessageContent
    for event in events:
        if isinstance(event, MessageEvent):
            if not isinstance(event.message, (TextMessageContent, ImageMessageContent, FileMessageContent, StickerMessageContent)):
                continue
            # 解析 session_id：依來源類型決定（user / group / room）
            source = event.source
            user_input = event.message.text if isinstance(event.message, TextMessageContent) else ""
            
            is_group_or_room = False
            if hasattr(source, "group_id") and source.group_id:
                session_id = f"line_group_{source.group_id}"
                chat_id = source.group_id
                is_group_or_room = True
            elif hasattr(source, "room_id") and source.room_id:
                session_id = f"line_room_{source.room_id}"
                chat_id = source.room_id
                is_group_or_room = True
            else:
                session_id = f"line_{source.user_id}"
                chat_id = source.user_id

            # Phase 1: Group Mention Filter & Window
            if is_group_or_room:
                if isinstance(event.message, TextMessageContent):
                    # 支援多種群組喚醒方式：[@Agent K], [@AgentK], @Agent K, @AgentK
                    mentions = ["[@Agent K]", "[@AgentK]", "@Agent K", "@AgentK"]
                    found_mention = False
                    
                    # 1. 首先進入快取 (不論是否叫它，都進快取供後續引用)
                    _add_to_cache(chat_id, event.message.id, user_input)
                    
                    for m in mentions:
                        if m in user_input:
                            user_input = user_input.replace(m, "").strip()
                            found_mention = True
                            break
                    
                    if found_mention:
                        import time
                        _last_request_time[f"mention_{chat_id}"] = time.time()
                    else:
                        # 不是叫它，直接忽略 (bypass processing)
                        logger.info(f"[LINE] Skipped group text (cached but no mention): chat={chat_id}")
                        continue
                else:
                    # For Image/File/Sticker in groups, check if bot was mentioned recently (window of 120s for better UX)
                    import time
                    last_mention = _last_request_time.get(f"mention_{chat_id}", 0)

                    # Phase 6: Proactive Cache
                    # If mentioned within 120s, we process it as a direct command
                    just_cache = (time.time() - last_mention > 120)
                    msg_type = type(event.message).__name__

                    if just_cache:
                        logger.info(f"[LINE] Group {msg_type} received without recent mention. Will only cache: chat={chat_id}")
                    else:
                        logger.info(f"[LINE] Group {msg_type} received with recent mention. Processing: chat={chat_id}")
                    
                    background_tasks.add_task(
                        _process_line_message,
                        line_api=line_api,
                        line_api_blob=line_api_blob,
                        reply_token=event.reply_token,
                        user_id=source.user_id,
                        chat_id=chat_id,
                        session_id=session_id,
                        event_msg=event.message,
                        extracted_text="",
                        quoted_file_path=None,
                        just_cache=just_cache
                    )
                    continue

            # Phase 6: Quote Recognition (引用識別)
            quoted_text = ""
            quoted_file = None
            if isinstance(event.message, TextMessageContent):
                # 嘗試取得引用訊息 ID
                quoted_msg_id = getattr(event.message, "quoted_message_id", None)
                if not quoted_msg_id:
                    m_dict = event.message.to_dict()
                    quoted_msg_id = m_dict.get("quotedMessageId")
                
                if quoted_msg_id:
                    q_data = _get_from_cache(chat_id, quoted_msg_id)
                    quoted_text = q_data.get("text")
                    quoted_file = q_data.get("file_path")
                    
                    if quoted_text:
                        logger.info(f"[LINE] Quoted text found in cache: {quoted_msg_id}")
                        user_input = f"[引用內容: \"{quoted_text}\"]\n{user_input}"
                    if quoted_file:
                        logger.info(f"[LINE] Quoted file found in cache: {quoted_file}")

            background_tasks.add_task(
                _process_line_message,
                line_api=line_api,
                line_api_blob=line_api_blob,
                reply_token=event.reply_token,
                user_id=source.user_id,
                chat_id=chat_id,
                session_id=session_id,
                event_msg=event.message,
                extracted_text=user_input,
                quoted_file_path=quoted_file
            )
            logger.info(
                f"[LINE] Queued background task: session={session_id}, "
                f"input='{user_input[:40]}...'"
            )

    # D. 立即回覆 200 OK — 不等待 LLM 完成
    return "OK"


# ── Proactive Messaging Endpoints ─────────────────────────────────────────────

@router.post("/api/line/push", tags=["Integration"])
async def line_push_message(request: Request):
    """
    主動推送訊息給特定 LINE 用戶。
    Payload: {"chat_id": "...", "text": "..."}
    """
    data = await request.json()
    chat_id = data.get("chat_id")
    text = data.get("text")

    if not chat_id or not text:
        raise HTTPException(status_code=400, detail="Missing chat_id or text")

    try:
        handler, line_api, line_api_blob = _get_line_components()
        _send_line_reply(line_api, None, chat_id, text)
        return {"status": "success", "message": f"Message pushed to {chat_id}"}
    except Exception as e:
        logger.error(f"[LINE] Push failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/line/broadcast", tags=["Integration"])
async def line_broadcast(request: Request):
    """
    向所有活躍的對話對象廣播文字訊息。
    Payload: {"text": "..."}
    """
    data = await request.json()
    text = data.get("text")
    if not text:
        raise HTTPException(status_code=400, detail="Missing text")

    try:
        handler, line_api, line_api_blob = _get_line_components()
        # 從快取的對話中收集 chat_id
        active_chats = set()
        for session_id in list(_last_request_time.keys()):
            if session_id.startswith("line_"):
                # line_U... -> U...
                chat_parts = session_id.split("_", 2)
                chat_id = chat_parts[-1]
                active_chats.add(chat_id)

        count = 0
        for chat_id in active_chats:
            try:
                _send_line_reply(line_api, None, chat_id, text)
                count += 1
            except:
                continue

        return {"status": "success", "broadcast_count": count}
    except Exception as e:
        logger.error(f"[LINE] Broadcast failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Session Locking & UX ──────────────────────────────────────────────────────

_local_locks = {}
_local_lock_mutex = threading.Lock()
_last_request_time = {}  # 紀錄每個 session 的最後處理時間 (Debounce 用)

# ── Message Caching (Phase 6: Quote Support) ──────────────────────────────────
_message_cache = {}  # {chat_id: {msg_id: {"text": str, "file_path": str}}}
_MESSAGE_CACHE_LIMIT = 500  # 每回話上限 (LRU 簡化版)

def _add_to_cache(chat_id: str, msg_id: str, text: str = None, file_path: str = None):
    if chat_id not in _message_cache:
        _message_cache[chat_id] = {}
    cache = _message_cache[chat_id]
    
    # 如果已存在，更新非空欄位
    if msg_id in cache:
        if text: cache[msg_id]["text"] = text
        if file_path: cache[msg_id]["file_path"] = file_path
    else:
        cache[msg_id] = {"text": text, "file_path": file_path}
        
    if len(cache) > _MESSAGE_CACHE_LIMIT:
        oldest_key = next(iter(cache))
        del cache[oldest_key]

def _get_from_cache(chat_id: str, msg_id: str) -> dict:
    """回傳 {"text": str, "file_path": str}"""
    return _message_cache.get(chat_id, {}).get(msg_id, {"text": None, "file_path": None})

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

def _send_loading_animation(line_api, chat_id: str, seconds: int = 20):
    """呼叫 LINE Loading Animation API (使用官方 SDK)。可多次呼叫以延長動畫。"""
    from linebot.v3.messaging import ShowLoadingAnimationRequest

    try:
        # Note: LINE ShowLoadingAnimation only supports userId (Individual Chat).
        # It returns 400 Bad Request for groupId or roomId.
        if chat_id.startswith("U"):
            req = ShowLoadingAnimationRequest(chatId=chat_id, loadingSeconds=min(seconds, 60))
            line_api.show_loading_animation(req)
            logger.info(f"[LINE] Loading animation started for chat={chat_id} ({seconds}s)")
        else:
            logger.info(f"[LINE] Skipping loading animation for non-user chat={chat_id}")
    except Exception as e:
        logger.warning(f"[LINE] Exception starting loading animation: {e}")


def _send_status_push(line_api, chat_id: str, text: str):
    """推送中間狀態訊息給使用者（不佔用 reply_token）。"""
    try:
        from linebot.v3.messaging import TextMessage, PushMessageRequest
        line_api.push_message(
            PushMessageRequest(to=chat_id, messages=[TextMessage(text=text)])
        )
    except Exception as e:
        logger.warning(f"[LINE] Status push failed: {e}")


# ── Background Processing Function ────────────────────────────────────────────

def _preprocess_image(file_path: str) -> str:
    """實作 HEIC/RAW 自動轉 JPG 的預處理機制"""
    lower_path = file_path.lower()
    try:
        if lower_path.endswith(('.heic', '.heif')):
            import pillow_heif
            from PIL import Image
            heif_file = pillow_heif.read_heif(file_path)
            image = Image.frombytes(heif_file.mode, heif_file.size, heif_file.data)
            jpg_path = file_path.rsplit('.', 1)[0] + '.jpg'
            image.save(jpg_path, format="JPEG")
            return jpg_path
        elif lower_path.endswith(('.cr2', '.nef', '.arw', '.dng')):
            import rawpy
            from PIL import Image
            with rawpy.imread(file_path) as raw:
                rgb = raw.postprocess()
            image = Image.fromarray(rgb)
            jpg_path = file_path.rsplit('.', 1)[0] + '.jpg'
            image.save(jpg_path, format="JPEG")
            return jpg_path
    except Exception as e:
        logger.error(f"[LINE BG] Pre-process Failed for {file_path}: {e}")
    return file_path


def _download_sticker_image(sticker_id: str, uploads_dir: str, resource_type: str = "STATIC"):
    """
    嘗試從 LINE CDN 下載貼圖圖片，供 OpenAI Vision 辨識貼圖表情、動作與文字。
    針對不同貼圖類型 (STATIC / ANIMATION / POPUP) 使用不同的 CDN 路徑。
    回傳圖片路徑，失敗則回傳 None。
    """
    import os
    cached_path = os.path.join(uploads_dir, f"sticker_{sticker_id}.png")
    if os.path.exists(cached_path):
        return cached_path

    base = f"https://stickershop.line-scdn.net/stickershop/v1/sticker/{sticker_id}"

    # 依貼圖類型排列優先度：靜態 key frame 最清楚（文字可辨識），動態次之
    if resource_type in ("ANIMATION", "ANIMATION_SOUND", "POPUP", "POPUP_SOUND"):
        cdn_urls = [
            f"{base}/iPhone/sticker_key@2x.png",      # 動態貼圖的靜態 key frame（最清晰）
            f"{base}/iPhone/sticker@2x.png",           # 可能是 APNG，但 fallback 仍可用
            f"{base}/android/sticker.png",
        ]
    else:
        cdn_urls = [
            f"{base}/iPhone/sticker@2x.png",           # 標準靜態高解析度
            f"{base}/iPhone/sticker_key@2x.png",       # 部分靜態貼圖也有 key frame
            f"{base}/android/sticker.png",
            f"{base}/iPhone/sticker.png",
        ]

    for url in cdn_urls:
        try:
            resp = httpx.get(url, timeout=8, follow_redirects=True)
            if resp.status_code == 200 and len(resp.content) > 500:
                os.makedirs(uploads_dir, exist_ok=True)
                with open(cached_path, "wb") as f:
                    f.write(resp.content)
                logger.info(f"[LINE] Sticker image downloaded: id={sticker_id} type={resource_type} url={url} size={len(resp.content)}")
                return cached_path
        except Exception as e:
            logger.debug(f"[LINE] Sticker CDN failed ({url}): {e}")
            continue

    logger.warning(f"[LINE] All CDN URLs failed for sticker_id={sticker_id} type={resource_type}")
    return None


def _process_line_message(
    line_api,
    line_api_blob,
    reply_token: str,
    user_id: str,
    chat_id: str,
    session_id: str,
    event_msg,
    extracted_text: str = "",
    quoted_file_path: str = None,
    just_cache: bool = False
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
    _session_mgr = get_session_manager()
    import time

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
        #     使用 60 秒以涵蓋檔案下載 + Tool Calling 耗時
        _send_loading_animation(line_api, chat_id, 60)

        try:
            from linebot.v3.webhooks import TextMessageContent, ImageMessageContent, FileMessageContent, StickerMessageContent
            import os

            attached_file_path = quoted_file_path
            if isinstance(event_msg, TextMessageContent):
                user_input = extracted_text
                # 如果有引用檔案，注入特別提示
                if attached_file_path:
                    fname = os.path.basename(attached_file_path)
                    user_input = f"[系統通知：使用者引用了先前上傳的檔案 {fname}。檔案絕對路徑：{attached_file_path}]\n" + user_input

            elif isinstance(event_msg, StickerMessageContent):
                # ── Phase 7: Sticker Emotion Recognition ─────────────────────
                sticker_id = event_msg.sticker_id
                package_id = event_msg.package_id
                sticker_keywords = event_msg.keywords or []
                sticker_text = event_msg.text or ""
                resource_type = event_msg.sticker_resource_type or "STATIC"

                logger.info(
                    f"[LINE BG] Sticker received: pkg={package_id} id={sticker_id} "
                    f"keywords={sticker_keywords} text='{sticker_text}' type={resource_type}"
                )

                # 快取貼圖情緒資訊供後續引用
                cache_text = f"[貼圖: {', '.join(sticker_keywords)}]" if sticker_keywords else "[貼圖]"
                if sticker_text:
                    cache_text += f" {sticker_text}"
                _add_to_cache(chat_id, event_msg.id, text=cache_text)

                if just_cache:
                    logger.info(f"[LINE BG] Just cached sticker: session={session_id}, sticker_id={sticker_id}")
                    return

                # 嘗試下載貼圖圖片，供 Vision 模型辨識表情、動作與文字
                uploads_dir = os.path.join(os.getcwd(), "Agent_workspace", "line_uploads")
                sticker_image = _download_sticker_image(sticker_id, uploads_dir, resource_type)
                if sticker_image:
                    attached_file_path = sticker_image

                # 組裝情緒描述
                emotion_parts = []
                if sticker_keywords:
                    emotion_parts.append(f"情緒關鍵字：{', '.join(sticker_keywords)}")
                if sticker_text:
                    emotion_parts.append(f"貼圖中使用者自訂文字：「{sticker_text}」")
                emotion_desc = "；".join(emotion_parts) if emotion_parts else "（無 API 提供的情緒標籤）"

                has_image = "有" if sticker_image else "無"
                user_input = (
                    f"[系統通知：使用者傳送了一個 LINE 貼圖。\n"
                    f"貼圖後設資料：packageId={package_id}, stickerId={sticker_id}, 類型={resource_type}\n"
                    f"{emotion_desc}\n"
                    f"貼圖圖片：{has_image}\n\n"
                    f"【最重要 — 貼圖圖片 OCR】\n"
                    f"許多 LINE 貼圖的圖片上會印有中文、日文或英文字句（例如「辛苦了」「我罩你」「加油」等）。\n"
                    f"你必須仔細辨識圖片中所有文字並在回覆中自然融入，這是使用者表達的重點。\n"
                    f"如果圖片中有文字，請優先根據該文字的語意來理解使用者想表達什麼。\n\n"
                    f"【回覆風格】\n"
                    f"1. 觀察貼圖角色的表情、動作、姿態，加上圖中文字，綜合理解使用者情緒。\n"
                    f"2. 用活潑、口語化的語氣回覆，像朋友聊天一樣自然。\n"
                    f"3. 禁止說「你傳了一個貼圖」或複述系統通知內容。\n"
                    f"4. 禁止使用制式結語（如「如果有其他問題或需要協助的地方，隨時告訴我」之類的客套話）。\n"
                    f"5. 適當使用 emoji 讓回覆生動，但不過度。\n"
                    f"6. 結合對話上下文與貼圖情緒一起回覆。]"
                )

                _send_loading_animation(line_api, chat_id, 60)

            else:
                # Phase 2: Inbound Multimedia Download and Processing
                try:
                    import concurrent.futures
                    # Timeout protection: file download must complete within 30 seconds
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                        future = pool.submit(line_api_blob.get_message_content, event_msg.id)
                        try:
                            content_blob = future.result(timeout=30)
                        except concurrent.futures.TimeoutError:
                            raise TimeoutError(f"File download timed out after 30s (msg_id={event_msg.id})")

                    uploads_dir = os.path.join(os.getcwd(), "Agent_workspace", "line_uploads")
                    os.makedirs(uploads_dir, exist_ok=True)

                    if isinstance(event_msg, ImageMessageContent):
                        attached_file_path = os.path.join(uploads_dir, f"{event_msg.id}.jpg")
                        with open(attached_file_path, "wb") as f:
                            f.write(content_blob)

                        attached_file_path = _preprocess_image(attached_file_path)
                        user_input = "[系統通知：使用者傳送了一張圖片。請務必直接回答這張圖片裡面有什麼內容與細節，不要拒絕。]"

                    elif isinstance(event_msg, FileMessageContent):
                        filename = getattr(event_msg, "file_name", f"{event_msg.id}.bin")
                        attached_file_path = os.path.join(uploads_dir, f"{event_msg.id}_{filename}")
                        with open(attached_file_path, "wb") as f:
                            f.write(content_blob)

                        if attached_file_path.lower().endswith(('.heic', '.heif', '.cr2', '.nef', '.arw')):
                            attached_file_path = _preprocess_image(attached_file_path)
                            user_input = f"[系統通知：使用者傳送了一張高畫質圖片 {filename}，已轉為 {os.path.basename(attached_file_path)} 供您檢視]"
                        else:
                            abs_path = os.path.abspath(attached_file_path)
                            lower_name = filename.lower()

                            # All document types: LLM reads content via python-executor then analyzes semantically
                            # Supported: .docx, .txt, .md, .pdf, .xlsx, .xls, .csv, .json, .log, .py, .js, .xml
                            if lower_name.endswith(('.docx',)):
                                lib_hint = "python-docx"
                            elif lower_name.endswith(('.pdf',)):
                                lib_hint = "pdfplumber"
                            elif lower_name.endswith(('.xlsx', '.xls')):
                                lib_hint = "openpyxl 或 pandas"
                            elif lower_name.endswith(('.csv',)):
                                lib_hint = "pandas (pd.read_csv)"
                            else:
                                lib_hint = "open() with utf-8 encoding"

                            user_input = (
                                f"[系統通知：使用者上傳了文件 {filename}。檔案絕對路徑：{abs_path}。\n\n"
                                f"請先呼叫 `mcp-python-executor` 使用 {lib_hint} 讀取檔案內容並 print 出來，"
                                f"然後根據你的語意理解能力直接分析、總結或處理文件內容。\n"
                                f"環境已預裝 python-docx, pdfplumber, pandas, openpyxl, chardet。]"
                            )

                    # Re-trigger loading animation after download (timer may have expired)
                    _send_loading_animation(line_api, chat_id, 60)

                except Exception as e:
                    logger.error(f"[LINE BG] Download failed: {e}")
                    _send_status_push(line_api, chat_id, f"⚠️ 檔案下載失敗，請重新傳送。\n({e})")
                    return
                
                # 下載成功後補進快取，供後續引用
                if attached_file_path:
                    _add_to_cache(chat_id, event_msg.id, file_path=attached_file_path)

                if just_cache:
                    logger.info(f"[LINE BG] Just cached media: session={session_id}, msg_id={event_msg.id}")
                    return

            # 1. 取得或建立 Session（首次建立時注入 LINE 專屬 system prompt）
            # 注意：為了解決日期幻覺，偵測跨日對話並強制重置 OpenAI 狀態
            from datetime import datetime
            today_str = datetime.now().strftime("%Y-%m-%d")
            
            # Check for new day or new session in tracker
            if _session_days.get(session_id) != today_str:
                logger.info(f"[LINE BG] New day/session detected for {session_id} ({today_str}). Resetting OpenAI state.")
                _session_mgr.reset_openai_state(session_id)
                _session_days[session_id] = today_str

            _session_mgr.get_or_create_conversation(session_id, get_universal_system_prompt(platform="line"))
            _session_mgr._update_system_prompt(session_id, get_universal_system_prompt(platform="line"))
    
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
                # 擷取 System Prompt + 最近對話歷史
                # truncation="auto" 已在 Adapter 層防護 context overflow，
                # 此處放寬至 20 條訊息 (10 輪) 以提供更豐富的上下文
                history = _session_mgr.get_or_create_conversation(session_id)
                system_msgs = [m for m in history if m.get("role") == "system"]
                recent_msgs = [m for m in history if m.get("role") != "system"][-20:]
                truncated_history = system_msgs + recent_msgs

                # 傳入截斷的 history 副本，避免 generator 消費途中 list 被外部修改
                # execute_mode=False (/chat 模式) 時跳過 Tool Schema 注入，大幅節省 token
                result_gen = adapter.chat(
                    messages=truncated_history,
                    user_query=actual_input,
                    session_id=session_id,
                    attached_file=attached_file_path,
                    tools_enabled=execute_mode
                )

                # 5. 消費同步 Generator，組裝完整回覆字串
                #    傳入 line_api/chat_id 以便在 Tool Call 時刷新 loading 動畫
                final_reply = _collect_generator(result_gen, line_api=line_api, chat_id=chat_id)

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


def _collect_generator(result_gen, line_api=None, chat_id: str = None) -> str:
    """
    消費 adapter.chat() 的同步 generator，組裝完整回覆文字。

    當偵測到 Tool Call 中間狀態時，自動刷新 LINE loading 動畫，
    確保使用者在多輪 Tool Calling 期間持續看到 "..." 動畫。

    Generator 的 chunk 格式：
    - {"status": "streaming", "content": "<partial text>"}
    - {"status": "success",   "content": "<full text>"}
    - {"status": "error",     "message": "<error msg>"}
    """
    accumulated = ""
    for chunk in result_gen:
        status = chunk.get("status")
        if status == "streaming":
            content = chunk.get("content", "")
            accumulated += content
            # Detect tool call marker from OpenAI adapter: "⚙️ 執行技能: ..."
            # Re-trigger loading animation so user keeps seeing "..." during long tool calls
            if line_api and chat_id and "⚙️" in content:
                _send_loading_animation(line_api, chat_id, 60)
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
