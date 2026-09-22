#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
flexric_dir="$project_root/third_party/flexric"
build_dir="$flexric_dir/build_min"
install_dir="$project_root/.local/flexric"
venv_dir="$project_root/.venv-flexric"
patch_file="$project_root/patches/flexric-minimal.patch"
flexric_commit="ef6d722f22191eea74089966983da1f5ec1fedd4"
flexric_mirror="https://github.com/duranta-project/flexric.git"

missing=()
for command_name in git cmake gcc g++ make swig; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    missing+=("$command_name")
  fi
done
if ((${#missing[@]})); then
  echo "Missing build tools: ${missing[*]}" >&2
  exit 1
fi
if [[ ! -f /usr/include/netinet/sctp.h ]]; then
  echo "Missing libsctp-dev (/usr/include/netinet/sctp.h)." >&2
  exit 1
fi
if [[ ! -x /usr/bin/python3 ]]; then
  echo "/usr/bin/python3 is required." >&2
  exit 1
fi
python_include="$(/usr/bin/python3 -c 'import sysconfig; print(sysconfig.get_paths()["include"])')"
if [[ ! -f "$python_include/Python.h" ]]; then
  echo "Ubuntu python3-dev for /usr/bin/python3 is required." >&2
  exit 1
fi

mkdir -p "$project_root/third_party" "$project_root/.local"
if [[ ! -d "$flexric_dir/.git" ]]; then
  git clone "$flexric_mirror" "$flexric_dir"
  git -C "$flexric_dir" checkout --detach "$flexric_commit"
fi

current_commit="$(git -C "$flexric_dir" rev-parse HEAD)"
if [[ "$current_commit" != "$flexric_commit" ]]; then
  echo "FlexRIC commit mismatch: expected $flexric_commit, found $current_commit" >&2
  echo "Move the existing third_party/flexric directory aside and rerun setup." >&2
  exit 1
fi

if git -C "$flexric_dir" apply --check "$patch_file" >/dev/null 2>&1; then
  git -C "$flexric_dir" apply "$patch_file"
elif git -C "$flexric_dir" apply --reverse --check "$patch_file" >/dev/null 2>&1; then
  echo "FlexRIC minimal patch already applied."
else
  echo "FlexRIC source has changes that conflict with $patch_file" >&2
  exit 1
fi

if [[ ! -x "$venv_dir/bin/python" ]]; then
  /usr/bin/python3 -m venv "$venv_dir"
fi
"$venv_dir/bin/python" -m pip install --quiet --upgrade pip
"$venv_dir/bin/python" -m pip install --quiet -r "$project_root/requirements-flexric.txt"

cmake -S "$flexric_dir" -B "$build_dir" \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX="$install_dir" \
  -DXAPP_MULTILANGUAGE=ON \
  -DXAPP_DB=NONE_XAPP \
  -DUNIT_TEST=FALSE \
  -DPython3_EXECUTABLE=/usr/bin/python3 \
  -DPYTHON_EXECUTABLE=/usr/bin/python3 \
  -DNEAR_RIC_INSTALL=TRUE \
  -DEMU_AGENT_INSTALL=TRUE \
  -DXAPP_C_INSTALL=FALSE

cmake --build "$build_dir" --target \
  mac_sm rlc_sm pdcp_sm slice_sm tc_sm gtp_sm kpm_sm rc_sm \
  nearRT-RIC emu_agent_gnb xapp_sdk -j"$(nproc)"
cmake --install "$build_dir"

PYTHONPATH="$build_dir/examples/xApp/python3" \
  "$venv_dir/bin/python" -c \
  "import xapp_sdk as ric; assert hasattr(ric, 'init_with_paths'); assert hasattr(ric, 'control_mac_sm')"

echo "FlexRIC setup complete: $install_dir"
