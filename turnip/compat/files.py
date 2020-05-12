import sys


def fd_buffer(file_descriptor):
    """Returns the raw bytes stream for the given file descriptor.

    On Python2, it returns the file descriptor itself (since it reads raw
    binary data by default). On Python3, it returns the
    file_descriptor.buffer object (since py3, by default, opens files with
    an encoding).

    It's useful to read and write from sys.std{in,out,err} without reopening
    those files, for example.

    :param file_descriptor: The file descriptor to get raw buffer from.
    :return: A BufferedReader or BufferedWriter object."""
    PY3K = sys.version_info >= (3, 0)
    if PY3K:
        return file_descriptor.buffer
    else:
        return file_descriptor
