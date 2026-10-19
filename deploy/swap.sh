#!/usr/bin/env bash
# Give the server an 8 GB swap file, if it has no swap yet and the disk has room (run as root; safe to repeat).
# Swap is disk used as overflow memory. It is used only when memory runs short, such as when an update merges a
# large amount of new Hansard into the search index, so a short peak slows that step down instead of the system
# stopping the update.
set -euo pipefail
if [[ -n "$(swapon --show --noheadings 2>/dev/null)" ]]; then
  echo "swap: already on ($(swapon --show --noheadings --bytes | awk '{s+=$3} END {printf "%.0f GB", s/1e9}'))"
  exit 0
fi
free_gb=$(df --output=avail -BG / | tail -1 | tr -dc '0-9')
if (( free_gb < 20 )); then
  echo "swap: not added (only ${free_gb} GB of disk free; 20 GB needed)"
  exit 0
fi
fallocate -l 8G /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=8192 status=none
chmod 600 /swapfile
mkswap /swapfile >/dev/null
swapon /swapfile
grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
# use it only when memory is short, not for things that merely have not been used for a while
echo 'vm.swappiness = 10' > /etc/sysctl.d/90-where-they-stand-swap.conf
sysctl -q -p /etc/sysctl.d/90-where-they-stand-swap.conf
echo "swap: 8 GB swap file added"
