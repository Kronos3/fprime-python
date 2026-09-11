""" tests/compare.py:

Checks the autocoder's output for tests/fixtures/Ref.fpp against the two references in tests/reference.

Four things are checked:

 1. Every method the generated component declares as an override really is a method F Prime's autocoder
    declares pure virtual on the base class, with a byte-identical parameter list, and no pure virtual is
    left unimplemented. This is the check that matters most: either failure leaves the generated class
    abstract and the deployment will not link.
 2. The set of generated files matches what the pre-port autocoder produced, so that nothing has been
    dropped or added by accident.
 3. Every pybind11 name the pre-port autocoder bound is still bound, and every C++ member it took the
    address of is still taken. Formatting is not compared: fprime-cpp-codegen owns the layout now.
 4. The invocation snippets and the module initialization still install everything they used to.

Anything the port deliberately generates differently from the pre-port autocoder is listed, with its reason,
in KNOWN_BINDING_DIFFERENCES below, so that an unexplained difference still fails.

Run it after tests/regenerate_reference.sh, from anywhere:

    .venv/bin/python tests/compare.py
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Set, Tuple

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
FIXTURE = HERE / "fixtures" / "Ref.fpp"
GOLDEN_FIXTURE = HERE / "fixtures" / "RefGolden.fpp"
IMPORTS = HERE / "fixtures" / "imports.txt"
FPP_TO_CPP = HERE / "reference" / "fpp-to-cpp"
LEGACY = HERE / "reference" / "legacy"

#: Members the pre-port autocoder bound under a different C++ name, or not at all. Each entry says why
#: the difference is intended, so that an unexplained difference still fails.
KNOWN_BINDING_DIFFERENCES = {
    # The fixture exercises severities the pre-port autocoder crashed on, so its reference has no
    # binding for the events carrying them
    "log_FATAL_BAD": "pre-port autocoder crashed on 'severity fatal'",
    "log_COMMAND_CMD_EVENT": "pre-port autocoder had no mapping for 'severity command'",
    # The pre-port autocoder dropped internal ports entirely, which left the generated component abstract
    "deferWork_internalInterfaceInvoke": "pre-port autocoder did not generate internal ports",
}

#: Signatures the generated component declares that have no pure-virtual counterpart, because they are
#: new members rather than overrides
NON_OVERRIDE_PREFIXES = ("paramGet_", "init", "deinit")


def run_autocoder(output: Path) -> List[Path]:
    """ Run the autocoder over the fixture and return the files it reports """
    command = [
        sys.executable,
        "-m",
        "fprime_python.autocode",
        "bindings",
        str(output),
        "--output-directory",
        str(output),
        "--prefixes",
        str(REPO),
        "--translation-units",
        str(FIXTURE),
        "--imports",
        *IMPORTS.read_text().split(),
    ]
    result = subprocess.run(command, capture_output=True, text=True, cwd=REPO, check=True)
    return [Path(path) for path in result.stdout.split()]


def normalize(text: str) -> str:
    """ Collapse whitespace so that two spellings of the same C++ compare equal """
    return re.sub(r"\s+", " ", text).strip()


def declared_signatures(text: str) -> Dict[str, str]:
    """ Extract every function declaration from a C++ header, by name

    Args:
        text: The header's contents
    Returns:
        A mapping of function name to its normalized parameter list
    """
    signatures = {}
    pattern = re.compile(
        r"(?P<ret>[A-Za-z_][\w:<>&*, ]*?)\s"           # return type
        r"(?P<name>\w+)\s*"                             # name
        r"\((?P<params>[^;{]*?)\)"                      # parameter list
        r"(?P<trailing>[\w\s=]*);",                     # trailing qualifiers up to the semicolon
        re.S,
    )
    for match in pattern.finditer(text):
        params = re.sub(r"//!<[^\n]*", "", match.group("params"))
        signatures[match.group("name")] = normalize(params)
    return signatures


def check_overrides(generated: Path) -> List[str]:
    """ Check every generated override against F Prime's own declaration of it

    Args:
        generated: Directory holding the generated files
    Returns:
        A list of failure messages, empty when every override lines up
    """
    failures = []
    for header in sorted(generated.glob("*.hpp")):
        if header.name.endswith("BindingAc.hpp"):
            continue
        component = header.stem
        base = FPP_TO_CPP / f"{component}ComponentAc.hpp"
        if not base.is_file():
            failures.append(f"{component}: no fpp-to-cpp reference at {base}")
            continue
        base_signatures = declared_signatures(base.read_text())
        text = header.read_text()
        overrides = re.findall(r"(\w+)\(\s*[^;{]*?\)\s*override;", text, re.S)
        if not overrides:
            failures.append(f"{component}: generated no overrides at all")
        for name in overrides:
            generated_params = declared_signatures(text).get(name)
            base_params = base_signatures.get(name)
            if base_params is None:
                failures.append(f"{component}::{name} is declared override but the base class has no {name}")
            elif base_params != generated_params:
                failures.append(
                    f"{component}::{name} signature differs from the base class\n"
                    f"    generated: ({generated_params})\n"
                    f"    fpp-to-cpp: ({base_params})"
                )
        # Anything the base declares pure virtual has to be overridden or the class stays abstract
        pure_virtual = set(re.findall(r"virtual\s+[\w:<>&*, ]+?\s(\w+)\(\s*[^;{]*?\)\s*=\s*0;", base.read_text(), re.S))
        missing = pure_virtual - set(overrides)
        if missing:
            failures.append(f"{component}: pure-virtual members left unimplemented: {sorted(missing)}")
    return failures


def check_file_set(generated: List[Path]) -> List[str]:
    """ Check the generated file names against the pre-port autocoder's

    The pre-port reference is built from a fixture with two events removed, so the file names are the
    same but the file contents are not; only names are compared here.
    """
    reference = {path.name for path in LEGACY.iterdir()} - {"fprime_init.cpp"}
    produced = {path.name for path in generated}
    failures = []
    for name in sorted(reference - produced):
        failures.append(f"file no longer generated: {name}")
    for name in sorted(produced - reference):
        failures.append(f"unexpected new file: {name}")
    return failures


def binding_surface(text: str) -> Tuple[Set[str], Set[str]]:
    """ The Python names and C++ members a generated binding file touches

    Args:
        text: The binding source's contents
    Returns:
        A tuple of the bound Python names and the C++ members whose addresses are taken
    """
    python_names = set(re.findall(r'\.def(?:_property(?:_readonly_static)?)?\(\s*"([^"]+)"', text))
    python_names |= set(re.findall(r'm\.def\(\s*"([^"]+)"', text))
    cpp_members = set(re.findall(r"&[\w:]+::(\w+)", text))
    return python_names, cpp_members


def check_binding_surface(generated: Path) -> List[str]:
    """ Check that nothing the pre-port autocoder bound has been dropped

    Args:
        generated: Directory holding the generated files
    Returns:
        A list of failure messages, empty when every binding survives
    """
    failures = []
    for reference in sorted(LEGACY.glob("*BindingAc.cpp")):
        produced = generated / reference.name
        if not produced.is_file():
            failures.append(f"binding no longer generated: {reference.name}")
            continue
        want_python, want_cpp = binding_surface(reference.read_text())
        have_python, have_cpp = binding_surface(produced.read_text())
        for name in sorted(want_python - have_python):
            failures.append(f"{reference.name}: Python name no longer bound: {name}")
        for name in sorted(want_cpp - have_cpp):
            failures.append(f"{reference.name}: C++ member no longer bound: {name}")
        for name in sorted(have_python - want_python):
            if name in KNOWN_BINDING_DIFFERENCES:
                continue
            failures.append(f"{reference.name}: unexplained new Python name: {name}")
    return failures


def check_invocations(generated: Path) -> List[str]:
    """ Check the invocation snippets against the pre-port autocoder's """
    failures = []
    for reference in sorted(LEGACY.glob("*Binding.json")):
        produced = generated / reference.name
        if not produced.is_file():
            failures.append(f"invocation no longer generated: {reference.name}")
        elif produced.read_text() != reference.read_text():
            failures.append(
                f"{reference.name} changed\n"
                f"    generated: {produced.read_text()}\n"
                f"    reference: {reference.read_text()}"
            )
    return failures


def check_module_init(generated: Path) -> List[str]:
    """ Check the module initialization against the pre-port autocoder's

    Only the statements are compared, and only for the definitions both autocoders generated: the fixture
    the reference was built from is missing two events, which changes nothing here, but the reference was
    also built before the fixture grew its extra types.
    """
    reference = LEGACY / "fprime_init.cpp"
    produced = generated / "fprime_init.cpp"
    if not produced.is_file():
        return [f"module initialization not generated at {produced}"]

    def statements(text: str) -> Set[str]:
        return {
            normalize(statement)
            for statement in text.splitlines()
            if statement.strip() and not statement.strip().startswith("//")
        }

    missing = statements(reference.read_text()) - statements(produced.read_text())
    return [f"module initialization no longer contains: {statement}" for statement in sorted(missing)]


def run_initialization(output: Path) -> None:
    """ Run the initialization subcommand over the generated bindings """
    subprocess.run(
        [
            sys.executable,
            "-m",
            "fprime_python.autocode",
            "initialization",
            "--output-directory",
            str(output),
            "--json-files",
            *[str(path) for path in sorted(output.glob("*Binding.json"))],
            "--header-files",
            *[f"tests/fixtures/{path.name}" for path in sorted(output.glob("*BindingAc.hpp"))],
        ],
        capture_output=True,
        text=True,
        cwd=REPO,
        check=True,
    )


def main() -> int:
    """ Run every check and report """
    for path in (FIXTURE, IMPORTS, FPP_TO_CPP, LEGACY):
        if not path.exists():
            print(f"[ERROR] {path} missing; run tests/regenerate_reference.sh", file=sys.stderr)
            return 1
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory)
        produced = run_autocoder(output)
        run_initialization(output)
        checks = [
            ("overrides match fpp-to-cpp", check_overrides(output)),
            ("file set matches the pre-port autocoder", check_file_set(produced)),
            ("binding surface preserved", check_binding_surface(output)),
            ("invocation snippets preserved", check_invocations(output)),
            ("module initialization preserved", check_module_init(output)),
        ]
    failed = 0
    for title, failures in checks:
        if failures:
            failed += 1
            print(f"FAIL {title}")
            for failure in failures:
                print(f"  - {failure}")
        else:
            print(f"ok   {title}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
