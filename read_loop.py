#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""
Legge continuamente l'ora dalla centralina INIM e stampa il delta rispetto al server.

Uso:
    python3 read_loop.py [alarm_ip] [alarm_port]

Default: 192.168.0.125:5004
"""
import socket
import struct
import time
import datetime
import sys

ALARM_IP      = sys.argv[1] if len(sys.argv) > 1 else "192.168.0.125"
ALARM_PORT    = int(sys.argv[2]) if len(sys.argv) > 2 else 5004
TZ_OFFSET_MIN = int(sys.argv[3]) if len(sys.argv) > 3 else 60   # deve corrispondere a tz_offset_min nel config
INTERVAL      = 3  # secondi tra una lettura e l'altra

tz_byte  = TZ_OFFSET_MIN & 0xFF
read_hdr = bytes([0x00, 0x00, 0x00, 0x0D, tz_byte, 0x00, 0x04])
READ_CMD = read_hdr + bytes([sum(read_hdr) & 0xFF])
EPOCH    = datetime.datetime(2000, 1, 1)

print(f"Lettura ora centralina {ALARM_IP}:{ALARM_PORT} ogni {INTERVAL}s — Ctrl+C per fermare\n")

while True:
    t_send = datetime.datetime.now()
    try:
        with socket.create_connection((ALARM_IP, ALARM_PORT), timeout=2) as s:
            s.sendall(READ_CMD)
            s.settimeout(2)
            resp = s.recv(5)

        if len(resp) < 4:
            print(f"{t_send.strftime('%H:%M:%S')}  →  risposta troppo corta: {resp.hex()}")
        else:
            ts_sec     = struct.unpack_from("<I", resp)[0]
            panel_time = EPOCH + datetime.timedelta(seconds=ts_sec)
            server_now = datetime.datetime.now()
            delta      = (panel_time - server_now).total_seconds()
            sign       = "+" if delta >= 0 else ""
            print(f"{server_now.strftime('%H:%M:%S')}  →  centralina: {panel_time.strftime('%H:%M:%S')}  delta: {sign}{delta:.1f}s")

    except Exception as e:
        print(f"{t_send.strftime('%H:%M:%S')}  →  errore: {e}")

    time.sleep(INTERVAL)
