# SPDX-FileCopyrightText: 2026 aas-package3-python AUTHORS
#
# SPDX-License-Identifier: MIT

"""Configure Sphinx to build the documentation."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent))

import aas_package3  # noqa: E402

project = "aas-package3-python"
copyright = aas_package3.__copyright__  # noqa: A001
author = aas_package3.__author__
release = aas_package3.__version__
version = release

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.doctest",
    "sphinx.ext.intersphinx",
    "sphinx.ext.viewcode",
]

autodoc_typehints = "signature"
autodoc_member_order = "bysource"
autodoc_default_options = {
    "members": True,
    "show-inheritance": True,
}

intersphinx_mapping = {"python": ("https://docs.python.org/3", None)}

# NOTE (mristin):
# The ``>>>`` examples in the docstrings are doctest'ed by the pre-commit checks.
# Here we test only the explicit ``testcode`` directives.
doctest_test_doctest_blocks = ""

master_doc = "index"
exclude_patterns: list = []
html_theme = "sphinx_rtd_theme"
html_static_path = ["_static"]
