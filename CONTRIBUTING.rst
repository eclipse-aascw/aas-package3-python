************
Contributing
************

Coordinate First
================
Before you create a pull request, please `create a new issue`_ first to coordinate.

It might be that we are already working on the same or similar feature, but we haven't made our work visible yet.

.. _create a new issue: https://github.com/eclipse-aascw/aas-package3-python/issues/new

Repository Layout
=================
We split the library from its development tooling:

* ``aas_package3/`` is the library itself, described by ``pyproject.toml`` in the repository root.
* ``dev/`` contains everything needed for the development, described by ``dev/pyproject.toml``:

  * ``dev/tests/`` — the unit tests (including the fuzz tests),
  * ``dev/test_data/`` — the test data (*e.g.*, the packages written by other implementations),
  * ``dev/continuous_integration/`` — the pre-commit checks and the configuration of the linters, and
  * ``dev/live_tests/`` — the tests against the data on the internet.

* ``docs/`` contains the documentation, built with Sphinx and published to GitHub Pages.

Create a Development Environment
================================
We usually develop in a `virtual environment`_.
To create one, change to the root directory of the repository and invoke:

.. code-block::

    python -m venv venv

You need to activate it.
On \*nix (Linux, Mac, *etc.*):

.. code-block::

    source venv/bin/activate

and on Windows:

.. code-block::

    venv\Scripts\activate

.. _virtual environment: https://docs.python.org/3/tutorial/venv.html

Install Development Dependencies
================================
Once you activated the virtual environment, you can install the library and the development dependencies using ``pip``:

.. code-block::

    pip3 install --editable .
    pip3 install --editable dev

The `--editable <pip-editable_>`_ option is necessary so that all the changes made to the repository are automatically reflected in the virtual environment.

.. _pip-editable: https://pip.pypa.io/en/stable/reference/pip_install/#install-editable

Pre-commit Checks
=================
We provide a battery of pre-commit checks to make the code uniform and consistent across contributors:

* formatting with `black`_,
* sorting the statements by dependency with `ssort`_,
* type checking with `mypy`_ in the strict mode,
* linting with `ruff`_,
* unit tests (with the expensive pre- and post-conditions enabled) and the test coverage,
* doctests,
* checking that ``aas_package3/__init__.py`` and ``pyproject.toml`` are consistent, and
* checking the licensing information with `reuse`_.

.. _black: https://pypi.org/project/black/
.. _ssort: https://pypi.org/project/ssort/
.. _mypy: https://pypi.org/project/mypy/
.. _ruff: https://pypi.org/project/ruff/
.. _reuse: https://pypi.org/project/reuse/

To run all the checks, invoke:

.. code-block::

    python dev/continuous_integration/precommit.py

To automatically fix the formatting and the fixable linter warnings, invoke:

.. code-block::

    python dev/continuous_integration/precommit.py --overwrite

You can select or skip individual steps with ``--select`` and ``--skip``, respectively.
See ``--help`` for the list of steps.

To run a single test module, change to ``dev/`` and invoke, *e.g.*:

.. code-block::

    python -m unittest tests.test_package

Pre- and Post-conditions
========================
We document and check the contracts of the public methods with plain ``assert`` statements.

The cheap conditions are always checked, unless Python runs with ``-O``.
The expensive ones are checked only if you set the environment variable ``AAS_PACKAGE3_SLOW_CHECKS`` to ``1``, which we do in the unit tests.

We decided against `icontract`_ as the decorators added a few microseconds per call, which made a realistic workload (100 specs with 10 supplementaries each) several times slower than with the asserts.
The plain asserts also spare us a run-time dependency.

.. _icontract: https://pypi.org/project/icontract/

Fuzz Tests
==========
The unit tests in ``dev/tests/test_fuzz.py`` feed random, corrupted and truncated packages to the library, and expect only the exceptions of the library (*i.e.*, :code:`aas_package3.InvalidPackageError` and its subclasses).
Each fuzz test stops after its time budget of a couple of seconds.

Each iteration uses its own seed, which is reported if the test fails.
When the fuzzer finds a bug, please first reproduce it with a deterministic regression test in ``TestRegressionsFoundByFuzzing``, make sure that the test fails, and only then fix the bug.

Live Tests
==========
We check that the library reads and round-trips all the AASX files published in the `IDTA submodel templates`_:

.. code-block::

    python dev/live_tests/check_idta_submodel_templates.py \
        --cache_dir /some/cache/dir

The files are downloaded only once into the cache directory.
The live tests are not part of the continuous integration as they depend on the network and on a third-party repository.

.. _IDTA submodel templates: https://github.com/admin-shell-io/submodel-templates

Documentation
=============
The documentation lives in ``docs/source/``.
Build it with:

.. code-block::

    pip3 install -r docs/requirements.txt
    sphinx-build -W --keep-going docs/source docs/build

and open ``docs/build/index.html``.
The documentation is automatically published to GitHub Pages on every push to ``main``.

Style Guide
===========
We follow the style of `aas-core-codegen`_:

* Annotate the variables (``lst: List[str] = []``), not with type comments.
* Make the global constants ``Final``, and use read-only containers (``Mapping``, ``Sequence`` *etc.*) where possible.
* No ``get_`` prefix.
  Use ``_by_`` for mappings and ``_set`` for sets.
* No ``# TODO``.
  Write notes as ``# NOTE (your-github-username):``.
* Use ``pathlib``, not ``os.path``.

.. _aas-core-codegen: https://github.com/aas-core-works/aas-core-codegen

Commit Messages
===============
We follow Chris Beams' `guidelines on commit messages`_:

1) Separate subject from body with a blank line
2) Limit the subject line to 50 characters
3) Capitalize the subject line
4) Do not end the subject line with a period
5) Use the imperative mood in the subject line
6) Wrap the body at 72 characters
7) Use the body to explain *what* and *why* (instead of *how*)

.. _guidelines on commit messages: https://chris.beams.io/posts/git-commit/

Releasing
=========
1) Update the version in ``pyproject.toml`` and ``aas_package3/__init__.py``.
2) Add an entry to ``CHANGELOG.rst``.
3) Create a GitHub release.
   The package is published to PyPI automatically.
