#!/usr/bin/env bash
#
# Regenerate the reference output that tests/compare.py checks the autocoder against.
#
# Two independent references are produced from tests/fixtures/Ref.fpp:
#
#  - tests/reference/fpp-to-cpp/  the real F Prime autocoder's output for the fixture. This is the
#                                 authority on the C++ signatures the generated component must
#                                 override (handlers, command handlers, parameter getters).
#  - tests/reference/legacy/      the output of the pre-port autocoder, driven by fpp-to-json and
#                                 fprime-python-model. This is the authority on *what* has to be
#                                 generated, and is compared semantically -- the ported autocoder
#                                 lays its files out differently because fprime-cpp-codegen owns
#                                 the formatting.
#
# Both need the fpp JVM tools and fprime-python-model, which the port itself no longer depends on,
# so this script is developer-only and its output is checked in. It needs:
#
#   FPRIME=<path to an fprime checkout>          the framework FPP the fixture imports
#   LEGACY_VENV=<path to a venv>                 with fprime-python-model and the pre-port
#                                                fprime-python installed (provides fpp-to-json,
#                                                fpp-to-cpp and fprime-python-ac)
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "${HERE}/.." && pwd)"

FPRIME="${FPRIME:-}"
LEGACY_VENV="${LEGACY_VENV:-${REPO}/.venv-old}"

if [[ -z "${FPRIME}" || ! -d "${FPRIME}" ]]; then
    echo "[ERROR] set FPRIME to an fprime checkout" >&2
    exit 1
fi
for tool in fpp-to-json fpp-to-cpp fprime-python-ac; do
    if [[ ! -x "${LEGACY_VENV}/bin/${tool}" ]]; then
        echo "[ERROR] ${LEGACY_VENV}/bin/${tool} not found; see the header of this script" >&2
        exit 1
    fi
done

# The framework FPP closure the fixture needs. In a real build this list comes from
# fpp-depend, via the module's fpp-cache/stdout.txt.
BUILD_DIR="${FPRIME}/build-fprime-automatic-native"
CONFIG="${BUILD_DIR}/F-Prime/default/config"
PLATFORM="${BUILD_DIR}/cmake/platform/unix/Platform"
IMPORTS=(
    "${FPRIME}/Fw/Cmd/Cmd.fpp"
    "${FPRIME}/Fw/Log/Log.fpp"
    "${FPRIME}/Fw/Time/Time.fpp"
    "${FPRIME}/Fw/Tlm/Tlm.fpp"
    "${FPRIME}/Fw/Prm/Prm.fpp"
    "${FPRIME}/Svc/Sched/Sched.fpp"
    "${FPRIME}/Svc/Ping/Ping.fpp"
    "${CONFIG}/FpConfig.fpp"
    "${PLATFORM}/PlatformTypes.fpp"
)
for import in "${IMPORTS[@]}"; do
    if [[ ! -f "${import}" ]]; then
        echo "[ERROR] missing framework FPP ${import}; configure a native fprime build first" >&2
        exit 1
    fi
done
printf '%s\n' "${IMPORTS[@]}" > "${HERE}/fixtures/imports.txt"

# Three constructs in the fixture crash the legacy autocoder, so they are stripped for the legacy
# reference only. Each is a bug the port fixes, and each is recorded in tests/compare.py's
# KNOWN_BINDING_DIFFERENCES so that the resulting difference is accounted for rather than ignored.
"${LEGACY_VENV}/bin/python" - "${HERE}" <<'PYEOF'
import sys
from pathlib import Path

here = Path(sys.argv[1])
source = (here / "fixtures" / "Ref.fpp").read_text()
dropped = source
for construct in (
    # fprime-python-model reports a fatal event's severity as 'FATAL' where the legacy severity map
    # expects 'fatal', so the lookup raises KeyError
    '    @ A fatal\n    event BAD severity fatal format "bad"\n',
    # the legacy severity map has no 'command' entry at all
    '    @ A command-severity event\n    event CMD_EVENT severity command format "cmd"\n',
    # the legacy port filter reads `.kind` off every port node, which an internal port does not have
    '    @ An internal port, dispatched off this component\'s own queue\n'
    '    internal port deferWork(count: U32, label: string size 12)\n',
):
    if construct not in dropped:
        raise SystemExit(f"fixture no longer contains:\n{construct}")
    dropped = dropped.replace(construct, "")
(here / "fixtures" / "RefGolden.fpp").write_text(dropped)
PYEOF

echo "[1/3] fpp-to-cpp reference"
rm -rf "${HERE}/reference/fpp-to-cpp"
mkdir -p "${HERE}/reference/fpp-to-cpp"
# -p mirrors each output under the build location its model lives in, exactly as an F Prime build does,
# so the result is an include tree the generated code's own include paths resolve against
"${LEGACY_VENV}/bin/fpp-to-cpp" \
    -d "${HERE}/reference/fpp-to-cpp" \
    -p "${REPO},${FPRIME},${BUILD_DIR}" \
    "${IMPORTS[@]}" "${HERE}/fixtures/Ref.fpp"

echo "[2/3] fpp-to-json model for the legacy autocoder"
CACHE="$(mktemp -d)"
trap 'rm -rf "${CACHE}"' EXIT
(cd "${CACHE}" \
    && "${LEGACY_VENV}/bin/fpp-to-json" "${IMPORTS[@]}" "${HERE}/fixtures/RefGolden.fpp")

echo "[3/3] legacy autocoder reference"
rm -rf "${HERE}/reference/legacy"
mkdir -p "${HERE}/reference/legacy"
(cd "${REPO}" && "${LEGACY_VENV}/bin/fprime-python-ac" bindings "${CACHE}" \
    --output-directory "${HERE}/reference/legacy" \
    --prefixes "${REPO}" "${FPRIME}" \
    --translation-units tests/fixtures/RefGolden.fpp >/dev/null)
(cd "${REPO}" && "${LEGACY_VENV}/bin/fprime-python-ac" initialization \
    --output-directory "${HERE}/reference/legacy" \
    --json-files "${HERE}"/reference/legacy/*.json \
    --header-files $(cd "${HERE}/reference/legacy" \
        && ls *BindingAc.hpp | sed 's|^|tests/fixtures/|') >/dev/null)

echo "done: $(ls "${HERE}/reference/legacy" | wc -l | tr -d ' ') legacy files, \
$(ls "${HERE}/reference/fpp-to-cpp" | wc -l | tr -d ' ') fpp-to-cpp files"
