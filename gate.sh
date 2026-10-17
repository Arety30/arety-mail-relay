#!/usr/bin/env bash
# Every minute: mail (hydroxide + relay + its tunnel) runs only while more than 4 GB of RAM is free.
# Starts with a 300 MB margin so starting them does not itself push memory under the line.
avail=$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo)
units="hydroxide.service arety-mail-relay.service arety-mail-tunnel.service"
if [ "$avail" -lt 4096 ]; then
  systemctl --user is-active -q arety-mail-relay.service hydroxide.service arety-mail-tunnel.service && echo "$(date) ${avail} MB free: stopping mail"
  systemctl --user stop $units
elif [ "$avail" -ge 4400 ]; then
  systemctl --user is-active -q $units || { echo "$(date) ${avail} MB free: starting mail"; systemctl --user start $units; }
fi
