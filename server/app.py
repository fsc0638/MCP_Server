"""New application entrypoint."""

import asyncio
import logging

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from server.routes import models, documents, chat, skills, workspace, resources, auth
from server.integrations.line_connector import router as line_router
from main import PROJECT_ROOT
from server.dependencies.uma import get_uma_instance as get_uma
from server.core.retriever import retriever
from server.core.watcher import DirectoryWatcher
from server.dependencies.session import get_session_manager
from server.services.runtime import delta_index_skills, make_llm_callable

logger = logging.getLogger("MCP_Server.App")
__watcher = None
__scheduler = None  # Phase B2: APScheduler instance

app = FastAPI(
    title="MCP Agent Console API",
    description="Refactored entrypoint",
    version="2.2.0",
)

app.include_router(models.router)
app.include_router(documents.router)
app.include_router(chat.router)
app.include_router(skills.router)
app.include_router(workspace.router)
app.include_router(resources.router)
app.include_router(auth.router)
app.include_router(line_router)

frontend_dir = PROJECT_ROOT / "frontend"
app.mount("/ui", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")


# ── Phase B2 + D2: Scheduled Jobs ─────────────────────────────────────────────

def _scheduled_profile_update():
    """Scheduled job: update user/group profiles via LLM deep reasoning."""
    try:
        from server.services.profile_updater import ProfileUpdater
        updater = ProfileUpdater(str(PROJECT_ROOT))
        llm_fn = make_llm_callable()
        if llm_fn:
            updater.run_scheduled_update(llm_callable=llm_fn)
        else:
            logger.warning("[Scheduler] No LLM callable available for profile update.")
    except Exception as e:
        logger.error(f"[Scheduler] Profile update job failed: {e}")


def _scheduled_token_summary():
    """Scheduled job: rebuild token usage summary (daily 17:00)."""
    try:
        from server.services.token_tracker import TokenTracker
        tracker = TokenTracker(str(PROJECT_ROOT))
        tracker.rebuild_summary()
    except Exception as e:
        logger.error(f"[Scheduler] Token summary rebuild failed: {e}")


def _scheduled_cache_cleanup():
    """Scheduled job: cleanup expired message caches (daily 00:00)."""
    try:
        sm = get_session_manager()
        sm.cleanup_all_msg_caches()
    except Exception as e:
        logger.error(f"[Scheduler] Cache cleanup failed: {e}")


def _scheduled_push_tick():
    """Scheduled job: check and execute due push tasks (every minute)."""
    try:
        from server.services.scheduled_push import ScheduledPushService
        from server.integrations.line_connector import _get_line_components, _send_status_push

        svc = ScheduledPushService(str(PROJECT_ROOT))
        llm_fn = make_llm_callable()

        # Build tool_executor from UMA
        tool_executor = None
        try:
            uma = get_uma()
            tool_executor = lambda name, args: uma.execute_tool_call(name, args)
        except Exception:
            pass

        # Build push function
        def push_fn(chat_id: str, text: str):
            try:
                _, line_api, _ = _get_line_components()
                _send_status_push(line_api, chat_id, text)
            except Exception as e:
                logger.error(f"[ScheduledPush] LINE push failed for {chat_id}: {e}")

        svc.check_and_execute(
            llm_callable=llm_fn,
            tool_executor=tool_executor,
            push_fn=push_fn,
        )
    except Exception as e:
        logger.error(f"[Scheduler] Scheduled push tick failed: {e}")


def _scheduled_continuous_learner_tick():
    """Phase 3 scheduled job: continuous learner tick (every 10 minutes)."""
    try:
        from server.services.continuous_learner import ContinuousLearner
        from server.services.learning_compactor import LearningCompactor

        learner = ContinuousLearner(str(PROJECT_ROOT))
        llm_fn = make_llm_callable()
        learner.tick(llm_callable=llm_fn)

        # Step 3: compact/mix raw learnings into a structured snapshot
        LearningCompactor(PROJECT_ROOT).write_snapshot()

        # Step 3b: derive actionable behavior rules (deterministic)
        from server.services.behavior_rule_extractor import BehaviorRuleExtractor
        BehaviorRuleExtractor(PROJECT_ROOT).write()
    except Exception as e:
        logger.error(f"[Scheduler] Continuous learner tick failed: {e}")


def _setup_scheduler():
    """Initialize APScheduler with all scheduled jobs."""
    global __scheduler
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger

        __scheduler = BackgroundScheduler(timezone="Asia/Taipei")

        # Phase B2: Profile deep reasoning — 09:00, 12:00, 17:00 daily
        __scheduler.add_job(
            _scheduled_profile_update,
            CronTrigger(hour="9,12,17", minute=0),
            id="profile_update",
            name="Profile Deep Reasoning Update",
            replace_existing=True,
        )

        # Phase D2: Token summary rebuild — 17:00 daily
        __scheduler.add_job(
            _scheduled_token_summary,
            CronTrigger(hour=17, minute=5),
            id="token_summary",
            name="Token Usage Summary Rebuild",
            replace_existing=True,
        )

        # Phase A1: Cache cleanup — 00:00 daily
        __scheduler.add_job(
            _scheduled_cache_cleanup,
            CronTrigger(hour=0, minute=0),
            id="cache_cleanup",
            name="Message Cache Cleanup",
            replace_existing=True,
        )

        # Scheduled Push: check every minute for due tasks
        from apscheduler.triggers.interval import IntervalTrigger
        __scheduler.add_job(
            _scheduled_push_tick,
            IntervalTrigger(minutes=1),
            id="scheduled_push_tick",
            name="Scheduled Push Tick",
            replace_existing=True,
        )

        # Phase 3: Continuous learner tick — every 10 minutes
        __scheduler.add_job(
            _scheduled_continuous_learner_tick,
            IntervalTrigger(minutes=10),
            id="continuous_learner_tick",
            name="Continuous Learner Tick",
            replace_existing=True,
        )

        __scheduler.start()
        logger.info(
            "[Scheduler] APScheduler started with 5 jobs: profile_update(09/12/17h), token_summary(17h), cache_cleanup(00h), push_tick(1min), continuous_learner(10min)"
        )

    except ImportError:
        logger.warning(
            "[Scheduler] APScheduler not installed. Scheduled jobs disabled. "
            "Install with: pip install apscheduler"
        )
    except Exception as e:
        logger.error(f"[Scheduler] Failed to initialize: {e}")


@app.on_event("startup")
async def startup():
    async def _background_index():
        try:
            uma = get_uma()
            summary = await asyncio.get_event_loop().run_in_executor(None, delta_index_skills, uma, retriever)
            logger.info(
                f"[Startup] Delta index complete - added:{len(summary['added'])} "
                f"updated:{len(summary['updated'])} removed:{len(summary['removed'])} "
                f"unchanged:{len(summary['unchanged'])} errors:{len(summary['errors'])}"
            )
        except Exception as e:
            logger.error(f"[Startup] Background skill indexing failed: {e}")

    async def _sync_workspace_docs():
        try:
            ws_summary = await asyncio.get_event_loop().run_in_executor(None, retriever.sync_workspace, str(PROJECT_ROOT / "workspace"))
            logger.info(
                f"[Startup] Workspace sync complete - added:{len(ws_summary['added'])} "
                f"removed:{len(ws_summary['removed'])} already:{len(ws_summary['already'])}"
            )
        except Exception as e:
            logger.error(f"[Startup] Workspace sync failed: {e}")

    try:
        uma = get_uma()
        global __watcher
        __watcher = DirectoryWatcher(str(PROJECT_ROOT / "workspace"), str(uma.registry.skills_home), retriever)
        __watcher.start()
        asyncio.create_task(_background_index())
        asyncio.create_task(_sync_workspace_docs())

        # Phase B2: Start APScheduler
        _setup_scheduler()

    except Exception as e:
        logger.error(f"[Startup] Failed to initialize background services: {e}")


@app.on_event("shutdown")
async def shutdown():
    global __watcher, __scheduler
    if __watcher is not None:
        __watcher.stop()
    if __scheduler is not None:
        __scheduler.shutdown(wait=False)
        logger.info("[Scheduler] APScheduler shut down.")
    session_mgr = get_session_manager()
    session_mgr.flush_all_sessions(make_llm_callable())
