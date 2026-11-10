#!/usr/bin/env bash
set -uo pipefail
echo "== listening sockets =="        ; ss -tulpnH | awk '{print $1, $5}'
echo "== sshd effective config =="    ; sshd -T | grep -Ei '^(permitrootlogin|passwordauthentication|pubkeyauthentication|kbdinteractiveauthentication|x11forwarding)'
echo "== ssh auth failures (24h) ==" ; journalctl -u ssh --since -24h | grep -c 'Failed password' || true
echo "== firewall =="                 ; ufw status verbose 2>/dev/null || nft list ruleset | head -40
echo "== pending security updates ==" ; apt-get -s upgrade | grep -c ^Inst || true
echo "== reboot required =="          ; test -f /var/run/reboot-required && echo YES || echo no
echo "== auditd health =="

if ! command -v auditctl >/dev/null; then
  echo "  auditd not installed (expected if INSTALL_AUDITD=0)"
else
  # enabled should be 1; pid 0 means auditd is NOT running; lost > 0 means events were dropped
  auditctl -s -i 2>/dev/null | grep -Ei 'enabled|pid|lost|backlog|failure'
  echo "  rules loaded: $(auditctl -l | grep -c 'key=' || true)"
  if auditctl -l | grep -q 'never,task'; then
    echo "  WARNING: a 'never,task' rule is present - it disables all rule processing"
  fi
fi

echo "== auditd: changes to watched files since boot =="
if command -v ausearch >/dev/null && auditctl -s 2>/dev/null | grep -q 'enabled 1'; then
  aureport --key --summary 2>/dev/null | tail -20
  for k in identity sudoers sshd; do
    n=$(ausearch -k "$k" -ts boot --raw 2>/dev/null | grep -c '^type=' || true)
    echo "  key=$k events since boot: $n"
    [[ "$n" -gt 0 ]] && ausearch -k "$k" -ts boot -i 2>/dev/null \
      | grep -E 'auid=|comm=|name=' | tail -10
  done
else
  echo "  auditd not running - no event history to query"
fi

echo "== baseline hash drift =="
BASE=/var/lib/hardening/baseline.sha256
if [[ -r "$BASE" ]]; then
  if sha256sum -c --quiet "$BASE" 2>&1; then
    echo "  OK: no watched file differs from the hardening baseline"
  else
    echo "  DRIFT: files above differ - review, do not auto-revert"
  fi
else
  echo "  no baseline found - run harden.sh with INSTALL_AUDITD=1"
fi
