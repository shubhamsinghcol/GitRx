"""POSIX main-thread wall-clock query deadlines (macOS/Linux native runtime)."""
from contextlib import contextmanager
from functools import wraps
import signal
import threading
import time
import sys

class QueryTimeout(RuntimeError):
    pass

@contextmanager
def deadline(seconds):
    if type(seconds) not in (int,float) or not .01 <= seconds <= 60:
        raise ValueError('timeout_seconds must be 0.01..60')
    if threading.current_thread() is not threading.main_thread() or not hasattr(signal,'setitimer'):
        raise RuntimeError('Bounded native queries require a POSIX main-thread worker')
    old_handler=signal.getsignal(signal.SIGALRM);old_timer=signal.getitimer(signal.ITIMER_REAL)
    start=time.monotonic()
    def expired(*_):raise QueryTimeout('Whole-query deadline exceeded; no evidence answer. Retry with narrower bounds or a longer timeout.')
    signal.signal(signal.SIGALRM,expired)
    signal.setitimer(signal.ITIMER_REAL,min(seconds,old_timer[0]) if old_timer[0] else seconds)
    try:yield
    finally:
        # A nested timeout may have consumed the enclosing deadline. Re-arming
        # that expired timer during exception unwinding can deliver a second
        # SIGALRM after the outer context restores SIG_DFL, killing the process.
        # Block delivery while changing ownership and consume a pending alarm
        # belonging to the timer we just disabled.
        mask=signal.pthread_sigmask(signal.SIG_BLOCK,{signal.SIGALRM})
        overdue=time.monotonic()-start>=seconds
        try:
            signal.setitimer(signal.ITIMER_REAL,0)
            if signal.SIGALRM in signal.sigpending():signal.sigwait({signal.SIGALRM})
            signal.signal(signal.SIGALRM,old_handler)
            remaining=old_timer[0]-(time.monotonic()-start)
            if old_timer[0] and remaining>0:
                signal.setitimer(signal.ITIMER_REAL,remaining,old_timer[1])
            elif old_timer[0]:
                overdue=True
                if old_timer[1]:
                    # Preserve an external periodic timer's next future tick;
                    # never replay the expired tick during unwinding.
                    signal.setitimer(signal.ITIMER_REAL,remaining%old_timer[1] or old_timer[1],old_timer[1])
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK,mask)
        # If a body swallowed an alarm, leaving a nested context still cannot
        # turn an expired enclosing deadline into a successful answer.
        if overdue and sys.exc_info()[0] is None:
            raise QueryTimeout('Whole-query deadline exceeded; no evidence answer. Retry with narrower bounds or a longer timeout.')

def bounded(fn):
    @wraps(fn)
    def run(*args,timeout_seconds=10,**kwargs):
        with deadline(timeout_seconds):return fn(*args,**kwargs)
    return run
