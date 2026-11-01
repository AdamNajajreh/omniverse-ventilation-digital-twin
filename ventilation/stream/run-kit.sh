#!/bin/bash
# Runs the streaming app for the gateway. The public address tells browsers where
# to send the video traffic. Leave PUBLIC_IP empty to test on this machine.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PUBLIC_IP="${PUBLIC_IP-$(curl -s -H 'Metadata-Flavor: Google' http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip)}"
unset DISPLAY

exec "$ROOT/_build/linux-x86_64/release/adam.ventilation_editor_streaming.kit.sh" --no-window \
    "--/exts/adam.ventilation.cfd/visitorResetFile=$HOME/.local/state/ventilation-stream/visitor-left" \
    "--/exts/omni.kit.livestream.app/primaryStream/publicIp=$PUBLIC_IP"
