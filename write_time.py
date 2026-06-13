#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""
Imposta l'ora corrente sulla centralina INIM (singolo comando write).
Usare insieme a read_loop.py per verificare la precisione della scrittura.

Uso:
    python3 write_time.py [alarm_ip] [alarm_port] [security_code] [timezone_name] [tz_offset_min]

Default: 192.168.0.125 5004 091204 Europe/Rome 60
"""
import socket
import struct
import time
import datetime
import sys
from zoneinfo import ZoneInfo

ALARM_IP        = sys.argv[1] if len(sys.argv) > 1 else "192.168.0.125"
ALARM_PORT      = int(sys.argv[2]) if len(sys.argv) > 2 else 5004
SECURITY_CODE   = sys.argv[3] if len(sys.argv) > 3 else "091204"
TIMEZONE_NAME   = sys.argv[4] if len(sys.argv) > 4 else "Europe/Rome"
TZ_OFFSET_MIN   = int(sys.argv[5]) if len(sys.argv) > 5 else 60   # deve corrispondere a tz_offset_min nel config

EPOCH = datetime.datetime(2000, 1, 1)

def chk(b: bytes) -> int:
    return sum(b) & 0xFF

def write12(addr: int, d0: int, extra: bytes) -> bytes:
    """Write con 4 byte extra (d2=0x04): usato per timestamp e mese."""
    hdr = bytes([0x01, 0x00, (addr >> 8) & 0xFF, addr & 0xFF, d0, 0x00, 0x04])
    return hdr + bytes([chk(hdr)]) + extra

def write_short(addr: int, d0: int, extra: bytes) -> bytes:
    """Write con N byte extra (d2=len(extra)): usato per anno e giorno settimana."""
    hdr = bytes([0x01, 0x00, (addr >> 8) & 0xFF, addr & 0xFF, d0, 0x00, len(extra)])
    return hdr + bytes([chk(hdr)]) + extra

def send_recv(s, data: bytes, label: str, timeout=5) -> bytes:
    s.sendall(data)
    s.settimeout(timeout)
    try:
        resp = s.recv(16)
    except socket.timeout:
        resp = b""
    print(f"  {label:30s} → {resp.hex() if resp else '(nessuna)'}")
    return resp

def read_cmd(addr: int, d0: int, length: int) -> bytes:
    hdr = bytes([0x00, 0x00, (addr >> 8) & 0xFF, addr & 0xFF, d0, 0x00, length])
    return hdr + bytes([chk(hdr)])

print(f"Connessione a {ALARM_IP}:{ALARM_PORT} ...")
with socket.create_connection((ALARM_IP, ALARM_PORT), timeout=3) as s:

    # 1. Security code
    #s.sendall(SECURITY_CODE.encode('ascii'))
    time.sleep(0.5)

    # 2. Login read + read preparatorie (come SmartLeague)
    #send_recv(s, read_cmd(0x0040, 0x00, 0x0c), "read 0x0040 (login)",     timeout=3)
    #send_recv(s, read_cmd(0x000D, 0x7d, 0x01), "read 0x000D d0=0x7d len=1", timeout=3)
    #send_recv(s, read_cmd(0x0139, 0x7b, 0x06), "read 0x0139 d0=0x7b len=6", timeout=3)
    #send_recv(s, read_cmd(0x000D, 0x28, 0x02), "read 0x000D d0=0x28 len=2", timeout=3)

    # 3. Calcola il timestamp nel momento dell'invio
    now = datetime.datetime.now(tz=ZoneInfo(TIMEZONE_NAME)).replace(tzinfo=None)
    ts_sec = int((now - EPOCH).total_seconds())
    ts    = struct.pack("<I", ts_sec)
    tz    = TZ_OFFSET_MIN & 0xFF
    mon   = now.month
    yr    = now.year - 1990
    wd    = now.weekday()      # 0=Lun ... 5=Sab ... 6=Dom (come SmartLeague)

    print(f"Orario da inviare: {now.strftime('%H:%M:%S')}  ts={ts_sec}  tz={tz}  month={mon}  year-1990={yr}  wd={wd}")
    print()

    t_before = datetime.datetime.now()

    # 3. Write sequenza completa come SmartLeague (4 write)
    # Write 1: addr 0x000D — tz_offset + timestamp (risposta attesa in ~2s: centralina commita RTC)
    send_recv(s, write12(0x000D, tz,  ts),                    "write 0x000D (ts+tz)",    timeout=5)
    # Write 2: addr 0x0165 — mese + timestamp
    send_recv(s, write12(0x0165, mon, ts),                    "write 0x0165 (month)",    timeout=3)
    # Write 3: addr 0x0172 — anno-1990 + 0x04 (trigger reboot)
    send_recv(s, write_short(0x0172, yr, b'\x04'),            "write 0x0172 (year+0x04)",timeout=3)
    # Write 4: addr 0x0040 — giorno settimana ISO + 0x00 0x00
    send_recv(s, write_short(0x0040, wd, b'\x00\x00'),        "write 0x0040 (weekday)",  timeout=3)

    t_after = datetime.datetime.now()

    # Lascia la connessione aperta 1s (come SmartLeague)
    time.sleep(1.0)

print()
print(f"t_before: {t_before.strftime('%H:%M:%S.%f')[:-3]}  t_after: {t_after.strftime('%H:%M:%S.%f')[:-3]}")
print()
print("Dopo il reboot (~6s) controlla read_loop.py: il delta dovrebbe essere ~0.")
