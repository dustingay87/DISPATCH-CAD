#!/usr/bin/env python3
"""Lightweight UDP/TCP -> HTTP TAIP relay.

Cellular gateways that only support TAIP over UDP/TCP can send packets to the
public IP running this relay. The relay extracts TAIP sentences and POSTs them
to the VolCAD /taip/ingest endpoint.

Environment variables:
  TAIP_RELAY_TARGET      URL to POST to (default: https://cad.dispatchtodiscipleship.net/taip/ingest)
  TAIP_RELAY_UDP_PORT    UDP port to listen on (default: 5005, set 0 to disable)
  TAIP_RELAY_TCP_PORT    TCP port to listen on (default: 5005, set 0 to disable)
  TAIP_RELAY_BIND_HOST   Bind address (default: 0.0.0.0)
"""

import argparse
import json
import os
import re
import socket
import threading
import time
import urllib.request
from datetime import datetime, timezone
from typing import List, Optional

TAIP_SENTENCE_RE = re.compile(r'>[^<]+<')


def log(msg: str) -> None:
    print(f'{datetime.now(timezone.utc).isoformat()} {msg}', flush=True)


def extract_taip_sentences(text: str) -> List[str]:
    """Pull one or more TAIP sentences out of a raw byte stream."""
    return TAIP_SENTENCE_RE.findall(text)


def post_sentence(target: str, sentence: str) -> bool:
    """POST a single raw TAIP sentence to the VolCAD ingest endpoint."""
    body = json.dumps({'raw': sentence}).encode('utf-8')
    req = urllib.request.Request(
        target,
        data=body,
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
            log(f'POST OK {resp.status} sentence={sentence[:60]}')
            return True
    except urllib.error.HTTPError as e:
        log(f'POST ERR {e.code} {e.reason} sentence={sentence[:60]}')
        return False
    except Exception as e:
        log(f'POST FAIL {e} sentence={sentence[:60]}')
        return False


def _process_text(target: str, text: str, source: str) -> None:
    sentences = extract_taip_sentences(text)
    if not sentences:
        # Some gateways send bare key=value lines without >...< framing.
        stripped = text.strip()
        if stripped:
            log(f'No framed sentence from {source}; sending raw line as-is')
            post_sentence(target, stripped)
        return
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        post_sentence(target, sentence)


def run_udp_listener(target: str, host: str, port: int) -> None:
    if port <= 0:
        return
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
    except OSError as e:
        log(f'UDP bind error on {host}:{port}: {e}')
        return
    log(f'TAIP UDP relay listening on {host}:{port} -> {target}')
    while True:
        try:
            data, addr = sock.recvfrom(2048)
            text = data.decode('utf-8', errors='ignore')
            _process_text(target, text, f'UDP {addr}')
        except Exception as e:
            log(f'UDP receive error: {e}')


def _tcp_client_handler(target: str, conn: socket.socket, addr: str) -> None:
    buffer = ''
    try:
        while True:
            data = conn.recv(2048)
            if not data:
                break
            buffer += data.decode('utf-8', errors='ignore')
            while True:
                start = buffer.find('>')
                end = buffer.find('<', start)
                if start == -1 or end == -1:
                    break
                sentence = buffer[start:end + 1]
                buffer = buffer[end + 1:]
                sentence = sentence.strip()
                if sentence:
                    post_sentence(target, sentence)
            # Limit unbounded buffer growth
            if len(buffer) > 8192:
                log(f'TCP {addr} buffer overflow, flushing')
                _process_text(target, buffer, f'TCP {addr}')
                buffer = ''
    except Exception as e:
        log(f'TCP client error {addr}: {e}')
    finally:
        try:
            conn.close()
        except Exception:
            pass


def run_tcp_listener(target: str, host: str, port: int) -> None:
    if port <= 0:
        return
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server.bind((host, port))
        server.listen(5)
    except OSError as e:
        log(f'TCP bind error on {host}:{port}: {e}')
        return
    log(f'TAIP TCP relay listening on {host}:{port} -> {target}')
    while True:
        try:
            conn, addr = server.accept()
            threading.Thread(
                target=_tcp_client_handler,
                args=(target, conn, addr),
                daemon=True,
            ).start()
        except Exception as e:
            log(f'TCP accept error: {e}')


def main() -> None:
    parser = argparse.ArgumentParser(description='TAIP UDP/TCP to HTTP relay')
    parser.add_argument('--target', default=os.getenv('TAIP_RELAY_TARGET', 'https://cad.dispatchtodiscipleship.net/taip/ingest'))
    parser.add_argument('--udp-port', type=int, default=int(os.getenv('TAIP_RELAY_UDP_PORT', '5005')))
    parser.add_argument('--tcp-port', type=int, default=int(os.getenv('TAIP_RELAY_TCP_PORT', '5005')))
    parser.add_argument('--bind', default=os.getenv('TAIP_RELAY_BIND_HOST', '0.0.0.0'))
    args = parser.parse_args()

    if args.udp_port <= 0 and args.tcp_port <= 0:
        log('Nothing to do: both UDP and TCP ports are disabled')
        return

    if args.udp_port > 0:
        threading.Thread(
            target=run_udp_listener,
            args=(args.target, args.bind, args.udp_port),
            daemon=True,
        ).start()

    if args.tcp_port > 0:
        threading.Thread(
            target=run_tcp_listener,
            args=(args.target, args.bind, args.tcp_port),
            daemon=True,
        ).start()

    log(f'TAIP relay started. target={args.target} udp={args.udp_port} tcp={args.tcp_port}')
    while True:
        time.sleep(1)


if __name__ == '__main__':
    main()
