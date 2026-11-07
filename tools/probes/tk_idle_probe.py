"""Idle cost of a blank topmost tkinter window with a 1 Hz label update (probe P9).

Memory column is private bytes (PROCESS_MEMORY_COUNTERS_EX.PrivateUsage, committed private
memory), not the private working set."""
import argparse
import ctypes
import time
import tkinter as tk
from ctypes import wintypes

SAMPLE_SECONDS = 10


class PMC_EX(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_psapi = ctypes.WinDLL("psapi", use_last_error=True)
_k32.GetCurrentProcess.restype = wintypes.HANDLE
_psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC_EX), wintypes.DWORD]
_k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4


def private_bytes_mb():
    pmc = PMC_EX()
    pmc.cb = ctypes.sizeof(pmc)
    if not _psapi.GetProcessMemoryInfo(_k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    return pmc.PrivateUsage / (1024 * 1024)


def cpu_seconds():
    c, e, k, u = (wintypes.FILETIME() for _ in range(4))
    if not _k32.GetProcessTimes(_k32.GetCurrentProcess(), ctypes.byref(c), ctypes.byref(e), ctypes.byref(k), ctypes.byref(u)):
        raise ctypes.WinError(ctypes.get_last_error())
    ticks = lambda f: (f.dwHighDateTime << 32) | f.dwLowDateTime
    return (ticks(k) + ticks(u)) / 1e7


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--minutes", type=float, default=10.0)
    args = ap.parse_args()

    root = tk.Tk()
    root.title("tk idle probe")
    root.geometry("220x60+40+40")
    root.attributes("-topmost", True)
    label = tk.Label(root, text="0")
    label.pack(expand=True)

    state = {"ticks": 0, "mem": [], "cpu_pct": []}
    start = time.monotonic()
    end = start + args.minutes * 60
    last = {"wall": start, "cpu": cpu_seconds()}

    def tick():
        state["ticks"] += 1
        label.config(text=str(state["ticks"]))
        root.after(1000, tick)

    def sample():
        now = time.monotonic()
        cpu = cpu_seconds()
        dwall = now - last["wall"]
        if dwall > 0:
            state["cpu_pct"].append(100.0 * (cpu - last["cpu"]) / dwall)
        last["wall"], last["cpu"] = now, cpu
        state["mem"].append(private_bytes_mb())
        print(f"t={now - start:6.1f}s  private_bytes={state['mem'][-1]:.1f} MB  cpu={state['cpu_pct'][-1]:.2f}%", flush=True)
        if now >= end:
            root.quit()
        else:
            root.after(int(min(SAMPLE_SECONDS, max(end - now, 0.05)) * 1000), sample)

    state["mem"].append(private_bytes_mb())
    root.after(1000, tick)
    root.after(int(min(SAMPLE_SECONDS, args.minutes * 60) * 1000), sample)
    root.mainloop()
    root.destroy()

    total_cpu = cpu_seconds()
    avg = sum(state["cpu_pct"]) / len(state["cpu_pct"]) if state["cpu_pct"] else 0.0
    print(f"samples: {len(state['cpu_pct'])}")
    print(f"average CPU %: {avg:.3f} (of one core)")
    print(f"max private bytes MB: {max(state['mem']):.1f}")
    print(f"total process CPU seconds: {total_cpu:.2f}")


if __name__ == "__main__":
    main()
