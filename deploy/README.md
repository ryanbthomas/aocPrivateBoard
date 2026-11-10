# throwaway-linode

A single-purpose Linode that is expected to live **6–8 weeks**, be deployed **once**, hardened **once**, and then be destroyed. It may be rolled out again next year.

This repo is deliberately *not* Terraform and *not* Ansible. It is a
snapshot + scripts + log approach, chosen to cost under two hours total.

---

## Scope and non-goals

**In scope**

- One Linode instance, one operator
- A baseline Ubuntu 26.04 hardening pass (`harden.sh`)
- A verification pass (`verify.sh`) proving the hardening took effect
- A repeatable record: log, README, and a captured image

**Explicitly out of scope**

- Multi-host fleets, load balancers, autoscaling
- Configuration management / continuous convergence
- Compliance evidence (CIS/PCI/SOC2 attestation)
- Zero-downtime deploys or blue/green

If two or more of the "Graduation triggers" at the bottom become true,
revisit this decision.

---

## Why not Terraform / Ansible

| Tool | Verdict | Reason |
|---|---|---|
| Terraform | Skip | Its payoff is repeated, parallel changes to similar resources. Here there is one resource, one apply, one destroy. The state file is stale within weeks and misleading by next year. Terraform is for foundational resources that change; this one doesn't. |
| Ansible | Skip (for now) | Closest call, and the better of the two. But the documented break-even is around a second or third non-trivial thing to configure. This is one run on one host. |
| Bash + snapshot | **Use this** | Re-runnable transcript of what was actually done, plus an image that makes next year's rollout a two-minute deploy instead of a re-debug. |

The migration target, if this ever changes, is **Ansible first, Terraform second**.
Ansible earns its keep on rebuild frequency; Terraform only joins once there are surrounding resources (volumes, floating IPs, DNS) you'd otherwise forget to clean up.

---

## Files

| File | Purpose |
|---|---|
| `README.md` | This plan, plus the teardown and next-year checklists |
| `harden.sh` | Idempotent Ubuntu 26.04 baseline. Safe to re-run. |
| `verify.sh` | Read-only assertions. Run after hardening and again at week 4. |
| `logs/harden-YYYY-MM-DD.log` | Committed output of the run that actually happened |
| `logs/verify-YYYY-MM-DD.txt` | Corresponding verification snapshot |

The logs matter more than the scripts. They record the mid-run fixes and
surprises that no README ever captures.

---

## The hardening baseline

What `harden.sh` actually enforces:

**SSH**

- Key-only auth: `PasswordAuthentication no`, `KbdInteractiveAuthentication no`
- `PermitRootLogin no`
- Non-root sudo admin user (`ADMIN_USER`), keys inherited from root once
- `AllowUsers <admin>`, `MaxAuthTries 3`, `LoginGraceTime 30`
- `X11Forwarding no`
- Written as `00-hardening.conf` in `sshd_config.d/` so it sorts *before* the cloud-init drop-in

> **The trap this is designed around.** Linode's Ubuntu image ships
> `/etc/ssh/sshd_config.d/50-cloud-init.conf` containing
> `PasswordAuthentication yes`. Because sshd takes the *first* value it sees and
> includes `sshd_config.d/*.conf` near the top of the file, that drop-in beats
> both your edit to `sshd_config` and any higher-numbered drop-in you write. The
> script therefore uses a `00-` prefix, actively neutralizes the cloud-init file,
> and then **asserts against `sshd -T`** rather than trusting the file it wrote.

**Firewall**

- ufw, default deny incoming / allow outgoing
- SSH allowed **before** `ufw --force enable` (never enable deny-by-default with no SSH hole)
- 80 and 443 open; set `WEB_PORTS=""` for a non-web host
- Optional: `SSH_ALLOW_FROM=<cidr>` restricts 22 to one source

**fail2ban**

- systemd journal backend (no dependence on `/var/log/auth.log`)
- `mode = aggressive`, `maxretry 5`, `findtime 10m`, `bantime 1h`
- Expected value is modest — see "Known limitations"

**Patching**

- `unattended-upgrades` enabled and started, `20auto-upgrades` written
- Automatic upgrades are started after the script's package installs to avoid
  racing provisioning steps for the dpkg lock; package commands wait up to
  five minutes for a transient lock.
- Deliberately **not** enabling automatic reboot by default; a kernel or libc update needs a reboot to take effect, so decide explicitly

**Kernel / hygiene**

- sysctl baseline: no source routing, no ICMP redirects, no send_redirects,
  `tcp_syncookies=1`, `log_martians=1`, `fs.suid_dumpable=0`
- **Forwarding is deliberately not touched** — setting `ip_forward=0` breaks Docker, WireGuard, and any container runtime
- Persistent journald (`Storage=persistent`, 1G cap) so a reboot doesn't erase the evidence
- Disables cups / bluetooth / avahi when present
- Optional auditd (`INSTALL_AUDITD=1`) with watches on `/etc/passwd`,
  `/etc/shadow`, sudoers, and sshd config

---

## Deployment + hardening runbook

### 0. Before you create anything

- [ ] Hosting budget and expected destroy date noted: **destroy by ____**
- [ ] SSH keypair exists locally; public key in hand
- [ ] Confirm you know how to reach the **Lish** console (Cloud Manager) — this is the out-of-band recovery path if you firewall yourself out
- [ ] Note: Weblish asks for a **root password**. A password-less provisioned box may not have one set. If you think you might lock yourself out, set a root password deliberately before starting, or confirm the Cloud Manager reset path
- [ ] Repo cloned locally

### 1. Create the Linode

- [ ] Ubuntu 26.04 LTS, region close to you, smallest type that fits the job
- [ ] Add your **public key** at creation (do not set a root password unless you made a deliberate decision to)
- [ ] Enable **Backups** for the duration (cheap insurance on a short-lived box)
- [ ] Enable **private IP** if you will attach anything else to it

### 2. Snapshot before hardening

- [ ] Shut down, capture the disk as a private image, label it `pre-harden-YYYY-MM-DD`
- [ ] Record the image ID/label in the "Image lineage" section below

This is the fastest undo for a bad firewall or boot configuration. It costs
minutes and saves a rebuild.

### 3. Harden

- [ ] Open **two** SSH sessions. Run the script from one, keep the other idle
      and connected as the escape hatch
- [ ] `sudo ADMIN_USER=deploy ./harden.sh 2>&1 | tee logs/harden-$(date +%F).log`
- [ ] If you get the failure trap: **do not close the working session.** Recover
      via Lish, then `ufw disable`, remove
      `/etc/ssh/sshd_config.d/00-hardening.conf`, `systemctl reload ssh`

### 4. Verify — in a *new* session

- [ ] `./verify.sh | tee logs/verify-$(date +%F).txt`
- [ ] Read the output, don't just archive it:
  - `passwordauthentication no` and `permitrootlogin no` present
  - `sshd` AllowUsers lists only the admin user
  - ufw status active, exactly the intended ports
  - fail2ban sshd jail active
  - no unexpected listening sockets
  - pending security updates ≈ 0
- [ ] Confirm you can log in as the admin user **with keys only**
- [ ] Confirm root SSH is refused
- [ ] Commit both logs

### 5. Week-4 check (put it in your calendar now)

- [ ] Re-run `./verify.sh`, commit the output
- [ ] `diff` against the week-0 output — this is your entire drift-detection
      story at this scale
- [ ] Check `unattended-upgrades` has actually run:
      `journalctl -u unattended-upgrades --since -14d`
- [ ] Confirm `fail2ban-client status sshd` shows a sane ban count
- [ ] Address any `reboot required` flag

### 6. Capture the hardened image

- [ ] Shut down, capture the disk as a private image, label it
      `hardened-YYYY-MM-DD`
- [ ] Record it in "Image lineage"

**Next year's rollout is: deploy from `hardened-YYYY-MM-DD`, update keys, done.**
No scripts to re-debug, no drift question, hardening already baked in. This is
the single highest-value artifact in the repo.

### 7. Teardown checklist

The realistic way a short-lived box leaks money is an orphaned resource, not a
misconfigured one.

- [ ] Delete the Linode instance
- [ ] Delete any **Volumes** attached to it
- [ ] Release any **Floating IP**
- [ ] Delete **Cloud Firewalls** that referenced it
- [ ] Remove **DNS records** pointing at it
- [ ] Delete **StackScripts / images** you don't want to keep (keep the
      `hardened-` image if you plan to reuse next year)
- [ ] Turn off **Backups**
- [ ] Confirm the next invoice or the account's resource list is empty
- [ ] Note the destroy date and final cost in "Run log"

---

## Known limitations

Recorded honestly so future-you doesn't over-trust the setup.

**fail2ban is close to decorative on a key-only host.** The default filter does not match the log line produced by a *wrong key against a valid username* — in published testing on 24.04, six wrong-key attempts against a real user counted as **zero**, while wrong keys against nonexistent or disallowed usernames were caught. So it mainly bans bots that could never have succeeded anyway. Its real value is cutting log noise and catching scanners with `mode = aggressive`. It does **not** substitute for the firewall.

**Changing the SSH port doesn't work by editing `sshd_config` alone** under socket activation, and it breaks the fail2ban jail's port. Not attempted here.

**Docker breaks both assumptions.** Published container ports bypass ufw entirely, and the sshd jail won't cover them. The script warns if Docker is present. If you need containers, put them behind a reverse proxy on a host network you actually control.

**"Harden once" is fine; "patch once" is not.** Six to eight weeks of public exposure is longer than a monthly patch cycle. The week-6 risk is an unpatched CVE, not configuration drift — which is why automatic security updates are the single most important line in the script.

**ufw and the Linode Cloud Firewall are complementary, not redundant.** The cloud firewall filters before traffic reaches the VM; ufw filters on the host. Neither replaces the other.

**No OIDC-style short-lived credentials anywhere in this design.** That's part of why there's no CI here — nothing to leak.

**Linux-only.** Assumes `apt`, systemd, sshd, and ufw. Not portable to Alpine, RHEL, or BSD without rewriting.

---

## Graduation triggers

Move to **Ansible** (before Terraform) when any of these is true:

- Rebuilding the same box **more than ~3 times a year** — rebuild payoff is the real argument
- **2+ hosts** that must stay identical
- **Someone else** has to reproduce the environment without you
- You start wanting `--check --diff` or a real idempotency guarantee

Add **Terraform** only when:

- There are surrounding resources you'd forget to clean up (volumes, floating
  IPs, DNS, firewalls)
- More than one person creates infrastructure
- You need a reviewable plan diff before changes land

Intermediate step if you outgrow bash but don't want Ansible: keep the bash script, add **Packer** to build the image with it as a provisioner. Hardening then happens once per image build, is versioned, and is rollback-able.

---

## Future considerations

- **Next year's rebuild:** prefer the `hardened-` image over re-running the script. If the script has changed, rebuild the image from a clean Linode, don't patch the old image.
- **Image rot:** a captured image does not receive updates. If a year passes, the image is a year behind — deploy from it and let `unattended-upgrades` catch up in the first hour, or rebuild the image fresh.
- **Log shipping:** if this ever holds anything that matters, ship the persistent journal off-box (`systemd-journal-upload` or `restic` to object storage). A local journal dies with the VM.
- **Backups:** if any data matters, restic/Borg to object storage on a daily timer is ~15 minutes of work and is the only thing that survives an accidental destroy.
- **Restricting SSH further:** the cleaner alternative to fail2ban is making port 22 unreachable from the public internet at all — a Tailscale/WireGuard overlay, or an `SSH_ALLOW_FROM` rule for a single IP. Both beat banning scanners after the fact.
- **Ubuntu release:** re-verify the sshd drop-in behavior and any socket activation defaults if you move off 24.04.
**(TODO -- we are actually building on 26.04)**

- **Secrets:** if credentials ever land in this repo, they need a real answer (SOPS, agenix, or a password manager reference). Right now there are none, which is a feature.


---

## Image lineage

| Label | Date | Based on | Notes |
|---|---|---|---|
| `pre-harden-YYYY-MM-DD` | | Ubuntu 24.04 | Clean pre-hardening snapshot |
| `hardened-YYYY-MM-DD` | | Ubuntu 24.04 + harden.sh | **Use this for next year's deploy** |

## Run log

| Deployed | Hardened | Destroyed | Cost | Notes |
|---|---|---|---|---|
| | | | | |
