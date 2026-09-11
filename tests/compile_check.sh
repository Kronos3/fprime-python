#!/usr/bin/env bash
#
# Compile the C++ the autocoder generates for tests/fixtures/Ref.fpp.
#
# This is the check that the generated code is real C++ that agrees with F Prime's own autocoded output:
# the generated component implementation has to override the base class's pure virtuals exactly, and the
# generated bindings have to name members that exist. Only compilation is attempted, not linking, so no
# F Prime libraries have to be built -- but the framework's headers and pybind11 are needed:
#
#   FPRIME=<path to a configured fprime checkout>   with a native build directory
#   PYTHON=<interpreter>                            with pybind11 installed (default: ../.venv/bin/python)
#
# Run tests/regenerate_reference.sh first.
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "${HERE}/.." && pwd)"

FPRIME="${FPRIME:-}"
PYTHON="${PYTHON:-${REPO}/.venv/bin/python}"
BUILD="${FPRIME}/build-fprime-automatic-native"
REFERENCE="${HERE}/reference/fpp-to-cpp"

if [[ -z "${FPRIME}" || ! -d "${BUILD}" ]]; then
    echo "[ERROR] set FPRIME to a checkout with a build-fprime-automatic-native directory" >&2
    exit 1
fi
if [[ ! -d "${REFERENCE}" ]]; then
    echo "[ERROR] ${REFERENCE} missing; run tests/regenerate_reference.sh" >&2
    exit 1
fi

COMPILER="$(command -v clang++ || command -v g++ || command -v c++)"
PYBIND11_INCLUDE="$("${PYTHON}" -c 'import pybind11; print(pybind11.get_include())')"
PYTHON_INCLUDE="$("${PYTHON}" -c "import sysconfig; print(sysconfig.get_paths()['include'])")"

WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT
STAGE="${WORK}/stage"
mkdir -p "${STAGE}/tests/fixtures"

# The generated code includes its neighbours by their path relative to a build location, so the model and
# F Prime's autocoded headers for it are laid out under that path in a staging tree. fpp-to-cpp writes its
# output flat, so anything the fixture pulls in from the framework that the fprime build has not already
# generated is placed at the path its includes name.
cp "${REFERENCE}"/*.hpp "${REFERENCE}"/*.h "${STAGE}/tests/fixtures/"
cp "${HERE}/fixtures/Ref.fpp" "${STAGE}/tests/fixtures/"
"${PYTHON}" - "${REFERENCE}" "${STAGE}" "${BUILD}" "${BUILD}/F-Prime" "${FPRIME}" <<'PYEOF'
import re
import shutil
import sys
from pathlib import Path

reference, stage, *roots = (Path(argument) for argument in sys.argv[1:])
available = {path.name: path for path in reference.iterdir()}
wanted = set()
for header in reference.glob("*.hpp"):
    wanted.update(re.findall(r'#include "([^"]+)"', header.read_text()))
for include in sorted(wanted):
    include_path = Path(include)
    if any((root / include).is_file() for root in roots) or (stage / include).is_file():
        continue
    source = available.get(include_path.name)
    if source is None:
        continue
    (stage / include_path.parent).mkdir(parents=True, exist_ok=True)
    shutil.copy(source, stage / include_path)
PYEOF

# Headers a project writes by hand. The topology includes the implementation of every instance it holds,
# and PlainOld is deliberately not implemented in Python, so it has none generated for it.
cat > "${STAGE}/tests/fixtures/PlainOld.hpp" <<'EOF'
#ifndef STUB_PLAINOLD_HPP
#define STUB_PLAINOLD_HPP
#include "tests/fixtures/PlainOldComponentAc.hpp"
namespace Ref {
class PlainOld : public PlainOldComponentBase {
  public:
    explicit PlainOld(const char* name) : PlainOldComponentBase(name) {}
    void schedIn_handler(FwIndexType portNum, U32 context) override {}
};
}  // namespace Ref
#endif
EOF
# An FPP abstract type is by definition a C++ type the project supplies itself
cat > "${STAGE}/tests/fixtures/PyOpaque.hpp" <<'EOF'
#ifndef STUB_PYOPAQUE_HPP
#define STUB_PYOPAQUE_HPP
#include "Fw/FPrimeBasicTypes.hpp"
#include "Fw/Types/Serializable.hpp"
namespace Ref {
class PyOpaque final : public Fw::Serializable {
  public:
    enum { SERIALIZED_SIZE = sizeof(U32) };
    Fw::SerializeStatus serializeTo(Fw::SerialBufferBase& buffer,
                                    Fw::Endianness mode = Fw::Endianness::BIG) const override {
        return buffer.serializeFrom(m_value, mode);
    }
    Fw::SerializeStatus deserializeFrom(Fw::SerialBufferBase& buffer,
                                        Fw::Endianness mode = Fw::Endianness::BIG) override {
        return buffer.deserializeTo(m_value, mode);
    }
  private:
    U32 m_value = 0;
};
}  // namespace Ref
#endif
EOF
cat > "${STAGE}/tests/fixtures/PyTopologyTopologyDefs.hpp" <<'EOF'
#ifndef STUB_PYTOPOLOGYTOPOLOGYDEFS_HPP
#define STUB_PYTOPOLOGYTOPOLOGYDEFS_HPP
namespace Ref {
struct TopologyState {};
}  // namespace Ref
#endif
EOF

echo "[1/2] generating"
"${PYTHON}" -m fprime_python.autocode bindings "${WORK}" \
    --output-directory "${STAGE}/tests/fixtures" \
    --prefixes "${STAGE}" \
    --translation-units "${STAGE}/tests/fixtures/Ref.fpp" \
    --imports $(cat "${HERE}/fixtures/imports.txt") >/dev/null
"${PYTHON}" -m fprime_python.autocode initialization \
    --output-directory "${STAGE}/tests/fixtures" \
    --json-files "${STAGE}"/tests/fixtures/*Binding.json \
    --header-files $(cd "${STAGE}" && ls tests/fixtures/*BindingAc.hpp) >/dev/null

echo "[2/2] compiling"
# The same include set and language level F Prime compiles itself with
INCLUDES=(
    -I"${STAGE}"
    -I"${REPO}"
    -I"${FPRIME}"
    -I"${BUILD}"
    -I"${BUILD}/F-Prime"
    -I"${BUILD}/F-Prime/default/config/.."
    -I"${BUILD}/cmake/platform/unix/Platform/.."
    -I"${PYBIND11_INCLUDE}"
    -I"${PYTHON_INCLUDE}"
)
FAILED=0
for source in "${STAGE}"/tests/fixtures/Py*.cpp "${STAGE}"/tests/fixtures/fprime_init.cpp; do
    case "$(basename "${source}")" in
        *ComponentAc.cpp|*SerializableAc.cpp|*ArrayAc.cpp|*EnumAc.cpp|*PortAc.cpp|*TopologyAc.cpp) continue ;;
    esac
    if ! "${COMPILER}" -std=c++14 -fsyntax-only -Wall -Wextra -Wno-unused-parameter \
            "${INCLUDES[@]}" "${source}" 2>"${WORK}/errors.txt"; then
        echo "FAIL $(basename "${source}")"
        sed 's/^/    /' "${WORK}/errors.txt" | head -30
        FAILED=1
    else
        echo "ok   $(basename "${source}")"
    fi
done
exit "${FAILED}"
