# SPDX-FileCopyrightText: 2026 aas-package3-python AUTHORS
#
# SPDX-License-Identifier: MIT

"""Test reading and writing the packages."""

import gc
import io
import os
import pathlib
import tempfile
import unittest
import zipfile
from typing import Any, Final, List, Optional, Tuple

import aas_package3

from tests import common

ORIGIN_RELS: Final[
    str
] = """\
<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="R1" Type="http://admin-shell.io/aasx/relationships/aasx-origin" \
Target="/aasx/aasx-origin"/>
</Relationships>
"""

#: Modification time far in the past, in nanoseconds since the epoch
OLD_MTIME_NS: Final[int] = 946_684_800 * 1_000_000_000


def create_in_memory() -> aas_package3.PackageReadWrite:
    """Create an empty package in a memory stream."""
    return aas_package3.Packaging().create_in_stream(io.BytesIO())


def flush_to_bytes(package: aas_package3.PackageReadWrite) -> bytes:
    """Flush the ``package`` created in memory and return its bytes."""
    package.flush()
    stream = package._stream
    assert isinstance(stream, io.BytesIO)
    return stream.getvalue()


def reopen(package: aas_package3.PackageReadWrite) -> aas_package3.PackageRead:
    """Flush the ``package`` created in memory and open it again for reading."""
    return aas_package3.Packaging().open_read_from_stream(
        io.BytesIO(flush_to_bytes(package))
    )


def package_with_spec_supplementary_and_thumbnail() -> bytes:
    """Create a package with one spec, one supplementary and a thumbnail."""
    package = create_in_memory()
    spec = package.make_spec(
        package.put_part("/aasx/spec.aas.xml", "text/xml", b"<aas/>")
    )
    package.relate_supplementary_to_spec(
        package.put_part("/aasx/doc.pdf", "application/pdf", b"%PDF"), spec
    )
    package.set_thumbnail(package.put_part("/thumbnail.png", "image/png", b"PNG"))
    return flush_to_bytes(package)


def open_bytes(data: bytes) -> aas_package3.PackageRead:
    """Open the package ``data`` for reading."""
    return aas_package3.Packaging().open_read_from_stream(io.BytesIO(data))


class TestOpen(unittest.TestCase):
    def test_that_opening_a_non_package_file_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            pth = pathlib.Path(tmp_dir) / "dummy.aasx"
            pth.write_text("This is not OPC.", encoding="utf-8")

            with self.assertRaises(aas_package3.InvalidPackageError):
                aas_package3.Packaging().open_read(pth)

            with self.assertRaises(aas_package3.InvalidPackageError):
                aas_package3.Packaging().open_read_write(pth)

    def test_that_opening_a_non_package_stream_raises(self) -> None:
        with self.assertRaises(aas_package3.InvalidPackageError):
            aas_package3.Packaging().open_read_from_stream(io.BytesIO(b"not a zip"))

    def test_that_opening_an_empty_stream_raises(self) -> None:
        for method in (
            aas_package3.Packaging().open_read_from_stream,
            aas_package3.Packaging().open_read_write_from_stream,
        ):
            with self.subTest(method=method.__name__), self.assertRaises(
                aas_package3.InvalidPackageError
            ):
                method(io.BytesIO())

    def test_that_opening_a_non_existing_file_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            aas_package3.Packaging().open_read("/this/path/can/not/possibly/exist.aasx")

    def test_that_opening_a_zip_without_origin_raises(self) -> None:
        data = common.write_zip({"[Content_Types].xml": b"<Types/>"})

        with self.assertRaises(aas_package3.NoOriginError):
            open_bytes(data)

        with tempfile.TemporaryDirectory() as tmp_dir:
            pth = pathlib.Path(tmp_dir) / "dummy.aasx"
            pth.write_bytes(data)
            with self.assertRaises(aas_package3.NoOriginError):
                aas_package3.Packaging().open_read(pth)

            with self.assertRaises(aas_package3.NoOriginError):
                aas_package3.Packaging().open_read_write(pth)

    def test_that_opening_an_empty_zip_raises(self) -> None:
        with self.assertRaises(aas_package3.NoOriginError):
            open_bytes(common.write_zip({}))

    def test_that_origin_relationship_without_origin_part_raises(self) -> None:
        data = common.write_zip({"_rels/.rels": ORIGIN_RELS.encode("utf-8")})
        with self.assertRaises(aas_package3.NoOriginError):
            open_bytes(data)

    def test_minimal_package_with_relative_origin_target(self) -> None:
        data = common.write_zip(
            {
                "_rels/.rels": ORIGIN_RELS.replace(
                    'Target="/aasx/aasx-origin"', 'Target="aasx/aasx-origin"'
                ).encode("utf-8"),
                "aasx/aasx-origin": b"",
            }
        )
        package = open_bytes(data)
        self.assertEqual([], package.specs())

    def test_that_malformed_relationships_raise(self) -> None:
        for label, rels in [
            ("not XML", b"<Relationships"),
            ("wrong root", b"<Something/>"),
            (
                "missing target",
                b'<Relationships><Relationship Id="R1" Type="x"/></Relationships>',
            ),
            (
                "document type declaration",
                (
                    b'<!DOCTYPE lolz [<!ENTITY lol "lol">]>'
                    b"<Relationships>&lol;</Relationships>"
                ),
            ),
        ]:
            with self.subTest(label=label):
                data = common.write_zip({"_rels/.rels": rels, "aasx/aasx-origin": b""})
                with self.assertRaises(aas_package3.InvalidPackageError):
                    open_bytes(data)

    def test_that_malformed_content_types_raise(self) -> None:
        data = common.write_zip(
            {
                "[Content_Types].xml": b"<Types",
                "_rels/.rels": ORIGIN_RELS.encode("utf-8"),
                "aasx/aasx-origin": b"",
            }
        )
        with self.assertRaises(aas_package3.InvalidPackageError):
            open_bytes(data)

    def test_that_equivalent_part_names_raise(self) -> None:
        data = common.write_zip(
            {
                "_rels/.rels": ORIGIN_RELS.encode("utf-8"),
                "aasx/aasx-origin": b"",
                "aasx/data.txt": b"a",
                "aasx/DATA.txt": b"b",
            }
        )
        with self.assertRaises(aas_package3.InvalidPackageError):
            open_bytes(data)

    def test_that_path_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            pth = pathlib.Path(tmp_dir) / "dummy.aasx"
            packaging = aas_package3.Packaging()

            with packaging.create(str(pth)) as package:
                self.assertEqual(pth, package.path)

            with packaging.open_read(pth) as package_read:
                self.assertEqual(pth, package_read.path)

            with packaging.open_read_write(pth) as package_read_write:
                self.assertEqual(pth, package_read_write.path)

            with pth.open("rb") as stream:
                self.assertIsNone(packaging.open_read_from_stream(stream).path)


class TestCreate(unittest.TestCase):
    def test_new_package_is_empty_and_written_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            pth = pathlib.Path(tmp_dir) / "dummy.aasx"
            with aas_package3.Packaging().create(pth) as package:
                self.assertEqual([], package.specs())
                self.assertEqual([], package.supplementary_relationships())
                self.assertIsNone(package.thumbnail())

            # We did not flush, but the empty package must be valid already.
            with aas_package3.Packaging().open_read(pth) as package_read:
                self.assertEqual([], package_read.specs())

    def test_new_package_in_stream(self) -> None:
        stream = io.BytesIO()
        package = aas_package3.Packaging().create_in_stream(stream)
        self.assertIsNone(package.path)
        self.assertGreater(len(stream.getvalue()), 0)

        package_read = aas_package3.Packaging().open_read_from_stream(stream)
        self.assertEqual([], package_read.specs())
        self.assertIsNone(package_read.thumbnail())

    def test_that_creating_at_an_unreachable_path_raises(self) -> None:
        with self.assertRaises(OSError):
            aas_package3.Packaging().create(
                "/this/path/can/not/possibly/exist/dummy.aasx"
            )

    def test_that_creating_in_a_read_only_stream_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            pth = pathlib.Path(tmp_dir) / "dummy.bin"
            pth.write_bytes(b"")
            with pth.open("rb") as stream, self.assertRaises(OSError):
                aas_package3.Packaging().create_in_stream(stream)

    def test_the_layout_of_a_new_package(self) -> None:
        entries = common.read_zip(flush_to_bytes(create_in_memory()))
        self.assertEqual(
            [
                "[Content_Types].xml",
                "_rels/.rels",
                "aasx/aasx-origin",
            ],
            list(entries),
        )
        self.assertIn(
            aas_package3.RELATION_TYPE_AASX_ORIGIN.encode("utf-8"),
            entries["_rels/.rels"],
        )


class TestRead(unittest.TestCase):
    def test_grouping_specs_by_content_type(self) -> None:
        package = create_in_memory()
        package.make_spec(
            package.put_part("/aasx/some-company/data1.json", "text/json", b"{x: 1}")
        )
        package.make_spec(
            package.put_part("/aasx/some-company/data.json", "text/json", b"{}")
        )
        package.make_spec(
            package.put_part("/aasx/some-company/data.xml", "text/xml", b"<something/>")
        )

        specs_by_content_type = reopen(package).specs_by_content_type()

        self.assertEqual(["text/json", "text/xml"], list(specs_by_content_type))
        self.assertEqual(
            ["/aasx/some-company/data.json", "/aasx/some-company/data1.json"],
            [spec.uri for spec in specs_by_content_type["text/json"]],
        )
        self.assertEqual(
            ["/aasx/some-company/data.xml"],
            [spec.uri for spec in specs_by_content_type["text/xml"]],
        )

    def test_reading_specs_supplementaries_and_thumbnail(self) -> None:
        package = open_bytes(package_with_spec_supplementary_and_thumbnail())

        specs = package.specs()
        self.assertEqual(["/aasx/spec.aas.xml"], [spec.uri for spec in specs])
        self.assertEqual("text/xml", specs[0].content_type)
        self.assertEqual(b"<aas/>", specs[0].read_all_bytes())
        self.assertTrue(package.is_spec(specs[0]))

        self.assertEqual(
            ["/aasx/doc.pdf"],
            [part.uri for part in package.supplementaries_for(specs[0])],
        )

        relationships = package.supplementary_relationships()
        self.assertEqual(1, len(relationships))
        self.assertIs(specs[0], relationships[0].spec)
        self.assertEqual("/aasx/doc.pdf", relationships[0].supplementary.uri)
        self.assertFalse(package.is_spec(relationships[0].supplementary))

        thumbnail = package.thumbnail()
        assert thumbnail is not None
        self.assertEqual("/thumbnail.png", thumbnail.uri)
        self.assertEqual("image/png", thumbnail.content_type)
        self.assertEqual(b"PNG", thumbnail.read_all_bytes())

    def test_querying_a_thumbnail_in_a_package_without_one(self) -> None:
        self.assertIsNone(reopen(create_in_memory()).thumbnail())

    def test_that_a_dangling_thumbnail_relationship_raises(self) -> None:
        data = common.rewrite_entries(
            package_with_spec_supplementary_and_thumbnail(),
            lambda name, content: content,
        )
        entries = common.read_zip(data)
        del entries["thumbnail.png"]

        package = open_bytes(common.write_zip(entries))
        with self.assertRaises(aas_package3.DanglingRelationshipError):
            package.thumbnail()

    def test_that_a_dangling_spec_relationship_raises(self) -> None:
        entries = common.read_zip(package_with_spec_supplementary_and_thumbnail())
        del entries["aasx/spec.aas.xml"]

        package = open_bytes(common.write_zip(entries))
        with self.assertRaises(aas_package3.DanglingRelationshipError):
            package.specs()
        with self.assertRaises(aas_package3.DanglingRelationshipError):
            package.specs_by_content_type()

    def test_that_a_dangling_supplementary_relationship_raises(self) -> None:
        entries = common.read_zip(package_with_spec_supplementary_and_thumbnail())
        del entries["aasx/doc.pdf"]

        package = open_bytes(common.write_zip(entries))
        with self.assertRaises(aas_package3.DanglingRelationshipError):
            package.supplementary_relationships()

    def test_find_part_and_must_part(self) -> None:
        package = create_in_memory()
        part = package.put_part("/aasx/Some%20Company/Data.txt", "text/plain", b"x")

        self.assertIs(part, package.find_part("/aasx/Some%20Company/Data.txt"))
        # Part URIs are compared case-insensitively and percent-decoded.
        self.assertIs(part, package.find_part("/AASX/some company/data.TXT"))
        self.assertIs(part, package.must_part("/aasx/some%20company/data.txt"))

        self.assertIsNone(package.find_part("/aasx/non-existent.txt"))
        with self.assertRaises(aas_package3.PartNotFoundError):
            package.must_part("/aasx/non-existent.txt")

        # The part keeps its URI as given.
        self.assertEqual(
            "/aasx/Some%20Company/Data.txt",
            reopen(package).must_part("/aasx/some company/data.txt").uri,
        )

    def test_reading_the_content_of_a_part(self) -> None:
        package = create_in_memory()
        package.put_part("/aasx/data.bin", "application/octet-stream", b"\x00\x01")
        package.put_part("/aasx/data.txt", "text/plain", "﻿čšž".encode())

        package_read = reopen(package)
        binary = package_read.must_part("/aasx/data.bin")
        self.assertEqual(b"\x00\x01", binary.read_all_bytes())
        with binary.stream() as stream:
            self.assertEqual(b"\x00\x01", stream.read())

        text = package_read.must_part("/aasx/data.txt")
        self.assertEqual("čšž", text.read_all_text())
        self.assertEqual("﻿čšž", text.read_all_text(encoding="utf-8"))

    def test_deprecated_and_legacy_relationship_types(self) -> None:
        original = package_with_spec_supplementary_and_thumbnail()
        for label, old, new in [
            (
                "www host",
                "http://admin-shell.io/aasx/relationships/",
                "http://www.admin-shell.io/aasx/relationships/",
            ),
            (
                "https",
                "http://admin-shell.io/aasx/relationships/",
                "https://admin-shell.io/aasx/relationships/",
            ),
            (
                "aasx-spec of aas-package3-csharp",
                "http://admin-shell.io/aasx/relationships/aas-spec",
                "http://admin-shell.io/aasx/relationships/aasx-spec",
            ),
        ]:
            with self.subTest(label=label):
                data = common.replace_in_relationships(original, old, new)
                self.assertIn(
                    new.encode("utf-8"),
                    common.read_zip(data)["_rels/.rels"]
                    + common.read_zip(data)["aasx/_rels/aasx-origin.rels"],
                )

                package = aas_package3.Packaging().open_read_write_from_stream(
                    io.BytesIO(data)
                )
                specs = package.specs()
                self.assertEqual(["/aasx/spec.aas.xml"], [spec.uri for spec in specs])
                self.assertTrue(package.is_spec(specs[0]))
                self.assertEqual(1, len(package.supplementary_relationships()))

                # We always write the preferred relationship types.
                rels = b"".join(
                    content
                    for name, content in common.read_zip(
                        flush_to_bytes(package)
                    ).items()
                    if name.endswith(".rels")
                ).decode("utf-8")
                self.assertNotIn(new, rels)
                self.assertIn(aas_package3.RELATION_TYPE_AASX_SPEC, rels)
                self.assertIn(aas_package3.RELATION_TYPE_AASX_SUPPLEMENTARY, rels)

    def test_relative_relationship_targets(self) -> None:
        package = create_in_memory()
        spec = package.make_spec(
            package.put_part("/aasx/spec.aas.xml", "text/xml", b"<aas/>")
        )
        package.relate_supplementary_to_spec(
            package.put_part("/docs/manual.pdf", "application/pdf", b"%PDF"), spec
        )

        data = common.replace_in_relationships(
            flush_to_bytes(package),
            'Target="/docs/manual.pdf"',
            'Target="../docs/./manual.pdf"',
        )
        data = common.replace_in_relationships(
            data, 'Target="/aasx/spec.aas.xml"', 'Target="spec.aas.xml"'
        )

        package_read = open_bytes(data)
        self.assertEqual(
            ["/aasx/spec.aas.xml"], [spec.uri for spec in package_read.specs()]
        )
        self.assertEqual(
            "/docs/manual.pdf",
            package_read.supplementary_relationships()[0].supplementary.uri,
        )

    def test_root_level_source_parts(self) -> None:
        package = create_in_memory()
        spec = package.make_spec(
            package.put_part("/spec.aas.xml", "text/xml", b"<aas/>")
        )
        package.relate_supplementary_to_spec(
            package.put_part("/doc.pdf", "application/pdf", b"%PDF"), spec
        )

        data = flush_to_bytes(package)
        self.assertIn("_rels/spec.aas.xml.rels", common.read_zip(data))

        relationships = open_bytes(data).supplementary_relationships()
        self.assertEqual("/spec.aas.xml", relationships[0].spec.uri)
        self.assertEqual("/doc.pdf", relationships[0].supplementary.uri)

    def test_relationships_to_parts_wrongly_marked_as_external(self) -> None:
        # NOTE (mristin):
        # Some AASX writers mark the relationships to the parts as external.
        # See, e.g., IDTA 02099-1 Digital Product Passport.
        original = package_with_spec_supplementary_and_thumbnail()
        data = common.rewrite_entries(
            original,
            lambda name, content: (
                content.replace(b"/>", b' TargetMode="External"/>')
                if name.endswith(".rels")
                else content
            ),
        )
        self.assertIn(b'TargetMode="External"', common.read_zip(data)["_rels/.rels"])

        package = aas_package3.Packaging().open_read_write_from_stream(io.BytesIO(data))
        self.assertEqual(["/aasx/spec.aas.xml"], [spec.uri for spec in package.specs()])
        self.assertEqual(1, len(package.supplementary_relationships()))
        self.assertIsNotNone(package.thumbnail())

        # The relationships are kept as they were declared.
        self.assertIn(
            b'TargetMode="External"',
            common.read_zip(flush_to_bytes(package))["_rels/.rels"],
        )

    def test_that_external_relationships_survive_the_round_trip(self) -> None:
        external = (
            '<Relationship Id="Rext" Type="http://example.com/external-ref" '
            'Target="https://example.com/resource" TargetMode="External"/>'
        )
        data = common.rewrite_entries(
            package_with_spec_supplementary_and_thumbnail(),
            lambda name, content: (
                content.replace(
                    b"</Relationships>", external.encode("utf-8") + b"</Relationships>"
                )
                if name == "_rels/.rels"
                else content
            ),
        )

        package = aas_package3.Packaging().open_read_write_from_stream(io.BytesIO(data))
        package.unset_thumbnail()
        root_rels = common.read_zip(flush_to_bytes(package))["_rels/.rels"].decode(
            "utf-8"
        )

        self.assertIn(
            'Target="https://example.com/resource" TargetMode="External"', root_rels
        )
        self.assertNotIn(aas_package3.RELATION_TYPE_THUMBNAIL, root_rels)
        self.assertIn('Target="/aasx/aasx-origin"/>', root_rels)

    def test_content_types(self) -> None:
        package = create_in_memory()
        package.put_part("/a.xml", "text/xml", b"")
        package.put_part("/b.xml", "application/aas+xml", b"")
        package.put_part("/no-extension", "application/x-something", b"")
        package.put_part("/c.JSON", "application/json", b"")

        content_types = common.read_zip(flush_to_bytes(package))[
            "[Content_Types].xml"
        ].decode("utf-8")
        self.assertIn(
            '<Default Extension="xml" ContentType="text/xml"/>', content_types
        )
        self.assertIn(
            '<Override PartName="/b.xml" ContentType="application/aas+xml"/>',
            content_types,
        )
        self.assertIn(
            '<Override PartName="/no-extension" '
            'ContentType="application/x-something"/>',
            content_types,
        )

        package_read = reopen(package)
        for uri, content_type in [
            ("/a.xml", "text/xml"),
            ("/b.xml", "application/aas+xml"),
            ("/no-extension", "application/x-something"),
            ("/c.json", "application/json"),
            ("/aasx/aasx-origin", "text/plain"),
        ]:
            self.assertEqual(content_type, package_read.must_part(uri).content_type)

    def test_unknown_content_type_defaults_to_octet_stream(self) -> None:
        data = common.write_zip(
            {
                "_rels/.rels": ORIGIN_RELS.encode("utf-8"),
                "aasx/aasx-origin": b"",
            }
        )
        package = open_bytes(data)
        self.assertEqual(
            "application/octet-stream",
            package.must_part("/aasx/aasx-origin").content_type,
        )


class TestReadWrite(unittest.TestCase):
    def test_open_read_write_and_flush_to_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            pth = pathlib.Path(tmp_dir) / "dummy.aasx"
            packaging = aas_package3.Packaging()

            with packaging.create(pth) as package:
                package.make_spec(package.put_part("/aasx/a.txt", "text/plain", b"a"))
                package.flush()

            with packaging.open_read_write(pth) as package:
                package.make_spec(package.put_part("/aasx/b.txt", "text/plain", b"b"))
                package.flush()

            with packaging.open_read(pth) as package_read:
                self.assertEqual(
                    ["/aasx/a.txt", "/aasx/b.txt"],
                    [spec.uri for spec in package_read.specs()],
                )

    def test_flush_to_stream_overwrites_and_truncates(self) -> None:
        package = create_in_memory()
        package.put_part("/aasx/big.bin", "application/octet-stream", bytes(100_000))
        big = flush_to_bytes(package)

        stream = io.BytesIO(big)
        stream.seek(1234)
        package_rw = aas_package3.Packaging().open_read_write_from_stream(stream)
        package_rw.delete_part(package_rw.must_part("/aasx/big.bin"))
        package_rw.flush()

        self.assertLess(len(stream.getvalue()), len(big))
        self.assertIsNone(open_bytes(stream.getvalue()).find_part("/aasx/big.bin"))

    def test_put_part_overwrites_in_place(self) -> None:
        package = create_in_memory()
        part = package.put_part("/aasx/data.txt", "text/plain", b"old")
        package.make_spec(part)

        again = package.put_part("/AASX/DATA.txt", "application/json", b"new")

        self.assertIs(part, again)
        self.assertEqual("/aasx/data.txt", part.uri)
        self.assertEqual("application/json", part.content_type)
        self.assertEqual(b"new", part.read_all_bytes())
        self.assertTrue(package.is_spec(part))

        spec = reopen(package).specs()[0]
        self.assertEqual(b"new", spec.read_all_bytes())
        self.assertEqual("application/json", spec.content_type)

    def test_put_part_from_stream(self) -> None:
        package = create_in_memory()
        stream = io.BytesIO(b"skip<aas/>")
        stream.seek(4)
        part = package.put_part_from_stream("/aasx/data.xml", "text/xml", stream)
        self.assertEqual(b"<aas/>", part.read_all_bytes())
        self.assertEqual(
            b"<aas/>", reopen(package).must_part("/aasx/data.xml").read_all_bytes()
        )

    def test_that_invalid_part_uris_are_rejected(self) -> None:
        package = create_in_memory()
        for uri in [
            "relative.txt",
            "/",
            "/dir/",
            "/a//b.txt",
            "/a/../b.txt",
            "/[Content_Types].xml",
            "/_rels/.rels",
            "/aasx/_rels/data.json.rels",
        ]:
            with self.subTest(uri=uri):
                self.assertFalse(aas_package3.is_valid_part_uri(uri))
                with common.contract_violation(self):
                    package.put_part(uri, "text/plain", b"")

    def test_delete_part_removes_its_relationships(self) -> None:
        package = aas_package3.Packaging().open_read_write_from_stream(
            io.BytesIO(package_with_spec_supplementary_and_thumbnail())
        )
        spec = package.must_part("/aasx/spec.aas.xml")
        supplementary = package.must_part("/aasx/doc.pdf")
        thumbnail = package.thumbnail()
        assert thumbnail is not None

        package.delete_part(supplementary)
        self.assertEqual([], package.supplementaries_for(spec))

        package.delete_part(thumbnail)
        self.assertIsNone(package.thumbnail())

        package.delete_part(spec)
        self.assertEqual([], package.specs())

        package_read = reopen(package)
        self.assertEqual([], package_read.specs())
        self.assertIsNone(package_read.thumbnail())
        self.assertEqual(
            ["[Content_Types].xml", "_rels/.rels", "aasx/aasx-origin"],
            list(common.read_zip(flush_to_bytes(package))),
        )

    def test_that_deleting_the_origin_or_a_foreign_part_is_rejected(self) -> None:
        package = create_in_memory()
        with common.contract_violation(self):
            package.delete_part(package.must_part("/aasx/aasx-origin"))

        foreign = create_in_memory().put_part("/aasx/a.txt", "text/plain", b"")
        package.put_part("/aasx/a.txt", "text/plain", b"")
        with common.contract_violation(self):
            package.delete_part(foreign)

    def test_make_and_unmake_spec(self) -> None:
        package = create_in_memory()
        first = package.put_part("/aasx/first.txt", "text/plain", b"1")
        second = package.put_part("/aasx/second.txt", "text/plain", b"2")

        self.assertIs(first, package.make_spec(first))
        package.make_spec(first)  # idempotent
        package.make_spec(second)
        self.assertEqual([first, second], package.specs())

        self.assertIs(first, package.unmake_spec(first))
        self.assertFalse(package.is_spec(first))
        self.assertEqual([second], package.specs())

        # The part is kept.
        self.assertIs(first, package.find_part("/aasx/first.txt"))

        with common.contract_violation(self):
            package.unmake_spec(first)

        package_read = reopen(package)
        self.assertEqual(["/aasx/second.txt"], [s.uri for s in package_read.specs()])

    def test_relate_and_unrelate_supplementary(self) -> None:
        package = create_in_memory()
        spec = package.make_spec(package.put_part("/aasx/spec.xml", "text/xml", b""))
        not_spec = package.put_part("/aasx/not-spec.xml", "text/xml", b"")
        supplementary = package.put_part("/aasx/doc.pdf", "application/pdf", b"")

        with common.contract_violation(self):
            package.relate_supplementary_to_spec(supplementary, not_spec)

        self.assertIs(
            supplementary, package.relate_supplementary_to_spec(supplementary, spec)
        )
        package.relate_supplementary_to_spec(supplementary, spec)  # idempotent
        self.assertEqual([supplementary], package.supplementaries_for(spec))
        self.assertEqual(
            ["/aasx/doc.pdf"],
            [part.uri for part in reopen(package).supplementaries_for(spec)],
        )

        self.assertIs(
            supplementary,
            package.unrelate_supplementary_from_spec(supplementary, spec),
        )
        package.unrelate_supplementary_from_spec(supplementary, spec)  # idempotent
        self.assertEqual([], package.supplementaries_for(spec))
        self.assertIs(supplementary, package.find_part("/aasx/doc.pdf"))

        with common.contract_violation(self):
            package.unrelate_supplementary_from_spec(supplementary, not_spec)

    def test_unmaking_a_spec_hides_its_supplementaries(self) -> None:
        package = aas_package3.Packaging().open_read_write_from_stream(
            io.BytesIO(package_with_spec_supplementary_and_thumbnail())
        )
        spec = package.specs()[0]
        package.unmake_spec(spec)
        self.assertEqual([], package.supplementary_relationships())

        package.make_spec(spec)
        self.assertEqual(1, len(package.supplementary_relationships()))

    def test_set_and_unset_thumbnail(self) -> None:
        package = create_in_memory()
        first = package.put_part("/first.png", "image/png", b"1")
        second = package.put_part("/second.png", "image/png", b"2")

        self.assertIs(first, package.set_thumbnail(first))
        self.assertIs(first, package.thumbnail())

        package.set_thumbnail(second)
        self.assertIs(second, package.thumbnail())

        thumbnail = reopen(package).thumbnail()
        assert thumbnail is not None
        self.assertEqual("/second.png", thumbnail.uri)
        self.assertEqual(
            1,
            common.read_zip(flush_to_bytes(package))["_rels/.rels"].count(
                aas_package3.RELATION_TYPE_THUMBNAIL.encode("utf-8")
            ),
        )

        package.unset_thumbnail()
        self.assertIsNone(package.thumbnail())
        self.assertIs(first, package.find_part("/first.png"))
        self.assertIsNone(reopen(package).thumbnail())

    def test_modify_thumbnail_and_delete_the_existing_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            pth = pathlib.Path(tmp_dir) / "dummy.aasx"
            packaging = aas_package3.Packaging()

            with packaging.create(pth) as package:
                package.set_thumbnail(
                    package.put_part("/some-thumbnail.txt", "text/plain", b"old")
                )
                package.flush()

            with packaging.open_read_write(pth) as package:
                old = package.thumbnail()
                assert old is not None
                package.delete_part(old)
                package.set_thumbnail(
                    package.put_part("/another-thumbnail.txt", "text/plain", b"new")
                )
                package.flush()

            with packaging.open_read(pth) as package_read:
                thumbnail = package_read.thumbnail()
                assert thumbnail is not None
                self.assertEqual("/another-thumbnail.txt", thumbnail.uri)
                self.assertEqual(b"new", thumbnail.read_all_bytes())
                self.assertIsNone(package_read.find_part("/some-thumbnail.txt"))

    def test_flush_is_reproducible(self) -> None:
        data = package_with_spec_supplementary_and_thumbnail()
        self.assertEqual(data, package_with_spec_supplementary_and_thumbnail())

        package = aas_package3.Packaging().open_read_write_from_stream(io.BytesIO(data))
        self.assertEqual(data, flush_to_bytes(package))

    def test_new_relationship_identifiers_do_not_collide(self) -> None:
        data = common.replace_in_relationships(
            flush_to_bytes(create_in_memory()), 'Id="R00000001"', 'Id="R00000002"'
        )
        package = aas_package3.Packaging().open_read_write_from_stream(io.BytesIO(data))
        package.set_thumbnail(package.put_part("/thumbnail.png", "image/png", b""))

        root_rels = common.read_zip(flush_to_bytes(package))["_rels/.rels"].decode(
            "utf-8"
        )
        self.assertEqual(1, root_rels.count('Id="R00000002"'))
        self.assertEqual(2, root_rels.count("<Relationship "))

    def test_entries_are_compressed(self) -> None:
        package = create_in_memory()
        package.put_part("/aasx/zeros.bin", "application/octet-stream", bytes(10_000))
        with zipfile.ZipFile(io.BytesIO(flush_to_bytes(package))) as archive:
            info = archive.getinfo("aasx/zeros.bin")
            self.assertEqual(zipfile.ZIP_DEFLATED, info.compress_type)
            self.assertLess(info.compress_size, 1_000)


def modify_everything(package: aas_package3.PackageReadWrite) -> None:
    """
    Apply every modifying operation to ``package``.

    The ``package`` is expected to come from
    :py:func:`package_with_spec_supplementary_and_thumbnail`.
    """
    spec = package.must_part("/aasx/spec.aas.xml")
    supplementary = package.must_part("/aasx/doc.pdf")

    package.unrelate_supplementary_from_spec(supplementary, spec)
    package.delete_part(supplementary)
    package.unset_thumbnail()
    package.unmake_spec(spec)

    package.put_part(spec.uri, "text/xml", b"<changed/>")
    another = package.make_spec(
        package.put_part("/aasx/b.json", "application/json", b"{}")
    )
    package.relate_supplementary_to_spec(
        package.put_part_from_stream(
            "/aasx/manual.pdf", "application/pdf", io.BytesIO(b"%PDF")
        ),
        another,
    )
    package.set_thumbnail(package.put_part("/other.png", "image/png", b"PNG"))


class StreamRecordingWrites(io.BytesIO):
    """Record all the calls which would modify the stream."""

    def __init__(self, initial_bytes: bytes) -> None:
        super().__init__(initial_bytes)
        self.modifications: List[str] = []

    def write(self, data: Any) -> int:
        self.modifications.append("write")
        return super().write(data)

    def writelines(self, lines: Any) -> None:
        self.modifications.append("writelines")
        super().writelines(lines)

    def truncate(self, size: Optional[int] = None) -> int:
        self.modifications.append("truncate")
        return super().truncate(size)


class TestFlushOnClose(unittest.TestCase):
    def prepare_file(self, tmp_dir: pathlib.Path) -> Tuple[pathlib.Path, bytes]:
        pth = tmp_dir / "dummy.aasx"
        original = package_with_spec_supplementary_and_thumbnail()
        pth.write_bytes(original)

        # NOTE (mristin):
        # We set the modification time to a fixed past value so that we would
        # notice even a write of the very same bytes.
        os.utime(pth, ns=(OLD_MTIME_NS, OLD_MTIME_NS))
        return pth, original

    def assert_file_untouched(
        self, tmp_dir: pathlib.Path, pth: pathlib.Path, original: bytes
    ) -> None:
        self.assertEqual(original, pth.read_bytes())
        self.assertEqual(OLD_MTIME_NS, pth.stat().st_mtime_ns)

        # No temporary or backup files are left behind either.
        self.assertEqual([pth.name], sorted(p.name for p in tmp_dir.iterdir()))

    def assert_everything_modified(
        self, package_read: aas_package3.PackageRead
    ) -> None:
        """Assert that ``package_read`` holds the changes of :py:func:`modify_everything`."""
        self.assertEqual(["/aasx/b.json"], [spec.uri for spec in package_read.specs()])

        thumbnail = package_read.thumbnail()
        assert thumbnail is not None
        self.assertEqual("/other.png", thumbnail.uri)

        self.assertEqual(
            b"<changed/>",
            package_read.must_part("/aasx/spec.aas.xml").read_all_bytes(),
        )
        self.assertIsNone(package_read.find_part("/aasx/doc.pdf"))

    def test_with_block_flushes_on_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir_str:
            pth, _ = self.prepare_file(pathlib.Path(tmp_dir_str))

            with aas_package3.Packaging().open_read_write(pth) as package:
                modify_everything(package)

            with aas_package3.Packaging().open_read(pth) as package_read:
                self.assert_everything_modified(package_read)

    def test_explicit_close_flushes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir_str:
            pth, _ = self.prepare_file(pathlib.Path(tmp_dir_str))

            package = aas_package3.Packaging().open_read_write(pth)
            modify_everything(package)
            package.close()

            with aas_package3.Packaging().open_read(pth) as package_read:
                self.assert_everything_modified(package_read)

    def test_with_block_discards_on_exception(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir_str:
            tmp_dir = pathlib.Path(tmp_dir_str)
            pth, original = self.prepare_file(tmp_dir)

            packaging = aas_package3.Packaging()
            with self.assertRaises(RuntimeError), packaging.open_read_write(
                pth
            ) as package:
                modify_everything(package)
                raise RuntimeError("Something went wrong in the middle.")

            self.assert_file_untouched(tmp_dir, pth, original)

            # The package is closed, so closing it again must not flush either.
            package.close()
            self.assert_file_untouched(tmp_dir, pth, original)

    def test_discard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir_str:
            tmp_dir = pathlib.Path(tmp_dir_str)
            pth, original = self.prepare_file(tmp_dir)

            package = aas_package3.Packaging().open_read_write(pth)
            modify_everything(package)
            package.discard()
            package.close()

            self.assert_file_untouched(tmp_dir, pth, original)

    def test_closing_an_unmodified_package_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir_str:
            tmp_dir = pathlib.Path(tmp_dir_str)
            pth, original = self.prepare_file(tmp_dir)

            with aas_package3.Packaging().open_read_write(pth) as package:
                # Reading does not modify the package.
                self.assertEqual(1, len(package.specs()))
                self.assertIsNotNone(package.thumbnail())

            self.assert_file_untouched(tmp_dir, pth, original)

    def test_closing_after_flush_does_not_write_again(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir_str:
            tmp_dir = pathlib.Path(tmp_dir_str)
            pth, _ = self.prepare_file(tmp_dir)

            with aas_package3.Packaging().open_read_write(pth) as package:
                modify_everything(package)
                package.flush()

                flushed = pth.read_bytes()
                os.utime(pth, ns=(OLD_MTIME_NS, OLD_MTIME_NS))

            self.assert_file_untouched(tmp_dir, pth, flushed)

    def test_closing_twice_writes_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir_str:
            tmp_dir = pathlib.Path(tmp_dir_str)
            pth, _ = self.prepare_file(tmp_dir)

            package = aas_package3.Packaging().open_read_write(pth)
            modify_everything(package)
            package.close()

            flushed = pth.read_bytes()
            os.utime(pth, ns=(OLD_MTIME_NS, OLD_MTIME_NS))

            package.close()
            self.assert_file_untouched(tmp_dir, pth, flushed)

    def test_nothing_written_without_close(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir_str:
            tmp_dir = pathlib.Path(tmp_dir_str)
            pth, original = self.prepare_file(tmp_dir)

            package = aas_package3.Packaging().open_read_write(pth)
            modify_everything(package)
            del package
            gc.collect()

            # NOTE (mristin):
            # We deliberately do not flush in the finalizer, as we can not report
            # the errors from there.
            self.assert_file_untouched(tmp_dir, pth, original)

    def test_create_flushes_on_close(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir_str:
            pth = pathlib.Path(tmp_dir_str) / "dummy.aasx"

            with aas_package3.Packaging().create(pth) as package:
                package.make_spec(package.put_part("/aasx/a.txt", "text/plain", b"a"))

            with aas_package3.Packaging().open_read(pth) as package_read:
                self.assertEqual(
                    ["/aasx/a.txt"], [spec.uri for spec in package_read.specs()]
                )

    def test_create_discards_on_exception(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir_str:
            pth = pathlib.Path(tmp_dir_str) / "dummy.aasx"

            packaging = aas_package3.Packaging()
            with self.assertRaises(RuntimeError), packaging.create(pth) as package:
                package.make_spec(package.put_part("/aasx/a.txt", "text/plain", b"a"))
                raise RuntimeError("Something went wrong in the middle.")

            # Only the empty package, written on creation, remains.
            with aas_package3.Packaging().open_read(pth) as package_read:
                self.assertEqual([], package_read.specs())

    def test_stream_flushed_on_success(self) -> None:
        original = package_with_spec_supplementary_and_thumbnail()
        stream = StreamRecordingWrites(original)

        with aas_package3.Packaging().open_read_write_from_stream(stream) as package:
            modify_everything(package)
            self.assertEqual([], stream.modifications)

        self.assertIn("write", stream.modifications)
        self.assertFalse(stream.closed)
        self.assert_everything_modified(open_bytes(stream.getvalue()))

    def test_stream_untouched_on_exception(self) -> None:
        original = package_with_spec_supplementary_and_thumbnail()
        stream = StreamRecordingWrites(original)

        packaging = aas_package3.Packaging()
        with self.assertRaises(RuntimeError), packaging.open_read_write_from_stream(
            stream
        ) as package:
            modify_everything(package)
            raise RuntimeError("Something went wrong in the middle.")

        self.assertEqual([], stream.modifications)
        self.assertEqual(original, stream.getvalue())
        self.assertFalse(stream.closed)

    def test_stream_untouched_if_unmodified(self) -> None:
        stream = StreamRecordingWrites(package_with_spec_supplementary_and_thumbnail())

        with aas_package3.Packaging().open_read_write_from_stream(stream) as package:
            self.assertEqual(1, len(package.specs()))

        self.assertEqual([], stream.modifications)

    def test_read_only_package_never_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir_str:
            tmp_dir = pathlib.Path(tmp_dir_str)
            pth, original = self.prepare_file(tmp_dir)

            with aas_package3.Packaging().open_read(pth) as package_read:
                self.assertEqual(1, len(package_read.specs()))

            self.assert_file_untouched(tmp_dir, pth, original)


class TestInteroperability(unittest.TestCase):
    def test_reading_packages_written_by_other_implementations(self) -> None:
        for name in ["written_by_csharp.aasx", "written_by_golang.aasx"]:
            with self.subTest(name=name):
                package = aas_package3.Packaging().open_read(
                    common.TEST_DATA_DIR / "interop" / name
                )

                specs = package.specs()
                self.assertEqual(
                    ["/aasx/some-company/data.json"], [spec.uri for spec in specs]
                )
                self.assertEqual("application/json", specs[0].content_type)
                self.assertEqual('{"x": 1}', specs[0].read_all_text())

                relationships = package.supplementary_relationships()
                self.assertEqual(1, len(relationships))
                self.assertEqual(
                    "/aasx-suppl/Some%20Manual.pdf", relationships[0].supplementary.uri
                )
                self.assertEqual(
                    "application/pdf", relationships[0].supplementary.content_type
                )
                self.assertIs(
                    relationships[0].supplementary,
                    package.find_part("/aasx-suppl/Some Manual.pdf"),
                )

                thumbnail = package.thumbnail()
                assert thumbnail is not None
                self.assertEqual("/thumbnail.png", thumbnail.uri)
                self.assertEqual("image/png", thumbnail.content_type)


if __name__ == "__main__":
    unittest.main()
