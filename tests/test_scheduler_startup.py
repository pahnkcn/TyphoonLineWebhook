import importlib
import runpy
import sys
from types import ModuleType
from unittest.mock import Mock

import pytest


class _FakeScheduler:
    def __init__(self):
        self.running = False
        self.jobs = []
        self.start_calls = 0

    def add_job(self, func, trigger, **kwargs):
        self.jobs.append((func, trigger, kwargs))

    def start(self):
        self.start_calls += 1
        self.running = True

    def shutdown(self, wait=True):
        self.running = False


class _FailingScheduler(_FakeScheduler):
    def __init__(self, failing_job_id):
        super().__init__()
        self.failing_job_id = failing_job_id

    def add_job(self, func, trigger, **kwargs):
        if kwargs.get("id") == self.failing_job_id:
            raise RuntimeError("scheduler startup failed")
        super().add_job(func, trigger, **kwargs)


@pytest.fixture
def app_main_module():
    return importlib.import_module("app.app_main")


def _import_wsgi(monkeypatch, enable_scheduler=None):
    init_scheduler = Mock()
    fake_app_module = ModuleType("app.app_main")
    fake_app_module.app = object()
    fake_app_module.init_scheduler = init_scheduler

    monkeypatch.setitem(sys.modules, "app.app_main", fake_app_module)
    monkeypatch.delenv("ENABLE_SCHEDULER", raising=False)
    if enable_scheduler is not None:
        monkeypatch.setenv("ENABLE_SCHEDULER", enable_scheduler)

    sys.modules.pop("wsgi", None)
    module = importlib.import_module("wsgi")
    return module, init_scheduler


def _run_wsgi_as_main(monkeypatch, enable_scheduler=None):
    init_scheduler = Mock()
    serve = Mock()
    fake_app_module = ModuleType("app.app_main")
    fake_app_module.app = object()
    fake_app_module.init_scheduler = init_scheduler
    fake_waitress_module = ModuleType("waitress")
    fake_waitress_module.serve = serve

    monkeypatch.setitem(sys.modules, "app.app_main", fake_app_module)
    monkeypatch.setitem(sys.modules, "waitress", fake_waitress_module)
    monkeypatch.delenv("ENABLE_SCHEDULER", raising=False)
    if enable_scheduler is not None:
        monkeypatch.setenv("ENABLE_SCHEDULER", enable_scheduler)

    sys.modules.pop("wsgi", None)
    runpy.run_module("wsgi", run_name="__main__")
    return init_scheduler, serve


def test_wsgi_skips_scheduler_autostart_by_default(monkeypatch):
    module, init_scheduler = _import_wsgi(monkeypatch)

    init_scheduler.assert_not_called()
    assert module.application is module.app


def test_wsgi_autostarts_scheduler_when_enabled(monkeypatch):
    module, init_scheduler = _import_wsgi(monkeypatch, "1")

    init_scheduler.assert_called_once_with()
    assert module.application is module.app


def test_wsgi_invalid_log_level_falls_back_to_info(monkeypatch):
    module, init_scheduler = _import_wsgi(monkeypatch)

    monkeypatch.setenv("LOG_LEVEL", "not-a-real-level")
    sys.modules.pop("wsgi", None)
    module = importlib.import_module("wsgi")

    init_scheduler.assert_not_called()
    assert module.application is module.app


def test_wsgi_direct_run_does_not_double_start_scheduler(monkeypatch):
    init_scheduler, serve = _run_wsgi_as_main(monkeypatch, "1")

    init_scheduler.assert_called_once_with()
    serve.assert_called_once()


def test_init_scheduler_registers_jobs_once(monkeypatch, app_main_module):
    proactive_module = importlib.import_module("app.services.proactive_checkin")
    fake_scheduler = _FakeScheduler()
    registered_hooks = []

    monkeypatch.setattr(app_main_module, "scheduler", fake_scheduler)
    monkeypatch.setattr(app_main_module, "_scheduler_initialized", False)
    monkeypatch.setattr(proactive_module, "run_proactive_checkins", lambda: None)
    monkeypatch.setattr(app_main_module.atexit, "register", registered_hooks.append)

    app_main_module.init_scheduler()

    assert app_main_module._scheduler_initialized is True
    assert fake_scheduler.start_calls == 1
    assert len(fake_scheduler.jobs) == 2
    assert [job[2]["id"] for job in fake_scheduler.jobs] == [
        app_main_module._FOLLOW_UP_JOB_ID,
        app_main_module._PROACTIVE_CHECKIN_JOB_ID,
    ]
    assert all(job[2]["replace_existing"] is True for job in fake_scheduler.jobs)
    assert len(registered_hooks) == 1

    app_main_module.init_scheduler()

    assert fake_scheduler.start_calls == 1
    assert len(fake_scheduler.jobs) == 2
    assert len(registered_hooks) == 1


def test_init_scheduler_resets_initialized_flag_on_failure(monkeypatch, app_main_module):
    proactive_module = importlib.import_module("app.services.proactive_checkin")
    fake_scheduler = _FailingScheduler(app_main_module._PROACTIVE_CHECKIN_JOB_ID)

    monkeypatch.setattr(app_main_module, "scheduler", fake_scheduler)
    monkeypatch.setattr(app_main_module, "_scheduler_initialized", False)
    monkeypatch.setattr(proactive_module, "run_proactive_checkins", lambda: None)
    monkeypatch.setattr(app_main_module.atexit, "register", lambda fn: None)

    with pytest.raises(RuntimeError, match="scheduler startup failed"):
        app_main_module.init_scheduler()

    assert app_main_module._scheduler_initialized is False
    assert fake_scheduler.start_calls == 0
    assert fake_scheduler.running is False
