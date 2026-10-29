"""Read-only macOS process accounting for this task's own PostgreSQL tree.

Struct layout comes from the installed macOS SDK's sys/resource.h rusage_info_v2.
CPU includes reaped children plus currently live children. Physical footprints
are summed and sampled, so shared-page accounting and short peaks remain limits.
"""
from __future__ import annotations

import ctypes
import os
import resource
import sys


class RUsageV2(ctypes.Structure):
    _fields_ = [("uuid", ctypes.c_uint8 * 16)] + [(name, ctypes.c_uint64) for name in (
        "user", "system", "idle", "interrupt", "pageins", "wired", "resident", "footprint", "start", "exit",
        "child_user", "child_system", "child_idle", "child_interrupt", "child_pageins", "child_elapsed", "read", "write")]


class MacTree:
    def __init__(self, pid: int):
        self.pid = pid
        class Timebase(ctypes.Structure):
            _fields_ = [("numer", ctypes.c_uint32), ("denom", ctypes.c_uint32)]
        info = Timebase()
        system = ctypes.CDLL('/usr/lib/libSystem.B.dylib')
        system.mach_timebase_info(ctypes.byref(info))
        self.tick_seconds = info.numer / info.denom / 1e9
        self.lib = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
        self.lib.proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
        self.lib.proc_pid_rusage.restype = ctypes.c_int
        self.lib.proc_listchildpids.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
        self.lib.proc_listchildpids.restype = ctypes.c_int

    def process(self, pid):
        result = RUsageV2()
        if self.lib.proc_pid_rusage(pid, 2, ctypes.byref(result)):
            raise OSError(ctypes.get_errno(), "Native process accounting unavailable")
        return result

    def snapshot(self):
        children = (ctypes.c_int * 4096)()
        count = self.lib.proc_listchildpids(self.pid, children, ctypes.sizeof(children)) // ctypes.sizeof(ctypes.c_int)
        if count < 0:
            raise OSError("Native child accounting unavailable")
        alive = []
        for pid in children[:count]:
            if pid > 0:
                try:
                    alive.append(self.process(pid))
                except OSError:
                    pass  # A concurrently exiting child will enter reaped CPU accounting.
        root = self.process(self.pid)
        return {"cpu_seconds": (root.user + root.system + root.child_user + root.child_system
                                 + sum(c.user + c.system for c in alive)) * self.tick_seconds,
                "disk_read_bytes_root": root.read, "disk_write_bytes_root": root.write,
                "footprint_bytes": root.footprint + sum(c.footprint for c in alive),
                "live_children": len(alive)}


def python_usage():
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return {"cpu_seconds": usage.ru_utime + usage.ru_stime,
            "peak_rss_bytes": usage.ru_maxrss * (1 if sys.platform == 'darwin' else 1024)}


if __name__ == '__main__':
    import json
    tree = MacTree(os.getpid())
    before, python_before = tree.snapshot(), python_usage()
    value = sum(i * i for i in range(2_000_000))
    after, python_after = tree.snapshot(), python_usage()
    native = after['cpu_seconds'] - before['cpu_seconds']
    standard = python_after['cpu_seconds'] - python_before['cpu_seconds']
    assert abs(native - standard) < 0.02
    print(json.dumps({'native_cpu_seconds': native, 'getrusage_cpu_seconds': standard,
                      'native_self_accounting_agreement': True}, indent=2))
