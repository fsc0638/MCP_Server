import threading
from contextlib import contextmanager
import time

_local_locks = {}
_local_lock_mutex = threading.Lock()
_redis_client = None

@contextmanager
def _acquire_session_lock(session_id: str):
    lock_key = f"lock:{session_id}"
    local_lock = None

    with _local_lock_mutex:
        if lock_key not in _local_locks:
            _local_locks[lock_key] = threading.Lock()
        local_lock = _local_locks[lock_key]
    
    acquired = local_lock.acquire(blocking=False)
    if not acquired:
        print(f"{threading.current_thread().name}: failed to acquire")
        yield False
        return

    try:
        print(f"{threading.current_thread().name}: acquired lock")
        yield True
    finally:
        print(f"{threading.current_thread().name}: releasing lock")
        if local_lock:
            local_lock.release()

def process(session_id):
    with _acquire_session_lock(session_id) as acquired:
        if not acquired:
            print(f"{threading.current_thread().name}: ignoring concurrent")
            return
        
        print(f"{threading.current_thread().name}: working...")
        time.sleep(2)
        print(f"{threading.current_thread().name}: done")

threads = []
for i in range(5):
    t = threading.Thread(target=process, args=("test_session",), name=f"T-{i}")
    threads.append(t)
    t.start()

for t in threads:
    t.join()
