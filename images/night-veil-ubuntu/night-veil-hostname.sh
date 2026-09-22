#!/bin/sh
# night-veil-hostname.sh (roadmap step 5.3a): randomise this Cell's hostname on every boot.
#
# Location-blind defaults (codingrules section 8.7) include "randomized hostname" -- a fixed
# hostname baked into the image would itself be a fingerprint a Night Veil task's own traffic (or
# a compromised process on the Cell) could leak. /dev/urandom, not the Cell's own id or any other
# value the Queen already knows: the hostname must carry no information back to this Hive either.
set -eu

# 8 lowercase hex bytes: short enough to stay a valid hostname label, long enough that two Cells
# collide only by astronomical chance -- collision here has no real consequence anyway (nothing
# resolves this hostname from outside the Cell).
suffix="$(head -c 4 /dev/urandom | od -An -tx1 | tr -d ' \n')"
hostnamectl set-hostname "night-veil-${suffix}"
