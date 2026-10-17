#!/usr/bin/env bash
# Build the SG13G2 toolchain for ngspice: PDK models, OpenVAF-Reloaded, PSP103 OSDI, ngspice 45.
# Linux (tested on Ubuntu 24.04). Every step is skipped when its result already exists.
#   PREFIX=/path ./scripts/setup_sg13g2.sh      (default: <repo>/.tools)
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PREFIX="${PREFIX:-$REPO/.tools}"
JOBS="$(nproc 2>/dev/null || echo 4)"
mkdir -p "$PREFIX" && cd "$PREFIX"
log() { echo "[setup_sg13g2] $*"; }

SUDO=""
if [ "$(id -u)" -ne 0 ]; then SUDO="sudo"; fi
apt_install() {
  local missing=()
  for p in "$@"; do dpkg -s "$p" >/dev/null 2>&1 || missing+=("$p"); done
  if [ ${#missing[@]} -gt 0 ]; then
    log "installing system packages: ${missing[*]}"
    $SUDO apt-get update -qq && $SUDO apt-get install -y -qq "${missing[@]}"
  fi
}

# 1. PDK (only the parts ngspice needs)
if [ ! -f IHP-Open-PDK/ihp-sg13g2/libs.tech/ngspice/models/cornerMOSlv.lib ]; then
  log "downloading IHP-Open-PDK (ngspice models and Verilog-A sources only)"
  rm -rf IHP-Open-PDK
  git clone -q --depth 1 --filter=blob:none --sparse https://github.com/IHP-GmbH/IHP-Open-PDK
  git -C IHP-Open-PDK sparse-checkout set ihp-sg13g2/libs.tech/ngspice ihp-sg13g2/libs.tech/verilog-a
fi
VA="$PREFIX/IHP-Open-PDK/ihp-sg13g2/libs.tech/verilog-a"
OSDI="$PREFIX/IHP-Open-PDK/ihp-sg13g2/libs.tech/ngspice/osdi/psp103.osdi"

# 2. OpenVAF-Reloaded, then compile PSP103 to OSDI
if [ ! -f "$OSDI" ]; then
  OPENVAF="$PREFIX/OpenVAF-Reloaded/target/release/openvaf-r"
  if [ ! -x "$OPENVAF" ]; then
    apt_install git curl build-essential llvm-18-dev clang-18 libclang-18-dev
    if ! command -v cargo >/dev/null 2>&1; then
      [ -f "$HOME/.cargo/env" ] || curl -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal
      # shellcheck disable=SC1091
      source "$HOME/.cargo/env"
    fi
    [ -d OpenVAF-Reloaded ] || git clone -q --depth 1 https://github.com/OpenVAF/OpenVAF-Reloaded
    log "building OpenVAF-Reloaded (about 10 minutes)"
    (cd OpenVAF-Reloaded && LLVM_SYS_181_PREFIX=/usr/lib/llvm-18 cargo build -q --release --features llvm18 --bin openvaf-r)
  fi
  log "compiling PSP103 to OSDI"
  mkdir -p "$(dirname "$OSDI")"
  "$OPENVAF" "$VA/psp103/psp103.va" -o "$OSDI"
fi

# 3. ngspice 45 (Ubuntu's ngspice 42 only loads OSDI v0.3; OpenVAF-Reloaded writes v0.4)
if [ ! -x "$PREFIX/ngspice/bin/ngspice" ]; then
  apt_install git build-essential autoconf automake libtool bison flex libreadline-dev
  [ -d ngspice-src ] || git clone -q --depth 1 --branch ngspice-45.2 https://github.com/imr/ngspice ngspice-src
  log "building ngspice 45.2 (about 5 minutes)"
  (cd ngspice-src && ./autogen.sh >/dev/null && mkdir -p release && cd release \
    && ../configure -q --prefix="$PREFIX/ngspice" --enable-osdi --with-readline=yes --disable-debug --without-x >/dev/null \
    && make -s -j"$JOBS" >/dev/null && make -s install >/dev/null)
fi

log "done"
log "  PDK_ROOT=$PREFIX/IHP-Open-PDK"
log "  NGSPICE=$PREFIX/ngspice/bin/ngspice"
