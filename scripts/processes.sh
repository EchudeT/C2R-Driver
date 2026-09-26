#!/usr/bin/env bash
set -euo pipefail
echo "Relevant controller, Codex, container and QEMU processes (read-only):"
ps -eo pid=,ppid=,stat=,etime=,args= | awk 'BEGIN { IGNORECASE=1 } \
    /driver_port_factory|codex( |$)|docker (run|exec)|qemu-system/ && $0 !~ /awk/ { print }' || true
echo
echo "Controller locks (workspace arguments):"
ps -eo pid=,ppid=,stat=,etime=,args= | awk 'BEGIN { IGNORECASE=1 } \
    /driver_port_factory\.cli.*port run|driver_port_factory\.cli.*status/ && $0 !~ /awk/ { print }' || true
