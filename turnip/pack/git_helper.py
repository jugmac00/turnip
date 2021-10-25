#!/usr/bin/python3

# Copyright 2020 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

import fcntl
import json
import os
import resource
import subprocess
import sys
import time


if __name__ == '__main__':
    # We expect the caller to have opened FD 3, and will send information
    # about git's resource usage there.  Mark it close-on-exec so that the
    # git child process can't accidentally interfere with it.
    flags = fcntl.fcntl(3, fcntl.F_GETFD)
    fcntl.fcntl(3, fcntl.F_SETFD, flags | fcntl.FD_CLOEXEC)

    # Call git and wait for it to finish.
    start_time = time.clock_gettime(time.CLOCK_MONOTONIC)
    ret = subprocess.call(['git'] + sys.argv[1:])
    end_time = time.clock_gettime(time.CLOCK_MONOTONIC)

    # Dump resource usage information to FD 3.
    resource_fd = os.fdopen(3, 'w')
    rusage = resource.getrusage(resource.RUSAGE_CHILDREN)
    resource_fd.write(json.dumps({
        "clock_time": end_time - start_time,
        "user_time": rusage.ru_utime,
        "system_time": rusage.ru_stime,
        "max_rss": rusage.ru_maxrss,
        }))

    # Pass on git's exit status.
    sys.exit(ret)
