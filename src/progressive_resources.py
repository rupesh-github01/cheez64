"""Read RSS of owned processes without shell tools/dependencies; fail closed."""
import ctypes
import os
from pathlib import Path
import sys


def resident_bytes(pid):
    if sys.platform=='linux':
        return int(Path(f'/proc/{int(pid)}/statm').read_text().split()[1])*os.sysconf('SC_PAGE_SIZE')
    if sys.platform=='darwin':
        class Usage(ctypes.Structure):
            _fields_=[('uuid',ctypes.c_ubyte*16)]+[(k,ctypes.c_uint64) for k in
                 ('user','system','pkg','interrupt','pageins','wired','resident','footprint','start','exit')]
        lib=ctypes.CDLL('/usr/lib/libproc.dylib',use_errno=True);data=Usage()
        if lib.proc_pid_rusage(int(pid),0,ctypes.byref(data))!=0:raise OSError(ctypes.get_errno(),'Cannot verify owned process RSS')
        return data.resident
    raise OSError('Unsupported RSS accounting host')
