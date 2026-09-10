# SPDX-License-Identifier: GPL-2.0
FROM fedora:44

LABEL name=nipa-base

RUN echo "install_weak_deps=False" >> /etc/dnf/dnf.conf
RUN dnf install -y \
		bison ccache flex gcc make pigz rsync \
		bc cpio curl diffutils gawk git perl openssl patch which \
		elfutils-libelf-devel ncurses-devel openssl-devel && \
	sudo dnf clean all

# nipa user with "root" rights
RUN useradd -m nipa
RUN usermod -aG wheel nipa
RUN echo 'nipa ALL=(ALL) NOPASSWD:ALL' >> /etc/sudoers
# workaround https://github.com/apptainer/apptainer/issues/2756
RUN chmod 0400 /etc/shadow
USER nipa
WORKDIR /home/nipa

RUN git config --global user.name "Nipa Container" && \
	git config --global user.email "nipa@container"

# CCache
ENV PATH=/usr/lib64/ccache:${PATH}
ENV CCACHE_COMPRESS=true
ENV KBUILD_BUILD_TIMESTAMP="0"
