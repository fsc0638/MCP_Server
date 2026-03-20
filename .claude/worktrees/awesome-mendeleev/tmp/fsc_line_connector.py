"""
LINE Connector ??MCP_Server Integration Module
===============================================
Integrates LINE Messaging API into the existing FastAPI + UMA architecture.

Architecture:
  Inbound  ??POST /api/line/webhook   (signature verify + parse events)
  Session  ??core.session.SessionManager  (session_id = "line_{user_id}")
  Outbound ??OpenAIAdapter.chat() ??reply_message / push_message fallback

Design Principle:
  -蝡?? 200 OK (閫?捱 LINE Webhook 1~2 蝘?Timeout ?)
  - BackgroundTasks ??甇亙銵?LLM ????Tool Calling
  - 摰??暹? SessionManager + MEMORY.md ??????"""
import logging
import os
import threading
from contextlib import contextmanager

from fastapi import APIRouter, Request, BackgroundTasks, HTTPException
import httpx
import redis

logger = logging.getLogger("MCP_Server.LINE")
router = APIRouter()

# ?? Lazy-initialized LINE SDK components ??????????????????????????????????????
# 撱園????蝣箔?蝻箏? key ?撩?隞??嚗?蝝芋撘?
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


# ?? LINE-specific system prompt ????????????????????????????????????????????????
def _get_dynamic_system_prompt() -> str:
    from datetime import datetime
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    base_url = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")
    return (
        f"雿?蝯?MCP Agent Console ??LINE AI ?拍??n"
        f"?曉???荔?{now_str}\n"
        f"隢誑蝜?銝剜??陛瞏????雿輻?n"
        f"?仿?閬銵??賢極?瘀?隢?亙銵蒂?蝯??n\n"
        f"?雯頝舀?撠?蝭n"
        f"- ?嗡?雿輻 `mcp-web-search` ?脣?鞈?敺?敹??冽??恍?銝???皞??澆?蝭?嚗n"
        f"  [1] 璅? - 蝬脣?\n"
        f"  [2] 璅? - 蝬脣?\n"
        f"- ?乩蝙?刻票銝雯?閬???嚗??芸?雿輻 `mcp-web-search` ??`target_url` ??脰??渲??n\n"
        f"??隞嗥???蝭n"
        f"憒?雿蝙?冽?撠?????隢Ⅱ撠?撠???渡敦蝭?神?交?獢葉嚗?閬撖急?憿n"
        f"瑼?摮?潘?`{os.path.join(os.getcwd(), 'workspace', 'downloads')}`\n"
        f"銝行?靘?頛雯?嚗{base_url}/downloads/瑼??迂`?n\n"
        f"??隢?嗅 3000 摮誑?改?靽?皜????
    )

# ?? LINE ?桀?閮摮?銝? ???????????????????????????????????????????????????????
_LINE_MAX_CHARS = 4900


# ?? Webhook Endpoint ??????????????????????????????????????????????????????????

@router.post("/api/line/webhook", tags=["Integration"])
async def line_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    LINE Messaging API Webhook ?交蝡舫???
    瘚?嚗?    A. 撽? X-Line-Signature嚗?賡?瘙?
    B. 閫?? LINE Events
    C. TextMessage ??銝 BackgroundTasks嚗圾??LLM 撱園嚗?    D. 蝡?? 200 OK嚗圾瘙?Timeout ?園嚗?    """
    # A. ??銝阡?霅?Signature
    try:
        handler, line_api, line_api_blob = _get_line_components()
    except KeyError as e:
        logger.error(f"[LINE] Missing configuration: {e}")
        raise HTTPException(status_code=500, detail=f"LINE configuration error: {e}")

    signature = request.headers.get("X-Line-Signature", "")
    body_bytes = await request.body()
    body_text = body_bytes.decode("utf-8")

    # B. 閫??鈭辣
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

    # C. ???? TextMessage / ImageMessage / FileMessage Event
    from linebot.v3.webhooks import MessageEvent, TextMessageContent, ImageMessageContent, FileMessageContent
    for event in events:
        if isinstance(event, MessageEvent):
            if not isinstance(event.message, (TextMessageContent, ImageMessageContent, FileMessageContent)):
                continue
            # 閫?? session_id嚗?靘?憿?瘙箏?嚗ser / group / room嚗?            source = event.source
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
                    # ?舀憭車蝢斤????孵?嚗@Agent K], [@AgentK], @Agent K, @AgentK
                    mentions = ["[@Agent K]", "[@AgentK]", "@Agent K", "@AgentK"]
                    found_mention = False
                    
                    # 1. 擐??脣敹怠? (銝??臬?怠?嚗?脣翰??敺?撘)
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
                        # 銝?怠?嚗?亙蕭??(bypass processing)
                        logger.info(f"[LINE] Skipped group text (cached but no mention): chat={chat_id}")
                        continue
                else:
                    # For Image/File in groups, check if bot was mentioned recently (window of 60s)
                    import time
                    last_mention = _last_request_time.get(f"mention_{chat_id}", 0)
                    
                    # Phase 6: Proactive Cache
                    just_cache = (time.time() - last_mention > 60)
                    if just_cache:
                        logger.info(f"[LINE] Group media/file received without mention. Will only cache: chat={chat_id}")
                    
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

            # Phase 6: Quote Recognition (撘霅)
            quoted_text = ""
            quoted_file = None
            if isinstance(event.message, TextMessageContent):
                # ?岫??撘閮 ID
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
                        user_input = f"[撘?批捆: \"{quoted_text}\"]\n{user_input}"
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

    # D. 蝡?? 200 OK ??銝?敺?LLM 摰?
    return "OK"


# ?? Session Locking & UX ??????????????????????????????????????????????????????

_local_locks = {}
_local_lock_mutex = threading.Lock()
_last_request_time = {}  # 蝝????session ??敺?????(Debounce ??

# ?? Message Caching (Phase 6: Quote Support) ??????????????????????????????????
_message_cache = {}  # {chat_id: {msg_id: {"text": str, "file_path": str}}}
_MESSAGE_CACHE_LIMIT = 500  # 瘥?閰曹???(LRU 蝪∪???

def _add_to_cache(chat_id: str, msg_id: str, text: str = None, file_path: str = None):
    if chat_id not in _message_cache:
        _message_cache[chat_id] = {}
    cache = _message_cache[chat_id]
    
    # 憒?撌脣??剁??湔?征甈?
    if msg_id in cache:
        if text: cache[msg_id]["text"] = text
        if file_path: cache[msg_id]["file_path"] = file_path
    else:
        cache[msg_id] = {"text": text, "file_path": file_path}
        
    if len(cache) > _MESSAGE_CACHE_LIMIT:
        oldest_key = next(iter(cache))
        del cache[oldest_key]

def _get_from_cache(chat_id: str, msg_id: str) -> dict:
    """? {"text": str, "file_path": str}"""
    return _message_cache.get(chat_id, {}).get(msg_id, {"text": None, "file_path": None})

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
    """?澆 LINE Loading Animation API (雿輻摰 SDK)"""
    from linebot.v3.messaging import ShowLoadingAnimationRequest

    try:
        req = ShowLoadingAnimationRequest(chatId=chat_id, loadingSeconds=20)
        line_api.show_loading_animation(req)
        logger.info(f"[LINE] Loading animation started for chat={chat_id}")
    except Exception as e:
        logger.warning(f"[LINE] Exception starting loading animation: {e}")


# ?? Background Processing Function ????????????????????????????????????????????

def _preprocess_image(file_path: str) -> str:
    """撖虫? HEIC/RAW ?芸?頧?JPG ????璈"""
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
    ??賣嚗LM ?? ??Tool ?瑁? ??蝯??? ???? LINE??
    ??暹??辣嚗?    - router._session_mgr  (SessionManager)
    - OpenAIAdapter.chat() (full Tool Calling + RAG)
    - MEMORY.md ????append_message ?芸?閫貊嚗?    """
    from main import get_uma
    from adapters.openai_adapter import OpenAIAdapter
    from router import _session_mgr  # ?梁?? SessionManager 撖虫?
    import time

    logger.info(f"[LINE BG] Start processing: session={session_id}")

    # 0. ?脤??璈 (Debounce)嚗?瞈?2 蝘??閫貊??隞?    current_time = time.time()
    last_time = _last_request_time.get(session_id, 0)
    if current_time - last_time < 2.0:
        logger.warning(f"[LINE BG] Debounced input for session={session_id} (Too fast)")
        return
    _last_request_time[session_id] = current_time

    with _acquire_session_lock(session_id) as acquired:
        if not acquired:
            logger.warning(f"[LINE BG] Session {session_id} is locked. Ignoring concurrent input.")
            return

        # 0.5 憿舐內 loading ? (摰雿輻??敺??嚗????chat_id
        _send_loading_animation(line_api, chat_id)

        try:
            from linebot.v3.webhooks import TextMessageContent, ImageMessageContent, FileMessageContent
            import os

            attached_file_path = quoted_file_path
            if isinstance(event_msg, TextMessageContent):
                user_input = extracted_text
                # 憒????冽?獢?瘜典?孵?內
                if attached_file_path:
                    fname = os.path.basename(attached_file_path)
                    user_input = f"[蝟餌絞?嚗蝙?刻??其???銝??獢?{fname}??獢?撠楝敺?{attached_file_path}]\n" + user_input
            else:
                # Phase 2: Inbound Multimedia Download and Processing
                try:
                    content_blob = line_api_blob.get_message_content(event_msg.id)
                    uploads_dir = os.path.join(os.getcwd(), "Agent_workspace", "line_uploads")
                    os.makedirs(uploads_dir, exist_ok=True)
                    
                    if isinstance(event_msg, ImageMessageContent):
                        attached_file_path = os.path.join(uploads_dir, f"{event_msg.id}.jpg")
                        with open(attached_file_path, "wb") as f:
                            f.write(content_blob)
                            
                        attached_file_path = _preprocess_image(attached_file_path)
                        user_input = "[蝟餌絞?嚗蝙?刻??銝撘萄??????湔???撐??鋆⊿??暻澆摰寡?蝝啁?嚗?閬?蝯"
                        
                    elif isinstance(event_msg, FileMessageContent):
                        filename = getattr(event_msg, "file_name", f"{event_msg.id}.bin")
                        attached_file_path = os.path.join(uploads_dir, f"{event_msg.id}_{filename}")
                        with open(attached_file_path, "wb") as f:
                            f.write(content_blob)
                            
                        if attached_file_path.lower().endswith(('.heic', '.heif', '.cr2', '.nef', '.arw')):
                            attached_file_path = _preprocess_image(attached_file_path)
                            user_input = f"[蝟餌絞?嚗蝙?刻??銝撘菟??怨釭?? {filename}嚗歇頧 {os.path.basename(attached_file_path)} 靘瑼Ｚ?]"
                        else:
                            abs_path = os.path.abspath(attached_file_path)
                            # Phase 2: Knowledge Base Document Instruction
                            user_input = (
                                f"[蝟餌絞?嚗蝙?刻??喃??辣 {filename}??獢?撠楝敺?{abs_path}?n\n"
                                f"?之?內嚗n"
                                f"1. ??怠????誘??嚗anual嚗????撘Ⅳ蝭??n"
                                f"2. **敺??敺?銝?頛芰?撠?甇Ｗ?甈∪?怨府????* 雿????喳??蒂?澆 `mcp-python-executor` ?啣神銝血銵???撘Ⅳ?n"
                                f"3. ?啣?撌脤?鋆?pypdf, pdfplumber, pandas, python-docx???湔?箸??蝭??脰????"
                            )
                        
                except Exception as e:
                    logger.error(f"[LINE BG] Download failed: {e}")
                    user_input = "[蝟餌絞?嚗蝙?刻??瑼?嚗?隡箸??其?頛仃?"
                
                # 銝???敺??脣翰??靘?蝥???                if attached_file_path:
                    _add_to_cache(chat_id, event_msg.id, file_path=attached_file_path)

                if just_cache:
                    logger.info(f"[LINE BG] Just cached media: session={session_id}, msg_id={event_msg.id}")
                    return

            # 1. ???遣蝡?Session嚗?甈∪遣蝡?瘜典 LINE 撠惇 system prompt嚗?            # 瘜冽?嚗鈭圾瘙箸?劂閬綽?瘥活撠店?質?靽????舀??啁?嚗? session ?萇?敺???撖?system prompt
            # ?隞交??瘥活撠店??撘瑕?湔 System Prompt
            _session_mgr.get_or_create_conversation(session_id, _get_dynamic_system_prompt())
            _session_mgr._update_system_prompt(session_id, _get_dynamic_system_prompt())
    
            # 2. 餈賢?雿輻???航 Session
            _session_mgr.append_message(session_id, "user", user_input)

            # 3. 閫???航??隞文?蝬湛?????璅∪?
            actual_input, execute_mode = _parse_command_prefix(user_input)

            # 4. ????Adapter嚗蝙?券?閮?model嚗靘?瘙 Gemini/Claude嚗?            uma = get_uma()
            adapter = OpenAIAdapter(uma=uma)

            if not adapter.is_available:
                final_reply = "?? AI ???急??⊥?雿輻嚗?蝣箄? OPENAI_API_KEY 閮剖???
            else:
                # ?箔??踹???瑟?撠店撠 OpenAI 429 Too Many Requests (Token Limit)
                # 撘瑕?芣??System Prompt + ?餈?5 頛芸?閰?(10 璇???
                # ??? _update_system_prompt嚗??啣?敺???history
                history = _session_mgr.get_or_create_conversation(session_id)
                system_msgs = [m for m in history if m.get("role") == "system"]
                recent_msgs = [m for m in history if m.get("role") != "system"][-10:]
                truncated_history = system_msgs + recent_msgs

                # ?喳?芣??history ?舀嚗??generator 瘨祥?葉 list 鋡怠??其耨??                result_gen = adapter.chat(
                    messages=truncated_history,
                    user_query=actual_input,
                    session_id=session_id,
                    attached_file=attached_file_path
                )

                # 5. 瘨祥?郊 Generator嚗?鋆??游?閬?銝?                final_reply = _collect_generator(result_gen)

            # 6. ?芣??LINE 摮?銝?
            if len(final_reply) > _LINE_MAX_CHARS:
                final_reply = final_reply[:_LINE_MAX_CHARS] + "\n\n?佗????嚗歇?芣嚗?

            # 7. 撖怠 Session 閮嚗孛??MEMORY.md ????
            _session_mgr.append_message(session_id, "assistant", final_reply)

            # 8. ? LINE嚗eply_token ?芸?嚗暹?敺? push_message嚗?            _send_line_reply(line_api, reply_token, chat_id, final_reply)

        except Exception as e:
            logger.error(
                f"[LINE BG] Unhandled error for session={session_id}: {e}", exc_info=True
            )
            _send_error_push(line_api, chat_id)


def _parse_command_prefix(user_input: str) -> tuple[str, bool]:
    """
    閫?? LINE 閮?韌?誘嚗??捱摰銵芋撘?
    /tool <msg>  ??Agent 璅∪?嚗撥??Tool Calling嚗?    /chat <msg>  ??蝝?閰望芋撘?    ?嗡?         ???身 Agent 璅∪?
    """
    if user_input.startswith("/tool "):
        return user_input[6:].strip(), True
    elif user_input.startswith("/chat "):
        return user_input[6:].strip(), False
    return user_input, True  # ?身? Tool Calling


def _collect_generator(result_gen) -> str:
    """
    瘨祥 adapter.chat() ??甇?generator嚗?鋆??游?閬?摮?
    Generator ??chunk ?澆?嚗?    - {"status": "streaming", "content": "<partial text>"}
    - {"status": "success",   "content": "<full text>"}
    - {"status": "error",     "message": "<error msg>"}
    """
    accumulated = ""
    for chunk in result_gen:
        status = chunk.get("status")
        if status == "streaming":
            accumulated += chunk.get("content", "")
        elif status == "success":
            # success chunk ?摰?蝯摰?            final = chunk.get("content", "")
            return final if final else accumulated
        elif status == "error":
            err_msg = chunk.get("message", "?芰?航炊")
            logger.error(f"[LINE BG] Adapter error: {err_msg}")
            return f"???潛??航炊嚗err_msg}"
        elif status == "requires_approval":
            tool_name = chunk.get("tool_name", "?芰撌亙")
            return (
                f"?? 撌亙 `{tool_name}` ?閬犖撌亦Ⅱ隤???瑁??n"
                "隢 Web Console ??甇日?憸券????
            )

    return accumulated if accumulated else "嚗I ?芰??閬?隢?敺?閰佗?"


def _send_line_reply(line_api, reply_token: str, chat_id: str, text: str):
    """
    ?潮?閬 LINE??    ?芸?雿輻 reply_token嚗? 30 蝘???嚗暹?敺???push_message??    """
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
        # reply_token 撌脤???憭望?嚗??push_message 銝餃??券?        logger.warning(
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
    """?潮?航炊???LINE 雿輻??""
    try:
        from linebot.v3.messaging import TextMessage, PushMessageRequest

        line_api.push_message(
            PushMessageRequest(
                to=chat_id,
                messages=[
                    TextMessage(text="?? 蝟餌絞?潛??折?航炊嚗?蝔??岫?蝯∠恣???)
                ],
            )
        )
    except Exception as e:
        logger.error(f"[LINE] Failed to send error notification to {chat_id}: {e}")
