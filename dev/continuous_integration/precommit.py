#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 aas-package3-python AUTHORS
#
# SPDX-License-Identifier: MIT

"""Run pre-commit checks on the repository."""

import argparse
import enum
import os
import pathlib
import shlex
import subprocess
import sys
from typing import Final, Mapping, Optional, Sequence


class Step(enum.Enum):
    """Enumerate different pre-commit steps."""

    REFORMAT = "reformat"
    SSORT = "ssort"
    MYPY = "mypy"
    RUFF = "ruff"
    TEST = "test"
    DOCTEST = "doctest"
    CHECK_INIT_AND_PYPROJECT_CONSISTENT = "check-init-and-pyproject-consistent"
    REUSE = "reuse"


def call_and_report(
    verb: str,
    cmd: Sequence[str],
    cwd: Optional[pathlib.Path] = None,
    env: Optional[Mapping[str, str]] = None,
) -> int:
    """
    Wrap a subprocess call with the reporting to STDERR if it failed.

    Return 1 if there is an error and 0 otherwise.
    """
    cmd_str = " ".join(shlex.quote(part) for part in cmd)

    if cwd is not None:
        print(f"Executing from {cwd}: {cmd_str}")
    else:
        print(f"Executing: {cmd_str}")

    exit_code = subprocess.call(cmd, cwd=str(cwd) if cwd is not None else None, env=env)

    if exit_code != 0:
        print(
            f"Failed to {verb} with exit code {exit_code}: {cmd_str}", file=sys.stderr
        )

    return exit_code


#: Python sources which we check with black, ssort, mypy and ruff
PYTHON_TARGETS: Final[Sequence[str]] = [
    "aas_package3",
    "dev/continuous_integration",
    "dev/live_tests",
    "dev/tests",
]


def main() -> int:
    """Execute the main routine."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--overwrite",
        help="Try to automatically fix the offending files (e.g., by re-formatting).",
        action="store_true",
    )
    parser.add_argument(
        "--select",
        help=(
            "If set, only the selected steps are executed. "
            "This is practical if some of the steps failed and you want to "
            "fix them in isolation. "
            "The steps are given as a space-separated list of: "
            + " ".join(value.value for value in Step)
        ),
        metavar="",
        nargs="+",
        choices=[value.value for value in Step],
    )
    parser.add_argument(
        "--skip",
        help=(
            "If set, skips the specified steps. "
            "This is practical if some of the steps passed and "
            "you want to fix the remainder in isolation. "
            "The steps are given as a space-separated list of: "
            + " ".join(value.value for value in Step)
        ),
        metavar="",
        nargs="+",
        choices=[value.value for value in Step],
    )

    args = parser.parse_args()

    overwrite = bool(args.overwrite)

    selects = (
        [Step(value) for value in args.select]
        if args.select is not None
        else list(Step)
    )
    skips = [Step(value) for value in args.skip] if args.skip is not None else []

    repo_root = pathlib.Path(os.path.realpath(__file__)).parent.parent.parent

    if Step.REFORMAT in selects and Step.REFORMAT not in skips:
        print("Re-formatting...")
        if overwrite:
            exit_code = call_and_report(
                verb="black",
                cmd=[sys.executable, "-m", "black", *PYTHON_TARGETS, "docs/source"],
                cwd=repo_root,
            )
        else:
            exit_code = call_and_report(
                verb="check with black",
                cmd=[
                    sys.executable,
                    "-m",
                    "black",
                    "--check",
                    *PYTHON_TARGETS,
                    "docs/source",
                ],
                cwd=repo_root,
            )
        if exit_code != 0:
            return 1
    else:
        print("Skipped re-formatting.")

    if Step.SSORT in selects and Step.SSORT not in skips:
        print("Ssort'ing...")

        if overwrite:
            exit_code = call_and_report(
                verb="ssort",
                cmd=[sys.executable, "-m", "ssort", *PYTHON_TARGETS],
                cwd=repo_root,
            )
            if exit_code != 0:
                return 1

            # NOTE (mristin):
            # We have to re-format just after the ssort,
            # see: https://pypi.org/project/ssort/,
            # "We recommend that you reformat using isort and black
            # immediately after running ssort."
            exit_code = call_and_report(
                verb="black after ssort",
                cmd=[sys.executable, "-m", "black", *PYTHON_TARGETS],
                cwd=repo_root,
            )
        else:
            exit_code = call_and_report(
                verb="check with ssort",
                cmd=[
                    sys.executable,
                    "-m",
                    "ssort",
                    "--check",
                    "--diff",
                    *PYTHON_TARGETS,
                ],
                cwd=repo_root,
            )
        if exit_code != 0:
            return 1
    else:
        print("Skipped ssort'ing.")

    if Step.MYPY in selects and Step.MYPY not in skips:
        print("Mypy'ing...")
        exit_code = call_and_report(
            verb="mypy",
            cmd=[
                sys.executable,
                "-m",
                "mypy",
                "--strict",
                "--config-file",
                str(pathlib.Path("dev/continuous_integration") / "mypy.ini"),
                *PYTHON_TARGETS,
            ],
            cwd=repo_root,
        )
        if exit_code != 0:
            return 1
    else:
        print("Skipped mypy'ing.")

    if Step.RUFF in selects and Step.RUFF not in skips:
        print("Ruff'ing...")
        config = pathlib.Path("dev/continuous_integration") / "ruff.toml"

        exit_code = call_and_report(
            verb="ruff",
            cmd=[sys.executable, "-m", "ruff", "check", f"--config={config}"]
            + (["--fix"] if overwrite else [])
            + list(PYTHON_TARGETS),
            cwd=repo_root,
        )
        if exit_code != 0:
            return 1
    else:
        print("Skipped ruff'ing.")

    if Step.TEST in selects and Step.TEST not in skips:
        print("Testing...")
        env = os.environ.copy()
        env["AAS_PACKAGE3_SLOW_CHECKS"] = "1"

        exit_code = call_and_report(
            verb="execute unit tests",
            cmd=[
                sys.executable,
                "-m",
                "coverage",
                "run",
                "--source",
                "aas_package3",
                "-m",
                "unittest",
                "discover",
                "--start-directory",
                "dev/tests",
                "--top-level-directory",
                "dev",
            ],
            cwd=repo_root,
            env=env,
        )
        if exit_code != 0:
            return 1

        exit_code = call_and_report(
            verb="report the coverage",
            cmd=[sys.executable, "-m", "coverage", "report", "--show-missing"],
            cwd=repo_root,
        )
        if exit_code != 0:
            return 1
    else:
        print("Skipped testing.")

    if Step.DOCTEST in selects and Step.DOCTEST not in skips:
        print("Doctest'ing...")

        exit_code = call_and_report(
            verb="doctest",
            cmd=[sys.executable, "-m", "doctest", "aas_package3/__init__.py"],
            cwd=repo_root,
        )
        if exit_code != 0:
            return 1
    else:
        print("Skipped doctest'ing.")

    if (
        Step.CHECK_INIT_AND_PYPROJECT_CONSISTENT in selects
        and Step.CHECK_INIT_AND_PYPROJECT_CONSISTENT not in skips
    ):
        print(
            "Checking that aas_package3/__init__.py and pyproject.toml "
            "are consistent..."
        )
        exit_code = call_and_report(
            verb=(
                "check that aas_package3/__init__.py and pyproject.toml "
                "are consistent"
            ),
            cmd=[
                sys.executable,
                "dev/continuous_integration/check_init_and_pyproject_consistent.py",
            ],
            cwd=repo_root,
        )
        if exit_code != 0:
            return 1
    else:
        print(
            "Skipped checking that aas_package3/__init__.py and "
            "pyproject.toml are consistent."
        )

    if Step.REUSE in selects and Step.REUSE not in skips:
        print("Checking the REUSE compliance...")
        exit_code = call_and_report(
            verb="check the REUSE compliance",
            cmd=[sys.executable, "-m", "reuse", "lint"],
            cwd=repo_root,
        )
        if exit_code != 0:
            return 1
    else:
        print("Skipped checking the REUSE compliance.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
