#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""
TCP Proxy / Sniffer per centralina INIM
----------------------------------------
Mettiti in mezzo tra SmartLeague e la centralina per catturare
i byte scambiati (utile per fare reverse engineering del protocollo).

Utilizzo:
    python3 sniffer.py --alarm-ip <IP_CENTRALINA> --alarm-port <PORTA_CENTRALINA> [--listen-port <PORTA_LOCALE>]

Poi in SmartLeague configura la connessione verso 127.0.0.1:<PORTA_LOCALE>
(o verso l'IP di questa macchina se SmartLeague gira su un altro PC).

Output: tutti i byte vengono stampati su stdout in formato hex + ascii,
        e salvati in sniffer_<timestamp>.log
"""

import argparse
import binascii
import datetime
import socket
import sys
import threading
import os

# ─────────────────────────────────────────────────────────────────────────────
# Utility
# ─────────────────────────────────────────────────────────────────────────────

def hex_dump(data: bytes, prefix: str = "") -> str:
    """Formatta i byte in hex + ascii, stile wireshark."""
    lines = []
    for i in range(0, len(data), 16):
        chunk = data[i:i+16]
        hex_part  = " ".join(f"{b:02x}" for b in chunk)
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{prefix}  {i:04x}  {hex_part:<47}  |{ascii_part}|")
    return "\n".join(lines)


def log(msg: str, log_file):
    ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
    line = f"[{ts}] {msg}"
    print(line)
    log_file.write(line + "\n")
    log_file.flush()


# ─────────────────────────────────────────────────────────────────────────────
# Forwarding bidirezionale
# ─────────────────────────────────────────────────────────────────────────────

def forward(src: socket.socket, dst: socket.socket,
            direction: str, log_file, stop_event: threading.Event):
    """Legge da src, scrive su dst, logga tutto."""
    try:
        while not stop_event.is_set():
            src.settimeout(1.0)
            try:
                data = src.recv(4096)
            except socket.timeout:
                continue
            if not data:
                break
            # Logga
            hex_raw = binascii.hexlify(data).decode()
            log(f"{direction}  ({len(data)} byte)  hex: {hex_raw}", log_file)
            dump = hex_dump(data, prefix="   ")
            if dump:
                print(dump)
                log_file.write(dump + "\n")
                log_file.flush()
            # Invia all'altro lato
            dst.sendall(data)
    except Exception as e:
        if not stop_event.is_set():
            log(f"[!] {direction} errore: {e}", log_file)
    finally:
        stop_event.set()


# ─────────────────────────────────────────────────────────────────────────────
# Gestione connessione
# ─────────────────────────────────────────────────────────────────────────────

def handle_client(client_sock: socket.socket, client_addr,
                  alarm_ip: str, alarm_port: int, log_file):
    log(f">>> Nuova connessione da {client_addr}", log_file)

    try:
        alarm_sock = socket.create_connection((alarm_ip, alarm_port), timeout=10)
        log(f">>> Connesso alla centralina {alarm_ip}:{alarm_port}", log_file)
    except Exception as e:
        log(f"[!] Impossibile connettersi alla centralina: {e}", log_file)
        client_sock.close()
        return

    stop_event = threading.Event()

    t1 = threading.Thread(
        target=forward,
        args=(client_sock, alarm_sock, "SmartLeague → Centralina", log_file, stop_event),
        daemon=True
    )
    t2 = threading.Thread(
        target=forward,
        args=(alarm_sock, client_sock, "Centralina → SmartLeague", log_file, stop_event),
        daemon=True
    )
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    log(f"<<< Connessione chiusa da {client_addr}", log_file)
    client_sock.close()
    alarm_sock.close()


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="TCP Proxy/Sniffer per centralina INIM (reverse engineering)"
    )
    parser.add_argument("--alarm-ip",    required=True,       help="IP della centralina INIM")
    parser.add_argument("--alarm-port",  required=True, type=int, help="Porta TCP della centralina")
    parser.add_argument("--listen-port", default=10001, type=int, help="Porta locale su cui ascoltare (default: 10001)")
    args = parser.parse_args()

    log_filename = f"sniffer_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    log_file = open(log_filename, "w", encoding="utf-8")

    print(f"")
    print(f"  ┌─ INIM TCP Sniffer ───────────────────────────────────────────┐")
    print(f"  │  Ascolta su          : 0.0.0.0:{args.listen_port}            ")
    print(f"  │  Centralina target   : {args.alarm_ip}:{args.alarm_port}     ")
    print(f"  │  Log file            : {os.path.abspath(log_filename)}       ")
    print(f"  │                                                               ")
    print(f"  │  Configura SmartLeague → connessione verso questo PC         ")
    print(f"  │  sulla porta {args.listen_port}, poi esegui l'operazione      ")
    print(f"  │  che vuoi catturare (es. impostazione ora).                  ")
    print(f"  │  Ctrl+C per fermare.                                         ")
    print(f"  └──────────────────────────────────────────────────────────────┘")
    print(f"")

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", args.listen_port))
    server.listen(5)
    server.settimeout(1.0)

    try:
        while True:
            try:
                client_sock, client_addr = server.accept()
            except socket.timeout:
                continue
            t = threading.Thread(
                target=handle_client,
                args=(client_sock, client_addr, args.alarm_ip, args.alarm_port, log_file),
                daemon=True
            )
            t.start()
    except KeyboardInterrupt:
        print("\n[*] Sniffer fermato.")
    finally:
        server.close()
        log_file.close()


if __name__ == "__main__":
    main()
