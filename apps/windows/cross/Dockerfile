# Cross-builds BeamcamCamera.dll on Linux: clang-cl and lld-link with Microsoft's C runtime and
# Windows SDK, fetched by xwin (accepting Microsoft's license for them). See build.sh.
FROM debian:13
RUN apt-get update \
 && apt-get install -y --no-install-recommends clang lld llvm cmake ninja-build ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*
ARG XWIN=0.10.0
RUN curl -sSfL https://github.com/Jake-Shadle/xwin/releases/download/${XWIN}/xwin-${XWIN}-x86_64-unknown-linux-musl.tar.gz \
    | tar -xz -C /usr/local/bin --strip-components=1 xwin-${XWIN}-x86_64-unknown-linux-musl/xwin \
 && xwin --accept-license --arch x86_64 --cache-dir /tmp/xwin-cache splat --output /xwin \
 && rm -rf /tmp/xwin-cache
# Debian has no clang-cl link; clang acts as clang-cl when called by that name.
RUN ln -s $(ls /usr/lib/llvm-*/bin/clang | head -n1) /usr/bin/clang-cl
RUN for tool in lld-link llvm-rc llvm-mt llvm-lib; do \
      [ -e /usr/bin/$tool ] || ln -s $(ls /usr/lib/llvm-*/bin/$tool | head -n1) /usr/bin/$tool; done
