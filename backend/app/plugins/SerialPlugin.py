"""Generic serial communication plugin.

Provides basic serial port operations (send, read, send+read)
without depending on lge.auto.
"""

import serial


class SerialPlugin:
    """Generic serial communication plugin."""

    def __init__(self, port: str = "", bps: int = 115200):
        self.port = port
        self.bps = bps
        self._serial: serial.Serial | None = None

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def Connect(self) -> str:
        """Open the serial port."""
        if self._serial and self._serial.is_open:
            return "Already connected"
        self._serial = serial.Serial(self.port, self.bps, timeout=1)
        return f"Connected to {self.port} @ {self.bps}"

    def Disconnect(self) -> str:
        """Close the serial port."""
        if self._serial and self._serial.is_open:
            self._serial.close()
        self._serial = None
        return "Disconnected"

    def IsConnected(self) -> bool:
        """Check if the serial port is open."""
        return self._serial is not None and self._serial.is_open

    # ------------------------------------------------------------------
    # I/O
    # ------------------------------------------------------------------

    def SendCommand(self, command: str, encoding: str = "utf-8",
                    append_newline: bool = True) -> str:
        """Send a string command to the serial port."""
        if not self._serial or not self._serial.is_open:
            raise RuntimeError("Serial port not connected")
        data = command
        if append_newline and not data.endswith("\n"):
            data += "\n"
        self._serial.write(data.encode(encoding))
        return "OK"

    def ReadLine(self, timeout: float = 1.0) -> str:
        """Read one line from the serial port."""
        if not self._serial or not self._serial.is_open:
            raise RuntimeError("Serial port not connected")
        self._serial.timeout = timeout
        return self._serial.readline().decode("utf-8", errors="replace").strip()

    def ReadAll(self, timeout: float = 1.0) -> str:
        """Read all available data from the serial port."""
        if not self._serial or not self._serial.is_open:
            raise RuntimeError("Serial port not connected")
        self._serial.timeout = timeout
        data = self._serial.read(self._serial.in_waiting or 1)
        return data.decode("utf-8", errors="replace")

    def SendAndRead(self, command: str, timeout: float = 1.0,
                    encoding: str = "utf-8") -> str:
        """Send a command and read the response line."""
        self.SendCommand(command, encoding)
        return self.ReadLine(timeout)

    def SendHex(self, hex_string: str) -> str:
        """Send raw hex bytes (e.g. 'FF 01 A0')."""
        if not self._serial or not self._serial.is_open:
            raise RuntimeError("Serial port not connected")
        raw = bytes.fromhex(hex_string.replace(" ", ""))
        self._serial.write(raw)
        return f"Sent {len(raw)} bytes"

    def ReadHex(self, count: int = 1, timeout: float = 1.0) -> str:
        """Read N bytes and return as hex string."""
        if not self._serial or not self._serial.is_open:
            raise RuntimeError("Serial port not connected")
        self._serial.timeout = timeout
        data = self._serial.read(count)
        return data.hex(" ").upper()

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def SetBaudrate(self, baudrate: int) -> str:
        """Change the baud rate."""
        self.bps = baudrate
        if self._serial and self._serial.is_open:
            self._serial.baudrate = baudrate
        return f"Baudrate set to {baudrate}"

    def GetPortInfo(self) -> str:
        """Return current port and baud rate info."""
        connected = self.IsConnected()
        return f"port={self.port}, baud={self.bps}, connected={connected}"
