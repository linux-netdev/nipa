FROM debian:bookworm

# Duplicate uid useradd option is necessary for people who run this as root.
ARG nipauid
RUN useradd -o -m -u $nipauid nipa

RUN apt-get update
RUN apt-get install -y procps python3 git patatt build-essential \
        ccache flex bison libssl-dev libelf-dev clang sparse bc cpio \
        gdb strace vim

# needed for spdxcheck.py from kernel sources
RUN apt-get install -y python3-ply python3-git

RUN mkdir -p /home/nipa/.local/share/patatt && chown -R nipa: /home/nipa

# This is also needed for people who are running as root.
RUN git config --global --add safe.directory /nipa-run/tree
