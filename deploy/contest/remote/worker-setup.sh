#!/bin/bash -xe

# Cocci
# also install ocaml itself
sudo dnf install ocaml-findlib ocaml-findlib-devel
./configure --enable-ocaml --enable-pcre-syntax
make
make install
# explore local installation, ./configure output suggests how

# Let runners use git on NIPA
git config --global --add safe.directory /opt/nipa

sudo dnf install pip meson

sudo dnf install perf bpftrace
sudo dnf install nftables.x86_64
sudo dnf install pixman-devel.x86_64 pixman.x86_64 libgudev.x86_64
sudo dnf install libpcap-devel libpcap cmake
sudo dnf install clang numactl-devel.x86_64
sudo dnf install socat wireshark nmap-ncat.x86_64
sudo dnf install libdaemon-devel libdaemon
sudo dnf install libtool patch
sudo dnf install ninja-build.x86_64 texinfo
sudo dnf install bison flex openssl-devel
sudo dnf install capstone bzip2-devel libssh-devel
sudo dnf install git libmnl-devel
sudo dnf install elfutils-devel elfutils-libs elfutils-libelf elfutils-libelf-devel
sudo dnf install iptables

# NIPA setup
git clone https://github.com/kuba-moo/nipa.git
sudo mv nipa/ /opt/
sudo useradd virtme

# nginx setup
sudo dnf -y install nginx
sudo systemctl enable nginx
sudo systemctl start nginx
# do basic config, then
sudo dnf -y install certbot certbot-nginx

# virtme
git clone https://github.com/arighi/virtme-ng.git

# as admin:
sudo dnf install python3.11.x86_64 python3.11-devel.x86_64 python3.11-pip.noarch python3.11-libs.x86_64
# as virtme:
pip-3.11 install requests
pip-3.11 install psutil

# prep for outside (system wide)
# QEMU
download QEMU
cd qemu-*
pip install sphinx
sudo dnf install glib2 glib2-devel
./configure --target-list=x86_64-softmmu,x86_64-linux-user
udo make install prefix=/usr

# libcli
git clone https://github.com/dparrish/libcli.git
cd libcli
make -j
sudo make install PREFIX=/usr

### Local

mkdir tools
cd tools

# netperf
git clone https://github.com/HewlettPackard/netperf.git
cd netperf
./autogen.sh
./configure --disable-omni # fails build otherwise
make install DESTDIR=/home/virtme/tools/fs prefix=/usr

exit 0

# Install libbpf
cd $kernel
cd tools/lib/bpf
make -j 40
sudo make install prefix=/usr

# bpftool
cd $kernel
make -C tools/bpf/bpftool
cp tools/bpf/bpftool/bpftool ../tools/fs/

# Tests need
sudo dnf install socat libcap-devel

# Build locally
sudo dnf install libnl3.x86_64 libnl3-cli.x86_64 libnl3-devel.x86_64 libnl3-doc.x86_64
git clone https://github.com/jpirko/libteam.git
cd libteam
./autogen.sh
./configure
make -j 40
# needs manual install
cp ./utils/teamdctl ../fs/usr/bin/
cp ./utils/teamnl ../fs/usr/bin/
cp -v ./libteam/.libs/libteam.so* ../fs/usr/lib/
cp -v ./libteamdctl/.libs/libteamdctl.so* ../fs/usr/lib/

# refresh iproute2
git clone https://git.kernel.org/pub/scm/network/iproute2/iproute2-next.git
cd iproute2-next
git remote add current https://git.kernel.org/pub/scm/network/iproute2/iproute2.git
git fetch --all
git reset --hard origin/main
git merge current/main -m "merge in current"

./configure
make -j 40
make install DESTDIR=/home/virtme/tools/fs prefix=/usr PREFIX=/usr

# msend / mreceive
git clone https://github.com/troglobit/mtools.git
cd mtools
make
make install DESTDIR=/home/virtme/tools/fs prefix=/usr PREFIX=/usr

# smcrouted
git clone https://github.com/troglobit/smcroute.git
cd smcroute
./autogen.sh
./configure
make install DESTDIR=/home/virtme/tools/fs prefix=/usr PREFIX=/usr
# it looks for a socket in /usr/local/var/run
sudo su
mkdir -p /usr/local/var/
ln -sv /run /usr/local/var/

# ndisc6 (ndisc6 package on Fedora)
dnf -y install gettext-devel
git clone https://git.remlab.net/git/ndisc6.git
cd ndisc6/
./autogen.sh
./configure
make -j
make install DESTDIR=/home/virtme/tools/fs prefix=/usr PREFIX=/usr
# make sure the SUID bits don't stick
find tools/fs/ -perm -4000
fs=$(find tools/fs/ -perm -4000)
chmod -s $fs
ls -l $fs

# dropwatch (DNF on fedora)
dnf -y install readline-devel binutils-devel
git clone https://github.com/nhorman/dropwatch
cd dropwatch/
./autogen.sh
./configure
make -j
make install DESTDIR=/home/virtme/tools/fs prefix=/usr PREFIX=/usr

# ethtool
git clone https://git.kernel.org/pub/scm/network/ethtool/ethtool.git
cd ethtool
./autogen.sh
./configure
make -j
make install DESTDIR=/home/virtme/tools/fs prefix=/usr PREFIX=/usr

# psample
git clone https://github.com/Mellanox/libpsample
cd libpsample
cmake -DCMAKE_INSTALL_PREFIX:PATH=/home/virtme/tools/fs/usr .
make -j
make install

# netsniff-ng
sudo dnf install libnetfilter_conntrack.x86_64 libnetfilter_conntrack-devel.x86_64
sudo dnf install libsodium-devel.x86_64 libsodium.x86_64
sudo dnf install libnet libnet-devel
git clone https://github.com/netsniff-ng/netsniff-ng.git
cd netsniff-ng
./configure
make -j


# AWS iputils are buggy
dnf -y install libxslt-devel libidn2-devel
git clone https://github.com/iputils/iputils.git
cd iputils
./configure
make -j
make install DESTDIR=/tmp
cp -v /tmp/usr/local/bin/* ../fs/usr/bin/
cd ../fs/usr/bin/
ln -s ping ping6

# ipv6toolkit (ra6 for fib_tests.sh)
git clone https://github.com/fgont/ipv6toolkit
cd ipv6toolkit/
make
make install DESTDIR=/home/virtme/tools/fs PREFIX=/usr

# for nf tests
sudo dnf install conntrack iperf3 ipvsadm

git clone git://git.netfilter.org/libnftnl
./autogen.sh
./configure
make -j 30
make install DESTDIR=/home/virtme/tools/fs prefix=/usr PREFIX=/usr

libtool --finish /home/virtme/tools/fs/usr/lib
sudo dnf install gmp gmp-devel

git clone git://git.netfilter.org/nftables
export PKG_CONFIG_PATH=/home/virtme/tools/fs:/home/virtme/tools/fs/usr:/home/virtme/tools/fs/usr/lib/pkgconfig/
./configure --with-json --with-xtables

# Edit paths into the makefile
# LIBNFTNL_CFLAGS = -I/usr/local/include -I/home/virtme/tools/fs/usr/include
# LIBNFTNL_LIBS = -L/usr/local/lib -L/home/virtme/tools/fs/usr/lib -lnftnl

make install DESTDIR=/home/virtme/tools/fs prefix=/usr PREFIX=/usr
# note that library LD_LIBRARY_PATH must have local libs before /lib64 !

git clone git://git.netfilter.org/ebtables
./autogen.sh
./configure --prefix=/ --exec-prefix=/home/virtme/tools/fs
make -j 8
make install DESTDIR=/home/virtme/tools/fs prefix=/usr PREFIX=/usr
cd /home/virtme/tools/fs/usr/sbin/
ln -v ebtables-legacy ebtables

sudo cp /etc/ethertypes /usr/local/etc/

# packetdrill
sudo dnf install glibc-static.x86_64

git clone https://github.com/google/packetdrill.git
cd packetdrill/gtests/net/packetdrill
./configure
make

cp packetdrill ~/tools/fs/usr/bin/

# Net tests need pyroute2 (for OvS tests)
sudo dnf install python3-pyroute2.noarch

# uring (needs ZC)
 git clone https://github.com/axboe/liburing/
 cd liburing
 ./configure --prefix=/usr
 make -j
 sudo make install
