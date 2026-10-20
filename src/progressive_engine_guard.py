"""Private Unix guardian: retain slot until owned UCI child has been killed/reaped."""
import fcntl
import json
import os
import selectors
import signal
import subprocess
import sys
import time
from .progressive_resources import resident_bytes


def main():
    lock=os.open(sys.argv[1],os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    if os.fstat(lock).st_uid!=os.getuid() or os.fstat(lock).st_nlink!=1:raise RuntimeError('Unsafe host lock')
    try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError: raise SystemExit(73)
    child=None
    try:
        command=json.loads(sys.argv[2])
        child=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                               start_new_session=True,pass_fds=(lock,),bufsize=0)
        os.set_blocking(child.stdin.fileno(),False); os.set_blocking(sys.stdout.fileno(),False)
        pending_in=b''; pending_out=b'info string guardian-ready\n'
        selector=selectors.DefaultSelector(); selector.register(sys.stdin.buffer,selectors.EVENT_READ,'parent')
        selector.register(child.stdout,selectors.EVENT_READ,'engine')
        while child.poll() is None:
            # Three known processes only; unsupported/unreadable accounting stops the child.
            if sum(resident_bytes(pid) for pid in (os.getpid(),os.getppid(),child.pid))>512*1024**2:return
            for key,_ in selector.select(.1):
                data=os.read(key.fileobj.fileno(),4096)
                if not data: return
                if key.data=='parent': pending_in+=data
                else: pending_out+=data
                if len(pending_in)>16384 or len(pending_out)>65536: return
            try:
                if pending_in: pending_in=pending_in[os.write(child.stdin.fileno(),pending_in):]
                if pending_out: pending_out=pending_out[os.write(sys.stdout.fileno(),pending_out):]
            except BlockingIOError: pass
            except BrokenPipeError: return
    finally:
        if child is not None:
            if child.poll() is None:
                try: child.stdin.write(b'stop\nquit\n')
                except (BrokenPipeError,OSError): pass
                try: child.wait(timeout=.2)
                except subprocess.TimeoutExpired:
                    # The only PID killed is this still-owned Popen process group.
                    os.killpg(child.pid,signal.SIGKILL); child.wait(timeout=2)
            child.wait(timeout=2)
        os.close(lock)


if __name__=='__main__': main()
