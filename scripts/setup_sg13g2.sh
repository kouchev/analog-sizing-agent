#!/usr/bin/env bash
# Set up IHP SG13G2 for ngspice: PDK models, OpenVAF-Reloaded, PSP103 OSDI, ngspice >= 45.
# Tested on Ubuntu 24.04. Installs into $HOME/eda by default.
set -euo pipefail
PREFIX="${PREFIX:-$HOME/eda}"
mkdir -p "$PREFIX" && cd "$PREFIX"

# 1. PDK (only the parts ngspice needs)
if [ ! -d IHP-Open-PDK ]; then
  git clone --depth 1 --filter=blob:none --sparse https://github.com/IHP-GmbH/IHP-Open-PDK
  git -C IHP-Open-PDK sparse-checkout set ihp-sg13g2/libs.tech/ngspice ihp-sg13g2/libs.tech/verilog-a
fi

# 2. OpenVAF-Reloaded (compiles the PSP103 Verilog-A model), needs Rust and LLVM 18
sudo apt-get install -y llvm-18-dev clang-18 libclang-18-dev
if [ ! -x OpenVAF-Reloaded/target/release/openvaf-r ]; then
  git clone --depth 1 https://github.com/OpenVAF/OpenVAF-Reloaded
  (cd OpenVAF-Reloaded && LLVM_SYS_181_PREFIX=/usr/lib/llvm-18 cargo build --release --features llvm18 --bin openvaf-r)
fi
VA="$PREFIX/IHP-Open-PDK/ihp-sg13g2/libs.tech/verilog-a"
mkdir -p "$VA/../ngspice/osdi"
"$PREFIX/OpenVAF-Reloaded/target/release/openvaf-r" "$VA/psp103/psp103.va" -o "$VA/../ngspice/osdi/psp103.osdi"

# 3. ngspice 45 (ngspice 42 from Ubuntu only loads OSDI v0.3; OpenVAF-Reloaded writes v0.4)
sudo apt-get install -y autoconf automake libtool bison flex libreadline-dev
if [ ! -x "$PREFIX/ngspice/bin/ngspice" ]; then
  git clone --depth 1 --branch ngspice-45.2 https://github.com/imr/ngspice ngspice-src
  (cd ngspice-src && ./autogen.sh && mkdir -p release && cd release \
    && ../configure --prefix="$PREFIX/ngspice" --enable-osdi --with-readline=yes --disable-debug --without-x \
    && make -j"$(nproc)" && make install)
fi

echo
echo "Done. Add to your shell:"
echo "  export PDK_ROOT=$PREFIX/IHP-Open-PDK"
echo "  export NGSPICE=$PREFIX/ngspice/bin/ngspice"
