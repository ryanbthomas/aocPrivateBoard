#!/usr/bin/env bash
# harden.sh - Ubuntu 26.04 baseline for a short-lived single-purpose VM.
# Idempotent: safe to re-run. Run as root, with a second SSH session already open
# and Lish reachable in a browser tab.
#
#   sudo ADMIN_USER=deploy ./harden.sh
#   sudo ADMIN_USER=deploy SSH_ALLOW_FROM=203.0.113.10/32 ./harden.sh
set -euo pipefail

ADMIN_USER="${ADMIN_USER:-}"
SSH_PORT="${SSH_PORT:-22}"
WEB_PORTS="${WEB_PORTS:-80 443}"        # set WEB_PORTS="" for a non-web host
SSH_ALLOW_FROM="${SSH_ALLOW_FROM:-}"    # empty = SSH open to the world, keys only
INSTALL_AUDITD="${INSTALL_AUDITD:-1}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"

log()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33m[!] %s\033[0m\n' "$*"; }
die()  { printf '\033[31m[x] %s\033[0m\n' "$*" >&2; exit 1; }
trap 'warn "FAILED at the step above. Do not close your working SSH session. Recover via Lish (Linode console), then: ufw disable; rm -f /etc/ssh/sshd_config.d/00-hardening.conf; systemctl reload ssh"' ERR

# ---------------------------------------------------------------- preflight
[[ $EUID -eq 0 ]] || die "run as root (sudo)"
[[ -n "$ADMIN_USER" ]] || die "set ADMIN_USER, e.g. sudo ADMIN_USER=deploy ./harden.sh"
[[ "$ADMIN_USER" != "root" ]] || die "refusing to harden with ADMIN_USER=root"
command -v sshd >/dev/null || die "openssh-server is not installed"
. /etc/os-release
[[ "${VERSION_ID:-}" == "24.04" ]] || warn "written for 24.04; this is ${VERSION_ID:-unknown}"

log "Preflight: ensure $ADMIN_USER exists and has a working public key"
if ! id -u "$ADMIN_USER" >/dev/null 2>&1; then
  adduser --disabled-password --gecos "" "$ADMIN_USER"
  usermod -aG sudo "$ADMIN_USER"
fi
install -d -m 700 -o "$ADMIN_USER" -g "$ADMIN_USER" "/home/$ADMIN_USER/.ssh"
AK="/home/$ADMIN_USER/.ssh/authorized_keys"
if [[ ! -s "$AK" ]]; then
  # Linode writes deploy-time keys to root; inherit them once.
  [[ -s /root/.ssh/authorized_keys ]] \
    || die "no key for $ADMIN_USER and none in /root/.ssh/authorized_keys - add a key before disabling passwords"
  install -m 600 -o "$ADMIN_USER" -g "$ADMIN_USER" /root/.ssh/authorized_keys "$AK"
fi
chown "$ADMIN_USER:$ADMIN_USER" "$AK"; chmod 600 "$AK"

# ---------------------------------------------------------------- packages
log "Updating base packages"
export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a
apt-get update -qq
apt-get -y -o Dpkg::Options::=--force-confdef \
           -o Dpkg::Options::=--force-confold upgrade

# ---------------------------------------------------------------- sshd
log "Hardening sshd"
install -d -m 755 /etc/ssh/sshd_config.d
cp -a /etc/ssh/sshd_config "/etc/ssh/sshd_config.bak.$TS"

cat > /etc/ssh/sshd_config.d/00-hardening.conf <<EOF
# Managed by harden.sh - this filename sorts before 50-cloud-init.conf on purpose.
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitEmptyPasswords no
PermitRootLogin no
PubkeyAuthentication yes
X11Forwarding no
MaxAuthTries 3
LoginGraceTime 30
ClientAliveInterval 300
ClientAliveCountMax 2
AllowUsers $ADMIN_USER
EOF
chmod 644 /etc/ssh/sshd_config.d/00-hardening.conf

# cloud-init's drop-in says "PasswordAuthentication yes" and would win otherwise.
CI=/etc/ssh/sshd_config.d/50-cloud-init.conf
if [[ -f "$CI" ]] && grep -qiE '^[[:space:]]*PasswordAuthentication[[:space:]]+yes' "$CI"; then
  cp -a "$CI" "$CI.bak.$TS"
  sed -i -E 's/^[[:space:]]*PasswordAuthentication[[:space:]]+yes/PasswordAuthentication no/I' "$CI"
  log "Neutralised $CI"
fi

sshd -t || die "sshd config invalid - not reloading"
grep -qx 'passwordauthentication no' <(sshd -T) || die "password auth STILL enabled - aborting before reload"
if systemctl reload ssh 2>/dev/null; then :; else systemctl restart ssh; fi
log "Effective SSH settings:"
sshd -T | grep -E '^(passwordauthentication|kbdinteractiveauthentication|permitrootlogin|pubkeyauthentication|maxauthtries|allowusers) '

# ---------------------------------------------------------------- firewall
log "Configuring ufw (allow SSH *before* enabling)"
apt-get install -y ufw
ufw default deny incoming
ufw default allow outgoing
if [[ -n "$SSH_ALLOW_FROM" ]]; then
  ufw allow from "$SSH_ALLOW_FROM" to any port "$SSH_PORT" proto tcp comment 'ssh'
else
  ufw allow "$SSH_PORT"/tcp comment 'ssh'   # 'ufw limit' also works but counts connections, not failures
fi
for p in $WEB_PORTS; do ufw allow "$p"/tcp comment "web-$p"; done
ufw --force enable
ufw status verbose

# ---------------------------------------------------------------- fail2ban
log "Configuring fail2ban (systemd journal backend - no /var/log/auth.log needed)"
apt-get install -y fail2ban
cat > /etc/fail2ban/jail.d/sshd.local <<EOF
[sshd]
enabled  = true
backend  = systemd
port     = $SSH_PORT
maxretry = 5
findtime = 10m
bantime  = 1h
mode     = aggressive
EOF
fail2ban-client -t || die "fail2ban config invalid"
systemctl enable --now fail2ban
systemctl restart fail2ban
fail2ban-client status sshd || warn "sshd jail not listed - check: journalctl -u fail2ban -n 50"

# ---------------------------------------------------------------- patching
log "Enabling unattended security upgrades"
apt-get install -y unattended-upgrades apt-listchanges
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
EOF
systemctl enable --now unattended-upgrades
# Decide deliberately: kernel/libc updates need a reboot to take effect.
# To reboot automatically at 03:00 local time, also set in
# /etc/apt/apt.conf.d/50unattended-upgrades:
#   Unattended-Upgrade::Automatic-Reboot "true";
#   Unattended-Upgrade::Automatic-Reboot-Time "03:00";
unattended-upgrade --dry-run --debug >/dev/null 2>&1 || warn "dry-run reported issues"

# ---------------------------------------------------------------- kernel
log "Applying sysctl baseline"
cat > /etc/sysctl.d/99-hardening.conf <<'EOF'
# Note: ip_forward / forwarding deliberately NOT set here - it breaks Docker,
# WireGuard and any container runtime. Add it yourself only on a plain host.
net.ipv4.conf.all.accept_source_route = 0
net.ipv4.conf.default.accept_source_route = 0
net.ipv6.conf.all.accept_source_route = 0
net.ipv6.conf.default.accept_source_route = 0
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.default.accept_redirects = 0
net.ipv6.conf.all.accept_redirects = 0
net.ipv6.conf.default.accept_redirects = 0
net.ipv4.conf.all.send_redirects = 0
net.ipv4.conf.default.send_redirects = 0
net.ipv4.tcp_syncookies = 1
net.ipv4.conf.all.log_martians = 1
net.ipv4.icmp_echo_ignore_broadcasts = 1
fs.suid_dumpable = 0
EOF
sysctl --system >/dev/null

# ---------------------------------------------------------------- hygiene
log "Persistent journal (so a reboot doesn't erase the evidence)"
install -d -m 755 /etc/systemd/journald.conf.d
printf '[Journal]\nStorage=persistent\nSystemMaxUse=1G\n' \
  > /etc/systemd/journald.conf.d/99-persistent.conf
systemctl restart systemd-journald

log "Disabling obviously unnecessary services, if present"
for s in cups cups-browsed bluetooth avahi-daemon; do
  if systemctl is-enabled "$s" >/dev/null 2>&1; then
    systemctl disable --now "$s" || warn "could not disable $s"
  fi
done


if [[ "$INSTALL_AUDITD" == "1" ]]; then
  log "Installing auditd with syscall-form watches"
  apt-get install -y auditd audispd-plugins

  cat > /etc/audit/rules.d/hardening.rules <<'EOF'
## Identity / privilege files
-a always,exit -F arch=b64 -F path=/etc/passwd -F perm=wa -k identity
-a always,exit -F arch=b64 -F path=/etc/shadow -F perm=wa -k identity
-a always,exit -F arch=b64 -F path=/etc/group  -F perm=wa -k identity
-a always,exit -F arch=b64 -F path=/etc/gshadow -F perm=wa -k identity
-a always,exit -F arch=b64 -F path=/etc/sudoers -F perm=wa -k sudoers
-a always,exit -F arch=b64 -F dir=/etc/sudoers.d -F perm=wa -k sudoers
-a always,exit -F arch=b64 -F dir=/etc/ssh -F perm=wa -k sshd
## Rename-based edits land on the temp file - catch those too
-a always,exit -F arch=b64 -F path=/etc/passwd+ -F perm=wa -k identity
-a always,exit -F arch=b64 -F path=/etc/shadow+ -F perm=wa -k identity
EOF

  augenrules --load || warn "augenrules --load failed; rules may not be active"
  systemctl enable --now auditd

  # auditctl -l is the ground truth: what the kernel actually holds.
  rules_loaded=$(auditctl -l | grep -c 'key=' || true)
  [[ "$rules_loaded" -ge 8 ]] || warn "only $rules_loaded audit rules loaded (expected >=8) - check: augenrules --check"

  # Point-in-time hash baseline. auditd says *who* touched what; this says *what* differs.
  BASE=/var/lib/hardening/baseline.sha256
  install -d -m 700 /var/lib/hardening
  sha256sum /etc/passwd /etc/shadow /etc/group /etc/gshadow \
            /etc/sudoers /etc/ssh/sshd_config \
            /etc/ssh/sshd_config.d/*.conf 2>/dev/null \
    | sort -k2 > "$BASE"
  log "Wrote baseline ($(wc -l < "$BASE") files) to $BASE"
fi


apt-get -y autoremove --purge >/dev/null 2>&1 || true

# ---------------------------------------------------------------- report
command -v docker >/dev/null && warn "Docker present: published container ports bypass ufw and the sshd jail will not cover them"
command -v fail2ban-client >/dev/null && ufw status | grep -q "Status: active" \
  || warn "ufw is not active - check before you log out"

log "Verification snapshot"
sshd -T | grep -E '^(passwordauthentication|permitrootlogin|allowusers|maxauthtries) '
ufw status verbose
fail2ban-client status sshd 2>/dev/null || true
ss -tulpnH | awk '{print $1, $5}'
[[ -f /var/run/reboot-required ]] && warn "reboot required ($(tr '\n' ' ' </var/run/reboot-required.pkgs 2>/dev/null))" || true

log "Done. Now open a SECOND terminal and confirm you can log in as $ADMIN_USER."
log "If you cannot: use Lish from the Linode Cloud Manager, then 'ufw disable'."
