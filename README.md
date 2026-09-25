# RiedelMuoNConfig

Batch configuration tool for Redel **MuoN** cards and **Fusion**
gateways (the emsfp platform, `http://<ip>/emsfp/node/v1`). Define a settings
profile once and apply it to any number of devices. Where the device has
them, you can override single settings per device or per I/O: SDI output,
channel, flow leg, PTP port or SFP port.

It needs only Python 3 and the standard library, the same as the
[Fusion dashboard](https://github.com/t-schaefer/RiedelFusionDashboard).

## Start

```
Start-MuoNConfig.bat
```

or `python config-tool/server.py`, then open http://localhost:8091/.

By default the tool is **only reachable from this machine**. It writes to
broadcast devices and has no login. To open it to the network deliberately,
set `MUON_CONFIG_HOST=0.0.0.0` (and optionally `MUON_CONFIG_PORT`).

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
