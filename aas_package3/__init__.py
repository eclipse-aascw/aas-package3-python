# SPDX-FileCopyrightText: 2026 aas-package3-python AUTHORS
#
# SPDX-License-Identifier: MIT

"""
Read and write the packaged file format (AASX) of an Asset Administration Shell V3.

The package follows the Open Packaging Conventions (OPC, ECMA-376 Part 2).

The whole package is kept in memory. The changes are written to the underlying
file or stream only when you call :py:meth:`PackageReadWrite.flush`.

The objects of this module are not thread-safe.
"""

import io
import os
import pathlib
import posixpath
import re
import urllib.parse
import xml.parsers.expat
import zipfile
import zlib
from types import TracebackType
from typing import (
    BinaryIO,
    Dict,
    Final,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    Type,
    Union,
)

__version__: Final[str] = "1.0.0rc1"
__author__: Final[str] = "Marko Ristin"
__copyright__: Final[str] = "2026 aas-package3-python AUTHORS"
__license__: Final[str] = "License :: OSI Approved :: MIT License"
__status__: Final[str] = "Beta"

RELATION_TYPE_AASX_ORIGIN: Final[
    str
] = "http://admin-shell.io/aasx/relationships/aasx-origin"
"""Relationship type from the package root to the origin part."""

RELATION_TYPE_AASX_SPEC: Final[
    str
] = "http://admin-shell.io/aasx/relationships/aas-spec"
"""Relationship type from the origin part to an AAS spec part."""

RELATION_TYPE_AASX_SUPPLEMENTARY: Final[
    str
] = "http://admin-shell.io/aasx/relationships/aas-suppl"
"""Relationship type from an AAS spec part to a supplementary part."""

RELATION_TYPE_THUMBNAIL: Final[
    str
] = "http://schemas.openxmlformats.org/package/2006/relationships/metadata/thumbnail"
"""Relationship type from the package root to the thumbnail part."""

_AASX_RELATIONSHIPS_PREFIX: Final[str] = "http://admin-shell.io/aasx/relationships/"

_AASX_RELATIONSHIP_TYPE_RE: Final[re.Pattern[str]] = re.compile(
    r"^https?://(?:www\.)?admin-shell\.io/aasx/relationships/(.+)$", re.IGNORECASE
)

# NOTE (mristin):
# Some versions of aas-package3-csharp write ``aasx-spec`` instead of ``aas-spec``
# as the spec relationship. We read both, but write only the latter, which is
# prescribed by the specification.
_LEGACY_AASX_RELATIONSHIP_SUFFIXES: Final[Mapping[str, str]] = {"aasx-spec": "aas-spec"}

_OPC_RELATIONSHIPS_NAMESPACE: Final[
    str
] = "http://schemas.openxmlformats.org/package/2006/relationships"
_OPC_CONTENT_TYPES_NAMESPACE: Final[
    str
] = "http://schemas.openxmlformats.org/package/2006/content-types"
_RELATIONSHIPS_CONTENT_TYPE: Final[
    str
] = "application/vnd.openxmlformats-package.relationships+xml"
_CONTENT_TYPES_ENTRY: Final[str] = "[Content_Types].xml"
_DEFAULT_CONTENT_TYPE: Final[str] = "application/octet-stream"

_ORIGIN_URI: Final[str] = "/aasx/aasx-origin"
_ORIGIN_CONTENT: Final[bytes] = b"Intentionally empty."

# NOTE (mristin):
# We fix the timestamp of the ZIP entries so that flushing the same package
# twice results in the same bytes.
_ZIP_DATE_TIME: Final[Tuple[int, int, int, int, int, int]] = (1980, 1, 1, 0, 0, 0)

_SLOW_CHECKS: Final[bool] = os.environ.get(
    "AAS_PACKAGE3_SLOW_CHECKS", ""
).strip().lower() in ("1", "true")
"""
Enable the expensive pre- and post-conditions in addition to the cheap ones.

The cheap conditions are checked with ``assert`` and are thus disabled by
running Python with ``-O``. Set the environment variable
``AAS_PACKAGE3_SLOW_CHECKS`` to ``1`` to enable the expensive ones as well.
"""

_URI_SCHEME_RE: Final[re.Pattern[str]] = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")

# NOTE (mristin):
# We denote the package root as a relationship source with an empty string as
# no part can have an empty URI.
_PACKAGE_ROOT: Final[str] = ""


class InvalidPackageError(Exception):
    """Signal that the data does not represent a valid AAS package."""


class NoOriginError(InvalidPackageError):
    """Signal that the package lacks the origin part."""


class DanglingRelationshipError(InvalidPackageError):
    """Signal that a relationship points to a part which does not exist."""


class PartNotFoundError(LookupError):
    """Signal that the requested part does not exist in the package."""


class Part:
    """
    Represent a part of an AAS package.

    You obtain parts from a package, *e.g.*, with :py:meth:`PackageRead.specs`
    or :py:meth:`PackageReadWrite.put_part`. Do not construct them yourself.
    """

    def __init__(self, uri: str, content_type: str, content: bytes) -> None:
        """Initialize with the given values."""
        self._uri = uri
        self._content_type = content_type
        self._content = content

    @property
    def uri(self) -> str:
        """URI of the part within the package, *e.g.*, ``/aasx/data.json``."""
        return self._uri

    @property
    def content_type(self) -> str:
        """MIME type of the part."""
        return self._content_type

    def stream(self) -> BinaryIO:
        """Open a read stream over the content of the part."""
        return io.BytesIO(self._content)

    def read_all_bytes(self) -> bytes:
        """Read the whole content of the part as bytes."""
        return self._content

    def read_all_text(self, encoding: str = "utf-8-sig") -> str:
        """
        Read the content of the part as text.

        By default, the content is decoded as UTF-8, and an optional byte order
        mark is stripped.
        """
        return self._content.decode(encoding)

    def __repr__(self) -> str:
        """Represent the part for debugging."""
        return (
            f"{self.__class__.__name__}("
            f"uri={self._uri!r}, content_type={self._content_type!r})"
        )


class SupplementaryRelationship:
    """Represent a relationship between a spec and its supplementary part."""

    def __init__(self, spec: Part, supplementary: Part) -> None:
        """Initialize with the given values."""
        self.spec = spec
        self.supplementary = supplementary

    def __repr__(self) -> str:
        """Represent the relationship for debugging."""
        return (
            f"{self.__class__.__name__}("
            f"spec={self.spec!r}, supplementary={self.supplementary!r})"
        )


def is_valid_part_uri(uri: str) -> bool:
    """
    Check that ``uri`` can be used as a URI of a new part in a package.

    The URI must be absolute within the package, must not end with a slash,
    must not contain empty, ``.`` or ``..`` segments, and must not clash with
    the parts reserved by the Open Packaging Conventions.

    >>> is_valid_part_uri("/aasx/data.json")
    True
    >>> is_valid_part_uri("aasx/data.json")
    False
    >>> is_valid_part_uri("/aasx/../data.json")
    False
    >>> is_valid_part_uri("/aasx/_rels/data.json.rels")
    False
    """
    if not uri.startswith("/") or uri.endswith("/"):
        return False

    if any(character in uri for character in "\\?#"):
        return False

    segments = uri[1:].split("/")
    if any(segment in ("", ".", "..") or segment.endswith(".") for segment in segments):
        return False

    if len(segments) == 1 and segments[0].lower() == _CONTENT_TYPES_ENTRY.lower():
        return False

    if (
        len(segments) >= 2
        and segments[-2].lower() == "_rels"
        and segments[-1].lower().endswith(".rels")
    ):
        return False

    return True


def _key(uri: str) -> str:
    """
    Normalize the ``uri`` so that equivalent part URIs map to the same key.

    The part URIs are compared case-insensitively and percent-decoded as
    prescribed by the Open Packaging Conventions.

    >>> _key("/aasx/Some%20Data.JSON")
    '/aasx/some data.json'
    >>> _key("aasx/data.json")
    '/aasx/data.json'
    >>> _key("")
    ''
    """
    if uri == _PACKAGE_ROOT:
        return _PACKAGE_ROOT

    decoded = urllib.parse.unquote(uri)
    if not decoded.startswith("/"):
        decoded = "/" + decoded

    return decoded.lower()


def _canonicalize_relationship_type(relationship_type: str) -> str:
    """
    Map the deprecated and legacy relationship types to the preferred ones.

    >>> _canonicalize_relationship_type(
    ...     "http://www.admin-shell.io/aasx/relationships/aas-suppl"
    ... )
    'http://admin-shell.io/aasx/relationships/aas-suppl'
    >>> _canonicalize_relationship_type(
    ...     "http://admin-shell.io/aasx/relationships/aasx-spec"
    ... )
    'http://admin-shell.io/aasx/relationships/aas-spec'
    """
    stripped = relationship_type.strip()
    match = _AASX_RELATIONSHIP_TYPE_RE.match(stripped)
    if match is None:
        return stripped

    suffix = match.group(1)
    return _AASX_RELATIONSHIPS_PREFIX + _LEGACY_AASX_RELATIONSHIP_SUFFIXES.get(
        suffix, suffix
    )


class _Relationship:
    """Represent an OPC relationship from a source to a target."""

    __slots__ = (
        "external",
        "identifier",
        "part_key",
        "part_uri",
        "relationship_type",
        "target",
    )

    def __init__(
        self,
        identifier: str,
        relationship_type: str,
        target: str,
        external: bool,
        part_uri: Optional[str],
    ) -> None:
        #: Identifier of the relationship, unique within its source
        self.identifier = identifier

        #: Canonical type of the relationship
        self.relationship_type = relationship_type

        #: Target as it is to be written to the package
        self.target = target

        #: Target mode as it is to be written to the package
        self.external = external

        #: URI of the target part if the target lies within the package
        self.part_uri = part_uri

        # NOTE (mristin):
        # We cache the normalized part URI as normalizing is expensive, while
        # the relationships are compared often.

        #: Normalized :py:attr:`part_uri`
        self.part_key = _key(part_uri) if part_uri is not None else None


class _RelationshipsOfSource:
    """Hold all the relationships originating from a source."""

    __slots__ = ("relationships", "source")

    def __init__(self, source: str) -> None:
        self.source = source
        self.relationships: List[_Relationship] = []


class _Content:
    """Hold the in-memory content of a package."""

    def __init__(self, origin_uri: str) -> None:
        self.origin_uri = origin_uri
        self.part_by_key: Dict[str, Part] = dict()
        self.relationships_by_source_key: Dict[str, _RelationshipsOfSource] = dict()
        self.next_relationship_number = 1

    def internal_relationships(
        self, source: str, relationship_type: str
    ) -> List[_Relationship]:
        """
        List the relationships of ``relationship_type`` from ``source`` to parts.

        A relationship points to a part if its target lies within the package,
        regardless of the declared target mode. Some AASX writers declare
        the relationships to the parts as external by mistake.
        """
        of_source = self.relationships_by_source_key.get(_key(source), None)
        if of_source is None:
            return []

        return [
            relationship
            for relationship in of_source.relationships
            if relationship.part_key is not None
            and relationship.relationship_type == relationship_type
        ]

    def resolve_targets(self, source: str, relationship_type: str) -> List[Part]:
        """
        Resolve the targets of the internal relationships from ``source``.

        The duplicate targets are reported only once.

        :raise: :py:class:`DanglingRelationshipError` if a target part is missing
        """
        result: List[Part] = []
        observed_key_set = set()
        for relationship in self.internal_relationships(source, relationship_type):
            key = relationship.part_key
            assert key is not None
            if key in observed_key_set:
                continue
            observed_key_set.add(key)

            part = self.part_by_key.get(key, None)
            if part is None:
                raise DanglingRelationshipError(
                    f"The relationship {relationship.identifier!r} "
                    f"of type {relationship_type!r} from "
                    f"{source if source != _PACKAGE_ROOT else 'the package root'!r} "
                    f"points to the part {relationship.target!r}, "
                    f"but the part does not exist"
                )

            result.append(part)

        return result

    def has_relationship(
        self, source: str, target: str, relationship_type: str
    ) -> bool:
        """Check that an internal relationship exists from ``source`` to ``target``."""
        target_key = _key(target)
        return any(
            relationship.part_key == target_key
            for relationship in self.internal_relationships(source, relationship_type)
        )

    def add_relationship(
        self, source: str, target: str, relationship_type: str
    ) -> None:
        """Add a new internal relationship with a fresh identifier."""
        of_source = self.relationships_by_source_key.get(_key(source), None)
        if of_source is None:
            of_source = _RelationshipsOfSource(source=source)
            self.relationships_by_source_key[_key(source)] = of_source

        identifier_set = {
            relationship.identifier for relationship in of_source.relationships
        }
        while True:
            identifier = f"R{self.next_relationship_number:08x}"
            self.next_relationship_number += 1
            if identifier not in identifier_set:
                break

        of_source.relationships.append(
            _Relationship(
                identifier=identifier,
                relationship_type=relationship_type,
                target=target,
                external=False,
                part_uri=target,
            )
        )

    def remove_relationships(
        self, source: str, target: Optional[str], relationship_type: str
    ) -> None:
        """
        Remove the internal relationships of ``relationship_type`` from ``source``.

        If ``target`` is given, remove only the relationships pointing to it.
        """
        of_source = self.relationships_by_source_key.get(_key(source), None)
        if of_source is None:
            return

        target_key = _key(target) if target is not None else None

        of_source.relationships = [
            relationship
            for relationship in of_source.relationships
            if not (
                relationship.part_key is not None
                and relationship.relationship_type == relationship_type
                and (target_key is None or relationship.part_key == target_key)
            )
        ]

    def remove_all_relationships_of(self, uri: str) -> None:
        """Remove all the relationships whose source or internal target is ``uri``."""
        key = _key(uri)
        self.relationships_by_source_key.pop(key, None)

        for of_source in self.relationships_by_source_key.values():
            of_source.relationships = [
                relationship
                for relationship in of_source.relationships
                if relationship.part_key != key
            ]


def _source_of_relationships_entry(name: str) -> Optional[str]:
    """
    Determine the source URI of the relationships ZIP entry ``name``.

    Return None if ``name`` does not denote a relationships entry.

    >>> _source_of_relationships_entry("_rels/.rels")
    ''
    >>> _source_of_relationships_entry("aasx/_rels/aasx-origin.rels")
    '/aasx/aasx-origin'
    >>> _source_of_relationships_entry("_rels/thumbnail.png.rels")
    '/thumbnail.png'
    >>> _source_of_relationships_entry("aasx/data.json") is None
    True
    """
    directory, _, base = name.rpartition("/")
    if not base.endswith(".rels"):
        return None

    if directory == "_rels":
        parent = ""
    elif directory.endswith("/_rels"):
        parent = directory[: -len("/_rels")]
    else:
        return None

    source_name = base[: -len(".rels")]
    if source_name == "":
        return _PACKAGE_ROOT if parent == "" else None

    return f"/{parent}/{source_name}" if parent != "" else f"/{source_name}"


def _relationships_entry_of_source(source: str) -> str:
    """
    Determine the name of the ZIP entry holding the relationships of ``source``.

    >>> _relationships_entry_of_source("")
    '_rels/.rels'
    >>> _relationships_entry_of_source("/aasx/aasx-origin")
    'aasx/_rels/aasx-origin.rels'
    >>> _relationships_entry_of_source("/thumbnail.png")
    '_rels/thumbnail.png.rels'
    """
    if source == _PACKAGE_ROOT:
        return "_rels/.rels"

    directory, base = posixpath.split("/" + source.lstrip("/"))
    directory = directory.strip("/")
    return f"{directory}/_rels/{base}.rels" if directory != "" else f"_rels/{base}.rels"


def _resolve_target(source: str, target: str) -> str:
    """
    Resolve the internal relationship ``target`` against its ``source``.

    >>> _resolve_target("/aasx/spec.xml", "../docs/./manual.pdf")
    '/docs/manual.pdf'
    >>> _resolve_target("", "aasx/aasx-origin")
    '/aasx/aasx-origin'
    >>> _resolve_target("/aasx/aasx-origin", "/aasx/spec.xml")
    '/aasx/spec.xml'
    >>> _resolve_target("/aasx/aasx-origin", "spec.xml")
    '/aasx/spec.xml'
    """
    target = target.replace("\\", "/")
    if target.startswith("/"):
        joined = target
    else:
        source_directory = posixpath.dirname(source) if source != _PACKAGE_ROOT else "/"
        joined = posixpath.join(source_directory or "/", target)

    normalized = posixpath.normpath(joined)

    # NOTE (mristin):
    # POSIX allows exactly two leading slashes to be kept by normpath.
    return "/" + normalized.lstrip("/")


def _parse_xml(data: bytes, expected_root: str, what: str) -> List[Mapping[str, str]]:
    """
    Parse the OPC XML ``data`` and collect the attributes of the root's children.

    We parse with expat directly so that we can reject document type
    declarations which the Open Packaging Conventions forbid anyhow.

    :raise: :py:class:`InvalidPackageError` if the XML is invalid
    """
    parser = xml.parsers.expat.ParserCreate(namespace_separator="}")

    depth = 0
    root_name: Optional[str] = None
    children: List[Mapping[str, str]] = []

    def start_element(name: str, attributes: Dict[str, str]) -> None:
        nonlocal depth, root_name
        local_name = name.rpartition("}")[2]

        if depth == 0:
            root_name = local_name
        elif depth == 1:
            children.append(
                {
                    "": local_name,
                    **{k.rpartition("}")[2]: v for k, v in attributes.items()},
                }
            )

        depth += 1

    def end_element(name: str) -> None:
        nonlocal depth
        depth -= 1

    def start_doctype(*args: object) -> None:
        raise InvalidPackageError(
            f"The {what} must not contain a document type declaration"
        )

    parser.StartElementHandler = start_element
    parser.EndElementHandler = end_element
    parser.StartDoctypeDeclHandler = start_doctype

    try:
        parser.Parse(data, True)
    except (
        xml.parsers.expat.ExpatError,
        # NOTE (mristin):
        # Expat raises LookupError if the declared encoding is unknown.
        LookupError,
    ) as exception:
        raise InvalidPackageError(
            f"The {what} is not valid XML: {exception}"
        ) from exception

    if root_name != expected_root:
        raise InvalidPackageError(
            f"Expected the root element of the {what} to be {expected_root!r}, "
            f"but got {root_name!r}"
        )

    return children


def _parse_relationships(
    data: bytes, source: str, entry_name: str
) -> List[_Relationship]:
    """Parse the relationships ZIP entry with the given ``source``."""
    result: List[_Relationship] = []

    for child in _parse_xml(
        data,
        expected_root="Relationships",
        what=f"relationships entry {entry_name!r}",
    ):
        if child[""] != "Relationship":
            continue

        identifier = child.get("Id", None)
        relationship_type = child.get("Type", None)
        target = child.get("Target", None)
        if identifier is None or relationship_type is None or target is None:
            raise InvalidPackageError(
                f"A relationship in {entry_name!r} lacks "
                f"one of the attributes Id, Type or Target"
            )

        has_scheme = _URI_SCHEME_RE.match(target) is not None
        external = (
            child.get("TargetMode", "").strip().lower() == "external" or has_scheme
        )

        # NOTE (mristin):
        # Some AASX writers mark the relationships to the parts as external.
        # We follow aas-package3-csharp and resolve every relative target as
        # a part, but keep the declared target mode and the target verbatim
        # when writing the package back.
        part_uri = _resolve_target(source, target) if not has_scheme else None

        result.append(
            _Relationship(
                identifier=identifier,
                relationship_type=_canonicalize_relationship_type(relationship_type),
                target=target if external else part_uri or target,
                external=external,
                part_uri=part_uri,
            )
        )

    return result


def _extension(uri: str) -> str:
    """
    Extract the lower-case extension of the part ``uri`` without the dot.

    >>> _extension("/aasx/data.JSON")
    'json'
    >>> _extension("/aasx/aasx-origin")
    ''
    >>> _extension("/aasx/.hidden")
    ''
    """
    base = uri.rpartition("/")[2]
    head, dot, tail = base.rpartition(".")
    if dot == "" or head == "":
        return ""

    return tail.lower()


def _parse_package(data: bytes) -> _Content:
    """
    Parse the package from its serialized ``data``.

    :raise: :py:class:`InvalidPackageError` if the package is invalid
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()

            # NOTE (mristin):
            # An empty part name is invalid. Moreover, ZipInfo.is_dir() raises
            # IndexError for an empty entry name in Python 3.10, so we check
            # the name before we check for the directories.
            for info in infos:
                if info.filename == "":
                    raise InvalidPackageError(
                        "The package contains an entry with an empty name"
                    )

            content_by_name: Dict[str, bytes] = {
                info.filename: archive.read(info)
                for info in infos
                if not info.filename.endswith("/")
            }
    except (
        zipfile.BadZipFile,
        zlib.error,
        ValueError,
        EOFError,
        # NOTE (mristin):
        # The zipfile module raises RuntimeError for encrypted entries, and
        # NotImplementedError, which is a RuntimeError, for unsupported versions
        # and compression methods.
        RuntimeError,
    ) as exception:
        raise InvalidPackageError(
            f"The package is not a valid ZIP archive: {exception}"
        ) from exception

    content_type_by_extension: Dict[str, str] = dict()
    content_type_by_key: Dict[str, str] = dict()

    content_types_data = content_by_name.get(_CONTENT_TYPES_ENTRY)
    if content_types_data is not None:
        for child in _parse_xml(
            content_types_data, expected_root="Types", what=_CONTENT_TYPES_ENTRY
        ):
            content_type = child.get("ContentType", None)
            if content_type is None:
                continue

            if child[""] == "Default" and "Extension" in child:
                content_type_by_extension[child["Extension"].lower()] = content_type
            elif child[""] == "Override" and "PartName" in child:
                content_type_by_key[_key(child["PartName"])] = content_type

    relationships_by_source_key: Dict[str, _RelationshipsOfSource] = dict()
    part_names: List[str] = []

    for name, entry_data in content_by_name.items():
        if name == _CONTENT_TYPES_ENTRY:
            continue

        source = _source_of_relationships_entry(name)
        if source is None:
            part_names.append(name)
            continue

        of_source = relationships_by_source_key.get(_key(source))
        if of_source is None:
            of_source = _RelationshipsOfSource(source=source)
            relationships_by_source_key[_key(source)] = of_source

        of_source.relationships.extend(
            _parse_relationships(entry_data, source=source, entry_name=name)
        )

    origin_uri: Optional[str] = None
    root_relationships = relationships_by_source_key.get(_PACKAGE_ROOT)
    if root_relationships is not None:
        for relationship in root_relationships.relationships:
            if (
                relationship.part_uri is not None
                and relationship.relationship_type == RELATION_TYPE_AASX_ORIGIN
            ):
                origin_uri = relationship.part_uri
                break

    if origin_uri is None:
        raise NoOriginError("No origin part found")

    content = _Content(origin_uri=origin_uri)
    content.relationships_by_source_key = relationships_by_source_key

    for name in part_names:
        uri = "/" + name.lstrip("/")
        key = _key(uri)
        if key in content.part_by_key:
            raise InvalidPackageError(
                f"The package contains multiple parts equivalent to {uri!r}"
            )

        content_type = content_type_by_key.get(key)
        if content_type is None:
            content_type = content_type_by_extension.get(
                _extension(uri), _DEFAULT_CONTENT_TYPE
            )

        content.part_by_key[key] = Part(
            uri=uri, content_type=content_type, content=content_by_name[name]
        )

    if _key(origin_uri) not in content.part_by_key:
        raise NoOriginError(
            f"The origin relationship points to the part {origin_uri!r}, "
            f"but the part does not exist"
        )

    return content


def _escape_attribute(text: str) -> str:
    """
    Escape ``text`` so that it can be put in a double-quoted XML attribute.

    >>> _escape_attribute('a<b & "c"')
    'a&lt;b &amp; &quot;c&quot;'
    """
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _serialize_content_types(parts: Sequence[Part]) -> bytes:
    """Serialize the ``[Content_Types].xml`` for the ``parts``."""
    content_type_by_extension: Dict[str, str] = {"rels": _RELATIONSHIPS_CONTENT_TYPE}
    overrides: List[Part] = []

    for part in parts:
        extension = _extension(part.uri)
        if extension == "":
            overrides.append(part)
            continue

        content_type = content_type_by_extension.get(extension)
        if content_type is None:
            content_type_by_extension[extension] = part.content_type
        elif content_type != part.content_type:
            overrides.append(part)

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<Types xmlns="{_OPC_CONTENT_TYPES_NAMESPACE}">',
    ]
    for extension in sorted(content_type_by_extension):
        lines.append(
            f'  <Default Extension="{_escape_attribute(extension)}" '
            f'ContentType="{_escape_attribute(content_type_by_extension[extension])}"/>'
        )
    for part in overrides:
        lines.append(
            f'  <Override PartName="{_escape_attribute(part.uri)}" '
            f'ContentType="{_escape_attribute(part.content_type)}"/>'
        )
    lines.append("</Types>")
    lines.append("")

    return "\n".join(lines).encode("utf-8")


def _serialize_relationships(relationships: Sequence[_Relationship]) -> bytes:
    """Serialize the ``relationships`` of a source as an OPC XML."""
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<Relationships xmlns="{_OPC_RELATIONSHIPS_NAMESPACE}">',
    ]
    for relationship in relationships:
        target_mode = ' TargetMode="External"' if relationship.external else ""
        lines.append(
            f'  <Relationship Id="{_escape_attribute(relationship.identifier)}" '
            f'Type="{_escape_attribute(relationship.relationship_type)}" '
            f'Target="{_escape_attribute(relationship.target)}"{target_mode}/>'
        )
    lines.append("</Relationships>")
    lines.append("")

    return "\n".join(lines).encode("utf-8")


def _serialize_package(content: _Content) -> bytes:
    """Serialize the package ``content`` as a ZIP archive."""
    parts = sorted(content.part_by_key.values(), key=lambda part: part.uri)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:

        def write(name: str, data: bytes) -> None:
            info = zipfile.ZipInfo(filename=name, date_time=_ZIP_DATE_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)

        write(_CONTENT_TYPES_ENTRY, _serialize_content_types(parts))

        for of_source in sorted(
            content.relationships_by_source_key.values(),
            key=lambda an_of_source: an_of_source.source,
        ):
            if len(of_source.relationships) == 0:
                continue

            write(
                _relationships_entry_of_source(of_source.source),
                _serialize_relationships(of_source.relationships),
            )

        for part in parts:
            write(part.uri.lstrip("/"), part.read_all_bytes())

    return buffer.getvalue()


def _new_content() -> _Content:
    """Create the content of an empty AAS package."""
    content = _Content(origin_uri=_ORIGIN_URI)
    content.part_by_key[_key(_ORIGIN_URI)] = Part(
        uri=_ORIGIN_URI, content_type="text/plain", content=_ORIGIN_CONTENT
    )
    content.add_relationship(
        source=_PACKAGE_ROOT,
        target=_ORIGIN_URI,
        relationship_type=RELATION_TYPE_AASX_ORIGIN,
    )
    return content


def _read_stream(stream: BinaryIO) -> bytes:
    """Read the whole ``stream`` from its beginning, if seekable."""
    if stream.seekable():
        stream.seek(0)
    return stream.read()


class PackageRead:
    """
    Read from an AAS package.

    Use :py:class:`Packaging` to open a package. The package can be used as
    a context manager.
    """

    def __init__(self, path: Optional[pathlib.Path], content: _Content) -> None:
        """
        Initialize with the given values.

        Use :py:class:`Packaging` instead of calling the constructor directly.
        """
        self._path = path
        self._content = content

    @property
    def path(self) -> Optional[pathlib.Path]:
        """
        Path associated with the package.

        None if the package does not live in a file (*e.g.*, if it comes from
        a stream).
        """
        return self._path

    def close(self) -> None:
        """
        Close the package.

        The streams which you passed in are not closed, as they are owned by you.
        """
        # NOTE (mristin):
        # The whole package lives in memory, so there are no resources to release
        # apart from the memory itself.

    def specs(self) -> List[Part]:
        """
        List AAS specs contained in the package.

        :raise: :py:class:`DanglingRelationshipError` if a spec part is missing
        """
        return self._content.resolve_targets(
            self._content.origin_uri, RELATION_TYPE_AASX_SPEC
        )

    def specs_by_content_type(self) -> Mapping[str, List[Part]]:
        """
        List AAS specs grouped by their MIME content type.

        Both the content types and the specs within a group are sorted to make
        the result reproducible.

        :raise: :py:class:`DanglingRelationshipError` if a spec part is missing
        """
        specs_by_content_type: Dict[str, List[Part]] = dict()
        for spec in self.specs():
            specs_by_content_type.setdefault(spec.content_type, []).append(spec)

        result = {
            content_type: sorted(
                specs_by_content_type[content_type], key=lambda spec: spec.uri
            )
            for content_type in sorted(specs_by_content_type)
        }

        assert all(
            all(spec.content_type == content_type for spec in specs)
            for content_type, specs in result.items()
        ), "The content type of spec must match its group."

        return result

    def is_spec(self, part: Part) -> bool:
        """Check that ``part`` is related to the origin of the package as a spec."""
        return self._content.has_relationship(
            self._content.origin_uri, part.uri, RELATION_TYPE_AASX_SPEC
        )

    def supplementaries_for(self, spec: Part) -> List[Part]:
        """
        List the supplementary parts related to the ``spec``.

        :raise: :py:class:`DanglingRelationshipError` if a supplementary is missing
        """
        return self._content.resolve_targets(spec.uri, RELATION_TYPE_AASX_SUPPLEMENTARY)

    def supplementary_relationships(self) -> List[SupplementaryRelationship]:
        """
        List all the supplementary relationships from all the specs.

        :raise: :py:class:`DanglingRelationshipError` if a related part is missing
        """
        return [
            SupplementaryRelationship(spec=spec, supplementary=supplementary)
            for spec in self.specs()
            for supplementary in self.supplementaries_for(spec)
        ]

    def find_part(self, uri: str) -> Optional[Part]:
        """
        Try to find the package part with the given ``uri``.

        The URIs are compared case-insensitively and percent-decoded as prescribed
        by the Open Packaging Conventions.

        :return: the part, or None if it does not exist
        """
        return self._content.part_by_key.get(_key(uri), None)

    def must_part(self, uri: str) -> Part:
        """
        Retrieve the package part with the given ``uri``.

        :raise: :py:class:`PartNotFoundError` if the part does not exist
        """
        part = self.find_part(uri)
        if part is None:
            raise PartNotFoundError(f"The part does not exist in the package: {uri}")

        return part

    def thumbnail(self) -> Optional[Part]:
        """
        Retrieve the thumbnail of the package.

        :return: the thumbnail, or None if there is no thumbnail in the package
        :raise:
            :py:class:`DanglingRelationshipError` if the thumbnail relationship
            exists, but the part does not
        """
        relationships = self._content.internal_relationships(
            _PACKAGE_ROOT, RELATION_TYPE_THUMBNAIL
        )
        if len(relationships) == 0:
            return None

        target = relationships[0].target
        part = self._content.part_by_key.get(_key(target), None)
        if part is None:
            raise DanglingRelationshipError(
                f"The thumbnail relationship from the package root exists, "
                f"but the target part does not: {target}"
            )

        return part

    def __enter__(self) -> "PackageRead":
        """Return the package itself as the context."""
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        """Close the package."""
        self.close()


class PackageReadWrite(PackageRead):
    """
    Read from and write to an AAS package.

    The changes are kept in memory until you call :py:meth:`flush` or
    :py:meth:`close`.

    If you use the package as a context manager, it is closed, and hence flushed,
    when the ``with`` block finishes normally. If the ``with`` block raises
    an exception, the changes are discarded so that you do not end up with
    a half-modified package.
    """

    def __init__(
        self,
        path: Optional[pathlib.Path],
        stream: Optional[BinaryIO],
        content: _Content,
    ) -> None:
        """
        Initialize with the given values.

        Use :py:class:`Packaging` instead of calling the constructor directly.
        """
        super().__init__(path=path, content=content)
        self._stream = stream

        # NOTE (mristin):
        # We track the modifications so that closing an unmodified package does
        # not rewrite the file. Any call to a modifying method marks the package
        # as modified, even if the call did not effectively change anything.
        self._modified = False
        self._closed = False

    def put_part(self, uri: str, content_type: str, content: bytes) -> Part:
        """
        Write the ``content`` to the package as a part with ``content_type``.

        This method needs to be used to put *any* content into the package.
        You have to introduce the relationships yourself by calling, *e.g.*,
        :py:meth:`make_spec` or :py:meth:`relate_supplementary_to_spec`.

        If a part with an equivalent URI already exists, its content and content
        type are overwritten, and its relationships are kept. Be careful not to
        unintentionally overwrite existing parts which are already related to
        others (*e.g.*, do not overwrite a spec with a supplementary material).

        :return: the written part
        """
        assert is_valid_part_uri(uri), f"Invalid part URI: {uri!r}"

        self._modified = True

        key = _key(uri)
        part = self._content.part_by_key.get(key, None)
        if part is not None:
            # NOTE (mristin):
            # We update the part in place so that the references held by the caller
            # remain valid.
            part._content_type = content_type
            part._content = bytes(content)
            return part

        part = Part(uri=uri, content_type=content_type, content=bytes(content))
        self._content.part_by_key[key] = part

        assert self.find_part(uri) is part, "The part should be in the package."
        return part

    def put_part_from_stream(
        self, uri: str, content_type: str, stream: BinaryIO
    ) -> Part:
        """
        Write the content of ``stream`` to the package as a part.

        The ``stream`` is read from its current position till its end.
        See :py:meth:`put_part` for more details.

        :return: the written part
        """
        return self.put_part(uri, content_type, stream.read())

    def delete_part(self, part: Part) -> None:
        """
        Remove the ``part`` from the package.

        All the relationships from and to the part are removed as well so that
        the package stays consistent.
        """
        assert self.find_part(part.uri) is part, "The part must belong to the package."
        assert _key(part.uri) != _key(
            self._content.origin_uri
        ), "The origin part must not be deleted."

        self._modified = True

        del self._content.part_by_key[_key(part.uri)]
        self._content.remove_all_relationships_of(part.uri)

        assert self.find_part(part.uri) is None, "The part should not exist anymore."

    def make_spec(self, part: Part) -> Part:
        """
        Relate the ``part`` to the package origin as a spec.

        If the part is already a spec, nothing happens.

        :return: the same unchanged ``part``, so that you can chain the calls
        """
        assert self.find_part(part.uri) is part, "The part must belong to the package."

        self._modified = True

        if not self.is_spec(part):
            self._content.add_relationship(
                self._content.origin_uri, part.uri, RELATION_TYPE_AASX_SPEC
            )

        assert self.is_spec(part), "The part fulfills the spec property."
        if __debug__ and _SLOW_CHECKS:
            assert any(spec is part for spec in self.specs()), "Spec must be listed."

        return part

    def unmake_spec(self, part: Part) -> Part:
        """
        Remove the relationship from the package origin to the ``part`` as a spec.

        The part itself is kept. Call :py:meth:`delete_part` if you want to remove
        it as well.

        :return: the same unchanged ``part``, so that you can chain the calls
        """
        assert self.is_spec(part), "The part fulfills the spec property."

        if __debug__ and _SLOW_CHECKS:
            old_spec_key_set = {_key(spec.uri) for spec in self.specs()}

        self._modified = True

        self._content.remove_relationships(
            self._content.origin_uri, part.uri, RELATION_TYPE_AASX_SPEC
        )

        assert not self.is_spec(part), "The spec must not be listed in the specs."
        if __debug__ and _SLOW_CHECKS:
            assert {_key(spec.uri) for spec in self.specs()} == old_spec_key_set - {
                _key(part.uri)
            }, "No other spec has been removed."

        return part

    def relate_supplementary_to_spec(self, supplementary: Part, spec: Part) -> Part:
        """
        Relate the ``supplementary`` to the ``spec`` as a supplementary material.

        If the relationship already exists, nothing happens.

        :return:
            the same unchanged ``supplementary``, so that you can chain the calls
        """
        assert self.is_spec(spec), "The spec fulfills the spec property."
        assert (
            self.find_part(supplementary.uri) is supplementary
        ), "The supplementary must belong to the package."

        self._modified = True

        if not self._content.has_relationship(
            spec.uri, supplementary.uri, RELATION_TYPE_AASX_SUPPLEMENTARY
        ):
            self._content.add_relationship(
                spec.uri, supplementary.uri, RELATION_TYPE_AASX_SUPPLEMENTARY
            )

        if __debug__ and _SLOW_CHECKS:
            assert any(
                a_supplementary is supplementary
                for a_supplementary in self.supplementaries_for(spec)
            ), "The supplementary must be listed."

        return supplementary

    def unrelate_supplementary_from_spec(self, supplementary: Part, spec: Part) -> Part:
        """
        Remove the supplementary relationship between ``supplementary`` and ``spec``.

        If the relationship has not been established, nothing happens.
        The part itself is kept. Call :py:meth:`delete_part` if you want to remove
        it as well.

        :return:
            the same unchanged ``supplementary``, so that you can chain the calls
        """
        assert self.is_spec(spec), "The spec fulfills the spec property."

        self._modified = True

        self._content.remove_relationships(
            spec.uri, supplementary.uri, RELATION_TYPE_AASX_SUPPLEMENTARY
        )

        assert not self._content.has_relationship(
            spec.uri, supplementary.uri, RELATION_TYPE_AASX_SUPPLEMENTARY
        ), "The supplementary must not be related to the spec anymore."

        return supplementary

    def set_thumbnail(self, part: Part) -> Part:
        """
        Relate the ``part`` to the package root as the thumbnail.

        If there is a thumbnail already, its relationship is replaced. The previous
        thumbnail part itself is kept. Call :py:meth:`delete_part` if you want
        to remove it as well.

        :return: the same unchanged ``part``, so that you can chain the calls
        """
        assert self.find_part(part.uri) is part, "The part must belong to the package."

        self._modified = True

        self._content.remove_relationships(_PACKAGE_ROOT, None, RELATION_TYPE_THUMBNAIL)
        self._content.add_relationship(_PACKAGE_ROOT, part.uri, RELATION_TYPE_THUMBNAIL)

        assert self.thumbnail() is part, "The thumbnail must point to the part."
        return part

    def unset_thumbnail(self) -> None:
        """
        Remove the relationship from the package root to the thumbnail, if any.

        The thumbnail part itself is kept. Call :py:meth:`delete_part` if you want
        to remove it as well.
        """
        self._modified = True

        self._content.remove_relationships(_PACKAGE_ROOT, None, RELATION_TYPE_THUMBNAIL)

        assert self.thumbnail() is None, "The thumbnail must not exist any more."

    def flush(self) -> None:
        """
        Write the package to the underlying file or stream.

        The stream is overwritten from its beginning and truncated afterwards.

        The package is written even if it has not been modified.
        """
        data = _serialize_package(self._content)

        if self._path is not None:
            self._path.write_bytes(data)
        elif self._stream is not None:
            self._stream.seek(0)
            self._stream.write(data)
            self._stream.truncate()
            self._stream.flush()

        self._modified = False

        assert not self._modified, "No changes are pending after the flush."

    def close(self) -> None:
        """
        Flush the package if it has been modified, and close it.

        Closing the package more than once has no effect.

        The streams which you passed in are not closed, as they are owned by you.

        :raise: :py:class:`OSError` if the file or the stream can not be written
        """
        if self._closed:
            return

        if self._modified:
            self.flush()

        self._closed = True

        assert not self._modified, "No changes are pending after the close."

    def discard(self) -> None:
        """
        Close the package *without* flushing it.

        The changes which have not been flushed yet are lost. Closing the package
        afterwards has no effect.
        """
        self._closed = True

    def __enter__(self) -> "PackageReadWrite":
        """Return the package itself as the context."""
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        """Close the package, or discard the changes if an exception was raised."""
        if exc_type is None:
            self.close()
        else:
            self.discard()


def _assert_empty(package: PackageRead) -> None:
    """Assert that the ``package`` has been newly created."""
    assert len(package.specs()) == 0, "Specs must be empty in a new package."
    assert (
        len(package.supplementary_relationships()) == 0
    ), "There must be no supplementary relationships in a new package."
    assert package.thumbnail() is None, "There must be no thumbnail in a new package."


class Packaging:
    """Open and create AAS packages."""

    def create(self, path: Union[str, "os.PathLike[str]"]) -> PackageReadWrite:
        """
        Create an empty AAS package at the given ``path``.

        If the file already exists, it is overwritten. The empty package is
        immediately written to the file.

        :raise: :py:class:`OSError` if the file can not be written
        """
        result = PackageReadWrite(
            path=pathlib.Path(path), stream=None, content=_new_content()
        )
        result.flush()

        if __debug__ and _SLOW_CHECKS:
            _assert_empty(result)

        return result

    def create_in_stream(self, stream: BinaryIO) -> PackageReadWrite:
        """
        Create an empty AAS package in the given ``stream``.

        The ``stream`` needs to be writable and seekable. The empty package is
        immediately written to it.

        The ``stream`` is not closed by the package; you need to close it yourself.

        :raise: :py:class:`OSError` if the stream can not be written or sought
        """
        result = PackageReadWrite(path=None, stream=stream, content=_new_content())
        result.flush()

        if __debug__ and _SLOW_CHECKS:
            _assert_empty(result)

        return result

    def open_read(self, path: Union[str, "os.PathLike[str]"]) -> PackageRead:
        """
        Open the package at ``path`` for reading.

        :raise:
            :py:class:`OSError` if the file can not be read, and
            :py:class:`InvalidPackageError` if the file is not a valid package
        """
        pth = pathlib.Path(path)
        return PackageRead(path=pth, content=_parse_package(pth.read_bytes()))

    def open_read_from_stream(self, stream: BinaryIO) -> PackageRead:
        """
        Open the package in ``stream`` for reading.

        The whole ``stream`` is read in memory. If the stream is seekable, it is
        read from its beginning. The ``stream`` is not closed.

        :raise: :py:class:`InvalidPackageError` if the stream is not a valid package
        """
        return PackageRead(path=None, content=_parse_package(_read_stream(stream)))

    def open_read_write(self, path: Union[str, "os.PathLike[str]"]) -> PackageReadWrite:
        """
        Open the package at ``path`` for reading and writing.

        :raise:
            :py:class:`OSError` if the file can not be read, and
            :py:class:`InvalidPackageError` if the file is not a valid package
        """
        pth = pathlib.Path(path)
        return PackageReadWrite(
            path=pth, stream=None, content=_parse_package(pth.read_bytes())
        )

    def open_read_write_from_stream(self, stream: BinaryIO) -> PackageReadWrite:
        """
        Open the package in ``stream`` for reading and writing.

        The whole ``stream`` is read in memory. The ``stream`` needs to be
        seekable and writable so that :py:meth:`PackageReadWrite.flush` can
        overwrite it. The ``stream`` is not closed.

        :raise: :py:class:`InvalidPackageError` if the stream is not a valid package
        """
        return PackageReadWrite(
            path=None, stream=stream, content=_parse_package(_read_stream(stream))
        )
