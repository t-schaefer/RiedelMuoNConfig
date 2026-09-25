# RiedelMuoNConfig

Tooling for Nevion Virtuoso **MuoN** cards. Step 1 is reconnaissance: find
out which management interface the card exposes before building the
configuration tool.

## muon_recon.py

Read-only probe (TCP ports, HTTP/HTTPS paths, Fusion-style `emsfp` API,
NMOS `x-nmos`, SNMP v2c `sysDescr`/`sysName`). Only GET requests are sent.
Python 3.8+, standard library only.

```
python muon_recon.py 10.102.12.162
python muon_recon.py 10.101.12.162 --blue      # Red + Blue address
```

Writes `muon-recon-<host>.json`. That report is the input for designing
the configuration tool.
