"""Mitsubishi MELSEC-Q and KEYENCE KV MC/SLMP 3E binary TCP client.

This module can be used with both Mitsubishi and KEYENCE PLCs that support
QnA-compatible 3E binary frames. The CLI can identify the vendor and port from
the CPU model response; explicit selection is also supported. Mitsubishi uses
D/M addresses, while KEYENCE uses DM/MR notation. The PLC must have a matching
MC-protocol TCP listener configured. No third-party packages are required.
"""

import argparse
import io
import socket
import struct
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Sequence


class MCProtocolError(RuntimeError):
    """The PLC returned an error or an invalid MC response."""


class MC3EClient:
    """MC 3E binary client for Mitsubishi and KEYENCE word/bit devices."""

    MAX_POINTS = 256  # Conservative limit for this example, not a PLC limit.
    MAX_WIRE_ADDRESS = 0xFFFFFF

    def __init__(self, ip_address: str, vendor: str, port: int,
                 timeout: float = 3.0):
        self.vendor = self._vendor(vendor)
        self._validate_connection_params(ip_address, port, timeout)
        self.ip_address = ip_address
        self.port = port
        self.timeout = timeout

    @staticmethod
    def _vendor(vendor: str) -> str:
        if not isinstance(vendor, str) or vendor.lower() not in ("mitsubishi", "keyence"):
            raise ValueError("vendor must be 'mitsubishi' or 'keyence'")
        return vendor.lower()

    @staticmethod
    def _validate_connection_params(ip_address: str, port: int,
                                    timeout: float) -> None:
        """Validate endpoint parameters without opening a TCP connection."""
        if not isinstance(ip_address, str) or not ip_address:
            raise ValueError("ip_address is required")
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise ValueError("port must be 1..65535")
        if not isinstance(timeout, (int, float)) or timeout <= 0:
            raise ValueError("timeout must be positive")

    @staticmethod
    def _count(length: int) -> None:
        if not isinstance(length, int) or isinstance(length, bool) or not 1 <= length <= MC3EClient.MAX_POINTS:
            raise ValueError("length must be 1..256")

    @staticmethod
    def _word_target(vendor: str, device: str, address: int, length: int) -> int:
        vendor = MC3EClient._vendor(vendor)
        MC3EClient._count(length)
        allowed = ("DM", "D") if vendor == "keyence" else ("D",)
        if not isinstance(device, str) or device.upper() not in allowed:
            raise ValueError("word device must be DM/D for KEYENCE or D for Mitsubishi")
        if not isinstance(address, int) or isinstance(address, bool) or address < 0:
            raise ValueError("start_address must be a non-negative integer")
        maximum = 65534 if vendor == "keyence" else MC3EClient.MAX_WIRE_ADDRESS
        if address + length - 1 > maximum:
            raise ValueError("word address range exceeds this client's supported range")
        return address  # Both use device code A8 for this supported word area.

    @staticmethod
    def _bit_target(vendor: str, device: str, address: int, length: int) -> int:
        vendor = MC3EClient._vendor(vendor)
        MC3EClient._count(length)
        expected = "MR" if vendor == "keyence" else "M"
        if not isinstance(device, str) or device.upper() != expected:
            raise ValueError("bit device must be MR for KEYENCE or M for Mitsubishi")
        if not isinstance(address, int) or isinstance(address, bool) or address < 0:
            raise ValueError("start_address must be a non-negative integer")
        if vendor == "keyence":
            word, bit = divmod(address, 100)
            if word > 3999 or bit > 15:
                raise ValueError("KEYENCE MR must use word 0..3999 and bit 00..15")
            wire_address = word * 16 + bit  # MR60000 -> M9600 on the wire.
            maximum = 63999
        else:
            wire_address = address  # Mitsubishi M is already a linear bit number.
            maximum = MC3EClient.MAX_WIRE_ADDRESS
        if wire_address + length - 1 > maximum:
            raise ValueError("bit address range exceeds this client's supported range")
        return wire_address

    @staticmethod
    def _request(command: int, subcommand: int, device_code: int,
                 wire_address: int, length: int, payload: bytes = b"") -> bytes:
        body = struct.pack("<HHH", 0x0010, command, subcommand)
        body += wire_address.to_bytes(3, "little")
        body += bytes((device_code,)) + struct.pack("<H", length) + payload
        # Standard direct/local 3E route: network 0, PC FF, I/O 03FF, station 0.
        header = struct.pack("<HBBHBH", 0x0050, 0, 0xFF, 0x03FF, 0, len(body))
        return header + body

    @staticmethod
    def _command_request(command: int, subcommand: int = 0) -> bytes:
        """Build a 3E request without a device operand (for example, 0101)."""
        body = struct.pack("<HHH", 0x0010, command, subcommand)
        header = struct.pack("<HBBHBH", 0x0050, 0, 0xFF, 0x03FF, 0, len(body))
        return header + body

    @staticmethod
    def _recv_exact(sock: socket.socket, count: int) -> bytes:
        data = bytearray()
        while len(data) < count:
            chunk = sock.recv(count - len(data))
            if not chunk:
                raise MCProtocolError("connection closed before the full response arrived")
            data.extend(chunk)
        return bytes(data)

    @staticmethod
    def _exchange(ip_address: str, port: int, timeout: float,
                  request: bytes) -> bytes:
        MC3EClient._validate_connection_params(ip_address, port, timeout)
        try:
            with socket.create_connection((ip_address, port), timeout=timeout) as sock:
                sock.settimeout(timeout)
                sock.sendall(request)
                header = MC3EClient._recv_exact(sock, 9)
                # A binary 3E response uses D0 00. Accepting 4E or a vendor-
                # specific frame here would be unsafe because its header layout
                # must be parsed differently.
                if header[:2] != b"\xD0\x00":
                    raise MCProtocolError(
                        "expected a 3E binary response (D0 00), got {}".format(
                            " ".join("{:02X}".format(value)
                                     for value in header[:2])))
                body_length = struct.unpack_from("<H", header, 7)[0]
                if not 2 <= body_length <= 4096:
                    raise MCProtocolError("invalid response length: {}".format(body_length))
                body = MC3EClient._recv_exact(sock, body_length)
        except socket.timeout as exc:
            raise ConnectionError("MC response timed out from {}:{}".format(ip_address, port)) from exc
        except OSError as exc:
            raise ConnectionError("cannot connect to {}:{}: {}".format(ip_address, port, exc)) from exc
        end_code = struct.unpack_from("<H", body)[0]
        if end_code:
            raise MCProtocolError("PLC end code: 0x{:04X}".format(end_code))
        return body[2:]

    @staticmethod
    def read_words_at(ip_address: str, vendor: str, port: int, device: str,
                      start_address: int, length: int,
                      timeout: float = 3.0) -> List[int]:
        """Read unsigned 16-bit D/DM words without creating an instance."""
        address = MC3EClient._word_target(vendor, device, start_address, length)
        data = MC3EClient._exchange(ip_address, port, timeout,
                                    MC3EClient._request(0x0401, 0, 0xA8, address, length))
        if len(data) != length * 2:
            raise MCProtocolError("expected {} word-data bytes, got {}".format(length * 2, len(data)))
        return list(struct.unpack("<" + "H" * length, data))

    @staticmethod
    def write_words_at(ip_address: str, vendor: str, port: int, device: str,
                       start_address: int, values: Sequence[int],
                       timeout: float = 3.0) -> None:
        """Write unsigned 16-bit D/DM words without creating an instance."""
        words = list(values)
        address = MC3EClient._word_target(vendor, device, start_address, len(words))
        if any(not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= 0xFFFF for v in words):
            raise ValueError("each word value must be an unsigned 16-bit integer")
        payload = struct.pack("<" + "H" * len(words), *words)
        data = MC3EClient._exchange(ip_address, port, timeout,
                                    MC3EClient._request(0x1401, 0, 0xA8, address, len(words), payload))
        if data:
            raise MCProtocolError("unexpected write response data: {}".format(data.hex()))

    @staticmethod
    def read_bits_at(ip_address: str, vendor: str, port: int, device: str,
                     start_address: int, length: int,
                     timeout: float = 3.0) -> List[bool]:
        """Read Mitsubishi M or KEYENCE MR bits without creating an instance."""
        address = MC3EClient._bit_target(vendor, device, start_address, length)
        data = MC3EClient._exchange(ip_address, port, timeout,
                                    MC3EClient._request(0x0401, 1, 0x90, address, length))
        expected = (length + 1) // 2
        if len(data) != expected:
            raise MCProtocolError("expected {} bit-data bytes, got {}".format(expected, len(data)))
        nibbles = [(data[i // 2] >> (4 if i % 2 == 0 else 0)) & 0x0F for i in range(length)]
        if any(nibble not in (0, 1) for nibble in nibbles):
            raise MCProtocolError("invalid bit value in response")
        return [bool(nibble) for nibble in nibbles]

    @staticmethod
    def write_bits_at(ip_address: str, vendor: str, port: int, device: str,
                      start_address: int, values: Sequence[int],
                      timeout: float = 3.0) -> None:
        """Write Mitsubishi M or KEYENCE MR bits without creating an instance."""
        bits = list(values)
        address = MC3EClient._bit_target(vendor, device, start_address, len(bits))
        if any(not isinstance(value, (int, bool)) or value not in (0, 1) for value in bits):
            raise ValueError("each bit value must be 0 or 1")
        payload = bytearray((len(bits) + 1) // 2)
        for i, value in enumerate(bits):
            payload[i // 2] |= int(value) << (4 if i % 2 == 0 else 0)
        data = MC3EClient._exchange(ip_address, port, timeout,
                                    MC3EClient._request(0x1401, 1, 0x90, address, len(bits), bytes(payload)))
        if data:
            raise MCProtocolError("unexpected write response data: {}".format(data.hex()))

    @staticmethod
    def read_model_at(ip_address: str, vendor: str, port: int,
                      timeout: float = 3.0) -> dict:
        """Read Mitsubishi or KEYENCE CPU model name/code with command 0101."""
        MC3EClient._vendor(vendor)
        data = MC3EClient._exchange(ip_address, port, timeout,
                                    MC3EClient._command_request(0x0101))
        return MC3EClient._decode_model_response(data)

    @staticmethod
    def _decode_model_response(data: bytes) -> dict:
        if len(data) != 18:
            raise MCProtocolError("expected 18 model bytes, got {}".format(len(data)))
        try:
            name = data[:16].decode("ascii").rstrip(" \x00")
        except UnicodeDecodeError as exc:
            raise MCProtocolError("CPU model name is not ASCII") from exc
        return {"name": name, "code": struct.unpack_from("<H", data, 16)[0]}

    @staticmethod
    def detect_plc_at(ip_address: str, port=None, timeout: float = 3.0) -> dict:
        """Detect a Mitsubishi/KEYENCE PLC with read-only command 0101."""
        ports = [port] if port is not None else [5000, 5002, 1025, 1026, 4999, 5010]
        failures = []
        for candidate in ports:
            try:
                data = MC3EClient._exchange(
                    ip_address, candidate, min(timeout, 1.0),
                    MC3EClient._command_request(0x0101))
                model = MC3EClient._decode_model_response(data)
                name = model["name"].upper()
                if name.startswith(("V", "KV")):
                    vendor = "keyence"
                elif name.startswith(("Q", "L", "R", "FX", "A")):
                    vendor = "mitsubishi"
                else:
                    raise MCProtocolError("unknown CPU model returned by 0101: {}".format(model["name"]))
                return {"vendor": vendor, "port": candidate, "model": model}
            except (ConnectionError, MCProtocolError) as exc:
                failures.append("{}: {}".format(candidate, exc))
        raise ConnectionError("PLC auto-detection failed for {} ({})".format(
            ip_address, "; ".join(failures)))

    @staticmethod
    def _keyence_cr_address(address: int, length: int) -> int:
        """Convert KEYENCE CR word/bit notation (CR2007) to a wire bit index."""
        MC3EClient._count(length)
        if not isinstance(address, int) or isinstance(address, bool) or address < 0:
            raise ValueError("CR address must be a non-negative integer")
        word, bit = divmod(address, 100)
        if word > 79 or bit > 15 or word * 16 + bit + length - 1 > 1279:
            raise ValueError("KEYENCE CR must fit CR0000..CR7915")
        return word * 16 + bit

    @staticmethod
    def read_keyence_cr_at(ip_address: str, vendor: str, port: int,
                           start_address: int, length: int,
                           timeout: float = 3.0) -> List[bool]:
        """Read KEYENCE control relays (CR) through the MC SM device code."""
        if MC3EClient._vendor(vendor) != "keyence":
            raise ValueError("CR reading is enabled only for KEYENCE")
        address = MC3EClient._keyence_cr_address(start_address, length)
        data = MC3EClient._exchange(ip_address, port, timeout,
                                    MC3EClient._request(0x0401, 1, 0x91, address, length))
        if len(data) != (length + 1) // 2:
            raise MCProtocolError("invalid CR response length: {}".format(len(data)))
        values = [(data[i // 2] >> (4 if i % 2 == 0 else 0)) & 0x0F for i in range(length)]
        if any(value not in (0, 1) for value in values):
            raise MCProtocolError("invalid CR bit value in response")
        return [bool(value) for value in values]

    @staticmethod
    def read_keyence_cm_at(ip_address: str, vendor: str, port: int,
                           start_address: int, length: int,
                           timeout: float = 3.0) -> List[int]:
        """Read KEYENCE control memory (CM) through the MC SD device code."""
        if MC3EClient._vendor(vendor) != "keyence":
            raise ValueError("CM reading is enabled only for KEYENCE")
        MC3EClient._count(length)
        if not isinstance(start_address, int) or isinstance(start_address, bool) or start_address < 0 or start_address + length - 1 > 5999:
            raise ValueError("KEYENCE CM range must fit CM0000..CM5999")
        data = MC3EClient._exchange(ip_address, port, timeout,
                                    MC3EClient._request(0x0401, 0, 0xA9, start_address, length))
        if len(data) != 2 * length:
            raise MCProtocolError("expected {} CM bytes, got {}".format(2 * length, len(data)))
        return list(struct.unpack("<" + "H" * length, data))

    @staticmethod
    def read_special_words_at(ip_address: str, vendor: str, port: int,
                              start_address: int, length: int,
                              timeout: float = 3.0) -> List[int]:
        """Read Mitsubishi SD special registers; no write method is provided."""
        if MC3EClient._vendor(vendor) != "mitsubishi":
            raise ValueError("SD reading is enabled only for Mitsubishi")
        MC3EClient._count(length)
        if not isinstance(start_address, int) or isinstance(start_address, bool) or start_address < 0 or start_address + length - 1 > 0xFFFFFF:
            raise ValueError("invalid SD address range")
        data = MC3EClient._exchange(ip_address, port, timeout,
                                    MC3EClient._request(0x0401, 0, 0xA9, start_address, length))
        if len(data) != 2 * length:
            raise MCProtocolError("expected {} SD bytes, got {}".format(2 * length, len(data)))
        return list(struct.unpack("<" + "H" * length, data))

    @staticmethod
    def read_special_bits_at(ip_address: str, vendor: str, port: int,
                             start_address: int, length: int,
                             timeout: float = 3.0) -> List[bool]:
        """Read Mitsubishi SM special relays; no write method is provided."""
        if MC3EClient._vendor(vendor) != "mitsubishi":
            raise ValueError("SM reading is enabled only for Mitsubishi")
        MC3EClient._count(length)
        if not isinstance(start_address, int) or isinstance(start_address, bool) or start_address < 0 or start_address + length - 1 > 0xFFFFFF:
            raise ValueError("invalid SM address range")
        data = MC3EClient._exchange(ip_address, port, timeout,
                                    MC3EClient._request(0x0401, 1, 0x91, start_address, length))
        if len(data) != (length + 1) // 2:
            raise MCProtocolError("invalid SM response length: {}".format(len(data)))
        values = [(data[i // 2] >> (4 if i % 2 == 0 else 0)) & 0x0F for i in range(length)]
        if any(value not in (0, 1) for value in values):
            raise MCProtocolError("invalid SM bit value in response")
        return [bool(value) for value in values]

    @staticmethod
    def decode_q_status(sd203: int) -> dict:
        """Decode Q-series SD203 status; this is not a universal PLC status map."""
        if not isinstance(sd203, int) or not 0 <= sd203 <= 0xFFFF:
            raise ValueError("SD203 must be a 16-bit word")
        state = {0: "RUN", 1: "STEP-RUN", 2: "STOP", 3: "PAUSE"}.get(sd203 & 0x0F, "UNKNOWN")
        cause = {0: "switch", 1: "remote contact", 2: "remote operation",
                 3: "program instruction", 4: "error"}.get((sd203 >> 4) & 0x0F, "unknown")
        return {"raw": sd203, "state": state, "stop_pause_cause": cause}

    @staticmethod
    def decode_q_switch(sd200: int) -> str:
        """Decode the Q-series CPU switch state stored in SD200."""
        if not isinstance(sd200, int) or not 0 <= sd200 <= 0xFFFF:
            raise ValueError("SD200 must be a 16-bit word")
        return {0: "RUN", 1: "STOP", 2: "RESET/L.CLR"}.get(sd200 & 0x0F, "UNKNOWN")

    def read_model(self) -> dict:
        return self.read_model_at(self.ip_address, self.vendor, self.port, self.timeout)

    def read_special_words(self, start_address: int, length: int) -> List[int]:
        return self.read_special_words_at(self.ip_address, self.vendor, self.port,
                                          start_address, length, self.timeout)

    def read_special_bits(self, start_address: int, length: int) -> List[bool]:
        return self.read_special_bits_at(self.ip_address, self.vendor, self.port,
                                         start_address, length, self.timeout)

    def read_status(self) -> dict:
        if self.vendor == "keyence":
            running = self.read_keyence_cr_at(self.ip_address, self.vendor, self.port,
                                               2007, 1, self.timeout)[0]
            return {"raw": int(running), "state": "RUN" if running else "PROGRAM",
                    "stop_pause_cause": "not available"}
        return self.decode_q_status(self.read_special_words(203, 1)[0])

    def read_diagnostics(self) -> dict:
        if self.vendor == "keyence":
            arithmetic_error = self.read_keyence_cr_at(
                self.ip_address, self.vendor, self.port, 2012, 1, self.timeout)[0]
            alarm = self.read_keyence_cr_at(
                self.ip_address, self.vendor, self.port, 3500, 1, self.timeout)[0]
            details = self.read_keyence_cm_at(
                self.ip_address, self.vendor, self.port, 5150, 27, self.timeout)
            return {"CR2012": arithmetic_error, "CR3500": alarm,
                    "CM5150_5176": details}
        flags = self.read_special_bits(0, 2)
        code = self.read_special_words(0, 1)[0]
        return {"SM0": flags[0], "SM1": flags[1], "SD0": code}

    def read_system_summary(self) -> dict:
        if self.vendor == "keyence":
            bits = self.read_keyence_cr_at(
                self.ip_address, self.vendor, self.port, 2002, 6, self.timeout)
            names = ("CR2002", "CR2003", "CR2004", "CR2005", "CR2006", "CR2007")
            summary = dict(zip(names, bits))
            summary["status"] = {"raw": int(bits[5]),
                                 "state": "RUN" if bits[5] else "PROGRAM",
                                 "stop_pause_cause": "not available"}
            return summary
        registers = self.read_special_words(200, 4)
        return {"SD200": registers[0], "SD201": registers[1],
                "SD202": registers[2], "SD203": registers[3],
                "switch": self.decode_q_switch(registers[0]),
                "status": self.decode_q_status(registers[3])}

    def read_cpu_information(self) -> dict:
        """Collect the supported read-only Mitsubishi CPU information."""
        return {"model": self.read_model(), "diagnostics": self.read_diagnostics(),
                "system": self.read_system_summary()}

    def read(self, device: str, start_address: int, length: int) -> List[int]:
        return self.read_words_at(self.ip_address, self.vendor, self.port, device,
                                  start_address, length, self.timeout)

    def write(self, device: str, start_address: int, values: Sequence[int]) -> None:
        self.write_words_at(self.ip_address, self.vendor, self.port, device,
                            start_address, values, self.timeout)

    def read_bits(self, device: str, start_address: int, length: int) -> List[bool]:
        return self.read_bits_at(self.ip_address, self.vendor, self.port, device,
                                 start_address, length, self.timeout)

    def write_bits(self, device: str, start_address: int, values: Sequence[int]) -> None:
        self.write_bits_at(self.ip_address, self.vendor, self.port, device,
                           start_address, values, self.timeout)


def _add_connection_options(parser, subparser=False) -> None:
    default = argparse.SUPPRESS if subparser else None
    parser.add_argument("--vendor", choices=("auto", "mitsubishi", "keyence"),
                        default=default if subparser else "auto",
                        help="PLC vendor (default: auto-detect)")
    parser.add_argument("--ip", default=default, help="PLC IP address")
    parser.add_argument("--port", type=int, default=default,
                        help="MC TCP port (default: auto-detect)")
    parser.add_argument("--timeout", type=float,
                        default=default if subparser else 3.0)


def _print_cpu_information(client: MC3EClient) -> None:
    information = client.read_cpu_information()
    model = information["model"]
    diagnostics = information["diagnostics"]
    system = information["system"]
    print("CPU model: {} (code 0x{:04X})".format(model["name"], model["code"]))
    if client.vendor == "keyence":
        print("CPU mode = {} (CR2007={})".format(
            system["status"]["state"], "ON" if system["CR2007"] else "OFF"))
        print("CR2012 arithmetic error = {}; CR3500 alarm = {}".format(
            "ON" if diagnostics["CR2012"] else "OFF",
            "ON" if diagnostics["CR3500"] else "OFF"))
        print("CM5150..CM5176 = {}".format(
            " ".join("0x{:04X}".format(value) for value in diagnostics["CM5150_5176"])))
        print("CR2002..CR2007 = {}".format(
            " ".join("ON" if system[key] else "OFF" for key in
                     ("CR2002", "CR2003", "CR2004", "CR2005", "CR2006", "CR2007"))))
    else:
        print("CPU switch = {}; CPU state = {}; cause = {}".format(
            system["switch"], system["status"]["state"],
            system["status"]["stop_pause_cause"]))
        print("SM0 = {}; SM1 = {}; SD0 error code = 0x{:04X}".format(
            "ON" if diagnostics["SM0"] else "OFF",
            "ON" if diagnostics["SM1"] else "OFF", diagnostics["SD0"]))
        print("SD200..SD203 = {}".format(
            " ".join("0x{:04X}".format(system[key]) for key in
                     ("SD200", "SD201", "SD202", "SD203"))))


def main() -> None:
    parser = argparse.ArgumentParser(description="Mitsubishi/KEYENCE MC 3E binary TCP client")
    _add_connection_options(parser)
    actions = parser.add_subparsers(dest="action")
    for name in ("read", "readbit"):
        action = actions.add_parser(name)
        action.add_argument("device")
        action.add_argument("start", type=int)
        action.add_argument("length", type=int)
        _add_connection_options(action, True)
    for name in ("write", "writebit"):
        action = actions.add_parser(name)
        action.add_argument("device")
        action.add_argument("start", type=int)
        action.add_argument("values", nargs="+", type=lambda s: int(s, 0))
        _add_connection_options(action, True)
    for name in ("model", "status", "errors", "summary", "info"):
        action = actions.add_parser(name, help="read-only PLC CPU information")
        _add_connection_options(action, True)
    args = parser.parse_args()
    if args.action is None:
        parser.error("choose read, write, readbit, writebit, model, status, errors, summary, or info")
    if not getattr(args, "ip", None):
        parser.error("--ip is required")

    try:
        vendor = getattr(args, "vendor", "auto")
        port = getattr(args, "port", None)
        cpu_actions = ("model", "status", "errors", "summary", "info")
        detected = None
        if vendor == "auto" or port is None or args.action in cpu_actions:
            detected = MC3EClient.detect_plc_at(args.ip, port, args.timeout)
            if vendor != "auto" and vendor != detected["vendor"]:
                raise ValueError("specified vendor {} does not match detected vendor {}".format(
                    vendor, detected["vendor"]))
            vendor = detected["vendor"]
            port = detected["port"]
            print("Detected PLC: vendor={}, model={} (0x{:04X}), port={}".format(
                vendor, detected["model"]["name"], detected["model"]["code"], port))

        client = MC3EClient(args.ip, vendor, port, args.timeout)
        if args.action == "read":
            for offset, value in enumerate(client.read(args.device, args.start, args.length)):
                print("{}{} = {} (0x{:04X})".format(args.device.upper(), args.start + offset, value, value))
        elif args.action == "write":
            client.write(args.device, args.start, args.values)
            print("wrote {} word(s) starting at {}{}".format(
                len(args.values), args.device.upper(), args.start))
        elif args.action == "readbit":
            values = client.read_bits(args.device, args.start, args.length)
            wire_start = MC3EClient._bit_target(vendor, args.device, args.start, args.length)
            for offset, value in enumerate(values):
                wire_address = wire_start + offset
                display_address = ((wire_address // 16) * 100 + wire_address % 16
                                   if client.vendor == "keyence" else wire_address)
                print("{}{} = {}".format(args.device.upper(), display_address,
                                         "ON" if value else "OFF"))
        elif args.action == "writebit":
            client.write_bits(args.device, args.start, args.values)
            print("wrote {} bit(s) starting at {}{}".format(
                len(args.values), args.device.upper(), args.start))
        elif args.action in ("status", "info"):
            _print_cpu_information(client)
        elif args.action == "model":
            model = detected["model"] if detected else client.read_model()
            print("CPU model: {} (code 0x{:04X})".format(model["name"], model["code"]))
        elif args.action == "errors":
            diagnostics = client.read_diagnostics()
            if client.vendor == "keyence":
                print("CR2012 arithmetic error = {}; CR3500 alarm = {}".format(
                    "ON" if diagnostics["CR2012"] else "OFF",
                    "ON" if diagnostics["CR3500"] else "OFF"))
                print("CM5150..CM5176 = {}".format(
                    " ".join("0x{:04X}".format(value) for value in
                             diagnostics["CM5150_5176"])))
            else:
                print("SM0 = {}; SM1 = {}; SD0 error code = 0x{:04X}".format(
                    "ON" if diagnostics["SM0"] else "OFF",
                    "ON" if diagnostics["SM1"] else "OFF", diagnostics["SD0"]))
        else:
            system = client.read_system_summary()
            if client.vendor == "keyence":
                for key in ("CR2002", "CR2003", "CR2004", "CR2005", "CR2006", "CR2007"):
                    print("{} = {}".format(key, "ON" if system[key] else "OFF"))
                print("CPU mode = {}".format(system["status"]["state"]))
            else:
                for key in ("SD200", "SD201", "SD202", "SD203"):
                    print("{} = 0x{:04X}".format(key, system[key]))
                print("CPU switch = {}; CPU state = {}".format(
                    system["switch"], system["status"]["state"]))
    except (ConnectionError, MCProtocolError, ValueError) as exc:
        parser.exit(1, "Error: {}\n".format(exc))


class _Tee:
    def __init__(self, stream, capture):
        self.stream = stream
        self.capture = capture

    def write(self, value):
        self.stream.write(value)
        self.capture.write(value)
        return len(value)

    def flush(self):
        self.stream.flush()


def _run_with_log() -> None:
    """Run the CLI and append its command/output to output/yyyyMMdd_log.txt."""
    started = datetime.now().astimezone()
    capture = io.StringIO()
    original_stdout, original_stderr = sys.stdout, sys.stderr
    sys.stdout = _Tee(original_stdout, capture)
    sys.stderr = _Tee(original_stderr, capture)
    exit_code = 0
    try:
        main()
    except SystemExit as exc:
        exit_code = exc.code if isinstance(exc.code, int) else 1
        raise
    except BaseException as exc:
        exit_code = 1
        capture.write("Unhandled error: {!r}\n".format(exc))
        raise
    finally:
        finished = datetime.now().astimezone()
        sys.stdout, sys.stderr = original_stdout, original_stderr
        log_directory = Path(__file__).resolve().parent / "output"
        try:
            log_directory.mkdir(parents=True, exist_ok=True)
            log_path = log_directory / (started.strftime("%Y%m%d") + "_log.txt")
            command = "python " + subprocess.list2cmdline(sys.argv)
            with log_path.open("a", encoding="utf-8") as log_file:
                log_file.write("=" * 72 + "\n")
                log_file.write("Started: {}\n".format(started.isoformat()))
                log_file.write("Command: {}\n".format(command))
                log_file.write(capture.getvalue())
                if capture.getvalue() and not capture.getvalue().endswith("\n"):
                    log_file.write("\n")
                log_file.write("Exit code: {}\n".format(exit_code))
                log_file.write("Finished: {}\n".format(finished.isoformat()))
            print("Log file: {}".format(log_path), file=original_stdout)
        except OSError as exc:
            print("Log write error: {}".format(exc), file=original_stderr)


if __name__ == "__main__":
    _run_with_log()
