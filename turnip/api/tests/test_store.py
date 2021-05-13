# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

import os.path
import re
import subprocess
import uuid

from fixtures import (
    EnvironmentVariable,
    MonkeyPatch,
    TempDir,
    )
import pygit2
from testtools import TestCase
import yaml

from turnip.api import store
from turnip.api.tests.test_helpers import (
    open_repo,
    RepoFactory,
    )
from turnip.tests.tasks import CeleryWorkerFixture


class InitTestCase(TestCase):

    def setUp(self):
        super(InitTestCase, self).setUp()
        self.repo_store = self.useFixture(TempDir()).path
        self.useFixture(EnvironmentVariable("REPO_STORE", self.repo_store))

    def assertAllLinkCounts(self, link_count, path):
        count = 0
        for dirpath, _, filenames in os.walk(path):
            for filename in filenames:
                count += 1
                self.assertEqual(
                    link_count,
                    os.stat(os.path.join(dirpath, filename)).st_nlink)
        return count

    def assertAdvertisedRefs(self, present, absent, repo_path):
        out, err = subprocess.Popen(
            ['git', 'receive-pack', '--advertise-refs', repo_path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE).communicate()
        self.assertEqual(b'', err)
        for ref, hex in present:
            self.assertIn(
                hex.encode('ascii') + b' ' + ref.encode('utf-8'), out)
        for ref in absent:
            self.assertNotIn(absent, out)

    def assertPackedRefs(self, refs, repo_path):
        """Assert the exact format of a packed-refs file.

        We're writing this out directly, so make sure it's as we expect.

        :param refs: A mapping from ref names to (oid, peeled_oid) tuples,
            where peeled_oid may be None if the ref points directly to a
            commit object.
        :param repo_path: The path to the .git directory to check.
        """
        expected_packed_refs = [
            b'# pack-refs with: peeled fully-peeled sorted ']
        for ref_name, (oid, peeled_oid) in sorted(refs.items()):
            if not isinstance(ref_name, bytes):
                ref_name = ref_name.encode('utf-8')
            expected_packed_refs.append(
                b'%s %s' % (oid.encode('ascii'), ref_name))
            if peeled_oid is not None:
                expected_packed_refs.append(
                    b'^%s' % (peeled_oid.encode('ascii'),))
        with open(os.path.join(repo_path, 'packed-refs'), 'rb') as packed_refs:
            self.assertEqual(
                b'\n'.join(expected_packed_refs) + b'\n', packed_refs.read())

    def assertAlternates(self, expected_paths, repo_path):
        alt_path = store.alternates_path(repo_path)
        if not os.path.exists(os.path.dirname(alt_path)):
            raise Exception("No repo at %s." % repo_path)
        actual_paths = []
        if os.path.exists(alt_path):
            with open(alt_path) as altf:
                actual_paths = [
                    re.sub('/objects\n$', '', line) for line in altf]
        self.assertEqual(
            set([path.rstrip('/') for path in expected_paths]),
            set(actual_paths))

    def makeOrig(self):
        self.orig_path = os.path.join(self.repo_store, 'orig/')
        self.orig_factory = RepoFactory(
            self.orig_path, num_branches=3, num_commits=2, num_tags=2)
        orig = self.orig_factory.build()
        self.orig_refs = {}
        for ref in orig.references.objects:
            obj = orig[ref.target]
            self.orig_refs[ref.name] = (
                obj.hex,
                ref.peel().hex if obj.type != pygit2.GIT_OBJ_COMMIT else None)
        self.master_oid = orig.references['refs/heads/master'].target
        self.orig_objs = os.path.join(self.orig_path, '.git/objects')

    def test_from_scratch(self):
        path = os.path.join(self.repo_store, 'repo/')
        store.init_repo(path)
        r = pygit2.Repository(path)
        self.assertEqual([], r.listall_references())

    def test_repo_config(self):
        """Assert repository is initialised with correct config defaults."""
        repo_path = os.path.join(self.repo_store, 'repo')
        store.init_repo(repo_path)
        repo_config = pygit2.Repository(repo_path).config
        with open('git.config.yaml') as f:
            yaml_config = yaml.safe_load(f)

        self.assertEqual(bool(yaml_config['core.logallrefupdates']),
                         bool(repo_config['core.logallrefupdates']))
        self.assertEqual(str(yaml_config['pack.depth']),
                         repo_config['pack.depth'])

    def test_is_repository_available(self):
        repo_path = os.path.join(self.repo_store, 'repo/')

        # Fail to set status if repository directory doesn't exist.
        self.assertRaises(
            ValueError, store.set_repository_creating, repo_path, False)

        store.init_repository(repo_path, True)
        store.set_repository_creating(repo_path, True)
        self.assertFalse(store.is_repository_available(repo_path))

        store.set_repository_creating(repo_path, False)
        self.assertTrue(store.is_repository_available(repo_path))

        # Duplicate call to set_repository_creating(False) should ignore
        # eventually missing ".turnip-creating" file.
        store.set_repository_creating(repo_path, False)
        self.assertTrue(store.is_repository_available(repo_path))

    def test_open_ephemeral_repo(self):
        """Opening a repo where a repo name contains ':' should return
        a new ephemeral repo.
        """
        # Create repos A and B with distinct commits, and C which has no
        # objects of its own but has a clone of B as its
        # turnip-subordinate.
        repos = {}
        for name in ['A', 'B']:
            factory = RepoFactory(os.path.join(self.repo_store, name))
            factory.generate_branches(2, 2)
            repos[name] = factory.repo
        repo_path_c = os.path.join(self.repo_store, 'C')
        store.init_repo(
            repo_path_c, clone_from=os.path.join(self.repo_store, 'B'))
        repos['C'] = pygit2.Repository(repo_path_c)

        # Opening the union of one and three includes the objects from
        # two, as they're in three's turnip-subordinate.
        repo_name = 'A:C'

        with store.open_repo(self.repo_store, repo_name) as ephemeral_repo:
            self.assertAlternates(
                [repos['A'].path, repos['C'].path,
                 os.path.join(repos['A'].path, 'turnip-subordinate'),
                 os.path.join(repos['C'].path, 'turnip-subordinate')],
                ephemeral_repo.path)
            self.assertIn(repos['A'].head.target, ephemeral_repo)
            self.assertIn(repos['B'].head.target, ephemeral_repo)

    def test_open_ephemeral_repo_already_exists(self):
        """If an ephemeral repo already exists, open_repo fails correctly."""
        repos = {}
        for name in ('A', 'B'):
            repos[name] = RepoFactory(os.path.join(self.repo_store, name)).repo
        ephemeral_uuid = uuid.uuid4()
        self.useFixture(MonkeyPatch('uuid.uuid4', lambda: ephemeral_uuid))
        ephemeral_path = os.path.join(
            self.repo_store, 'ephemeral-' + ephemeral_uuid.hex)
        os.mkdir(ephemeral_path)

        def open_test_repo():
            with store.open_repo(self.repo_store, 'A:B'):
                pass

        e = self.assertRaises(pygit2.GitError, open_test_repo)
        self.assertEqual(
            str(e), "Repository '%s' already exists" % ephemeral_path)
        self.assertEqual(
            {'A', 'B', os.path.basename(ephemeral_path)},
            set(os.listdir(self.repo_store)))

    def test_open_ephemeral_repo_init_exception(self):
        """If init_repo fails, open_repo cleans up but preserves the error."""
        class InitException(Exception):
            pass

        def mock_write_alternates(*args, **kwargs):
            raise InitException()

        self.useFixture(MonkeyPatch(
            'turnip.api.store.write_alternates', mock_write_alternates))
        repos = {}
        for name in ('A', 'B'):
            repos[name] = RepoFactory(os.path.join(self.repo_store, name)).repo

        def open_test_repo():
            with store.open_repo(self.repo_store, 'A:B'):
                pass

        self.assertRaises(InitException, open_test_repo)
        self.assertEqual({'A', 'B'}, set(os.listdir(self.repo_store)))

    def test_repo_with_alternates(self):
        """Ensure objects path is defined correctly in repo alternates."""
        factory = RepoFactory(os.path.join(self.repo_store, uuid.uuid1().hex))
        repo_path_with_alt = os.path.join(self.repo_store, uuid.uuid1().hex)
        store.init_repo(
            repo_path_with_alt, alternate_repo_paths=[factory.repo.path])
        self.assertAlternates([factory.repo_path], repo_path_with_alt)

    def test_repo_alternates_objects_shared(self):
        """Ensure objects are shared from alternate repo."""
        factory = RepoFactory(os.path.join(self.repo_store, uuid.uuid1().hex))
        commit_oid = factory.add_commit('foo', 'foobar.txt')
        repo_path_with_alt = os.path.join(self.repo_store, uuid.uuid4().hex)
        store.init_repo(
            repo_path_with_alt, alternate_repo_paths=[factory.repo.path])
        repo_with_alt = open_repo(repo_path_with_alt)
        self.assertEqual(commit_oid.hex, repo_with_alt.get(commit_oid).hex)

    def test_clone_with_refs(self):
        self.makeOrig()
        self.assertAllLinkCounts(1, self.orig_objs)

        # init_repo with clone_from=orig and clone_refs=True creates a
        # repo with the same set of refs. And the objects are copied
        # too.
        to_path = os.path.join(self.repo_store, 'to/')
        self.assertFalse(store.is_repository_available(to_path))
        store.init_repo(to_path, clone_from=self.orig_path, clone_refs=True)
        self.assertTrue(store.is_repository_available(to_path))

        to = pygit2.Repository(to_path)
        self.assertIsNot(None, to[self.master_oid])
        self.assertEqual(
            sorted(self.orig_refs), sorted(to.listall_references()))
        self.assertPackedRefs(self.orig_refs, to_path)

        # Advance master and remove branch-2, so that the commit referenced
        # by the original repository's master isn't referenced by any of the
        # cloned repository's refs; otherwise git >= 2.13 deduplicates the
        # ref in the alternate object store which makes it hard to test that
        # it's set up properly.
        RepoFactory(to_path, num_commits=2).build()
        to.references.delete('refs/heads/branch-2')
        to_master_oid = to.references['refs/heads/master'].target

        # Internally, the packs are hardlinked into a subordinate
        # alternate repo, so minimal space is used by the clone.
        self.assertAlternates(['../turnip-subordinate'], to_path)
        to_sub_path = os.path.join(to_path, 'turnip-subordinate')
        self.assertTrue(os.path.exists(to_sub_path))
        self.assertAllLinkCounts(2, self.orig_objs)
        self.assertPackedRefs(self.orig_refs, to_sub_path)

        self.assertAdvertisedRefs(
            [('.have', self.master_oid.hex),
             ('refs/heads/master', to_master_oid.hex)],
            [], to_path)

    def test_clone_without_refs(self):
        self.makeOrig()
        self.assertAllLinkCounts(1, self.orig_objs)

        # init_repo with clone_from=orig and clone_refs=False creates a
        # repo without any refs, but the objects are copied.
        to_path = os.path.join(self.repo_store, 'to/')
        self.assertFalse(store.is_repository_available(to_path))
        store.init_repo(to_path, clone_from=self.orig_path, clone_refs=False)
        self.assertTrue(store.is_repository_available(to_path))

        to = pygit2.Repository(to_path)
        self.assertIsNot(None, to[self.master_oid])
        self.assertEqual([], to.listall_references())
        self.assertFalse(os.path.exists(os.path.join(to_path, 'packed-refs')))

        # Internally, the packs are hardlinked into a subordinate
        # alternate repo, so minimal space is used by the clone.
        self.assertAlternates(['../turnip-subordinate'], to_path)
        to_sub_path = os.path.join(to_path, 'turnip-subordinate')
        self.assertTrue(os.path.exists(to_sub_path))
        self.assertAllLinkCounts(2, self.orig_objs)
        self.assertPackedRefs(self.orig_refs, to_sub_path)

        # No refs exist, but receive-pack advertises the clone_from's
        # refs as extra haves.
        self.assertAdvertisedRefs(
            [('.have', self.master_oid.hex)], ['refs/'], to_path)

    def test_clone_of_clone(self):
        self.makeOrig()
        orig = pygit2.Repository(self.orig_path)
        orig_blob = orig.create_blob(b'orig')

        self.assertAllLinkCounts(1, self.orig_objs)
        to_path = os.path.join(self.repo_store, 'to/')
        self.assertFalse(store.is_repository_available(to_path))
        store.init_repo(to_path, clone_from=self.orig_path)
        self.assertTrue(store.is_repository_available(to_path))

        self.assertAllLinkCounts(2, self.orig_objs)
        to = pygit2.Repository(to_path)
        to_blob = to.create_blob(b'to')
        to.create_branch(
            'branch-0',
            orig[orig.references['refs/heads/branch-1'].target.hex], True)
        to.create_branch(
            'new-branch',
            orig[orig.references['refs/heads/branch-1'].target.hex])
        packed_refs = dict(self.orig_refs)
        packed_refs['refs/heads/branch-0'] = (
            orig.references['refs/heads/branch-1'].target.hex, None)
        packed_refs['refs/heads/new-branch'] = (
            orig.references['refs/heads/branch-1'].target.hex, None)

        too_path = os.path.join(self.repo_store, 'too/')
        store.init_repo(too_path, clone_from=to_path)
        self.assertAllLinkCounts(3, self.orig_objs)
        too_blob = pygit2.Repository(too_path).create_blob(b'too')

        # Each clone has just its subordinate as an alternate, and the
        # subordinate has no alternates of its own.
        for path in (to_path, too_path):
            self.assertAlternates(['../turnip-subordinate'], path)
            self.assertAlternates([], os.path.join(path, 'turnip-subordinate'))
            self.assertIn(self.master_oid.hex, pygit2.Repository(path))
            self.assertAdvertisedRefs(
                [('.have', self.master_oid.hex)], [], path)

        # Objects from all three repos are in the third.
        too = pygit2.Repository(too_path)
        self.assertIn(orig_blob, too)
        self.assertIn(to_blob, too)
        self.assertIn(too_blob, too)

        # Each clone has refs from its (transitive) parents in its
        # subordinate.
        self.assertPackedRefs(
            self.orig_refs, os.path.join(to_path, 'turnip-subordinate'))
        self.assertPackedRefs(
            packed_refs, os.path.join(too_path, 'turnip-subordinate'))

    def test_fetch_refs(self):
        celery_fixture = CeleryWorkerFixture()
        self.useFixture(celery_fixture)

        self.makeOrig()
        # Creates a new branch in the orig repository.
        orig_path = self.orig_path
        orig = self.orig_factory.repo
        master_tip = orig.references[b'refs/heads/master'].target.hex

        orig_branch_name = 'new-branch'
        orig_ref_name = 'refs/heads/new-branch'
        orig.create_branch(orig_branch_name, orig[master_tip])
        orig_commit_oid = self.orig_factory.add_commit(
            b'foobar file content', 'foobar.txt', parents=[master_tip],
            ref=orig_ref_name)
        orig_blob_id = orig[orig_commit_oid].tree[0].id

        dest_path = os.path.join(self.repo_store, 'to/')
        store.init_repo(dest_path, clone_from=self.orig_path)

        dest = pygit2.Repository(dest_path)
        self.assertEqual([], dest.references.objects)

        dest_ref_name = 'refs/merge/123'
        store.fetch_refs.apply_async(args=([
            (orig_path, orig_commit_oid.hex, dest_path, dest_ref_name)], ))
        celery_fixture.waitUntil(5, lambda: len(dest.references.objects) == 1)

        self.assertEqual(1, len(dest.references.objects))
        copied_ref = dest.references.objects[0]
        self.assertEqual(dest_ref_name, copied_ref.name)
        self.assertEqual(
            orig.references[orig_ref_name].target,
            dest.references[dest_ref_name].target)
        self.assertEqual(b'foobar file content', dest[orig_blob_id].data)

        # Updating and copying again should work too, and it should be
        # compatible with using the ref name instead of the commit ID too.
        orig_commit_oid = self.orig_factory.add_commit(
            b'changed foobar content', 'foobar.txt', parents=[orig_commit_oid],
            ref=orig_ref_name)
        orig_blob_id = orig[orig_commit_oid].tree[0].id

        store.fetch_refs.apply_async(args=([
            (orig_path, orig_ref_name, dest_path, dest_ref_name)], ))

        def waitForNewCommit():
            try:
                return dest[orig_blob_id].data == b'changed foobar content'
            except KeyError:
                return False
        celery_fixture.waitUntil(5, waitForNewCommit)

        self.assertEqual(1, len(dest.references.objects))
        copied_ref = dest.references.objects[0]
        self.assertEqual(dest_ref_name, copied_ref.name)
        self.assertEqual(
            orig.references[orig_ref_name].target,
            dest.references[dest_ref_name].target)
        self.assertEqual(b'changed foobar content', dest[orig_blob_id].data)

    def test_delete_ref(self):
        celery_fixture = CeleryWorkerFixture()
        self.useFixture(celery_fixture)

        self.makeOrig()
        orig_path = self.orig_path
        orig = self.orig_factory.repo

        master_tip = orig.references[b'refs/heads/master'].target.hex
        new_branch_name = 'new-branch'
        new_ref_name = 'refs/heads/new-branch'
        orig.create_branch(new_branch_name, orig[master_tip])
        self.orig_factory.add_commit(
            b'foobar file content', 'foobar.txt', parents=[master_tip],
            ref=new_ref_name)

        before_refs_len = len(orig.references.objects)
        operations = [(orig_path, new_ref_name)]
        store.delete_refs.apply_async((operations, ))
        celery_fixture.waitUntil(
            5, lambda: len(orig.references.objects) < before_refs_len)

        self.assertEqual(before_refs_len - 1, len(orig.references.objects))
        self.assertNotIn(
            new_branch_name, [i.name for i in orig.references.objects])

    def hasZeroLooseObjects(self, path):
        curdir = os.getcwd()
        os.chdir(path)
        objects = subprocess.check_output(['git', 'count-objects'],
                                          universal_newlines=True)
        if (int(objects[0:(objects.find(' objects'))]) == 0):
            os.chdir(curdir)
            return True
        else:
            os.chdir(curdir)
            return False

    def test_repack(self):
        celery_fixture = CeleryWorkerFixture()
        self.useFixture(celery_fixture)

        self.makeOrig()
        orig_path = self.orig_path

        # First assert we have loose objects for this repo
        self.assertFalse(self.hasZeroLooseObjects(orig_path))

        # Trigger the repack job
        store.repack.apply_async(queue='repacks',
                                 kwargs={'repo_path': orig_path, 'repo_id': 3})

        # Assert we have 0 loose objects after repack job ran
        celery_fixture.waitUntil(
            5, lambda: self.hasZeroLooseObjects(orig_path))

    def test_gc(self):
        celery_fixture = CeleryWorkerFixture()
        self.useFixture(celery_fixture)

        self.makeOrig()
        orig_path = self.orig_path

        # First assert we have loose objects for this repo
        self.assertFalse(self.hasZeroLooseObjects(orig_path))

        # Trigger the GC job
        store.gc.apply_async((orig_path, ))

        # Assert we have 0 loose objects after a gc job ran
        celery_fixture.waitUntil(
            5, lambda: self.hasZeroLooseObjects(orig_path))
