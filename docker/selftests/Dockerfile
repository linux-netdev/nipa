# SPDX-License-Identifier: GPL-2.0
ARG BASE_PREFIX="ghcr.io/linux-netdev"
ARG BASE_TAG="latest"
FROM ${BASE_PREFIX}/nipa-base:${BASE_TAG}

LABEL name=nipa-selftests-base

# a common base to run net selftests
RUN sudo dnf install -y \
		busybox kmod qemu-system-x86 systemd virtiofsd \
		clang cmake \
		python3-argcomplete python3-jsonschema python3-pip \
		  python3-pyroute2 python3-requests python3-scapy \
		  python3-yaml \
		iptables-devel libbpf-devel libcap-devel libdb-devel \
		  libmnl-devel libnl3-devel libpcap-devel libselinux-devel \
		  libtirpc-devel mbedtls-devel \
		bpftool bpftrace bridge-utils conntrack dropwatch dwarves \
		  ebtables-legacy ethtool iperf iperf3 iptables-nft \
		  iptables-legacy iptables-utils iputils ipvsadm jq mtools \
		  ndisc6 ngrep netperf netsniff-ng net-tools nftables \
		  nmap-ncat numactl-devel perf pppoe-server procps-ng psmisc \
		  smcrouted socat strace tcpdump teamd traceroute tshark \
		  udevadm \
		&& \
	sudo dnf clean all

# systemd changing the MAC address is racy: it does it too slowly and some tests
# start doing their thing, then systemd comes in and flips the address.
# Disable that by setting MACAddressPolicy=none
COPY systemd-network-macaddresspolicy-none /etc/systemd/network/99-default.link

# use nft by default
RUN sudo alternatives --set iptables /usr/bin/iptables-nft

# net/netfilter/bridge_brouter.sh requires ebtables-legacy
RUN sudo alternatives --set ebtables /usr/bin/ebtables-legacy

# Not worth it to build in isolated containers: build deps still needed for slft

# Needed for net and drivers/net/hw targets
ARG LIBURING_GIT_URL="https://github.com/axboe/liburing.git"
ARG LIBURING_GIT_SHA="liburing-2.14"
RUN git clone "${LIBURING_GIT_URL}" liburing && \
	cd liburing && \
		git checkout "${LIBURING_GIT_SHA}" && \
		./configure && \
		make -j"$(nproc)" -l"$(nproc)" && \
		sudo make install && \
		cd .. && \
	rm -rf liburing

# Needed for drivers/net/netdevsim target
ARG LIBPSAMPLE_GIT_URL="https://github.com/Mellanox/libpsample.git"
ARG LIBPSAMPLE_GIT_SHA="62bb27d9a49424e45191eee81df7ce0d8c74e774"
RUN git clone "${LIBPSAMPLE_GIT_URL}" libpsample && \
	cd libpsample && \
		cmake -DCMAKE_POLICY_VERSION_MINIMUM=3.5 -DCMAKE_INSTALL_PREFIX=/usr . && \
		make -j"$(nproc)" -l"$(nproc)" && \
		sudo make install && \
		cd .. && \
	rm -rf libpsample

# Needed for net/ppp target
ARG XL2TPD_GIT_URL="https://github.com/xelerance/xl2tpd.git"
ARG XL2TPD_GIT_SHA="v1.3.20"
RUN git clone "${XL2TPD_GIT_URL}" xl2tpd && \
	cd xl2tpd && \
		git checkout "${XL2TPD_GIT_SHA}" && \
		make -j"$(nproc)" -l"$(nproc)" && \
		sudo make install && \
		cd .. && \
	rm -rf xl2tpd

# Needed for net target
ARG IPV6TOOLKIT_GIT_URL="https://github.com/fgont/ipv6toolkit.git"
# v2.2 has bad csum: https://github.com/fgont/ipv6toolkit/issues/104
ARG IPV6TOOLKIT_GIT_SHA="367bbe60489a6ae68898b9c81e672b48ad81df43"
RUN git clone "${IPV6TOOLKIT_GIT_URL}" ipv6toolkit && \
	cd ipv6toolkit && \
		git checkout "${IPV6TOOLKIT_GIT_SHA}" && \
		make -j"$(nproc)" -l"$(nproc)" && \
		sudo make install && \
		cd .. && \
	rm -rf ipv6toolkit

# Needed for net/forwarding target
ARG MTOOLS_GIT_URL="https://github.com/troglobit/mtools.git"
ARG MTOOLS_GIT_SHA="v3.2"
RUN git clone "${MTOOLS_GIT_URL}" mtools && \
	cd mtools && \
		git checkout "${MTOOLS_GIT_SHA}" && \
		make -j"$(nproc)" -l"$(nproc)" && \
		sudo make install && \
		cd .. && \
	rm -rf mtools

# Needed for net/packetdrill and net/netfilter targets
ARG PACKETDRILL_GIT_URL="https://github.com/google/packetdrill.git"
ARG PACKETDRILL_GIT_SHA="83f72d3f9085d0e26eb4d206fe4d7cfab5b6d872"
RUN git clone "${PACKETDRILL_GIT_URL}" packetdrill && \
	cd packetdrill/gtests/net/packetdrill && \
		git checkout "${PACKETDRILL_GIT_SHA}" && \
		./configure && \
		make -j"$(nproc)" -l"$(nproc)" && \
		sudo cp packetdrill /usr/local/bin/ && \
		cd .. && \
	rm -rf packetdrill

# Needed for most targets
# Multiple remotes are possible, likely we want next + in-review patches
ARG IPROUTE2_GIT_URL="https://github.com/linux-netdev/iproute2.git"
# ARG IPROUTE2_GIT_URL="https://kernel.googlesource.com/pub/scm/network/iproute2/iproute2-next.git"
# ARG IPROUTE2_GIT_URL="https://kernel.googlesource.com/pub/scm/network/iproute2/iproute2.git"
ARG IPROUTE2_GIT_SHA="3de06c2e2774d0795c7dc2aac148b422e86174c6"
RUN git clone "${IPROUTE2_GIT_URL}" iproute2 && \
	cd iproute2 && \
		git checkout "${IPROUTE2_GIT_SHA}" && \
		./configure --color=auto && \
		make -j"$(nproc)" -l"$(nproc)" && \
		sudo make install && \
		cd .. && \
	rm -rf iproute2

ARG VIRTME_NG_VERSION="1.41"
RUN sudo pip3 install --no-cache-dir \
	virtme-ng=="${VIRTME_NG_VERSION}"

CMD ["bash"]
