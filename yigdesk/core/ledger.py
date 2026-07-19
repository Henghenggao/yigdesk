from __future__ import annotations
import os
from contextlib import contextmanager
from pathlib import Path
from .ops import Op, op_to_json, op_from_json

try:                                    # POSIX
    import fcntl
    def _acquire(fd: int) -> None: fcntl.flock(fd, fcntl.LOCK_EX)
    def _release(fd: int) -> None: fcntl.flock(fd, fcntl.LOCK_UN)
except ImportError:                     # Windows
    import msvcrt, time
    def _acquire(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        while True:
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1); return
            except OSError:
                time.sleep(0.01)
    def _release(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

class Ledger:
    """Append-only operation log with a portable cross-process write transaction."""
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)
        self._lock_path = self.path.with_name(self.path.name + ".lock")

    def read(self) -> list[Op]:
        text = self.path.read_text(encoding="utf-8")
        return [op_from_json(l) for l in text.splitlines() if l.strip()]

    def next_seq(self) -> int:
        ops = self.read()
        return ops[-1].seq + 1 if ops else 1

    @contextmanager
    def transaction(self):
        fd = os.open(self._lock_path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            # Ensure the locked byte is backed by real file content. Locking a
            # region beyond EOF is permitted on Windows but the well-defined,
            # portable behavior is to lock a byte that exists.
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"\0")
            _acquire(fd)
            yield _Txn(self)
        finally:
            try: _release(fd)
            finally: os.close(fd)

    def append(self, kind, actor, role, payload, base_seq=0) -> Op:
        with self.transaction() as txn:
            return txn.append(kind, actor, role, payload, base_seq)

class _Txn:
    def __init__(self, ledger: Ledger): self._ledger = ledger
    def read(self): return self._ledger.read()
    def next_seq(self): return self._ledger.next_seq()
    def append(self, kind, actor, role, payload, base_seq=0) -> Op:
        op = Op(self._ledger.next_seq(), kind, actor, role, payload, base_seq)
        with self._ledger.path.open("a", encoding="utf-8") as f:
            f.write(op_to_json(op) + "\n"); f.flush(); os.fsync(f.fileno())
        return op
