# RiedelMuoNConfig

Batch configuration tool for Redel **MuoN** cards and **Fusion**
gateways (the emsfp platform, `http://<ip>/emsfp/node/v1`). Define a settings
profile once and apply it to any number of devices. Where the device has
them, you can override single settings per device or per I/O: SDI output,
channel, flow leg, PTP port or SFP port.

It needs only Python 3 and the standard library, the same as the
[Fusion dashboard](https://github.com/t-schaefer/RiedelFusionDashboard).

## Install as a service (reachable from the network)

Set up the same way as the Fusion dashboard:

1. Double-click **`Install-MuoNConfig-Service.bat`** and approve the UAC
   prompt. This registers the scheduled task `MuoNConfigToolService`. It runs
   as SYSTEM, starts at boot, restarts after a crash and needs no login. It
   also opens the firewall for TCP 8091.
2. Open `http://<this PC>:8091/` from any machine. The dashboard stays on 8090.

To update later, double-click **`Update-MuoNConfig.bat`**. It runs
`git pull` and restarts the service. Your device list, `site-default`
profile and backups are not in git, so they are kept. To remove the service,
run `config-tool\uninstall-task-windows.ps1` as administrator.

### Login

The tool is password protected. The installer asks for a password if none is
set yet. To change it later, double-click **`Set-MuoNConfig-Password.bat`**.
The change takes effect at once, with no restart. The password is stored
only as a salted PBKDF2 hash in `config-tool/auth.json`, which is not in git.

- There is one shared password. A session lasts 12 h and is extended on
  every use. Restarting the service logs everyone out. The header has a
  *Log out* button.
- After 5 wrong attempts from one address, each further attempt waits longer (up to 5 min).
- Logins and failed attempts are written to `config-tool/config-tool.log`.
- As long as no password is set, the tool only answers requests from this
  PC itself. Remote requests get a "no password set" page.

## Start by hand (local only)

`Start-MuoNConfig.bat` (or `python config-tool/server.py`) runs the tool in
a console window, reachable only from this machine at http://localhost:8091/.
Don't use it while the service is installed, because both use port 8091.
`--host 0.0.0.0` and `--port` override the defaults.

## Workflow

1. **Devices** (left): add IPs, or *Import dashboard list* to take over the
   Fusion dashboard's `devices.json`. Select devices and click *Read
   selected*. This is read-only and pulls the complete config.
2. **Batch profile**: the editable **site-default** profile is loaded on
   start. Tick settings and give them values. Next to each setting you see
   the current values across the selected devices, and how many devices don't
   have it. Every category has **All on** and **All off** buttons (start
   from the devices' current values), and **Apply only this →** to preview and
   apply just that category. The toolbar has search, *only active*, and
   *Expand all* / *Collapse all* (the collapsed view is a one-line overview
   per category). *Save* overwrites the selected profile; *unsaved* marks
   pending edits. *Capture from device…* fills the profile from a
   well-configured device. Profiles live in `config-tool/profiles/`.
   `site-default.json` is local and git-ignored. It is seeded once from
   `site-default.example.json`, so `git pull` never overwrites it.
3. **Per I/O**: a grid per I/O type (SDI outputs, clean switch per channel,
   flows, PTP ports, SFP ports). A value there overrides the batch value for
   that one I/O on all selected devices. Empty means inherit.
4. **Per device**: every setting of one device, including the per-device-only
   ones (hostname, IPs, flow addresses, SDI audio map), with current values.
   You can also import flow addresses for many devices from CSV
   (`ip,flow,src_ip,src_port,dst_ip,dst_port,igmp_src,enable`), and load a
   backup as overrides to restore a device.
5. **Preview & apply**: builds a per-device diff (current → new; *n/a* =
   the device doesn't have this setting, so it is skipped). *Apply* asks for
   the word `APPLY`. Then, per device, the tool does a fresh read, writes a backup to
   `config-tool/backups/`, sends one POST per section (media first, then
   management addresses last) and reads every changed field back
   (*verified* / *mismatch*).

Precedence, last wins: batch value → per-I/O value → device value →
device per-I/O value.

## Supported settings

See `config-tool/catalog.py`. Every setting is one entry there:

- **Identity & network**: hostname; Red/Blue static IP, gateway, DHCP, VLAN; 5 static routes
- **Syslog**: server, port, enable, all monitoring flags (common/encap/decap)
- **NMOS**: registry mode/address 1+2, domain, mDNS, 4 DNS servers, control network
- **System**: IGMP version, ST 2022-7 class, LED, mDNS/SAP, LLDP, FEC, SDI bit rate
- **PTP**: mode, two-step, delay-req, announce timeout, TTL; per port domain/VLAN/DSCP
- **SDI output** (per output): loss of input, VPID, line offset/timing,
  audio delay, frame buffer, colour bars, 16-channel embedded audio map
- **Clean switch** (per channel): mode, type, IGMP setup delay, timeout
- **Flows** (per leg): label, source/destination IP and port, SSM source,
  enable, RTP PT, VLAN, SSRC, all packet filters
- **SFP ports** (Fusion): host pinout, SFP type

Tags in the UI:
- **danger**: can take a device or signal off air (addresses, enable,
  FEC, pinout, colour bars).
- **unverified**: the write shape is derived from the API's field names and
  hasn't been confirmed on a device yet. Run `tools/probe-writes.py` on a
  test card first.

## Tools

- `tools/crawl.py <ip> <out.json>`: read-only full dump of a device's API.
- `tools/probe-writes.py <ip> --test-card`: checks the unverified write paths
  on a card that is **not on air**. It changes each field, reads it back and
  restores it.

API notes: [docs/muon-api.md](docs/muon-api.md).
