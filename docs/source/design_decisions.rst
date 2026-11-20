****************
Design Decisions
****************

Port of the Other Implementations
=================================
We ported `aas-package3-csharp`_, `aas-package3-golang`_ and `aas-package3-typescript`_ to Python.
The C# implementation served as the reference for the semantics, while we took over the later refinements of the Go and TypeScript implementations.
We note below where we deliberately deviate.

.. _aas-package3-csharp: https://github.com/aas-core-works/aas-package3-csharp

API Close to the Other Implementations
======================================
We keep the API close to `aas-package3-golang`_ and `aas-package3-typescript`_ (``Packaging``, ``PackageRead``, ``PackageReadWrite``, ``Part``), but follow the Python naming conventions (``put_part`` instead of ``PutPart``).

.. _aas-package3-golang: https://github.com/aas-core-works/aas-package3-golang
.. _aas-package3-typescript: https://github.com/aas-core-works/aas-package3-typescript

The part URIs are plain strings, such as ``/aasx/data.json``.

Exceptions instead of Results
=============================
The C# implementation returns a ``PackageOrException`` when opening a package so that the exceptions do not slow down checking many files.
The exceptions are cheap in Python, so we simply raise them.

In-memory Packages and Flush on Close
=====================================
Like the Go and TypeScript implementations, we read the whole package into memory.

The C# implementation always saves the package on dispose, even if the ``using`` block failed half-way.
We follow instead the convention of the Python standard library (*e.g.*, the transactions of :py:mod:`sqlite3`), as do the Go and TypeScript implementations:

* The package is flushed when it is closed, and in particular when the ``with`` block finishes normally.
* The changes are discarded if the ``with`` block raises an exception, so that a half-modified package never ends up on the disk.
* The package is only flushed on close if it has been modified, so that merely reading a package opened for writing does not rewrite the file.
* The package is not flushed in the finalizer, since we could not report the errors from there.

Lenient Reading, Strict Writing
===============================
We accept the variations that we encountered in the wild:

* The deprecated ``http://www.admin-shell.io/aasx/relationships/...`` relationship types, and ``https`` instead of ``http``,
* ``.../aasx-spec`` instead of ``.../aas-spec`` as the spec relationship (written by some versions of aas-package3-csharp),
* relationships to the parts which are wrongly marked as ``TargetMode="External"``, and
* relative relationship targets, including ``..`` segments.

When writing, we always use the preferred relationship types.
The external relationships which are not related to the AAS are kept verbatim.

Deleting a Part Deletes Its Relationships
=========================================
In contrast to the C# implementation, :py:meth:`aas_package3.PackageReadWrite.delete_part` removes all the relationships from and to the deleted part, like the TypeScript implementation does.
This way, you can not write a package with a dangling relationship.
If you read a package with a dangling relationship, the read methods raise :py:class:`aas_package3.DanglingRelationshipError`.

Reproducible Output
===================
The ZIP entries are sorted and carry a fixed timestamp, so flushing the same package twice gives the same bytes.

Contracts with Plain Asserts
============================
We check the pre- and post-conditions with plain ``assert`` statements instead of `icontract`_.
We measured that icontract adds a few microseconds per call, which slowed down a realistic workload several times, while the asserts add tens of nanoseconds.
The expensive conditions are only checked if the environment variable ``AAS_PACKAGE3_SLOW_CHECKS`` is set to ``1``.

.. _icontract: https://pypi.org/project/icontract/

Safety
======
We parse the XML with expat and reject the document type declarations, which the Open Packaging Conventions forbid anyhow.
This rules out the entity expansion attacks.
The corrupted ZIP archives and XML documents are reported as :py:class:`aas_package3.InvalidPackageError`, which we continuously check with fuzz tests.
