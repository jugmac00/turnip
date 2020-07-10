# Copyright 2020 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

import os
import subprocess
import sys

import atexit

from turnip.tasks import app

worker_proc = None


def setupCelery():
    app.conf.update(broker='pyamqp://guest@localhost/test-vhost/')
    startCeleryWorker()


def startCeleryWorker(quiet=True):
    """Start a celery worker for test.

    :param quiet: If True, do not output celery worker on stdout.
    """
    global worker_proc
    if worker_proc is not None:
        return
    bin_path = os.path.dirname(sys.executable)
    celery = os.path.join(bin_path, 'celery')
    cwd = os.path.join(os.path.dirname(__file__), '..')
    if quiet:
        log_arg = '--quiet'
    else:
        log_arg = '--loglevel=info'
    cmd = [
        celery, 'worker', '-A', 'tasks', log_arg, '--pool=gevent',
        '--concurrency=2']
    worker_proc = subprocess.Popen(cmd, cwd=cwd)
    atexit.register(stopCeleryWorker)


def stopCeleryWorker():
    if worker_proc is not None:
        worker_proc.kill()
