"""Sandbox — actually compile/run generated artifacts, best-effort isolated.

This grounds the AUTO_CHECK / TESTS review lens in *real execution* instead of a
model reading text. It is OFF by default and opt-in (`tehai run --sandbox`),
because executing model-generated code is inherently risky.

Isolation, strongest first:
- **OS namespaces via `unshare`** (when available, unprivileged): user + network +
  pid + ipc + uts namespaces. The network namespace has no interfaces, so generated
  code CANNOT reach the network (blocks the main risk: exfiltration). Auto-detected
  and probed once; falls back to the best-effort mode below if unsupported.
- a fresh temp dir; artifacts written there with sanitized basenames (no `..`/`/`),
- minimal environment (no inherited secrets/tokens, no PYTHONPATH),
- `shell=False`, fixed runner commands,
- POSIX resource limits (CPU, address space, file size) via `resource`,
- new session so a SIGKILL on timeout reaps the whole process group (works even
  when the runner is PID 1 in its namespace and would ignore SIGTERM).
- **FS read-hardening (deny-list)**: empty tmpfs is mounted over /home and /root
  inside the mount namespace, so generated code can't read user secrets (SSH keys,
  dotfiles, tokens). python's paths (/usr, /lib) are untouched.

PARTIAL only: this is a deny-list, not a rootfs jail. Secrets outside /home,/root
(e.g. some /etc files) remain readable. A full allow-list jail needs
bwrap/nsjail/a container (absent here) — see FUTURE.md / ASSUMPTIONS.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Optional


# OS-level isolation via unshare: user + network + mount + pid + ipc + uts
# namespaces, unprivileged (maps the caller to root inside the userns). The `--net`
# namespace has no interfaces, so generated code cannot reach the network.
_UNSHARE_FLAGS = ["unshare", "--user", "--map-root-user", "--net", "--mount",
                  "--pid", "--fork", "--mount-proc", "--ipc", "--uts", "--"]

# FS read-hardening (deny-list): inside the mount namespace, shadow these dirs with
# empty tmpfs so generated code can't read user secrets (SSH keys, dotfiles,
# tokens). python's own paths (/usr, /lib) are untouched. Best-effort: a mount that
# fails is skipped (the code still runs, network-isolated). NOTE: a deny-list, not a
# full rootfs jail — secrets outside these dirs (e.g. under /etc) remain readable.
_FS_DENY = ("/home", "/root")


def _fs_bootstrap() -> str:
    dirs = " ".join(_FS_DENY)
    return (f'for d in {dirs}; do [ -d "$d" ] && mount -t tmpfs tmpfs "$d" 2>/dev/null; done; '
            f'exec "$@"')


def _unshare_prefix() -> list:
    # ... unshare <ns flags> -- /bin/sh -c '<deny-mounts>; exec "$@"' tehai-sbx <runner...>
    return _UNSHARE_FLAGS + ["/bin/sh", "-c", _fs_bootstrap(), "tehai-sbx"]


@dataclass
class SandboxResult:
    ran: bool                      # did we execute at least one runner?
    passed: bool                   # all runners exited 0
    runner: str = "none"           # label of the (failing or last) runner
    exit_code: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    reason: str = ""
    timed_out: bool = False
    isolation: str = "none"        # resolved backend: "unshare" | "none"
    files: list[str] = field(default_factory=list)


def _safe_name(name: str) -> str:
    base = os.path.basename((name or "").strip()) or "artifact.txt"
    base = re.sub(r"[^A-Za-z0-9._-]", "_", base)
    if base in ("", ".", ".."):
        base = "artifact.txt"
    return base[:100]


_CODE_EXTS = (".py", ".js", ".mjs", ".cjs", ".ts")


def _sniff_ext(content: str) -> Optional[str]:
    """Guess a code extension from content, for artifacts the model named with prose
    (e.g. 'Python file containing ...') instead of a real filename."""
    head = content.lstrip()[:2000]
    py = bool(re.search(r"(?m)^\s*(def|class|import|from)\s", head)) \
        or "if __name__" in head or "unittest" in head
    js = bool(re.search(r"(?m)^\s*(function|const|let|var|export|require)\b", head)) or "=>" in head
    if py and not js:
        return ".py"
    if js:
        ts = bool(re.search(r":\s*(string|number|boolean)\b", head)) or "interface " in head
        return ".ts" if ts else ".js"
    if py:
        return ".py"
    return None


class Sandbox:
    def __init__(self, timeout: int = 15, mem_limit_mb: int = 512,
                 fsize_limit_mb: int = 16, max_output: int = 4000,
                 isolation: str = "auto"):
        self.timeout = timeout
        self.mem = mem_limit_mb * 1024 * 1024
        self.fsize = fsize_limit_mb * 1024 * 1024
        self.max_output = max_output
        self.isolation_pref = isolation  # "auto" | "unshare" | "none"
        self._pytest: Optional[bool] = None      # cached real availability check
        self._iso: Optional[tuple[str, list]] = None  # cached (name, argv prefix)

    # ----- OS isolation ----- #
    def _unshare_works(self) -> bool:
        if not shutil.which("unshare"):
            return False
        try:
            r = subprocess.run(_unshare_prefix() + [sys.executable, "-c", "pass"],
                               capture_output=True, timeout=15,
                               env=self._base_env(tempfile.gettempdir()))
            return r.returncode == 0
        except Exception:
            return False

    def resolve_isolation(self) -> tuple[str, list]:
        """(name, argv-prefix). Probes once.

        - "none":   no OS isolation (best-effort layer only).
        - "auto"/"unshare": use unshare namespaces if usable, else fall back to none.
        - "strict": REQUIRE unshare; resolve to ("refuse", []) if unavailable, so the
          sandbox declines to run model code unconfined rather than degrade silently.
        """
        if self._iso is None:
            pref = self.isolation_pref
            if pref == "none":
                self._iso = ("none", [])
            elif pref == "strict":
                self._iso = ("unshare", _unshare_prefix()) if self._unshare_works() \
                    else ("refuse", [])
            elif pref in ("auto", "unshare") and self._unshare_works():
                self._iso = ("unshare", _unshare_prefix())
            else:
                self._iso = ("none", [])
        return self._iso

    def _base_env(self, home: str) -> dict:
        # Minimal env: no inherited secrets, and (deliberately) no PYTHONPATH, so a
        # tool only "available" via the parent's venv path is treated as absent.
        return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": home,
                "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"}

    def _pytest_available(self) -> bool:
        # Must use the SAME stripped env as real execution, else we'd detect pytest
        # in the parent venv but fail to run it under the sandbox env.
        if self._pytest is None:
            try:
                r = subprocess.run([sys.executable, "-c", "import pytest"],
                                   capture_output=True, timeout=10,
                                   env=self._base_env(tempfile.gettempdir()))
                self._pytest = (r.returncode == 0)
            except Exception:
                self._pytest = False
        return self._pytest

    # ----- runner selection ----- #
    def _runners(self, files: dict[str, str]) -> list[tuple[list[str], str]]:
        runs: list[tuple[list[str], str]] = []
        py = [f for f in files if f.endswith(".py")]
        for f in py:                       # syntax check every python file first
            runs.append(([sys.executable, "-m", "py_compile", f], f"python:py_compile:{f}"))
        test_py = [f for f in py if "test" in f.lower()]
        non_test_py = [f for f in py if "test" not in f.lower()]
        # Only EXECUTE test files when the implementation is co-present, else the
        # test's import of a sibling module fails for the wrong reason.
        if test_py and non_test_py:
            if self._pytest_available():
                runs.append(([sys.executable, "-m", "pytest", "-q"], "python:pytest"))
            else:
                for f in test_py:
                    runs.append(([sys.executable, f], f"python:run:{f}"))
        if shutil.which("node"):
            for f in files:
                if f.endswith((".js", ".mjs", ".cjs")):
                    runs.append((["node", "--check", f], f"node:check:{f}"))
        if shutil.which("tsc"):
            for f in files:
                if f.endswith(".ts"):
                    runs.append((["tsc", "--noEmit", f], f"tsc:noEmit:{f}"))
        return runs

    def _limits(self):
        if os.name != "posix":
            return None
        import resource

        def apply():
            for res, val in (
                (resource.RLIMIT_CPU, (self.timeout + 1, self.timeout + 2)),
                (resource.RLIMIT_AS, (self.mem, self.mem)),
                (resource.RLIMIT_FSIZE, (self.fsize, self.fsize)),
            ):
                try:
                    resource.setrlimit(res, val)
                except Exception:
                    pass
        return apply

    def _exec(self, cmd: list[str], cwd: str) -> tuple[Optional[int], str, str, bool]:
        kwargs = dict(cwd=cwd, env=self._base_env(cwd), stdout=subprocess.PIPE,
                      stderr=subprocess.PIPE, text=True)
        if os.name == "posix":
            kwargs["preexec_fn"] = self._limits()
            kwargs["start_new_session"] = True
        p = subprocess.Popen(self.resolve_isolation()[1] + cmd, **kwargs)
        try:
            out, err = p.communicate(timeout=self.timeout)
            return p.returncode, out, err, False
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGKILL)
            except Exception:
                p.kill()
            out, err = p.communicate()
            return None, out or "", (err or "") + "\n[sandbox] killed: timeout", True

    def _clip(self, s: str) -> str:
        s = s or ""
        return s if len(s) <= self.max_output else s[: self.max_output] + "\n…[truncated]"

    # ----- public ----- #
    def run(self, artifacts: dict[str, str]) -> SandboxResult:
        tmp = tempfile.mkdtemp(prefix="tehai_sbx_")
        try:
            written: dict[str, str] = {}
            for name, content in artifacts.items():
                safe = _safe_name(name)
                # The model sometimes names a code artifact with a prose description;
                # recover a runnable extension by sniffing the content.
                if not safe.lower().endswith(_CODE_EXTS):
                    ext = _sniff_ext(content)
                    if ext:
                        safe = safe + ext
                path = os.path.join(tmp, safe)
                # extra guard: never escape tmp
                if os.path.commonpath([tmp, os.path.abspath(path)]) != tmp:
                    continue
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(content)
                written[safe] = content

            iso = self.resolve_isolation()[0]
            if iso == "refuse":
                return SandboxResult(
                    ran=False, passed=False, runner="none", isolation="refuse",
                    reason="strict isolation required but unavailable (no working unshare)",
                    files=list(written))
            runners = self._runners(written)
            if not runners:
                return SandboxResult(ran=False, passed=False, runner="none",
                                     reason="no runner available for these artifacts",
                                     isolation=iso, files=list(written))

            last = SandboxResult(ran=True, passed=True, isolation=iso, files=list(written))
            for cmd, label in runners:
                code, out, err, timed = self._exec(cmd, tmp)
                last = SandboxResult(
                    ran=True, passed=(code == 0), runner=label, exit_code=code,
                    stdout=self._clip(out), stderr=self._clip(err),
                    timed_out=timed, isolation=iso, files=list(written),
                    reason=("timeout" if timed else ("ok" if code == 0 else "non-zero exit")),
                )
                if code != 0:
                    return last  # stop at first failure
            return last
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
