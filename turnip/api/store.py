import shutil

import pygit2


class Store(object):
    """Provides methods for manipulating repos on disk with pygit2."""
    @classmethod
    def init(self, repo, isBare=True):
        """Initialise a git repo with pygit2."""
        try:
            repo_path = pygit2.init_repository(repo, isBare)
        except pygit2.GitError as e:
            print('Unable to create repository: %s' % e)
            return
        return repo_path

    @classmethod
    def delete(self, repo):
        """Permanently delete a git repository from repo store."""
        try:
            shutil.rmtree(repo)
        except (IOError, OSError) as e:
            print('Unable to delete repository: %s' % e)
