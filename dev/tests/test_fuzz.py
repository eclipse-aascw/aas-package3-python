# SPDX-FileCopyrightText: 2026 aas-package3-python AUTHORS
#
# SPDX-License-Identifier: MIT

"""Fuzz the package with random inputs and random operations."""

import datetime
import io
import random
import struct
import time
import unittest
import zipfile
from typing import Callable, Dict, Final, Optional, Set, Tuple

import aas_package3

from tests import common


#: Upper bound on the wall-clock time of a single fuzz test
TIME_BUDGET: Final[datetime.timedelta] = datetime.timedelta(seconds=2)


def sample_package() -> bytes:
    """Create a compressed package with a spec, a supplementary and a thumbnail."""
    stream = io.BytesIO()
    package = aas_package3.Packaging().create_in_stream(stream)
    spec = package.make_spec(
        package.put_part("/aasx/spec.xml", "text/xml", b"<aas/>" * 1_000)
    )
    package.relate_supplementary_to_spec(
        package.put_part("/aasx/doc.pdf", "application/pdf", bytes(range(256)) * 20),
        spec,
    )
    package.set_thumbnail(package.put_part("/thumbnail.png", "image/png", b"PNG" * 100))
    package.flush()
    return stream.getvalue()


def read_everything(data: bytes) -> None:
    """Open the package ``data`` and call all the read methods on it."""
    package = aas_package3.Packaging().open_read_from_stream(io.BytesIO(data))
    package.specs_by_content_type()
    package.supplementary_relationships()
    package.thumbnail()


def offset_of_local_entry_data(data: bytes, name: str) -> int:
    """Find the offset of the (compressed) data of the ZIP entry ``name``."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        header_offset = archive.getinfo(name).header_offset

    name_length, extra_length = struct.unpack_from("<HH", data, header_offset + 26)
    return int(header_offset + 30 + name_length + extra_length)


class TestRegressionsFoundByFuzzing(unittest.TestCase):
    def test_corrupted_deflate_stream(self) -> None:
        # NOTE (mristin):
        # The zipfile module raised zlib.error which we did not catch.
        data = bytearray(sample_package())
        offset = offset_of_local_entry_data(bytes(data), "aasx/spec.xml")
        data[offset : offset + 4] = b"\xff\xff\xff\xff"

        with self.assertRaises(aas_package3.InvalidPackageError):
            read_everything(bytes(data))

    def test_unsupported_zip_version(self) -> None:
        # NOTE (mristin):
        # The zipfile module raised NotImplementedError which we did not catch.
        data = bytearray(sample_package())
        central_offset = data.index(b"PK\x01\x02")
        # Version needed to extract (22.2) in the central directory header
        struct.pack_into("<H", data, central_offset + 6, 222)

        with self.assertRaises(aas_package3.InvalidPackageError):
            read_everything(bytes(data))

    def test_unknown_xml_encoding(self) -> None:
        # NOTE (mristin):
        # Expat raised LookupError for an unknown encoding which we did not catch.
        data = common.replace_in_relationships(
            sample_package(), 'encoding="UTF-8"', 'encoding="UTF-U"'
        )

        with self.assertRaises(aas_package3.InvalidPackageError):
            read_everything(data)

    def test_empty_entry_name(self) -> None:
        # NOTE (mristin):
        # ZipInfo.is_dir() raised IndexError for an empty entry name in Python
        # 3.10, which we did not catch. The later versions silently read the entry
        # as the invalid part "/".
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, mode="w") as archive:
            for name, content in common.read_zip(sample_package()).items():
                archive.writestr(name, content)

            archive.writestr(zipfile.ZipInfo(""), b"something")

        with self.assertRaises(aas_package3.InvalidPackageError):
            read_everything(buffer.getvalue())


class TestRandomInput(unittest.TestCase):
    """
    Feed random and corrupted inputs, and expect only our own exceptions.

    Each iteration uses its own seed so that a failure can be reproduced
    in isolation. The tests stop early once their time budget is spent.
    """

    def run_fuzz(
        self, iterations: int, make_input: Callable[[random.Random], bytes]
    ) -> None:
        deadline = time.perf_counter() + TIME_BUDGET.total_seconds()
        for seed in range(iterations):
            if time.perf_counter() > deadline:
                break

            data = make_input(random.Random(seed))
            try:
                read_everything(data)
            except aas_package3.InvalidPackageError:
                pass
            except Exception as exception:
                self.fail(
                    f"Unexpected {type(exception).__name__} for the seed {seed}: "
                    f"{exception}"
                )

    def test_random_bytes(self) -> None:
        self.run_fuzz(
            1_000,
            lambda rng: bytes(rng.randrange(256) for _ in range(rng.randrange(512))),
        )

    def test_corrupted_and_truncated_bytes(self) -> None:
        data = sample_package()

        def corrupt(rng: random.Random) -> bytes:
            corrupted = bytearray(data)
            for _ in range(rng.randint(1, 8)):
                corrupted[rng.randrange(len(corrupted))] = rng.randrange(256)
            if rng.random() < 0.2:
                corrupted = corrupted[: rng.randrange(len(corrupted))]
            return bytes(corrupted)

        self.run_fuzz(5_000, corrupt)

    def test_corrupted_entries(self) -> None:
        entries = common.read_zip(sample_package())

        def mutate(rng: random.Random) -> bytes:
            mutated = dict(entries)
            name = rng.choice(sorted(mutated))
            content = bytearray(mutated[name])
            for _ in range(rng.randint(1, 4)):
                if len(content) > 0:
                    content[rng.randrange(len(content))] = rng.randrange(256)
            mutated[name] = bytes(content)
            return common.write_zip(mutated)

        self.run_fuzz(2_000, mutate)


class TestRandomOperations(unittest.TestCase):
    def test_against_a_model(self) -> None:
        """Apply random operations and compare the package with a simple model."""
        rng = random.Random(3)
        packaging = aas_package3.Packaging()
        uris = [
            f"/aasx/Part%20{i}.{ext}"
            for i, ext in enumerate(["xml", "json", "pdf", "png", "bin", "txt"])
        ]

        for _ in range(30):
            stream = io.BytesIO()
            package = packaging.create_in_stream(stream)

            content_by_uri: Dict[str, Tuple[str, bytes]] = dict()
            spec_set: Set[str] = set()
            supplementaries: Set[Tuple[str, str]] = set()
            thumbnail: Optional[str] = None

            for _ in range(60):
                uri = rng.choice(uris)
                operation = rng.choice(
                    [
                        "put",
                        "put",
                        "delete",
                        "spec",
                        "unspec",
                        "relate",
                        "unrelate",
                        "thumb",
                        "unthumb",
                        "reopen",
                    ]
                )
                part = package.find_part(uri)

                if operation == "put":
                    content_type = rng.choice(["text/xml", "application/json", "x/y"])
                    content = bytes(
                        rng.randrange(256) for _ in range(rng.randrange(64))
                    )
                    package.put_part(uri, content_type, content)
                    content_by_uri[uri] = (content_type, content)
                elif operation == "delete" and part is not None:
                    package.delete_part(part)
                    del content_by_uri[uri]
                    spec_set.discard(uri)
                    supplementaries = {
                        pair for pair in supplementaries if uri not in pair
                    }
                    if thumbnail == uri:
                        thumbnail = None
                elif operation == "spec" and part is not None:
                    package.make_spec(part)
                    spec_set.add(uri)
                elif operation == "unspec" and part is not None and uri in spec_set:
                    package.unmake_spec(part)
                    spec_set.discard(uri)
                elif (
                    operation in ("relate", "unrelate")
                    and part is not None
                    and len(spec_set) > 0
                ):
                    spec_uri = rng.choice(sorted(spec_set))
                    spec = package.must_part(spec_uri)
                    if operation == "relate":
                        package.relate_supplementary_to_spec(part, spec)
                        supplementaries.add((spec_uri, uri))
                    else:
                        package.unrelate_supplementary_from_spec(part, spec)
                        supplementaries.discard((spec_uri, uri))
                elif operation == "thumb" and part is not None:
                    package.set_thumbnail(part)
                    thumbnail = uri
                elif operation == "unthumb":
                    package.unset_thumbnail()
                    thumbnail = None
                elif operation == "reopen":
                    package.flush()
                    package = packaging.open_read_write_from_stream(stream)

            package.flush()
            package_read = packaging.open_read_from_stream(stream)

            for uri in uris:
                found = package_read.find_part(uri)
                if uri in content_by_uri:
                    assert found is not None
                    self.assertEqual(
                        content_by_uri[uri],
                        (found.content_type, found.read_all_bytes()),
                    )
                else:
                    self.assertIsNone(found)

            self.assertEqual(spec_set, {spec.uri for spec in package_read.specs()})
            self.assertEqual(
                {pair for pair in supplementaries if pair[0] in spec_set},
                {
                    (relationship.spec.uri, relationship.supplementary.uri)
                    for relationship in package_read.supplementary_relationships()
                },
            )
            actual_thumbnail = package_read.thumbnail()
            self.assertEqual(
                thumbnail,
                actual_thumbnail.uri if actual_thumbnail is not None else None,
            )


if __name__ == "__main__":
    unittest.main()
