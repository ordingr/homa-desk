"""Pure os.scandir crawl with directory-level excludes (probe P7)."""
import argparse
import ctypes
import fnmatch
import heapq
import os
import time
from ctypes import wintypes

DEFAULT_EXCLUDES = r"""
C:\Windows\
$RECYCLE.BIN/
System Volume Information/
C:\ProgramData\Microsoft\Search\
pagefile.sys
hiberfil.sys
swapfile.sys
*.vhdx
AppData/Local/Temp/
AppData/Local/Microsoft/WindowsApps/
AppData/Local/Packages/
AppData/Local/Microsoft/Edge/
AppData/Local/Google/Chrome/
AppData/Local/Docker/
**/node_modules/
**/.git/
**/.venv/
**/venv/
**/__pycache__/
**/target/
**/bin/
**/obj/
**/.gradle/
**/.m2/
**/.nuget/packages/
**/.cache/
**/.vs/
**/dist/
**/build/
**/.next/
**/.turbo/
AppData/Local/*/Code Cache/
AppData/Local/*/Cache/
AppData/Local/*/Cache_Data/
AppData/Local/*/GPUCache/
AppData/Local/*/Service Worker/
AppData/Local/*/Shared Dictionary/
AppData/Local/*/OfficeFileCache/
AppData/Roaming/*/Code Cache/
AppData/Roaming/*/Cache/
AppData/Roaming/*/Cache_Data/
AppData/Roaming/*/GPUCache/
AppData/Roaming/*/Service Worker/
AppData/Roaming/*/Shared Dictionary/
AppData/Roaming/*/OfficeFileCache/
AppData/Local/npm-cache/
AppData/Roaming/npm-cache/
AppData/Local/NuGet/v3-cache/
AppData/Local/NuGet/plugins-cache/
"""


class Excludes:
    """Gitignore-style subset: trailing '/' = directories only, a pattern containing
    '/' matches the tail of the root-relative path, otherwise the entry name, and a
    drive-letter pattern matches that absolute path and everything under it."""

    def __init__(self, text):
        self.absolute = []
        self.names = []
        self.paths = []
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            dir_only = line.endswith(("/", "\\"))
            line = line.rstrip("/\\")
            if len(line) > 1 and line[1] == ":":
                self.absolute.append((os.path.normcase(os.path.normpath(line)), raw.strip()))
                continue
            line = line.replace("\\", "/")
            while line.startswith("**/"):
                line = line[3:]
            entry = (line.lower(), dir_only, raw.strip())
            (self.paths if "/" in line else self.names).append(entry)

    def matching_rule(self, full, rel, name, is_dir):
        """Returns the pattern line that excludes the entry, or None."""
        n = name.lower()
        for pat, dir_only, label in self.names:
            if (is_dir or not dir_only) and fnmatch.fnmatchcase(n, pat):
                return label
        if is_dir and self.absolute:
            f = os.path.normcase(os.path.normpath(full))
            for a, label in self.absolute:
                if f == a:
                    return label
        if self.paths:
            r = rel.lower()
            for pat, dir_only, label in self.paths:
                if (is_dir or not dir_only) and (fnmatch.fnmatchcase(r, pat) or fnmatch.fnmatchcase(r, "*/" + pat)):
                    return label
        return None


class PMC(ctypes.Structure):
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
    ]


def peak_working_set_mb():
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
    pmc = PMC()
    pmc.cb = ctypes.sizeof(pmc)
    if not psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    return pmc.PeakWorkingSetSize / (1024 * 1024)


def crawl(root, excludes, stats, top):
    root = os.path.abspath(root)
    stack = [(root, "")]
    while stack:
        path, rel = stack.pop()
        count = 0
        try:
            it = os.scandir(path)
        except OSError:
            stats["errors"] += 1
            continue
        with it:
            while True:
                try:
                    entry = next(it)
                except StopIteration:
                    break
                except OSError:
                    stats["errors"] += 1
                    break
                try:
                    is_link = entry.is_symlink()
                    is_dir = entry.is_dir(follow_symlinks=False)
                    reparse = is_link or (
                        is_dir and bool(entry.stat(follow_symlinks=False).st_file_attributes & 0x400)
                    )
                except OSError:
                    stats["errors"] += 1
                    is_dir = reparse = False
                erel = f"{rel}/{entry.name}" if rel else entry.name
                if excludes:
                    rule = excludes.matching_rule(entry.path, erel, entry.name, is_dir)
                    if rule:
                        stats["skipped"][rule] = stats["skipped"].get(rule, 0) + 1
                        continue
                count += 1
                stats["entries"] += 1
                if is_dir and not reparse:
                    stats["dirs"] += 1
                    stack.append((entry.path, erel))
                elif is_dir:
                    stats["dirs"] += 1
                    stats["reparse_skipped"] += 1
        item = (count, path)
        if len(top) < 20:
            heapq.heappush(top, item)
        elif item > top[0]:
            heapq.heapreplace(top, item)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--roots", nargs="+", required=True)
    ap.add_argument("--excludes", default="default", help="default | none | path to a pattern file")
    ap.add_argument("--cold-hint", action="store_true", help="label this run as cold-cache in the output")
    args = ap.parse_args()

    if args.excludes == "default":
        excludes = Excludes(DEFAULT_EXCLUDES)
    elif args.excludes == "none":
        excludes = None
    else:
        with open(args.excludes, encoding="utf-8") as fh:
            excludes = Excludes(fh.read())

    stats = {"entries": 0, "dirs": 0, "errors": 0, "reparse_skipped": 0, "skipped": {}}
    top = []
    t0 = time.perf_counter()
    for root in args.roots:
        crawl(root, excludes, stats, top)
    secs = time.perf_counter() - t0

    print(f"run: {'cold (labelled by --cold-hint)' if args.cold_hint else 'warm/unlabelled'}")
    print(f"roots: {args.roots}")
    print(f"excludes: {args.excludes}")
    print(f"entries: {stats['entries']}")
    print(f"dirs: {stats['dirs']}")
    print(f"seconds: {secs:.2f}")
    print(f"peak working set MB: {peak_working_set_mb():.1f}")
    print(f"permission/OS errors: {stats['errors']}")
    print(f"reparse points not followed: {stats['reparse_skipped']}")
    print("entries skipped per exclude rule (matched entry only, its contents are not counted):")
    for rule, n in sorted(stats["skipped"].items(), key=lambda kv: -kv[1]):
        print(f"  {n:>9}  {rule}")
    print("top 20 directories by entry count:")
    for count, path in sorted(top, reverse=True):
        print(f"  {count:>9}  {path}")


if __name__ == "__main__":
    main()
