# SPDX-FileCopyrightText: 2026 aas-package3-python AUTHORS
#
# SPDX-License-Identifier: MIT

"""Execute the Python code blocks in the readme so that they stay correct."""

import os
import pathlib
import re
import tempfile
import textwrap
import unittest
from typing import Final, List

REPO_ROOT: Final[pathlib.Path] = pathlib.Path(__file__).resolve().parent.parent.parent

CODE_BLOCK_RE: Final[re.Pattern[str]] = re.compile(
    r"^\.\. code-block:: python\n\n((?:    .*\n|\n)+)", re.MULTILINE
)


def extract_python_code_blocks(text: str) -> List[str]:
    """Extract the Python code blocks from the reStructuredText ``text``."""
    return [textwrap.dedent(match.group(1)) for match in CODE_BLOCK_RE.finditer(text)]


class TestReadme(unittest.TestCase):
    def test_code_blocks(self) -> None:
        code_blocks = extract_python_code_blocks(
            (REPO_ROOT / "README.rst").read_text(encoding="utf-8")
        )
        self.assertGreater(len(code_blocks), 0)

        cwd = pathlib.Path.cwd()
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.chdir(tmp_dir)
            try:
                for code_block in code_blocks:
                    exec(compile(code_block, "README.rst", "exec"), {})

                self.assertTrue((pathlib.Path(tmp_dir) / "some-file.aasx").exists())
            finally:
                os.chdir(cwd)


if __name__ == "__main__":
    unittest.main()
