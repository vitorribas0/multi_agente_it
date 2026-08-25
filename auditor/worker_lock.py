"""Trava exclusiva de arquivo multiplataforma para o worker local da Atena.

O worker local usa ``runtime/agent-worker.lock`` como trava exclusiva: só um
worker pode mantê-la e o processo web a usa para detectar se há um worker vivo.

Em Unix (SageMaker/Linux, ECS) a primitiva é ``fcntl.flock``; no Windows é
``msvcrt.locking``. Ambos oferecem uma trava exclusiva **não bloqueante**:
``try_acquire`` devolve ``False`` quando outro processo já a mantém, em vez de
bloquear. A trava é liberada em ``release`` ou automaticamente quando o arquivo
é fechado (fim do processo).
"""

from __future__ import annotations

try:  # Unix
    import fcntl
except ImportError:  # pragma: no cover - Windows não possui fcntl
    fcntl = None

try:  # Windows
    import msvcrt
except ImportError:  # pragma: no cover - Unix não possui msvcrt
    msvcrt = None


def try_acquire(lock_file) -> bool:
    """Obtém uma trava exclusiva não bloqueante; ``False`` se já estiver presa."""
    if fcntl is not None:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except (BlockingIOError, OSError):
            return False
    if msvcrt is not None:
        try:
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    # Sem primitiva de trava disponível: degrade com segurança assumindo sucesso.
    return True


def release(lock_file) -> None:
    """Libera a trava obtida por ``try_acquire`` (silencioso se não houver)."""
    if fcntl is not None:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
    elif msvcrt is not None:
        try:
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
