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
    # Only fixed event names and numeric OS metadata leave the guardian. Never
    # forward child stderr, exception text, argv, paths or environment values.
    events=0
    os.set_blocking(sys.stderr.fileno(),False)
    def diagnostic(event,**numbers):
        nonlocal events
        if events>=32:return
        events+=1
        try:os.write(sys.stderr.fileno(),(json.dumps({'event':event,**numbers})+'\n').encode())
        except (OSError,BlockingIOError):pass
    diagnostic('GUARDIAN_START')
    lock=None;child=None
    try:
        diagnostic('LOCK_OPEN')
        lock=os.open(sys.argv[1],os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
        if os.fstat(lock).st_uid!=os.getuid() or os.fstat(lock).st_nlink!=1:
            diagnostic('LOCK_UNSAFE');return 77
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:diagnostic('LOCK_BUSY');return 73
        diagnostic('LOCK_ACQUIRED')
        command=json.loads(sys.argv[2]);diagnostic('CHILD_SPAWN')
        child=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                               start_new_session=True,pass_fds=(lock,),bufsize=0)
        diagnostic('CHILD_SPAWNED',pid=child.pid)
        os.set_blocking(child.stdin.fileno(),False);os.set_blocking(sys.stdout.fileno(),False)
        pending_in=b'';pending_out=b'info string guardian-ready\n'
        selector=selectors.DefaultSelector();selector.register(sys.stdin.buffer,selectors.EVENT_READ,'parent')
        selector.register(child.stdout,selectors.EVENT_READ,'engine')
        initialized=False;sample_started=time.monotonic();sample_checkpoints=iter((.1,.5,1.,2.,4.));sample_due=next(sample_checkpoints)
        while child.poll() is None:
            # Read the same three roles once; diagnostics reuse these exact values.
            pids=(os.getpid(),os.getppid(),child.pid)
            try:guardian_rss,worker_rss,child_rss=(resident_bytes(pid) for pid in pids)
            except OSError as exc:
                diagnostic('RSS_UNAVAILABLE',errno=exc.errno or 0);return 78
            size=guardian_rss+worker_rss+child_rss
            memory={'rss':size,'guardian_rss':guardian_rss,'worker_rss':worker_rss,'child_rss':child_rss,
                    'guardian_pid':pids[0],'worker_pid':pids[1],'child_pid':pids[2],'limit':512*1024**2,
                    'elapsed_ms':int((time.monotonic()-sample_started)*1000)}
            if not initialized:diagnostic('RSS_READY',**memory);initialized=True
            if size>512*1024**2:diagnostic('RSS_REFUSED',**memory);return 78
            if time.monotonic()-sample_started>=sample_due:
                diagnostic('RSS_SAMPLE',**memory);sample_due=next(sample_checkpoints,float('inf'))
            for key,_ in selector.select(.1):
                data=os.read(key.fileobj.fileno(),4096)
                if not data:
                    diagnostic('PARENT_EOF' if key.data=='parent' else 'CHILD_EOF');return 0
                if key.data=='parent':pending_in+=data
                else:pending_out+=data
                if len(pending_in)>16384 or len(pending_out)>65536:
                    diagnostic('RELAY_LIMIT');return 78
            try:
                if pending_in:pending_in=pending_in[os.write(child.stdin.fileno(),pending_in):]
                if pending_out:pending_out=pending_out[os.write(sys.stdout.fileno(),pending_out):]
            except BlockingIOError:pass
            except BrokenPipeError:diagnostic('RELAY_BROKEN_PIPE');return 0
        diagnostic('CHILD_EXIT_OBSERVED',returncode=child.returncode)
        return 0
    except OSError as exc:
        diagnostic('OS_ERROR',errno=exc.errno or 0);return 79
    except Exception:
        diagnostic('GUARDIAN_ERROR');return 79
    finally:
        if child is not None:
            if child.poll() is None:
                diagnostic('CHILD_STOP')
                try:child.stdin.write(b'stop\nquit\n')
                except (BrokenPipeError,OSError):pass
                try:child.wait(timeout=.2)
                except subprocess.TimeoutExpired:
                    # The only PID killed is this still-owned Popen process group.
                    diagnostic('CHILD_KILL',signal=signal.SIGKILL)
                    os.killpg(child.pid,signal.SIGKILL);child.wait(timeout=2)
            child.wait(timeout=2)
            diagnostic('CHILD_REAPED',returncode=child.returncode,signal=max(0,-child.returncode))
        if lock is not None:os.close(lock)
        diagnostic('GUARDIAN_CLOSED')


if __name__=='__main__':sys.exit(main())
