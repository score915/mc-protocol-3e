# MC 3E Binary Client for Mitsubishi and KEYENCE PLCs

[日本語版](README.ja.md)

`mc_3e.py` is a dependency-free Python client and command-line tool for PLCs
that support MC protocol / SLMP QnA-compatible 3E binary frames over TCP.

> [!WARNING]
> MC protocol traffic is not encrypted or authenticated. Never expose a PLC
> port directly to the Internet. Use a trusted, isolated control network or a
> properly secured VPN. Write commands can change machine behavior; verify the
> target device, interlocks, PLC mode, and online-write settings first.

It provides a common interface for:

- Mitsubishi MELSEC `D` word and `M` bit devices
- KEYENCE KV `DM` word and `MR` bit devices
- Word and bit batch read/write
- CPU model, operating status, and selected diagnostic information
- Read-only PLC vendor and TCP port auto-detection
- Automatic daily execution logs
- Reusable instance methods and static methods

## Requirements

- Python 3.6 or later
- A PLC with a QnA-compatible MC 3E binary TCP listener enabled
- Network access to the TCP port configured on the PLC

Only `mc_3e.py`, `README.md`, and `README.ja.md` are needed. The program uses
only the Python standard library.

## Quick start

Auto-detect the vendor and port, then display the available CPU information:

```console
python mc_3e.py status --ip 192.168.0.10
```

Example output from a KEYENCE KV-7500:

```text
Detected PLC: vendor=keyence, model=V7500 (0x0037), port=5000
CPU model: V7500 (code 0x0037)
CPU mode = RUN (CR2007=ON)
CR2012 arithmetic error = OFF; CR3500 alarm = OFF
CM5150..CM5176 = 0x0000 ...
CR2002..CR2007 = ON OFF OFF OFF ON ON
Log file: ...\output\20260927_log.txt
```

Auto-detection sends the read-only CPU model command `0101/0000` to ports
`5000`, `5002`, `1025`, `1026`, `4999`, and `5010`. This is a convenience list
based on ports commonly used in PLC projects and the configurations used during
development; these ports are not assigned by the MC protocol specification.
Use `--port` for another port. Detection currently recognizes common KEYENCE
`V`/`KV` and Mitsubishi `Q`/`L`/`R`/`FX`/`A` model-name prefixes.

## Command-line usage

### Word devices

```console
# KEYENCE: read DM60000 through DM60010
python mc_3e.py read DM 60000 11 --vendor keyence --ip 192.168.0.10 --port 5000

# KEYENCE: write 12345 to DM60002
python mc_3e.py write DM 60002 12345 --vendor keyence --ip 192.168.0.10 --port 5000

# Mitsubishi: read D100 through D102
python mc_3e.py read D 100 3 --vendor mitsubishi --ip 192.168.0.20 --port 5002

# Mitsubishi: write two words
python mc_3e.py write D 100 10 20 --vendor mitsubishi --ip 192.168.0.20 --port 5002
```

Words are unsigned 16-bit values from `0` through `65535`. Decimal and
Python-style prefixed values such as `0x1234` are accepted for writes.

### Bit devices

```console
# KEYENCE
python mc_3e.py readbit MR 60000 2 --vendor keyence --ip 192.168.0.10 --port 5000
python mc_3e.py writebit MR 60000 1 --vendor keyence --ip 192.168.0.10 --port 5000

# Mitsubishi
python mc_3e.py readbit M 100 8 --vendor mitsubishi --ip 192.168.0.20 --port 5002
python mc_3e.py writebit M 100 1 0 1 --vendor mitsubishi --ip 192.168.0.20 --port 5002
```

KEYENCE `MR` uses word-and-bit notation: the final two digits must be `00`
through `15`. For example, `MR60015` is followed by `MR60100`; `MR60016` is
invalid. Mitsubishi `M` uses a linear bit address.

### CPU information

These commands are read-only:

```console
python mc_3e.py model   --ip 192.168.0.10
python mc_3e.py status  --ip 192.168.0.10
python mc_3e.py errors  --ip 192.168.0.10
python mc_3e.py summary --ip 192.168.0.10
python mc_3e.py info    --ip 192.168.0.10
```

`status` and `info` display the combined model, status, diagnostic, and system
summary.

| Command | Mitsubishi | KEYENCE |
| --- | --- | --- |
| `model` | CPU name/code via `0101/0000` | CPU name/code via `0101/0000` |
| `status` / `info` | Model, SD200-SD203, SM0/SM1, SD0 | Model, CR2002-CR2007, CR2012, CR3500, CM5150-CM5176 |
| `errors` | SM0, SM1, SD0 | CR2012, CR3500, CM5150-CM5176 |
| `summary` | SD200-SD203 and decoded CPU state | CR2002-CR2007 and decoded CPU mode |

Mitsubishi status decoding is intended for Q-series-compatible special
registers. Meanings can vary by CPU family.

More specifically:

- Mitsubishi `errors` reads SM0 and SM1 as diagnostic flags and SD0 as the
  latest diagnostic error code.
- Mitsubishi `summary` returns raw SD200-SD203 values, decodes the CPU switch
  from SD200, and decodes the operating state and stop/pause cause from SD203.
  SD201 and SD202 are deliberately left as raw, model-dependent values.
- KEYENCE `errors` reads CR2012 (arithmetic execution error), CR3500 (alarm),
  and CM5150-CM5176 (arithmetic-error detail area).
- KEYENCE `summary` reads CR2002-CR2007 and uses CR2007 to report RUN or
  PROGRAM mode.

KEYENCE CR and CM are accessed through the MC special-relay (`SM`) and
special-register (`SD`) device codes respectively. This mapping is useful but
less familiar than ordinary DM/MR access, so verify the relevant addresses in
the manual for the target KV series.

### Options

Connection options may appear before or after the subcommand:

```console
python mc_3e.py --vendor keyence --ip 192.168.0.10 --port 5000 read DM 60000 1
python mc_3e.py read DM 60000 1 --vendor keyence --ip 192.168.0.10 --port 5000
```

| Option | Description |
| --- | --- |
| `--ip ADDRESS` | PLC IPv4/host address; required |
| `--vendor auto\|keyence\|mitsubishi` | Vendor selection; default: `auto` |
| `--port PORT` | MC TCP port; auto-detected when omitted |
| `--timeout SECONDS` | Socket timeout; default: 3 seconds |

## Automatic logs

Every CLI invocation creates an `output` directory beside `mc_3e.py` and
appends the command, output, errors, timestamps, and exit code to a daily UTF-8
log file:

```text
output/20260927_log.txt
```

The filename format is `yyyyMMdd_log.txt`. Log labels and command output are in
English. Existing content is preserved when another command runs that day.

## Python API

Create an instance when repeatedly accessing one PLC:

```python
from mc_3e import MC3EClient

kv = MC3EClient("192.168.0.10", "keyence", 5000, timeout=3.0)
words = kv.read("DM", 60000, 11)      # list[int]
bits = kv.read_bits("MR", 60000, 2)  # list[bool]
kv.write("DM", 60002, [12345])
kv.write_bits("MR", 60000, [1, 0])
cpu = kv.read_cpu_information()

plc = MC3EClient("192.168.0.20", "mitsubishi", 5002)
words = plc.read("D", 100, 3)
bits = plc.read_bits("M", 100, 8)
```

The protocol operations are also available as static methods:

```python
from mc_3e import MC3EClient

words = MC3EClient.read_words_at(
    "192.168.0.10", "keyence", 5000, "DM", 60000, 11
)
MC3EClient.write_words_at(
    "192.168.0.10", "keyence", 5000, "DM", 60002, [12345]
)
bits = MC3EClient.read_bits_at(
    "192.168.0.20", "mitsubishi", 5002, "M", 100, 8
)
MC3EClient.write_bits_at(
    "192.168.0.20", "mitsubishi", 5002, "M", 100, [1, 0, 1]
)

detected = MC3EClient.detect_plc_at("192.168.0.10")
```

## Supported scope and limitations

- QnA-compatible 3E binary frames over TCP only
- Direct route fixed to network `0`, PC `FF`, I/O `03FF`, station `0`
- Up to 256 points per operation. This is an intentional conservative safety
  cap in this example, not the maximum allowed by the MC protocol or every PLC.
- Normal devices: Mitsubishi `D`/`M`, KEYENCE `DM`/`MR`
- Unsigned 16-bit word values only
- No ASCII, UDP, 1E/4E frames, routed stations, or other device types
- Address ranges and online-write permissions depend on the PLC and its setup
- CPU information is a selected summary, not an expansion-unit list or complete
  error-history API

## Safety and security

Write commands change PLC memory and may affect connected equipment. Use only
confirmed, unused test devices, and restore values when necessary. Verify the
PLC project, operating mode, interlocks, and online-write settings first.

MC protocol TCP traffic is not encrypted or authenticated by this script. Use
it only on a trusted, isolated industrial network or through a properly secured
VPN. Do not expose a PLC port directly to the Internet.

## Why the 3E response header is checked strictly

This client requires the binary 3E response subheader `D0 00`. A 4E response or
another frame format has a different header layout and must not be parsed as a
3E response. The client therefore reports an explicit protocol error instead
of accepting an unknown subheader. Supporting another frame format should be
implemented as a separate parser.

## Hardware verification

The implementation has been exercised with:

- KEYENCE KV-7500: model/status/diagnostic and DM/MR reads and writes
- Mitsubishi Q00UJCPU: model/status/diagnostic and D/M reads and writes

Other models depend on their MC protocol support, device map, and Ethernet
settings.

## References

- [MELSEC Communication Protocol Reference Manual](https://dl.mitsubishielectric.com/dl/fa/document/manual/plc/sh080008/sh080008ab.pdf)
- [MELSEC-Q/L Ethernet Interface User's Manual](https://dl.mitsubishielectric.com/dl/fa/document/manual/plc/sh080811eng/sh080811engy.pdf)
- [KEYENCE PLC manuals](https://www.keyence.com/support/user/controls/plc/manual/building/)

Mitsubishi Electric, MELSEC, KEYENCE, and the product names mentioned above are
the property of their respective owners. This project is not affiliated with or
endorsed by either manufacturer.
