"""Exclude rules for the file index (AC-FI-1, AC-FI-7; plan 15.3 items 3 and 4).

Syntax is a gitignore subset, matched case-insensitively (NTFS):

* A trailing ``/`` makes the rule apply to directories only.
* A leading ``**/`` is accepted and ignored (rules without a ``/`` already match at any depth);
  ``/**/`` inside a pattern matches zero or more directories.
* A pattern without ``/`` matches the entry name; a pattern with ``/`` matches the root-relative
  path (forward slashes), or the tail of it.
* A drive-letter pattern (``C:\\Windows\\``) matches that absolute path and everything under it.
* Lines that are blank, start with ``#`` or start with ``!`` (re-include) are ignored; re-include
  is not supported.

``*`` crosses ``/`` (fnmatch semantics, chosen on purpose): ``AppData/Local/*/OfficeFileCache/``
therefore excludes ``AppData/Local/Microsoft/Office/16.0/OfficeFileCache`` at depth as well as the
one-level ``AppData/Local/App/OfficeFileCache``. A rule that must stay within one level would need
a different spelling; none of the defaults does.

``matching_rule(full, rel, name, is_dir)`` is called once per path segment while descending, and
returns the matching rule's text (its per-rule label) or ``None``. Each positive answer is tallied
in ``Excludes.counts``; the crawler additionally keeps per-walk tallies in ``CrawlResult.excludes``.
"""
import fnmatch
import os
import re
from typing import Iterable, Optional, Union

DEFAULT_EXCLUDES = (
    "C:\\Windows\\",
    "$RECYCLE.BIN/",
    "System Volume Information/",
    "C:\\ProgramData\\Microsoft\\Search\\",
    "pagefile.sys",
    "hiberfil.sys",
    "swapfile.sys",
    "*.vhdx",
    "AppData/Local/Temp/",
    "AppData/Local/Microsoft/WindowsApps/",
    "AppData/Local/Packages/",
    "AppData/Local/Microsoft/Edge/",
    "AppData/Local/Google/Chrome/",
    "AppData/Local/Docker/",
    "**/node_modules/",
    "**/.git/",
    "**/.venv/",
    "**/venv/",
    "**/__pycache__/",
    "**/target/",
    "**/bin/",
    "**/obj/",
    "**/.gradle/",
    "**/.m2/",
    "**/.nuget/packages/",
    "**/.cache/",
    "**/.vs/",
    "**/dist/",
    "**/build/",
    "**/.next/",
    "**/.turbo/",
    "AppData/Local/*/Code Cache/",
    "AppData/Local/*/Cache/",
    "AppData/Local/*/Cache_Data/",
    "AppData/Local/*/GPUCache/",
    "AppData/Local/*/Service Worker/",
    "AppData/Local/*/Shared Dictionary/",
    "AppData/Local/*/OfficeFileCache/",
    "AppData/Roaming/*/Code Cache/",
    "AppData/Roaming/*/Cache/",
    "AppData/Roaming/*/Cache_Data/",
    "AppData/Roaming/*/GPUCache/",
    "AppData/Roaming/*/Service Worker/",
    "AppData/Roaming/*/Shared Dictionary/",
    "AppData/Roaming/*/OfficeFileCache/",
    "AppData/Local/npm-cache/",
    "AppData/Roaming/npm-cache/",
    "AppData/Local/NuGet/v3-cache/",
    "AppData/Local/NuGet/plugins-cache/",
)

_WILD = re.compile(r"[*?\[]")


def _compile(pattern: str):
    return re.compile(fnmatch.translate(pattern)).match


class Excludes:
    def __init__(self, patterns: Union[str, Iterable[str]] = ()) -> None:
        lines = patterns.splitlines() if isinstance(patterns, str) else list(patterns)
        self.labels: list = []
        self.counts: dict = {}
        self._absolute: list = []
        self._literal: dict = {}
        self._names: list = []
        self._paths: list = []
        for raw in lines:
            if not isinstance(raw, str):
                continue
            label = raw.strip()
            if not label or label.startswith(("#", "!")):
                continue
            dir_only = label.endswith(("/", "\\"))
            line = label.rstrip("/\\")
            if len(line) > 1 and line[1] == ":":
                self._absolute.append((os.path.normcase(os.path.normpath(line)), dir_only, label))
                self.labels.append(label)
                continue
            line = line.replace("\\", "/")
            while line.startswith("**/"):
                line = line[3:]
            line = line.lower()
            if not line:
                continue
            self.labels.append(label)
            if "/" not in line:
                if _WILD.search(line):
                    self._names.append((_compile(line), dir_only, label))
                else:
                    self._literal.setdefault(line, []).append((dir_only, label))
                continue
            variants = [line]
            if "/**/" in line:
                variants.append(line.replace("/**/", "/"))
            for variant in variants:
                self._paths.append((_compile(variant), _compile("*/" + variant), dir_only, label))

    def matching_rule(self, full: str, rel: str, name: str, is_dir: bool) -> Optional[str]:
        hit = self._match(full, rel, name, is_dir)
        if hit is not None:
            self.counts[hit] = self.counts.get(hit, 0) + 1
        return hit

    def reset_counts(self) -> None:
        self.counts = {}

    def _match(self, full: str, rel: str, name: str, is_dir: bool) -> Optional[str]:
        n = name.lower()
        for dir_only, label in self._literal.get(n, ()):
            if is_dir or not dir_only:
                return label
        for match, dir_only, label in self._names:
            if (is_dir or not dir_only) and match(n):
                return label
        if self._absolute:
            f = os.path.normcase(os.path.normpath(full))
            for absolute, dir_only, label in self._absolute:
                if (is_dir or not dir_only) and (f == absolute or f.startswith(absolute + os.sep)):
                    return label
        if self._paths:
            r = rel.lower()
            for direct, tail, dir_only, label in self._paths:
                if (is_dir or not dir_only) and (direct(r) or tail(r)):
                    return label
        return None
