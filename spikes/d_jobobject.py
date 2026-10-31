"""(d) Windows Job Object kill-on-exit. Parent mode runs child mode per variant, hard-kills the child, checks survivors.
Variants: control (no job), root (assign Chrome root PID after launch), root+existing (also assign
already-existing children), self (put the sidecar process itself in the job BEFORE launching Chrome)."""
import asyncio, ctypes, json, subprocess, sys, time, os
from ctypes import wintypes as wt
import psutil

K = ctypes.WinDLL("kernel32", use_last_error=True)
class IO_COUNTERS(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOperationCount","WriteOperationCount","OtherOperationCount","ReadTransferCount","WriteTransferCount","OtherTransferCount")]
class BASIC(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64), ("LimitFlags", wt.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t), ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wt.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wt.DWORD), ("SchedulingClass", wt.DWORD)]
class EXT(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO_COUNTERS), ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]
K.CreateJobObjectW.restype = wt.HANDLE; K.CreateJobObjectW.argtypes = [wt.LPVOID, wt.LPCWSTR]
K.SetInformationJobObject.argtypes = [wt.HANDLE, ctypes.c_int, wt.LPVOID, wt.DWORD]
K.AssignProcessToJobObject.argtypes = [wt.HANDLE, wt.HANDLE]
K.OpenProcess.restype = wt.HANDLE; K.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
K.IsProcessInJob.argtypes = [wt.HANDLE, wt.HANDLE, ctypes.POINTER(wt.BOOL)]
K.GetCurrentProcess.restype = wt.HANDLE
K.CloseHandle.argtypes = [wt.HANDLE]
PROCESS_SET_QUOTA_TERMINATE_QUERY = 0x0100 | 0x0001 | 0x1000  # SET_QUOTA | TERMINATE | QUERY_LIMITED

def make_job():
    h = K.CreateJobObjectW(None, None)
    if not h: raise ctypes.WinError(ctypes.get_last_error())
    info = EXT(); info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not K.SetInformationJobObject(h, 9, ctypes.byref(info), ctypes.sizeof(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    return h

def assign_pid(job, pid):
    ph = K.OpenProcess(PROCESS_SET_QUOTA_TERMINATE_QUERY, False, pid)
    if not ph: return f"OpenProcess err {ctypes.get_last_error()}"
    try:
        ok = K.AssignProcessToJobObject(job, ph)
        return True if ok else f"Assign err {ctypes.get_last_error()}"
    finally:
        K.CloseHandle(ph)

def in_job(job, pid):
    ph = K.OpenProcess(0x1000, False, pid)
    if not ph: return None
    try:
        r = wt.BOOL(); K.IsProcessInJob(ph, job, ctypes.byref(r)); return bool(r.value)
    finally:
        K.CloseHandle(ph)

async def child(variant):
    sys.path.insert(0, os.path.dirname(__file__))
    from common import start
    job = make_job()
    if variant == "self":
        print("SELF assign", assign_pid(job, os.getpid()), flush=True)
    b = await start(headless=False)
    pid = b._process_pid
    pre = [p.pid for p in psutil.Process(pid).children(recursive=True)]
    info = {"variant": variant, "root": pid, "profile": b._spike_profile, "pre_assign_children": pre}
    if variant in ("root", "root+existing"):
        info["assign_root"] = assign_pid(job, pid)
    if variant == "root+existing":
        info["assign_existing"] = {str(c): assign_pid(job, c) for c in pre}
    await asyncio.sleep(1)
    await b.create_context("about:blank", new_window=True)  # spawns new renderer after assignment
    await asyncio.sleep(2)
    procs = [psutil.Process(pid)] + psutil.Process(pid).children(recursive=True)
    info["tree"] = {str(p.pid): {"name": p.name(), "in_job": (in_job(job, p.pid) if variant != "control" else None), "pre": p.pid in pre or p.pid == pid}
                    for p in procs}
    print("INFO " + json.dumps(info), flush=True)
    await asyncio.sleep(600)

def marker_procs(marker):
    r = []
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if "chrome" in (p.info["name"] or "").lower() and any(marker in c for c in (p.info["cmdline"] or [])):
                r.append(p)
        except psutil.Error: pass
    return r

def parent():
    here = os.path.abspath(__file__); py = sys.executable
    results = []
    for variant in ("control", "root", "root+existing", "self"):
        proc = subprocess.Popen([py, here, "--child", variant], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        info = None; t0 = time.time(); lines = []
        while time.time() - t0 < 60:
            line = proc.stdout.readline()
            if not line: break
            lines.append(line.strip())
            if line.startswith("INFO "): info = json.loads(line[5:]); break
        if not info:
            print("child failed", variant, lines); proc.kill(); continue
        marker = os.path.basename(info["profile"])
        before = marker_procs(marker)
        tree_before = {p.pid for p in before}
        subprocess.run(["taskkill", "/F", "/PID", str(proc.pid)], capture_output=True)
        t_kill = time.time(); survivors = []
        for _ in range(20):
            time.sleep(0.5)
            survivors = marker_procs(marker)
            if not survivors: break
        t_gone = round(time.time() - t_kill, 1)
        in_job = {k: v["in_job"] for k, v in info["tree"].items()}
        results.append({"variant": variant, "chrome_procs_before_kill": len(before), "tree_snapshot": len(info["tree"]),
                        "in_job_flags": in_job, "pre_assignment_pids": info["pre_assign_children"], "assign": {k: v for k, v in info.items() if k.startswith("assign")},
                        "survivors_after_kill": [(p.pid, p.name()) for p in survivors], "seconds_until_all_gone": t_gone if not survivors else None})
        for p in survivors:  # cleanup of control variant: only our marker-matched procs
            try: p.kill()
            except psutil.Error: pass
        time.sleep(1)
        import shutil; shutil.rmtree(info["profile"], ignore_errors=True)
    print(json.dumps(results, indent=2))
    open(os.path.join(os.path.dirname(here), "out", "d_results.json"), "w").write(json.dumps(results, indent=2))
    sys.path.insert(0, os.path.dirname(here))
    from common import leftover_spike_chromes
    print("leftover:", [p.pid for p in leftover_spike_chromes()])

if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--child": asyncio.run(child(sys.argv[2]))
    else: parent()
