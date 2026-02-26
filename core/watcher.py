import logging
import time
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

logger = logging.getLogger("MCP_Server.Watcher")

class WorkspaceEventHandler(FileSystemEventHandler):
    """Watches the workspace/ directory for document changes."""
    def __init__(self, retriever):
        self.retriever = retriever
        # Very simple debounce logic map: filepath -> last_processed_time
        self.last_handled = {}

    def _debounce(self, path: str) -> bool:
        """Returns True if should process, False if debounced."""
        now = time.time()
        last = self.last_handled.get(path, 0)
        if now - last < 2.0:  # 2 second debounce
            return False
        self.last_handled[path] = now
        return True

    def _is_supported(self, path: str) -> bool:
        ext = Path(path).suffix.lower()
        return ext in {".txt", ".md", ".pdf", ".csv"}

    def on_created(self, event):
        if event.is_directory or getattr(event, 'is_synthetic', False):
            return
        if self._is_supported(event.src_path) and self._debounce(event.src_path):
            logger.info(f"Workspace file created: {event.src_path}. Ingesting...")
            self.retriever.ingest_document(event.src_path)

    def on_modified(self, event):
        if event.is_directory or getattr(event, 'is_synthetic', False):
            return
        if self._is_supported(event.src_path) and self._debounce(event.src_path):
            logger.info(f"Workspace file modified: {event.src_path}. Re-ingesting...")
            # Automatically handled by ingest_document which checks if docstore already has it
            # To be 100% safe, delete then ingest
            filename = Path(event.src_path).name
            self.retriever.delete_document(filename)
            self.retriever.ingest_document(event.src_path)

    def on_deleted(self, event):
        if event.is_directory or getattr(event, 'is_synthetic', False):
            return
        if self._is_supported(event.src_path):
            filename = Path(event.src_path).name
            logger.info(f"Workspace file deleted: {event.src_path}. Removing from FAISS...")
            self.retriever.delete_document(filename)

    def on_moved(self, event):
        if event.is_directory or getattr(event, 'is_synthetic', False):
            return
        if self._is_supported(event.src_path):
            old_name = Path(event.src_path).name
            logger.info(f"Workspace file moved from: {event.src_path}. Removing old from FAISS...")
            self.retriever.delete_document(old_name)
        if self._is_supported(event.dest_path) and self._debounce(event.dest_path):
            logger.info(f"Workspace file moved to: {event.dest_path}. Ingesting new...")
            self.retriever.ingest_document(event.dest_path)


class SkillEventHandler(FileSystemEventHandler):
    """Watches the skills/ directory for SKILL.md changes."""
    def __init__(self, retriever):
        self.retriever = retriever
        self.last_handled = {}

    def _debounce(self, path: str) -> bool:
        now = time.time()
        last = self.last_handled.get(path, 0)
        if now - last < 2.0:
            return False
        self.last_handled[path] = now
        return True

    def _is_skill_md(self, path: str) -> bool:
        return Path(path).name == "SKILL.md"

    def _get_skill_name(self, path: str) -> str:
        # returns the parent directory name, which is the skill_name
        return Path(path).parent.name

    def on_created(self, event):
        if event.is_directory or getattr(event, 'is_synthetic', False):
            return
        if self._is_skill_md(event.src_path) and self._debounce(event.src_path):
            skill_name = self._get_skill_name(event.src_path)
            logger.info(f"Skill manually created: {skill_name}. Ingesting...")
            self.retriever.ingest_skill(skill_name, event.src_path)

    def on_modified(self, event):
        if event.is_directory or getattr(event, 'is_synthetic', False):
            return
        if self._is_skill_md(event.src_path) and self._debounce(event.src_path):
            skill_name = self._get_skill_name(event.src_path)
            logger.info(f"Skill manually modified: {skill_name}. Re-ingesting...")
            self.retriever.ingest_skill(skill_name, event.src_path)

    def on_deleted(self, event):
        if getattr(event, 'is_synthetic', False):
            return
        path_obj = Path(event.src_path)
        # If the skill directory is deleted, or just SKILL.md is deleted
        if event.is_directory:
            skill_name = path_obj.name
            logger.info(f"Skill directory deleted: {skill_name}. Removing from FAISS...")
            self.retriever.delete_document(skill_name)
        elif path_obj.name == "SKILL.md":
            skill_name = path_obj.parent.name
            logger.info(f"SKILL.md deleted: {skill_name}. Removing from FAISS...")
            self.retriever.delete_document(skill_name)

    def on_moved(self, event):
        if getattr(event, 'is_synthetic', False):
            return
        if event.is_directory:
            # Skill renamed
            old_name = Path(event.src_path).name
            new_name = Path(event.dest_path).name
            logger.info(f"Skill directory renamed {old_name} -> {new_name}. Updating FAISS...")
            self.retriever.delete_document(old_name)
            new_md = Path(event.dest_path) / "SKILL.md"
            if new_md.exists() and self._debounce(str(new_md)):
                self.retriever.ingest_skill(new_name, str(new_md))
        elif self._is_skill_md(event.src_path):
            old_name = self._get_skill_name(event.src_path)
            self.retriever.delete_document(old_name)
            if self._is_skill_md(event.dest_path) and self._debounce(event.dest_path):
                new_name = self._get_skill_name(event.dest_path)
                self.retriever.ingest_skill(new_name, event.dest_path)


class DirectoryWatcher:
    """Manages the watchdog observer for all configured directories."""
    def __init__(self, workspace_dir: str, skills_dir: str, retriever):
        self.observer = Observer()
        self.workspace_dir = workspace_dir
        self.skills_dir = skills_dir
        
        self.observer.schedule(WorkspaceEventHandler(retriever), self.workspace_dir, recursive=False)
        self.observer.schedule(SkillEventHandler(retriever), self.skills_dir, recursive=True)

    def start(self):
        self.observer.start()
        logger.info("Watchdog file system watcher started.")

    def stop(self):
        self.observer.stop()
        self.observer.join()
        logger.info("Watchdog file system watcher stopped.")
