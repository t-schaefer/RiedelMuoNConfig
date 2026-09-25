# MuoN card API (emsfp platform)

Recon done 2026-09-25 against the MuoN test card `10.101.12.162` (read-only
GETs only). A production Fusion (`10.101.4.158`, CS6.IP/HI 01) was crawled for
comparison and as the source of the site defaults. The raw dumps are written
by `tools/crawl.py` to `captures/`; that folder is git-ignored.

## Summary

MuoN cards use **the same REST API as the Fusion gateways**:
`http://<ip>/emsfp/node/v1/...`. The API describes itself: a GET on any
collection returns its children. Writes are `POST` with a partial JSON body
of the same shape as the GET response. For the full Fusion documentation
see `docs/fusion-api.json` in the RiedelFusionDashboard repo. What follows
is only what is new or different on the MuoN.

| | MuoN test card | Fusion reference |
|---|---|---|
| `self/information.type` | `18 - ST2110 UHD Decapsulator` | `22 - ST2110 UHD Transceiver` |
| `base_type` | `MuoN B` | `FusioN6` |
| Firmware | 4.8.0 (`MN-MuoN-B-APP-25-2110-SDI-2T-N`) | 4.8.1 |
| encap / decap | 0 / 2 | 2 / 6 |
| Channels (`devices/`) | 2 (ST2110 in → SDI out) | 6 |
| SDI outputs | 2 | 6 |
| Flows | 24 (rx only) | 96 (rx + tx) |
| `port/` (SFP cages) | — | 6 |
| `sdi/` (bit rate) | — | yes |
| `self/interfaces.oob` | — | yes |
| `self/system.fan_speed`, `flex_port_mode`, `access_control` | — | yes |
| `self/protocols.sap_announcement_enable` | — | yes |
| syslog monitoring `common.fan_speed`, `encap.*` | — | yes |
| License | clean_switch, uhd_support | + frame_sync, black_burst |

Port 80 serves HTTP only. 443, 22, 23, 8080 and 8443 are closed.

**Warning:** the root page `/` is a firmware management page with *Upload* and
*Clear* forms for the firmware slots. The tool never touches it.

## Resource tree (MuoN)

```
/emsfp/node/v1/
  self/ information sff diag/ firmware phy interfaces ipconfig static_route
        license system syslog protocols
  self/diag/ common firmware dns flow/{id} packet_interval_time devices/{id}
             2110-7_engine/{flow} refclk nmos
  flows/{id}  sources/  receivers/  devices/  sdp/{flow}  receivers_sdp/{rx}
  sdi_output/{id}  sdi_audio/{id}  clean_switch/{channel-id}
  route/bulk/{sender,receiver}/{channel-id}   (GET -> 400; write-only?)
  refclk  refclk/{uuid}  lldp
  telemetry/ node ports devices warnings/
/x-nmos/ node/v1.2  connection/v1.0  system/v1.0  channelmapping/v1.0
```

## Quirks relevant to configuration

- `flows/{id}.network` is a **list of legs** on the video receive flows
  (`flow 0`) and a **flat object** on every other flow. The vendor web client
  always POSTs `{"network": {...}}` flat, which writes leg 0.
- Each flow is one leg: `rx chN flow M pri` / `... sec` (Red / Blue).
  Flow 0 is video, flows 1–5 are audio or ANC.
- `sdi_output/{id}.sdi_aud_chans_cfg.chN` = `<flow uuid>:<flow channel>:<enable>`,
  where `:0:0` means unused. Flow UUIDs differ from device to device, so the tool
  addresses flows by name (`rx ch1 flow 1 pri:0:1`) and resolves the UUID per
  device.
- `sdi_output` IDs (`b0d2da17-…`, `b1d2da17-…`) are the **same on every
  device**. Channel and flow UUIDs are not.
- Wire types vary by field: `"0"`/`"1"` strings, real booleans, ints and
  strings. The tool always sends a value in the type the device returned.
- `self/interfaces.e2.dhcp` is `true` on the test card even though a static IP
  is set. On the Fusion it is `false`.
- `refclk` is an object with a `uuid` list. The per-port PTP settings
  (`domain_num`, `vlan_id`, `dscp`) live under `refclk/{uuid}`.

## Test card state (factory values)

Syslog `192.168.0.0`, disabled, all monitoring off. NMOS registry
`10.12.81.11:8080`, set but "connecting". All flows are disabled with
placeholder multicasts `239.0.1.x`. mDNS is off. PTP is locked on e1,
domain 127.

## Site defaults (from the Fusion reference)

Syslog `10.12.64.24:514` enabled; monitoring `ptp_event`, `temp_event` and
all decap events on. NMOS manual registry `10.12.81.11:8080`. IGMPv3,
ST 2022-7 class d, PTP domain 127, DSCP 46. Clean switch disabled / bbm /
300 ms. Flow filters: src IP + dst IP + dst port. This is packaged as
`config-tool/profiles/site-default.example.json`.

## Write verification status

All write shapes come either from the vendor web client (`evidence: js` in
`config-tool/catalog.py`) or from GET field names (`evidence: api`). None
has been fired at a MuoN yet. Run `tools/probe-writes.py <ip> --test-card`
on the test card. It changes each probe field, reads it back and restores it.
