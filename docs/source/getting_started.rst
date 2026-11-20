***************
Getting Started
***************

Installation
============
Install the library with pip:

.. code-block::

    pip3 install aas-package3

The library requires Python 3.10 or later, and has no further dependencies.

.. testsetup:: *

    import os
    import tempfile

    _tmp_dir = tempfile.TemporaryDirectory()
    _old_cwd = os.getcwd()
    os.chdir(_tmp_dir.name)

.. testcleanup:: *

    os.chdir(_old_cwd)
    _tmp_dir.cleanup()

Concepts
========
An AASX package is a ZIP archive following the `Open Packaging Conventions`_.
The package consists of *parts*, each with its URI within the package (*e.g.*, ``/aasx/data.json``) and a MIME content type.

.. _Open Packaging Conventions: https://en.wikipedia.org/wiki/Open_Packaging_Conventions

The parts are tied together with *relationships*:

* The *origin* is the entry point of the package.
  It is created for you.
* The *specs* are related to the origin and contain the AAS environments serialized as JSON or XML.
* The *supplementaries* are related to a spec, and hold the files that the spec refers to, such as manuals or images.
* The *thumbnail* is related to the package as a whole.

Entry Point
===========
You open and create the packages through :py:class:`aas_package3.Packaging`:

.. testcode::

    import aas_package3

    packaging = aas_package3.Packaging()

Packaging gives you either a :py:class:`aas_package3.PackageRead`, which only reads the package, or a :py:class:`aas_package3.PackageReadWrite`, which also writes to it.
Both can be used as context managers.

Create a Package
================
The whole package lives in memory.
The changes are written to the file when the ``with`` block finishes (see :ref:`saving-the-changes`).

.. testcode::

    with packaging.create("some-file.aasx") as pkg:
        spec = pkg.put_part(
            "/aasx/some-company/data.json",
            "application/json",
            b'{"assetAdministrationShells": []}',
        )
        pkg.make_spec(spec)

        supplementary = pkg.put_part(
            "/aasx-suppl/some-company/some-manual.pdf", "application/pdf", b"%PDF"
        )
        pkg.relate_supplementary_to_spec(supplementary, spec)

        thumbnail = pkg.put_part("/some-thumbnail.png", "image/png", b"PNG")
        pkg.set_thumbnail(thumbnail)

Read a Package
==============
.. testcode::

    with packaging.open_read("some-file.aasx") as pkg:
        for content_type, specs in pkg.specs_by_content_type().items():
            for spec in specs:
                print(content_type, spec.uri, spec.read_all_text())

        for relationship in pkg.supplementary_relationships():
            print(relationship.spec.uri, "->", relationship.supplementary.uri)

        thumbnail = pkg.thumbnail()
        if thumbnail is not None:
            print("Thumbnail:", thumbnail.uri)

.. testoutput::

    application/json /aasx/some-company/data.json {"assetAdministrationShells": []}
    /aasx/some-company/data.json -> /aasx-suppl/some-company/some-manual.pdf
    Thumbnail: /some-thumbnail.png

To look up a part by its URI, use :py:meth:`aas_package3.PackageRead.find_part` (returns ``None`` if missing) or :py:meth:`aas_package3.PackageRead.must_part` (raises :py:class:`aas_package3.PartNotFoundError`).
The URIs are compared case-insensitively and percent-decoded, as prescribed by the Open Packaging Conventions:

.. testcode::

    with packaging.open_read("some-file.aasx") as pkg:
        part = pkg.must_part("/AASX-SUPPL/some-company/some-manual.pdf")
        print(part.uri, part.content_type)

.. testoutput::

    /aasx-suppl/some-company/some-manual.pdf application/pdf

Modify a Package
================
.. testcode::

    with packaging.open_read_write("some-file.aasx") as pkg:
        spec = pkg.must_part("/aasx/some-company/data.json")
        supplementary = pkg.must_part("/aasx-suppl/some-company/some-manual.pdf")

        # Remove the relationship, and then the part itself.
        pkg.unrelate_supplementary_from_spec(supplementary, spec)
        pkg.delete_part(supplementary)

        # Overwrite the spec in place. The relationships are kept.
        pkg.put_part(spec.uri, "application/json", b'{"submodels": []}')

    with packaging.open_read("some-file.aasx") as pkg:
        print([spec.read_all_text() for spec in pkg.specs()])
        print(pkg.supplementary_relationships())

.. testoutput::

    ['{"submodels": []}']
    []

Note that :py:meth:`aas_package3.PackageReadWrite.delete_part` removes all the relationships from and to the part as well, so you can not end up with a dangling relationship.

Streams
=======
Instead of files, you can work with binary streams such as :py:class:`io.BytesIO`:

.. testcode::

    import io

    stream = io.BytesIO()
    with packaging.create_in_stream(stream) as pkg:
        pkg.make_spec(pkg.put_part("/aasx/data.xml", "text/xml", b"<environment/>"))

    with packaging.open_read_from_stream(stream) as pkg:
        print([spec.uri for spec in pkg.specs()])

.. testoutput::

    ['/aasx/data.xml']

The streams are owned by you: the packages never close them.

.. _saving-the-changes:

Saving the Changes
==================
The changes are kept in memory, and written to the file or the stream when:

* the ``with`` block finishes normally,
* you call :py:meth:`aas_package3.PackageReadWrite.close`, or
* you call :py:meth:`aas_package3.PackageReadWrite.flush` explicitly, *e.g.*, to save the intermediate state.

The package is only written on closing if it has been modified, so opening a file for writing and closing it without changes leaves the file as it is.

If the ``with`` block raises an exception, the changes are discarded so that you do not end up with a half-modified package:

.. testcode::

    try:
        with packaging.open_read_write("some-file.aasx") as pkg:
            pkg.unmake_spec(pkg.must_part("/aasx/some-company/data.json"))
            raise RuntimeError("Something went wrong")
    except RuntimeError:
        pass

    with packaging.open_read("some-file.aasx") as pkg:
        print([spec.uri for spec in pkg.specs()])

.. testoutput::

    ['/aasx/some-company/data.json']

If you do not use the ``with`` statement, call :py:meth:`aas_package3.PackageReadWrite.discard` to drop the changes explicitly.
The changes are *not* written if you neither close nor flush the package, *e.g.*, when it is merely garbage-collected.

Errors
======
Invalid packages raise :py:class:`aas_package3.InvalidPackageError` or one of its subclasses, while I/O errors raise the usual :py:class:`OSError`:

.. testcode::

    try:
        packaging.open_read_from_stream(io.BytesIO(b"not a package"))
    except aas_package3.InvalidPackageError as exception:
        print(type(exception).__name__)

.. testoutput::

    InvalidPackageError

The misuse of the API, such as making a non-existing part a spec, is caught by ``assert`` statements, unless you run Python with ``-O``.
