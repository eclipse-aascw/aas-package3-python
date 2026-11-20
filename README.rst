*******************
aas-package3-python
*******************

.. image:: https://github.com/eclipse-aascw/aas-package3-python/actions/workflows/ci.yml/badge.svg
    :target: https://github.com/eclipse-aascw/aas-package3-python/actions/workflows/ci.yml
    :alt: Continuous integration

.. image:: https://github.com/eclipse-aascw/aas-package3-python/actions/workflows/build-test-docs.yml/badge.svg
    :target: https://eclipse-aascw.github.io/aas-package3-python/
    :alt: Documentation

.. image:: https://coveralls.io/repos/github/eclipse-aascw/aas-package3-python/badge.svg?branch=main
    :target: https://coveralls.io/github/eclipse-aascw/aas-package3-python?branch=main
    :alt: Test coverage

.. image:: https://badge.fury.io/py/aas-package3.svg
    :target: https://badge.fury.io/py/aas-package3
    :alt: PyPI - version

.. image:: https://img.shields.io/pypi/pyversions/aas-package3.svg
    :alt: PyPI - Python Version

Aas-package3-python is a library for reading and writing packaged file format of an `Asset Administration Shell (AAS)`_ in Python.

.. _Asset Administration Shell (AAS): https://www.plattform-i40.de/PI40/Redaktion/DE/Downloads/Publikation/Details_of_the_Asset_Administration_Shell_Part1_V3.html

Status
======
The library is thoroughly tested and meant to be used in production.

The library is written in pure Python with no external dependencies beyond the standard library.
Python 3.10 or later is required.

Documentation
=============
The documentation is available at https://eclipse-aascw.github.io/aas-package3-python/.

Teaser
------
Here are short snippets to demonstrate how you can use the library.

To create and write to a package:

.. code-block:: python

    import aas_package3

    # General packaging handler to be shared across the program
    packaging = aas_package3.Packaging()

    spec_content = b'{"assetAdministrationShells": []}'
    thumbnail_content = b"..."  # PNG bytes
    supplementary_content = b"..."  # PDF bytes

    # Create a package
    with packaging.create("some-file.aasx") as pkg:
        spec = pkg.make_spec(
            pkg.put_part(
                "/aasx/some-company/data.json", "application/json", spec_content
            )
        )

        pkg.set_thumbnail(
            pkg.put_part("/some-thumbnail.png", "image/png", thumbnail_content)
        )

        pkg.relate_supplementary_to_spec(
            pkg.put_part(
                "/aasx-suppl/some-company/some-manual.pdf",
                "application/pdf",
                supplementary_content,
            ),
            spec,
        )

    # The changes are written when the with-block finishes without an exception.

To read from the package:

.. code-block:: python

    import aas_package3

    # General packaging handler to be shared across the program
    packaging = aas_package3.Packaging()

    # Read from the package
    with packaging.open_read("some-file.aasx") as pkg:
        # Read the specs
        specs_by_content_type = pkg.specs_by_content_type()
        for spec in specs_by_content_type.get("application/json", []):
            spec_content = spec.read_all_bytes()
            # Do something with the spec content

        # Read the thumbnail
        thumbnail = pkg.thumbnail()
        if thumbnail is not None:
            thumbnail_content = thumbnail.read_all_bytes()
            # Do something with the thumbnail content

        # Read a supplementary file
        supplementary_content = pkg.must_part(
            "/aasx-suppl/some-company/some-manual.pdf"
        ).read_all_bytes()

Please see the full documentation at https://eclipse-aascw.github.io/aas-package3-python/ for more details.

Installation
============
The library is available on PyPI at https://pypi.org/project/aas-package3/:

.. code-block::

    pip3 install aas-package3

Versioning
==========
The name of the library indicates the supported version of the `Asset Administration Shell (AAS)`_.

In case of ``aas-package3-python``, this means that Version 3 of the `Asset Administration Shell (AAS)`_ is supported.

We follow `Semantic Versioning`_ to version the library.
The version X.Y.Z indicates:

.. _Semantic Versioning: http://semver.org/spec/v1.0.0.html

* X is the major version (backward-incompatible),
* Y is the minor version (backward-compatible), and
* Z is the patch version (backward-compatible bug fix).

Contributing
============
Feature requests or bug reports are always very, very welcome!

Please see quickly if the issue does not already exist in the `issue section`_ and, if not, create a new issue.

.. _issue section: https://github.com/eclipse-aascw/aas-package3-python/issues

See `CONTRIBUTING.rst`_ for how to set up the development environment and run the checks.

.. _CONTRIBUTING.rst: https://github.com/eclipse-aascw/aas-package3-python/blob/main/CONTRIBUTING.rst
