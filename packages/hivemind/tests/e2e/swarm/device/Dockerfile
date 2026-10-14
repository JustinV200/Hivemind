# The second machine of the swarm tests: a plain Linux device with Python and the Pollen Packet.
#
# Built by `e2e/swarm/image.py`, never by hand: the build context holds this file, `wheelhouse/`
# (wheels of waggle, pollen and hivemind built from this tree, and of every third-party package
# hivemind pins, for CPython 3.12 on x86-64 Linux) and `ca/` (empty, or the certificate of a
# proxy the build has to trust).
#
# Why Ubuntu 24.04: its Python is 3.12, which is what the wheelhouse is built for, and its
# package mirror is reachable from the places these tests run (a sandbox may allow the Ubuntu
# mirror and not Debian's). Why the tools: `ps` takes the device's process table before and
# after a lease, `ip` and `ping` are what a diagnostic command on a real device would use.
#
# Variants: `image.py` passes `EXTRA_PACKAGES` (space-separated apt names) to make a device that has
# more installed than the plain one, such as the X server and tools of a desktop for the
# Exoskeleton tests. The default is empty, so the plain image is built exactly as it was.
FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive
# Set only when the build reaches the mirror through a proxy that containers cannot reach on
# their own (image.py then also builds on the host network); apt is pointed at it for this layer.
ARG APT_PROXY=""
# Extra apt packages for a variant of the device; empty for the plain one.
ARG EXTRA_PACKAGES=""

RUN --mount=type=bind,source=ca,target=/opt/build-ca \
    set -eu; \
    if [ -n "$APT_PROXY" ]; then \
        sed -i 's|http://|https://|g' /etc/apt/sources.list.d/ubuntu.sources; \
        printf 'Acquire::https::Proxy "%s";\nAcquire::https::CaInfo "/opt/build-ca/proxy.crt";\n' \
            "$APT_PROXY" > /etc/apt/apt.conf.d/99build-proxy; \
    fi; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        ca-certificates python3 python3-venv procps iproute2 iputils-ping $EXTRA_PACKAGES; \
    rm -rf /var/lib/apt/lists/* /etc/apt/apt.conf.d/99build-proxy

# The wheelhouse stays in the image: a device that is colonized later installs the Hive runtime
# from here, offline, the way a real device would from a source its operator configured.
COPY wheelhouse/ /opt/wheelhouse/
RUN python3 -m venv /opt/pollen \
    && /opt/pollen/bin/pip install --no-index --find-links /opt/wheelhouse pollen \
    && ln -s /opt/pollen/bin/pollen /usr/local/bin/pollen

# Output reaches `docker logs` as it is written, which is how a failed enrolment is diagnosed.
ENV PYTHONUNBUFFERED=1
# A bare `docker run` keeps the device up; the tests run the packet as the container's command.
CMD ["sleep", "infinity"]
