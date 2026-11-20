# SPDX-FileCopyrightText: 2026 aas-package3-python AUTHORS
#
# SPDX-License-Identifier: MIT

"""Provide common functionality for the tests."""

import contextlib
import io
import pathlib
import unittest
import zipfile
from typing import Callable, ContextManager, Dict, Final

#: Directory of the test data
TEST_DATA_DIR: Final[pathlib.Path] = (
    pathlib.Path(__file__).resolve().parent.parent / "test_data"
)


def read_zip(data: bytes) -> Dict[str, bytes]:
    """
    Read all the entries of the ZIP archive ``data``.

    :param data: content of a valid ZIP archive with unique entry names
    :return: mapping of the entry names to their contents, in the order of the archive
    """
    assert zipfile.is_zipfile(io.BytesIO(data)), "The data must be a ZIP archive."

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        infos = archive.infolist()

        # NOTE (mristin):
        # We would silently lose the content of the duplicate entries in
        # the mapping, so we forbid them.
        assert len({info.filename for info in infos}) == len(
            infos
        ), "The entry names must be unique."

        result = {info.filename: archive.read(info) for info in infos}

    assert list(result) == [
        info.filename for info in infos
    ], "All the entries must be read in the order of the archive."
    assert all(
        len(result[info.filename]) == info.file_size for info in infos
    ), "The content of each entry must be read completely."

    return result


def write_zip(entries: Dict[str, bytes]) -> bytes:
    """
    Write the ``entries`` as a ZIP archive.

    :param entries:
        mapping of the entry names to their contents; the names are relative
        (*i.e.*, without a leading slash) and denote files, not directories
    :return: content of the ZIP archive
    """
    # NOTE (mristin):
    # The zipfile module silently changes or drops some of these names, so
    # the entries could not be read back as they were given.
    assert all(
        len(name) > 0
        and not name.startswith("/")
        and not name.endswith("/")
        and "\x00" not in name
        for name in entries
    ), "The entry names must be non-empty, relative file names without a NUL."

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    result = buffer.getvalue()

    assert read_zip(result) == entries, "The entries must be read back unchanged."

    return result


def rewrite_entries(data: bytes, rewrite: Callable[[str, bytes], bytes]) -> bytes:
    """
    Apply ``rewrite`` to each entry of the ZIP archive ``data``.

    :param data: content of a valid ZIP archive with unique entry names
    :param rewrite: maps an entry name and its content to the new content
    :return: content of the ZIP archive with the same entries, but rewritten
    """
    assert zipfile.is_zipfile(io.BytesIO(data)), "The data must be a ZIP archive."

    entries = read_zip(data)
    rewritten = {name: rewrite(name, content) for name, content in entries.items()}
    result = write_zip(rewritten)

    assert list(read_zip(result)) == list(
        entries
    ), "The entry names and their order must be kept."
    assert read_zip(result) == rewritten, "Each entry must hold the rewritten content."

    return result


def replace_in_relationships(data: bytes, old: str, new: str) -> bytes:
    """
    Replace ``old`` with ``new`` in all the relationships entries of ``data``.

    The replacement is a plain substring replacement, not a regular expression.

    :param data: content of a valid ZIP archive with unique entry names
    :param old: non-empty text to be found in at least one relationships entry
    :param new: replacement text
    :return: content of the ZIP archive with the relationships entries replaced
    """
    assert len(old) > 0, "The old text must be non-empty."

    old_bytes = old.encode("utf-8")
    new_bytes = new.encode("utf-8")

    entries = read_zip(data)

    # NOTE (mristin):
    # We require ``old`` to occur so that a test does not silently degenerate
    # into a no-op, e.g., if the serialization of the relationships changes.
    assert any(
        old_bytes in content
        for name, content in entries.items()
        if name.endswith(".rels")
    ), f"The old text {old!r} must occur in at least one relationships entry."

    result = rewrite_entries(
        data,
        lambda name, content: (
            content.replace(old_bytes, new_bytes) if name.endswith(".rels") else content
        ),
    )

    assert read_zip(result) == {
        name: (
            content.replace(old_bytes, new_bytes) if name.endswith(".rels") else content
        )
        for name, content in entries.items()
    }, "Only the relationships entries must be changed, and all occurrences."

    return result


def contract_violation(test_case: unittest.TestCase) -> ContextManager[object]:
    """
    Expect a violated pre- or post-condition in the ``with`` block.

    The conditions are checked with ``assert`` and are hence not checked when
    Python runs with ``-O``. In that case, we expect nothing.
    """
    if __debug__:
        return test_case.assertRaises(AssertionError)

    return contextlib.nullcontext()
