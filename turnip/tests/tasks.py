# Copyright 2020 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

import os
import subprocess
import sys

import atexit

from turnip.tasks import app

BROKER_URL = 'pyamqp://guest@localhost/turnip-test-vhost'
worker_proc = None


def setupCelery():
    app.conf.update(broker_url=BROKER_URL)


def startCeleryWorker(loglevel="info"):
    """Start a celery worker for test.

    :param quiet: If True, do not output celery worker on stdout.
    """
    global worker_proc
    if worker_proc is not None:
        return
    bin_path = os.path.dirname(sys.executable)
    celery = os.path.join(bin_path, 'celery')
    turnip_path = os.path.join(os.path.dirname(__file__), '..')
    cmd = [
        celery, 'worker', '-A', 'tasks', '--quiet',
        '--pool=gevent',
        '--concurrency=2',
        '--broker=%s' % BROKER_URL,
        '--loglevel=%s' % loglevel]
    worker_proc = subprocess.Popen(cmd, env={'PYTHONPATH': turnip_path})
    atexit.register(stopCeleryWorker)


def stopCeleryWorker():
    if worker_proc is not None:
        worker_proc.kill()
