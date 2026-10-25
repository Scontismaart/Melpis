"""Process fencing: one model-owning service process per container instance."""

from contextlib import contextmanager
import os
from pathlib import Path
import tempfile


@contextmanager
def instance_lock(path=None):
    path = (
        Path(path)
        if path is not None
        else Path(tempfile.gettempdir()) / "melpis-embedding.lock"
    )
    with path.open("a+b") as handle:
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                if not handle.read(1):
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError("embedding_instance_already_running") from None
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)
