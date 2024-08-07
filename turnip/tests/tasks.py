# Copyright 2020 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

import atexit
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta

import fixtures

from turnip.config import config
from turnip.tasks import app

BROKER_URL = "pyamqp://guest@localhost/turnip-test-vhost"


def setupCelery():
    app.conf.update(broker_url=BROKER_URL)


class CeleryWorkerFixture(fixtures.Fixture):
    """Celery worker fixture for tests.

    This fixture starts a celery worker with the configuration set when the
    fixture is setUp. Keep in mind that this will run in a separated
    new process, so mock patches for example will be lost.
    """

    _worker_proc = None

    def __init__(
        self, loglevel="info", logfile=None, force_restart=True, env=None
    ):
        """
        Build a celery worker for test cases.

        :param loglevel: Which log level to use for the worker.
        :param force_restart: If True and a celery worker is already running,
            stop it. If False, do not restart if another worker is
            already running.
        :param env: The environment variables to be used when creating
            the worker.
        """
        self.force_restart = force_restart
        self.loglevel = loglevel
        self.logfile = logfile
        self.env = env

    def startCeleryWorker(self):
        """Start a celery worker for integration tests."""
        if self.force_restart:
            self.stopCeleryWorker()
        if CeleryWorkerFixture._worker_proc is not None:
            return
        bin_path = os.path.dirname(sys.executable)
        celery = os.path.join(bin_path, "celery")
        cmd = [
            celery,
            "worker",
            "-A",
            "turnip.tasks",
            "--quiet",
            "--pool=gevent",
            "--concurrency=20",
            "--broker=%s" % BROKER_URL,
            "--loglevel=%s" % self.loglevel,
            "--queue=repacks,celery",
        ]
        if self.logfile:
            cmd += ["--logfile=%s" % self.logfile]

        # Send to the subprocess, as env variables, the same configurations we
        # are currently using.
        proc_env = {}
        for k in config.defaults:
            value = config.get(k)
            if isinstance(value, bytes):
                value = value.decode("utf-8")
            else:
                value = str(value)
            proc_env[k.upper()] = value
        proc_env.update(self.env or {})

        CeleryWorkerFixture._worker_proc = subprocess.Popen(cmd, env=proc_env)
        atexit.register(self.stopCeleryWorker)

    def stopCeleryWorker(self):
        worker_proc = CeleryWorkerFixture._worker_proc
        if worker_proc:
            worker_proc.kill()
            worker_proc.wait()
        CeleryWorkerFixture._worker_proc = None
        # Cleanup the queue.
        app.control.purge()

    def waitUntil(self, seconds, callable, *args, **kwargs):
        """Waits some seconds until a callable(*args, **kwargs) returns
        true. Raises exception if that never happens"""
        start = datetime.now()
        while datetime.now() < start + timedelta(seconds=seconds):
            if callable(*args, **kwargs):
                return
            time.sleep(0.2)
        raise AttributeError(
            "%s(*%s, **%s) never returned True after %s seconds"
            % (callable.__name__, args, kwargs, seconds)
        )

    def _setUp(self):
        self.startCeleryWorker()
        self.addCleanup(self.stopCeleryWorker)
