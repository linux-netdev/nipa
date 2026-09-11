# SPDX-License-Identifier: GPL-2.0
ARG BASE_PREFIX="ghcr.io/linux-netdev"
ARG BASE_TAG="latest"
FROM ${BASE_PREFIX}/nipa-base:${BASE_TAG}

LABEL name=nipa-build

RUN sudo dnf install -y \
		clang llvm lld \
		b4 ruff pylint yamllint jsonschema shellcheck \
		dwarves libasan libasan-static glibc-devel.i686 \
		rustc \
		perl-Git lei \
		python-lxml python-yaml python-jsonschema python-GitPython \
		python-pyroute2 python-sphinx python-psutil \
		libcap-devel numactl-devel libmnl-devel \
		coccinelle && \
	sudo dnf clean all

# Sparse
ARG SPARSE_GIT_URL="https://kernel.googlesource.com/pub/scm/devel/sparse/sparse.git"
ARG SPARSE_GIT_SHA="37156835e3d725b6d750f000be33ba3814bb2310" # include a fix for __builtin_strlen
RUN git clone "${SPARSE_GIT_URL}" sparse && \
	cd "sparse" && \
		git checkout "${SPARSE_GIT_SHA}" && \
		make -j"$(nproc)" -l"$(nproc)" && \
		sudo make PREFIX=/usr install && \
		cd .. && \
	rm -rf "sparse"
