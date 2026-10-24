"""Fail-closed resource limits for isolated document-processing workers."""

from __future__ import annotations

from contextlib import contextmanager
import ctypes
import os
import sys
from typing import Iterator

try:
    import resource
except ImportError:  # Windows has no resource module.
    resource = None

_PLATFORM = sys.platform


def _positive_int(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"invalid_{name}")
    return value


def _bounded_limit(requested: int, soft: int, hard: int) -> int:
    finite = [limit for limit in (requested, soft, hard) if limit != resource.RLIM_INFINITY]
    return min(finite)


def _win_structures():
    """Build the documented Job Object extended limit structures."""

    class _BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", ctypes.c_uint32),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", ctypes.c_uint32),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", ctypes.c_uint32),
            ("SchedulingClass", ctypes.c_uint32),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class _ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimitInformation),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    return _BasicLimitInformation, _ExtendedLimitInformation


@contextmanager
def attach_windows_job(pid: int, memory_bytes: int) -> Iterator[int]:
    """Attach an owned process to a kill-on-close, memory-limited Job Object.

    The yielded job handle must remain open for the whole child lifetime. Closing
    the context closes the job handle, which terminates the child and its tree.
    Any setup error raises a code-only RuntimeError and never returns an
    unsandboxed process to the caller.
    """
    _positive_int(pid, "pid")
    memory_bytes = _positive_int(memory_bytes, "memory_budget")
    if _PLATFORM != "win32":
        raise RuntimeError("windows_job_unsupported")

    win_dll = getattr(ctypes, "WinDLL", None)
    if win_dll is None:
        raise RuntimeError("windows_job_api_unavailable")

    try:
        kernel32 = win_dll("kernel32", use_last_error=True)
    except Exception as exc:
        raise RuntimeError("windows_job_api_unavailable") from None

    job_handle = None
    process_handle = None
    try:
        kernel32.CreateJobObjectW.restype = ctypes.c_void_p
        kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, ctypes.c_wchar_p)
        job_handle = kernel32.CreateJobObjectW(None, None)
        if not job_handle:
            raise RuntimeError("windows_job_create_failed")

        _, extended_type = _win_structures()
        limits = extended_type()
        limits.BasicLimitInformation.LimitFlags = 0x2000 | 0x200  # KILL_ON_JOB_CLOSE | JOB_MEMORY
        limits.JobMemoryLimit = memory_bytes
        kernel32.SetInformationJobObject.argtypes = (
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_uint32,
        )
        kernel32.SetInformationJobObject.restype = ctypes.c_int
        if not kernel32.SetInformationJobObject(
            job_handle,
            9,  # JobObjectExtendedLimitInformation
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            raise RuntimeError("windows_job_configure_failed")

        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
        process_handle = kernel32.OpenProcess(0x0100 | 0x0001 | 0x0400, False, pid)
        if not process_handle:
            raise RuntimeError("windows_process_open_failed")

        kernel32.AssignProcessToJobObject.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
        kernel32.AssignProcessToJobObject.restype = ctypes.c_int
        if not kernel32.AssignProcessToJobObject(job_handle, process_handle):
            raise RuntimeError("windows_job_assign_failed")

        # The process handle is no longer needed after successful assignment.
        kernel32.CloseHandle(process_handle)
        process_handle = None
        try:
            yield job_handle
        finally:
            # Closing the job handle kills every process in the job.
            kernel32.CloseHandle(job_handle)
            job_handle = None
    except RuntimeError:
        raise
    except Exception:
        raise RuntimeError("windows_job_setup_failed") from None
    finally:
        if process_handle:
            kernel32.CloseHandle(process_handle)
        if job_handle:
            kernel32.CloseHandle(job_handle)


def set_posix_limits(memory_bytes: int, cpu_seconds: int | None = None) -> None:
    """Apply hard resource ceilings in a POSIX worker before heavy imports."""
    memory_bytes = _positive_int(memory_bytes, "memory_budget")
    if cpu_seconds is not None:
        cpu_seconds = _positive_int(cpu_seconds, "cpu_budget")
    if os.name != "posix" or resource is None or not hasattr(resource, "RLIMIT_DATA"):
        raise RuntimeError("posix_resource_limits_unsupported")

    try:
        current_soft, current_hard = resource.getrlimit(resource.RLIMIT_DATA)
        memory_limit = _bounded_limit(memory_bytes, current_soft, current_hard)
        resource.setrlimit(resource.RLIMIT_DATA, (memory_limit, memory_limit))

        if cpu_seconds is not None:
            cpu_soft, cpu_hard = resource.getrlimit(resource.RLIMIT_CPU)
            cpu_limit = _bounded_limit(cpu_seconds, cpu_soft, cpu_hard)
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_limit, cpu_limit))
    except (OSError, ValueError):
        raise RuntimeError("posix_resource_limit_setup_failed") from None
