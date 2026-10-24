"""Resource-limit setup tests; any spawned process is owned by this test."""

import ctypes
import ast
import os
import subprocess
import sys

import pytest

from src.core.documenti import process_limits


@pytest.mark.parametrize("memory", [0, -1, True, 1.5, "1024"])
def test_posix_limits_reject_invalid_memory_budgets(memory):
    with pytest.raises(ValueError, match="invalid_memory_budget"):
        process_limits.set_posix_limits(memory)


@pytest.mark.parametrize("cpu", [0, -1, True, 1.5, "3"])
def test_posix_limits_reject_invalid_cpu_budgets(cpu):
    with pytest.raises(ValueError, match="invalid_cpu_budget"):
        process_limits.set_posix_limits(1024, cpu)


@pytest.mark.skipif(os.name != "posix", reason="resource limits are POSIX-specific")
def test_posix_limits_apply_in_a_disposable_child_process():
    code = (
        "from src.core.documenti.process_limits import set_posix_limits; "
        "import resource; set_posix_limits(16777216, 3); "
        "print(resource.getrlimit(resource.RLIMIT_DATA)); "
        "print(resource.getrlimit(resource.RLIMIT_CPU))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    data_limits, cpu_limits = [ast.literal_eval(line) for line in completed.stdout.splitlines()]
    assert data_limits == (16777216, 16777216)
    assert cpu_limits == (3, 3)


class _FakeFunction:
    def __init__(self, callback):
        self.callback = callback
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self.callback(*args)


class _FakeKernel32:
    def __init__(self, *, assign=True):
        self.closed = []
        self.observed_limits = None
        self.assign = assign
        self.CreateJobObjectW = _FakeFunction(lambda *_: 11)
        self.SetInformationJobObject = _FakeFunction(self._set_limits)
        self.OpenProcess = _FakeFunction(lambda *_: 22)
        self.AssignProcessToJobObject = _FakeFunction(lambda *_: int(self.assign))
        self.CloseHandle = _FakeFunction(self.closed.append)

    def _set_limits(self, _job, info_class, pointer, _size):
        struct_type = process_limits._win_structures()[1]
        info = ctypes.cast(pointer, ctypes.POINTER(struct_type)).contents
        self.observed_limits = (info_class, info.BasicLimitInformation.LimitFlags, info.JobMemoryLimit)
        return 1


def _fake_windows(monkeypatch, kernel):
    monkeypatch.setattr(process_limits, "_PLATFORM", "win32")
    monkeypatch.setattr(process_limits.ctypes, "WinDLL", lambda *_args, **_kwargs: kernel, raising=False)


def test_windows_job_owns_handle_until_context_closes_and_configures_limits(monkeypatch):
    kernel = _FakeKernel32()
    _fake_windows(monkeypatch, kernel)

    with process_limits.attach_windows_job(1234, 64 * 1024 * 1024) as handle:
        assert handle == 11
        assert kernel.closed == [22]  # Process handle closes after successful assignment.
        assert kernel.observed_limits == (9, 0x2000 | 0x200, 64 * 1024 * 1024)

    assert kernel.closed == [22, 11]  # Closing the job kills its process tree.


def test_windows_job_assignment_failure_fails_closed_and_closes_owned_handles(monkeypatch):
    kernel = _FakeKernel32(assign=False)
    _fake_windows(monkeypatch, kernel)

    with pytest.raises(RuntimeError, match="windows_job_assign_failed"):
        with process_limits.attach_windows_job(1234, 64 * 1024 * 1024):
            pytest.fail("an unassigned process must never enter the caller's block")

    assert kernel.closed == [22, 11]


def test_windows_job_refuses_non_windows_platform(monkeypatch):
    monkeypatch.setattr(process_limits, "_PLATFORM", "linux")
    with pytest.raises(RuntimeError, match="windows_job_unsupported"):
        with process_limits.attach_windows_job(1234, 1024):
            pytest.fail("unsupported platform must not yield")
