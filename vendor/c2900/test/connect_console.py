#!/usr/bin/env python3

import os
import pty


PCIE_WARNING = b"Failed to initialize CPU PCIe controller 1 !!"
COMPACT_WARNING = b"WARNING: Unsupported compact flash detected. Use of this card during normal"
COMPACT_END = b"Please use supported compact flash cards only."
SMC_WARNING = b"%SMC-2-BAD_ID_HW:"
SMC_END = b"program such as SmartNet."
MONTH_PREFIXES = tuple(
	month + b" "
	for month in (b"Jan", b"Feb", b"Mar", b"Apr", b"May", b"Jun", b"Jul", b"Aug", b"Sep", b"Oct", b"Nov", b"Dec")
)


class ConsoleFilter:
	def __init__(self):
		self.mode = "normal"
		self.line_start = True
		self.pending = bytearray()

	def feed(self, data):
		output = bytearray()

		for byte in data:
			if self.mode == "drop_blanks":
				if byte in (ord("\r"), ord("\n")):
					continue
				self.mode = "normal"
				self.line_start = True

			if self.mode == "smc":
				self.pending.append(byte)
				if byte == ord("\n"):
					if SMC_END in self.pending:
						self.mode = "drop_blanks"
					self.pending.clear()
					self.line_start = True
				continue

			if self.mode == "compact":
				self.pending.append(byte)
				if byte == ord("\n"):
					if COMPACT_END in self.pending:
						self.mode = "drop_blanks"
					self.pending.clear()
					self.line_start = True
				continue

			if self.mode == "drop_line":
				if byte == ord("\n"):
					self.mode = "normal"
					self.line_start = True
				continue

			if self.mode == "timestamp":
				self.pending.append(byte)
				if byte == ord("\n"):
					if SMC_WARNING in self.pending:
						self.mode = "smc"
					else:
						output.extend(self.pending)
						self.mode = "normal"
					self.pending.clear()
					self.line_start = True
				continue

			if self.mode == "month":
				self.pending.append(byte)
				candidate = bytes(self.pending)
				if candidate in MONTH_PREFIXES:
					self.mode = "timestamp"
				elif not any(prefix.startswith(candidate) for prefix in MONTH_PREFIXES):
					output.extend(self.pending)
					self.pending.clear()
					self.mode = "normal"
					self.line_start = byte == ord("\n")
				continue

			if self.mode == "pcie":
				self.pending.append(byte)
				candidate = bytes(self.pending)
				if candidate == PCIE_WARNING:
					self.pending.clear()
					self.mode = "drop_line"
				elif not PCIE_WARNING.startswith(candidate):
					output.extend(self.pending)
					self.pending.clear()
					self.mode = "normal"
					self.line_start = byte == ord("\n")
				continue

			if self.mode == "compact_start":
				self.pending.append(byte)
				candidate = bytes(self.pending)
				if candidate == COMPACT_WARNING:
					self.pending.clear()
					self.mode = "compact"
				elif not COMPACT_WARNING.startswith(candidate):
					output.extend(self.pending)
					self.pending.clear()
					self.mode = "normal"
					self.line_start = byte == ord("\n")
				continue

			if self.line_start and byte == PCIE_WARNING[0]:
				self.pending.append(byte)
				self.mode = "pcie"
			elif self.line_start and byte == COMPACT_WARNING[0]:
				self.pending.append(byte)
				self.mode = "compact_start"
			elif self.line_start and any(prefix[0] == byte for prefix in MONTH_PREFIXES):
				self.pending.append(byte)
				self.mode = "month"
			else:
				output.append(byte)
				self.line_start = byte == ord("\n")

		return bytes(output)

	def finish(self):
		if self.mode in ("normal", "pcie", "compact_start", "month", "timestamp"):
			output = bytes(self.pending)
		else:
			output = b""
		self.pending.clear()
		return output


console_filter = ConsoleFilter()


def read_filtered(fd):
	try:
		data = os.read(fd, 1024)
	except OSError:
		return console_filter.finish()
	if not data:
		return console_filter.finish()
	return console_filter.feed(data) or b"\x00"


CONSOLE_PORT = os.environ.get("C2900_CONSOLE_PORT", "5554")
status = pty.spawn(
	["socat", "-,raw,echo=0", f"TCP:127.0.0.1:{CONSOLE_PORT}"],
	master_read=read_filtered,
)
raise SystemExit(os.waitstatus_to_exitcode(status))
