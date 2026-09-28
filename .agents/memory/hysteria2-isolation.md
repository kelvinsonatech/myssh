---
name: Hysteria 2 coexistence
description: Why HY2 installation and UDP 53 ownership must be isolated from other tunnels
---

Do not run the official get.hy2.sh installer inside this multi-protocol panel.
Keep HY2 installation separate from HY1.

**Why:** the generic installer uses the same binary/config locations as the
existing Hysteria 1 service. A valid HY2 install there would break HY1.

UDP 53 handover is an explicit, reversible user choice, never an automatic
port-conflict workaround. Failures must restore the prior SlowDNS active and
enabled state. Unknown owners are not safe to kill.

**Why:** the user approved temporary SlowDNS interruption for this switch,
not interruption of unrelated services. Re-running installation and menu
restart operations must also respect persistent handover ownership.

**How to apply:** keep account-auth testing separate from firewall/systemd
validation. A passing localhost QUIC transfer does not establish VPS firewall,
provider filtering, reboot, or real-client compatibility.