from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

from testtools import TestCase

from turnip import utils


class TestComposePath(TestCase):
    """Tests for path composition."""

    def test_basic(self):
        # The path is resolved within the given root tree.
        self.assertEqual(
            b'/foo/bar/baz/quux',
            utils.compose_path(b'/foo/bar', b'baz/quux'))

    def test_absolute(self):
        # Even absolute paths are contained.
        self.assertEqual(
            b'/foo/bar/baz/quux',
            utils.compose_path(b'/foo/bar', b'/baz/quux'))

    def test_normalises(self):
        # Paths are normalised.
        self.assertEqual(
            b'/foo/bar/baz/quux',
            utils.compose_path(b'///foo/.//bar', b'//baz/..//baz/./quux'))

    def test_no_escape(self):
        # You can't get out.
        self.assertRaises(
            ValueError, utils.compose_path, '/foo', '../bar')
        self.assertRaises(
            ValueError, utils.compose_path, '/foo', '/foo/../../bar')
