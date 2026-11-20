# SPDX-FileCopyrightText: 2026 aas-package3-python AUTHORS
#
# SPDX-License-Identifier: MIT

"""
Read and round-trip all the AASX files published in IDTA submodel-templates.

The files are downloaded from https://github.com/admin-shell-io/submodel-templates
into the cache directory, unless they have been already downloaded before.
"""

import argparse
import concurrent.futures
import io
import json
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Final, List, Optional, Tuple

import aas_package3

REPOSITORY: Final[str] = "admin-shell-io/submodel-templates"
BRANCH: Final[str] = "main"

#: Summary of a package used to compare it before and after the round trip
Summary = Tuple[
    List[Tuple[str, str, bytes]],
    List[Tuple[str, str, str, bytes]],
    Optional[Tuple[str, str, bytes]],
]


def fetch(url: str) -> bytes:
    """Fetch the content at ``url``, and retry on the transient network errors."""
    attempts = 5
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=120) as response:
                return bytes(response.read())
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            if attempt == attempts - 1:
                raise

            time.sleep(2**attempt)

    raise AssertionError("Unexpected execution path")


def list_aasx_paths() -> List[str]:
    """List the paths of all the AASX files in the repository."""
    url = f"https://api.github.com/repos/{REPOSITORY}/git/trees/{BRANCH}?recursive=1"
    tree = json.loads(fetch(url))

    if tree.get("truncated", False):
        raise RuntimeError(f"The listing of {REPOSITORY} has been truncated")

    return sorted(
        entry["path"]
        for entry in tree["tree"]
        if entry["path"].lower().endswith(".aasx")
    )


def download(path: str, cache_dir: pathlib.Path) -> pathlib.Path:
    """Download the AASX file at ``path`` in the repository, if not cached."""
    target = cache_dir / path
    if not target.exists():
        url = (
            f"https://raw.githubusercontent.com/{REPOSITORY}/{BRANCH}/"
            f"{urllib.parse.quote(path)}"
        )
        data = fetch(url)

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    return target


def summarize(package: aas_package3.PackageRead) -> Summary:
    """Summarize the AAS-relevant content of the ``package``."""
    thumbnail = package.thumbnail()
    return (
        [
            (spec.uri, spec.content_type, spec.read_all_bytes())
            for spec in package.specs()
        ],
        [
            (
                relationship.spec.uri,
                relationship.supplementary.uri,
                relationship.supplementary.content_type,
                relationship.supplementary.read_all_bytes(),
            )
            for relationship in package.supplementary_relationships()
        ],
        (
            (thumbnail.uri, thumbnail.content_type, thumbnail.read_all_bytes())
            if thumbnail is not None
            else None
        ),
    )


def check(pth: pathlib.Path) -> Optional[str]:
    """Check that the package at ``pth`` can be read and round-tripped."""
    packaging = aas_package3.Packaging()

    try:
        before = summarize(packaging.open_read(pth))
    except aas_package3.InvalidPackageError as exception:
        return f"Failed to read: {exception}"

    stream = io.BytesIO(pth.read_bytes())
    packaging.open_read_write_from_stream(stream).flush()

    try:
        after = summarize(packaging.open_read_from_stream(stream))
    except aas_package3.InvalidPackageError as exception:
        return f"Failed to read after the round trip: {exception}"

    if before != after:
        return "The content differs after the round trip"

    return None


def main() -> int:
    """Execute the main routine."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cache_dir",
        help="Directory where the AASX files are downloaded to",
        required=True,
    )
    args = parser.parse_args()

    cache_dir = pathlib.Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    paths = list_aasx_paths()
    print(f"Downloading {len(paths)} AASX file(s) to {cache_dir} ...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        local_paths = list(executor.map(lambda path: download(path, cache_dir), paths))

    failures: List[str] = []
    for path, local_path in zip(paths, local_paths, strict=True):
        error = check(local_path)
        if error is not None:
            failures.append(f"{path}: {error}")

    if len(failures) > 0:
        print(
            f"{len(failures)} out of {len(paths)} package(s) failed:", file=sys.stderr
        )
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1

    print(f"All {len(paths)} package(s) have been successfully checked.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
