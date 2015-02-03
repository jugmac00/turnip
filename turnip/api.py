from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

import os

from twisted.internet import defer
from twisted.internet.utils import getProcessValue
from twisted.web import (
    resource,
    server,
    static,
    )

from turnip.helpers import compose_path


class TurnipAPIResource(resource.Resource):

    def __init__(self, root):
        resource.Resource.__init__(self)
        self.root = root

    def getChild(self, name, request):
        if name == b'':
            return static.Data(b'Turnip API endpoint', type=b'text/plain')
        if name == b'create':
            return CreateResource(self.root)
        else:
            return resource.NoResource(b'No such resource')

    def render_GET(self, request):
        return b'Turnip API service endpoint'


class CreateResource(resource.Resource):

    def __init__(self, root):
        resource.Resource.__init__(self)
        self.root = root

    @defer.inlineCallbacks
    def createRepo(self, request, raw_path):
        repo_path = compose_path(self.root, raw_path)
        if os.path.exists(repo_path):
            raise Exception("Repository '%s' already exists" % repo_path)
        ret = yield getProcessValue('git', ('init', '--bare', repo_path))
        if ret != 0:
            raise Exception("'git init' failed")
        request.write(b'OK')
        request.finish()

    def render_POST(self, request):
        path = request.args['path'][0]
        d = self.createRepo(request, path)
        d.addErrback(request.processingFailed)
        return server.NOT_DONE_YET
