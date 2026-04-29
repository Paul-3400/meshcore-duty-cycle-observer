#!/usr/bin/env python3
""""
MeshCore Duty-Cycle-Observer v1.0.0
Passiver LoRa-Paket-Monitor fuer MeshCore-Netzwerke.
Empfaengt Pakete via MQTT, dekodiert Header und Payload,
berechnet LoRa Airtime und Duty Cycle, loggt alles in CSV.
Konfiguration: config.json
Protokoll-Referenz: siehe PROTOCOL.md
GitHub: https://github.com/Paul-3400/meshcore-duty-cycle-observer
"""


import json
import math
import signal
import sys
import csv
import os
import time as _time
import struct
import threading
from datetime import datetime, timedelta, date
from collections import defaultdict
import paho.mqtt.client as mqtt
import hashlib
import io as _io
from Crypto.Cipher import AES
from Crypto.Hash import HMAC, SHA256

# --- Konfiguration laden ---
CONFIG_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "config.json")

if not os.path.exists(CONFIG_FILE):
    print(f"FEHLER: {CONFIG_FILE} nicht gefunden!")
    print("Kopiere config.example.json nach config.json und passe sie an.")
    sys.exit(1)

with open(CONFIG_FILE, "r") as f:
    CONFIG = json.load(f)

MQTT_BROKER = CONFIG["mqtt"]["broker"]
MQTT_PORT = CONFIG["mqtt"]["port"]
MQTT_TOPIC = CONFIG["mqtt"]["topic"]

SF = CONFIG["lora"]["spreading_factor"]
BW_HZ = CONFIG["lora"]["bandwidth_hz"]
CR = CONFIG["lora"]["coding_rate"]
PREAMBLE = CONFIG["lora"]["preamble"]
DC_WINDOW_SEC = CONFIG["duty_cycle"]["window_sec"]
DC_LIMIT = CONFIG["duty_cycle"]["limit_pct"]
DC_WARNING = CONFIG["duty_cycle"]["warning_pct"]

LOG_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    CONFIG["logging"]["log_dir"])

BUFFER_DELAY_SEC = CONFIG["buffer"]["delay_sec"]
packet_buffer = {}
buffer_lock = threading.Lock()
duplicate_count = 0

CHANNEL_SECRETS = {}


def _init_channels():
    manual = {}
    for name in CONFIG.get("channels", []):
        manual[name] = hashlib.sha256(
            name.encode("utf-8")).hexdigest()[:32]
    auto_hash = [
        "#basel", "#prove", "#info", "#aargau",
        "#emergency", "#berlin", "#solothurn",
        "#salzburg"]
    for name in auto_hash:
        manual[name] = hashlib.sha256(
            name.encode("utf-8")).hexdigest()[:32]
    for name, secret_hex in manual.items():
        secret = bytes.fromhex(secret_hex)[:16]
        ch_hash = SHA256.new(secret).hexdigest()[0:2]
        CHANNEL_SECRETS[ch_hash] = {
            "name": name, "secret": secret}


def decrypt_grp_txt(payload):
    try:
        pk_buf = _io.BytesIO(payload)
        chan_hash = pk_buf.read(1).hex()
        cipher_mac = pk_buf.read(2)
        msg = pk_buf.read()
        if chan_hash not in CHANNEL_SECRETS:
            return {
                "channel": "?(" + chan_hash + ")",
                "message": None,
                "chan_hash": chan_hash}
        ch = CHANNEL_SECRETS[chan_hash]
        secret = ch["secret"]
        h = HMAC.new(secret, digestmod=SHA256)
        h.update(msg)
        if h.digest()[0:2] != cipher_mac:
            return {
                "channel": "?(" + chan_hash + ")",
                "message": None,
                "chan_hash": chan_hash}
        if len(msg) % 16 != 0:
            msg_padded = msg + b"\x00" * (
                16 - len(msg) % 16)
        else:
            msg_padded = msg
        cipher = AES.new(secret, AES.MODE_ECB)
        decrypted = cipher.decrypt(msg_padded)
        message = decrypted[5:].strip(
            b"\x00").decode("utf-8", "replace")
        return {
            "channel": ch["name"],
            "message": message,
            "chan_hash": chan_hash}
    except Exception:
        return None


PAYLOAD_TYPES = {
    0: "REQ", 1: "RESPONSE", 2: "TXT_MSG",
    3: "ACK", 4: "ADVERT", 5: "GRP_TXT",
    6: "GRP_DATA", 7: "ANON_REQ", 8: "PATH",
    9: "TRACE", 10: "MULTIPART", 11: "CONTROL",
    15: "RAW_CUSTOM"}

ROUTE_TYPES = {
    0: "T_FLOOD", 1: "FLOOD",
    2: "DIRECT", 3: "T_DIRECT"}

CSV_HEADER = [
    "packet_hash", "payload_hash", "timestamp", "packet_type", "route_type",
    "bytes", "airtime_ms", "hops", "path_hash_bytes", "rssi", "snr",
    "source_hash", "source_name", "source_collision",
    "dest_hash", "dest_name", "dest_collision",
    "node_mode", "node_key",
    "lat", "lon",
    "region_code", "channel", "message",
    "window_dc_pct"]

csv_current_date = None
csv_rows_written = 0

def reset_daily_stats():
    """Zeitungslesen-Prinzip: Jeden Tag frisch starten."""
    global total_pkts, total_air_ms
    known_nodes_by_hash.clear()
    known_nodes_by_key.clear()
    type_stats.clear()
    sender_stats.clear()
    total_pkts = 0
    total_air_ms = 0.0
    print("  Tages-Reset: Alle Zaehler und Kollisions-Dicts zurueckgesetzt")


def open_csv_file():
    """Kompatibilitaets-Stub (nicht mehr benoetigt)."""
    pass

def reset_daily_stats():
    """Zeitungslesen-Prinzip: Jeden Tag frisch starten."""
    global total_pkts, total_air_ms
    known_nodes_by_hash.clear()
    known_nodes_by_key.clear()
    type_stats.clear()
    sender_stats.clear()
    total_pkts = 0
    total_air_ms = 0.0
    print("  Tages-Reset: Alle Zaehler und Kollisions-Dicts zurueckgesetzt")
    
def write_csv_row(
        pkt_hash, payload_hash, timestamp, ptype_name, rtype_name,
        pkt_bytes, airtime_ms, hops, path_hash_bytes, rssi, snr,
        source_hash, source_name, source_collision,
        dest_hash, dest_name, dest_collision,
        node_mode, node_key,
        lat, lon, region_code,
        channel, message, window_dc):
    global csv_current_date, csv_rows_written
    today = date.today()
    filename = "duty_cycle_" + today.strftime(
        "%Y-%m-%d") + ".csv"
    filepath = os.path.join(LOG_DIR, filename)
    write_header = (not os.path.exists(filepath)
                    or os.path.getsize(filepath) == 0)

    if csv_current_date != today:
        if csv_current_date is not None:
            reset_daily_stats()

        if write_header:
            print("  CSV: Neue Datei " + filename)
        else:
            print("  CSV: Fortgesetzt " + filename)
        csv_current_date = today
        csv_rows_written = 0

    try:
        with open(filepath, "a", newline="",
                  encoding="utf-8") as f:
            writer = csv.writer(f, delimiter=";")
            if write_header:
                writer.writerow(CSV_HEADER)
            writer.writerow([
                pkt_hash, payload_hash, timestamp,
                ptype_name, rtype_name,
                pkt_bytes, round(airtime_ms, 1),
                hops, path_hash_bytes, rssi, snr,
                source_hash, source_name,
                source_collision,
                dest_hash, dest_name,
                dest_collision,
                node_mode, node_key,
                lat, lon, region_code,
                channel, message,
                round(window_dc, 4)])
        csv_rows_written += 1
    except Exception as e:
        print("  CSV-Schreibfehler: " + str(e))


def close_csv_file():
    """Tagesabschluss: Zusammenfassung loggen und Zaehler zuruecksetzen."""
    global csv_current_date, csv_rows_written
    if csv_current_date is not None:
        print("\n" + "=" * 60)
        print("  CSV-Tagesabschluss: " + str(csv_current_date))
        print("  Zeilen geschrieben: " + str(csv_rows_written))
        print("=" * 60 + "\n")
    csv_current_date = None
    csv_rows_written = 0


def schedule_midnight_rotation():
    """Mitternacht-Wecker: Plant den taeglichen CSV-Reset."""
    now = datetime.now()
    midnight = now.replace(
        hour=0, minute=0, second=0, microsecond=0
    ) + timedelta(days=1)
    seconds_until = (midnight - now).total_seconds()
    print("  Naechste CSV-Rotation in "
          + str(round(seconds_until)) + "s ("
          + midnight.strftime("%Y-%m-%d %H:%M") + ")")

    def _do_rotation():
        print("\n*** MITTERNACHT-ROTATION ***")
        close_csv_file()
        # Neue leere CSV mit Header erstellen
        today = date.today()
        filename = ("duty_cycle_"
                     + today.strftime("%Y-%m-%d") + ".csv")
        filepath = os.path.join(LOG_DIR, filename)
        try:
            with open(filepath, "w", newline="",
                       encoding="utf-8") as f:
                writer = csv.writer(f, delimiter=";")
                writer.writerow(CSV_HEADER)
            print("  Neue CSV erstellt: " + filename)
        except Exception as e:
            print("  Fehler beim Erstellen: " + str(e))
        # Wecker neu stellen
        schedule_midnight_rotation()

    timer = threading.Timer(seconds_until, _do_rotation)
    timer.daemon = True
    timer.start()


def decode_path_len(plb):
    hop_count = plb & 0x3F
    bph = ((plb >> 6) & 0x03) + 1
    if bph == 4:
        return plb, 1
    return hop_count * bph, bph


def extract_name(app_data):
    seqs = []
    cur = []
    for b in app_data:
        if 32 <= b <= 126:
            cur.append(chr(b))
        else:
            if len(cur) >= 2:
                seqs.append("".join(cur))
            cur = []
    if len(cur) >= 2:
        seqs.append("".join(cur))
    if seqs:
        return max(seqs, key=len)
    return None


def extract_payload_hex(raw_hex):
    try:
        data = bytes.fromhex(raw_hex)
        if len(data) < 2:
            return None
        header = data[0]
        rtype = header & 0x03
        has_transport = rtype in [0, 3]
        offset = 5 if has_transport else 1
        if len(data) <= offset:
            return None
        plb = data[offset]
        offset += 1
        path_byte_len, _ = decode_path_len(plb)
        if len(data) < offset + path_byte_len:
            return None
        offset += path_byte_len
        payload = data[offset:]
        if len(payload) == 0:
            return None
        return payload.hex()
    except Exception:
        return None


def parse_packet(raw_hex):
    try:
        data = bytes.fromhex(raw_hex)
        if len(data) < 2:
            return None
        header = data[0]
        version = (header >> 6) & 0x03
        ptype = (header >> 2) & 0x0F
        rtype = header & 0x03
        if version != 0:
            return None
        has_transport = rtype in [0, 3]
        offset = 5 if has_transport else 1
        if len(data) <= offset:
            return None
        region_code = ""
        if has_transport and len(data) >= 5:
            region_code = (
                format(data[1], "02X")
                + format(data[2], "02X"))
        plb = data[offset]
        offset += 1
        path_byte_len, path_hash_bytes = (
            decode_path_len(plb))
        if len(data) < offset + path_byte_len:
            return None
        path_data = data[offset:offset + path_byte_len]
        offset += path_byte_len
        payload = data[offset:]
        hops = []
        if path_hash_bytes > 0 and len(path_data) > 0:
            for i in range(
                    0, len(path_data), path_hash_bytes):
                hops.append(
                    path_data[i:i + path_hash_bytes]
                    .hex())
        result = {
            "ptype": ptype,
            "ptype_name": PAYLOAD_TYPES.get(
                ptype, "T" + str(ptype)),
            "rtype_name": ROUTE_TYPES.get(
                rtype, "R" + str(rtype)),
            "hop_count": len(hops),
            "header_bytes": offset,
            "region_code": region_code,
            "source_hash": "",
            "dest_hash": "",
        }
        if ptype == 4 and len(payload) >= 100:
            pk = payload[0:32].hex()
            result["pub_key_short"] = pk[:8].upper()
            result["source_hash"] = format(
                payload[0], "02X")
            app = payload[100:]
            if len(app) > 0:
                fb = app[0]
                types = {
                    1: "Chat", 2: "Repeater",
                    3: "Room", 4: "Sensor"}
                result["adv_mode"] = types.get(
                    fb & 0x0F, "T" + str(fb & 0x0F))
                name = extract_name(app)
                if name:
                    result["node_name"] = name
                has_gps = (fb >> 4) & 0x01
                if has_gps and len(payload) >= 109:
                    try:
                        lat = struct.unpack(
                            "<i", payload[101:105])[0] / 1_000_000
                        lon = struct.unpack(
                            "<i", payload[105:109])[0] / 1_000_000
                        if abs(lat) < 0.01 and abs(lon) <  0.01:
                            pass
                        elif -90 <= lat <= 90:
                            if -180 <= lon <= 180:
                                result["latitude"] = (
                                    round(lat, 6))
                                result["longitude"] = (
                                    round(lon, 6))
                    except Exception:
                        pass
        if ptype in [0, 1, 2, 3] and len(payload) >= 2:
            result["dest_hash"] = format(
                payload[0], "02X")
            result["source_hash"] = format(
                payload[1], "02X")
        if ptype == 7 and len(payload) >= 33:
            result["dest_hash"] = format(
                payload[0], "02X")
            result["anon_pub_key"] = (
                payload[1:33].hex())
            result["source_hash"] = format(
                payload[1], "02X")
        if ptype == 5 and len(payload) >= 4:
            grp = decrypt_grp_txt(payload)
            if grp:
                result["grp_channel"] = grp.get(
                    "channel", "?")
                result["grp_message"] = grp.get(
                    "message")
                result["grp_chan_hash"] = grp.get(
                    "chan_hash", "?")
        result["path_hash_bytes"] = path_hash_bytes
        result["payload_hex"] = payload.hex()
        return result
    except Exception:
        return None


def calc_airtime_ms(total_bytes):
    DE = 0
    IH = 0
    t_sym = (2 ** SF / BW_HZ) * 1000
    t_pre = (PREAMBLE + 4.25) * t_sym
    num = (8 * total_bytes - 4 * SF
           + 28 + 16 - 20 * IH)
    den = 4 * (SF - 2 * DE)
    n_pay = 8 + max(
        math.ceil(num / den) * (CR + 4), 0)
    return t_pre + n_pay * t_sym


known_nodes_by_hash = defaultdict(list)
known_nodes_by_key = {}
packet_log = []
type_stats = defaultdict(
    lambda: {"count": 0, "air_ms": 0.0})
sender_stats = defaultdict(
    lambda: {"count": 0, "air_ms": 0.0,
             "name": "?", "types": defaultdict(int)})
total_pkts = 0
total_air_ms = 0.0
start_time = None


def register_node(source_hash, name, mode, key,
                  lat=None, lon=None):
    node_info = {
        "name": name, "mode": mode, "key": key,
        "lat": lat, "lon": lon}
    known_nodes_by_key[key] = node_info
    existing = known_nodes_by_hash[source_hash]
    for n in existing:
        if n["key"] == key:
            n.update(node_info)
            return
    existing.append(node_info)


def lookup_name(hash_val):
    nodes = known_nodes_by_hash.get(hash_val, [])
    if len(nodes) == 1:
        return nodes[0]["name"]
    elif len(nodes) > 1:
        names = [n["name"] for n in nodes]
        return "[" + " | ".join(names) + "]"
    return ""


def get_window_dc():
    now = datetime.now()
    cutoff = now - timedelta(seconds=DC_WINDOW_SEC)
    w_air = sum(
        p["air"] for p in packet_log
        if p["t"] >= cutoff)
    return (w_air / 1000) / DC_WINDOW_SEC * 100


def cleanup():
    global packet_log
    cutoff = datetime.now() - timedelta(
        seconds=DC_WINDOW_SEC + 60)
    packet_log = [
        p for p in packet_log if p["t"] >= cutoff]


def process_packet(pkt_info):
    global total_pkts, total_air_ms
    raw = pkt_info["raw"]
    rssi = pkt_info["rssi"]
    snr = pkt_info["snr"]
    ts = pkt_info["ts"]
    pkt_len = pkt_info["pkt_len"]
    t_short = pkt_info["t_short"]
    pkt_hash = hashlib.sha256(
        bytes.fromhex(raw)
        ).hexdigest()[:16].upper()
    parsed = parse_packet(raw)
    payload_hash = ""
    if parsed:
        p_hex = parsed.get("payload_hex", "")
        if p_hex:
            payload_hash = hashlib.sha256(
                bytes.fromhex(p_hex)
                ).hexdigest()[:16].upper()
    total_pkts += 1
    if not parsed:
        print(
            str(total_pkts).rjust(4) + " | "
            + t_short.rjust(8) + " | "
            + "ERR".rjust(8) + " | "
            + "?".rjust(7) + " | "
            + str(pkt_len).rjust(5) + " | "
            + "?".rjust(8) + " | "
            + "?".rjust(4) + " | "
            + str(rssi).rjust(5) + " | "
            + str(snr).rjust(6) + " | Parse-Fehler")
        wdc = get_window_dc()
        write_csv_row(
            pkt_hash, "", ts, "PARSE_ERR", "?", pkt_len,
            0, 0, 0, rssi, snr, "", "", 0, "", "", 0,
            "", "", "", "", "", wdc)
        return
    air = calc_airtime_ms(pkt_len)
    total_air_ms += air
    now = datetime.now()
    packet_log.append({"t": now, "air": air})
    tn = parsed["ptype_name"]
    type_stats[tn]["count"] += 1
    type_stats[tn]["air_ms"] += air
    if air >= 1000:
        a_str = str(round(air / 1000, 2)) + "s"
    else:
        a_str = str(round(air)) + "ms"
    s_hash = parsed.get("source_hash", "")
    d_hash = parsed.get("dest_hash", "")
    region = parsed.get("region_code", "")
    source_name = ""
    dest_name = ""
    node_mode = ""
    node_key = ""
    channel = ""
    message = ""
    lat = ""
    lon = ""
    if parsed["ptype"] == 4:
        pk = parsed.get("pub_key_short", "?")
        name = parsed.get("node_name", "?")
        mode = parsed.get("adv_mode", "?")
        p_lat = parsed.get("latitude")
        p_lon = parsed.get("longitude")
        if pk != "?":
            register_node(
                s_hash, name, mode, pk, p_lat, p_lon)
        source_name = name
        node_mode = mode
        node_key = pk
        if p_lat is not None:
            lat = p_lat
            lon = p_lon
        icons = {"Repeater": "RPT", "Chat": "CHT",
                 "Room": "ROM", "Sensor": "SNS"}
        gps_str = ""
        if p_lat is not None:
            gps_str = (" ["
                + str(round(p_lat, 2)) + "/"
                + str(round(p_lon, 2)) + "]")
        display = (s_hash.rjust(2) + " |    | "
            + icons.get(mode, "?") + " " + name
            + " [" + pk + "]" + gps_str)
    elif parsed["ptype"] == 7:
        anon_key = parsed.get("anon_pub_key", "")
        if anon_key:
            pk_short = anon_key[:8].upper()
            register_node(
                s_hash, "(" + s_hash + ")",
                "?", pk_short)
            node_key = pk_short
        source_name = lookup_name(s_hash)
        dest_name = lookup_name(d_hash)
        s_disp = source_name if source_name else s_hash
        d_disp = dest_name if dest_name else d_hash
        display = (s_hash.rjust(2) + " | "
            + d_hash.rjust(2) + " | "
            + s_disp + " -> " + d_disp)
    elif parsed["ptype"] == 5:
        ch = parsed.get("grp_channel", "?")
        grp_msg = parsed.get("grp_message")
        channel = ch
        message = grp_msg if grp_msg else ""
        if grp_msg:
            display = ("   |    | [" + ch + "] "
                + grp_msg)
        else:
            display = ("   |    | [" + ch
                + "] (verschl.)")
    elif parsed["ptype"] in [0, 1, 2, 3]:
        source_name = lookup_name(s_hash)
        dest_name = lookup_name(d_hash)
        s_disp = source_name if source_name else s_hash
        d_disp = dest_name if dest_name else d_hash
        display = (s_hash.rjust(2) + " | "
            + d_hash.rjust(2) + " | "
            + s_disp + " -> " + d_disp)
    else:
        display = "   |    | " + tn
    if region:
        display = display + " [R:" + region + "]"

    if s_hash:
        sender_stats[s_hash]["count"] += 1
        sender_stats[s_hash]["air_ms"] += air
        sender_stats[s_hash]["types"][tn] += 1
        if source_name:
            sender_stats[s_hash]["name"] = source_name
    print(
        str(total_pkts).rjust(4) + " | "
        + t_short.rjust(8) + " | "
        + tn.rjust(8) + " | "
        + parsed["rtype_name"].rjust(7) + " | "
        + pkt_hash.ljust(16) + " | "
        + str(pkt_len).rjust(5) + " | "
        + a_str.rjust(8) + " | "
        + str(parsed["hop_count"]).rjust(4) + " | "
        + str(rssi).rjust(5) + " | "
        + str(snr).rjust(6) + " | "
        + display)
    s_coll = len(known_nodes_by_hash.get(s_hash, []))
    d_coll = len(known_nodes_by_hash.get(d_hash, []))
    wdc = get_window_dc()
    write_csv_row(
        pkt_hash, payload_hash, ts, tn, parsed["rtype_name"],
        pkt_len, air, parsed["hop_count"],
        parsed.get("path_hash_bytes", 0),
        rssi, snr, s_hash, source_name, s_coll,
        d_hash, dest_name, d_coll,
        node_mode, node_key,
        lat, lon, region,
        channel, message, wdc)
    if total_pkts % 50 == 0:
        cleanup()
        dc = get_window_dc()
        if dc < DC_WARNING:
            st = "OK"
        elif dc < DC_LIMIT:
            st = "WARNUNG"
        else:
            st = "KRITISCH"
        n_resolved = sum(
            1 for h in known_nodes_by_hash
            if len(known_nodes_by_hash[h]) > 0)
        n_collisions = sum(
            1 for h in known_nodes_by_hash
            if len(known_nodes_by_hash[h]) > 1)
        print(
            "\n  DC (" + str(DC_WINDOW_SEC) + "s): "
            + str(round(dc, 3)) + "% "
            + "[" + st + "] | Pkts: "
            + str(total_pkts) + " | "
            + "Dupl: " + str(duplicate_count) + " | "
            + "Knoten: "
            + str(len(known_nodes_by_key)) + " | "
            + "Hashes: " + str(n_resolved)
            + " (" + str(n_collisions)
            + " Kollisionen) | "
            + "CSV: " + str(csv_rows_written) + "\n")


def flush_expired_buffer():
    now = _time.time()
    expired_keys = []
    with buffer_lock:
        for payload_hex, entry in packet_buffer.items():
            age = now - entry["buffer_time"]
            if age >= BUFFER_DELAY_SEC:
                expired_keys.append(payload_hex)
        for key in expired_keys:
            entry = packet_buffer.pop(key)
            process_packet(entry["best"])


def on_connect(client, userdata, flags, rc, properties):
    global start_time
    if rc == 0:
        start_time = datetime.now()
        print("Verbunden mit "
            + MQTT_BROKER + ":" + str(MQTT_PORT))
        print("Topic: " + MQTT_TOPIC)
        print("LoRa: SF" + str(SF) + ", BW "
            + str(BW_HZ / 1000) + "kHz, CR 4/"
            + str(CR + 4))
        print("DC-Fenster: " + str(DC_WINDOW_SEC)
            + "s, Limit: " + str(DC_LIMIT) + "%")
        print("Duplikat-Filter: Payload-Vergleich, "
            + "Puffer: " + str(BUFFER_DELAY_SEC) + "s")
        print("v0.7.2: + Packet Hash aktiv")
        _init_channels()
        open_csv_file()
        print("=" * 119)
        print(
            "  Nr".rjust(4) + " | "
            + "Zeit".rjust(8) + " | "
            + "Typ".rjust(8) + " | "
            + "Route".rjust(7) + " | "
            + "Packet Hash".ljust(16) + " | "
            + "Bytes".rjust(5) + " | "
            + "Airtime".rjust(8) + " | "
            + "Hops".rjust(4) + " | "
            + "RSSI".rjust(5) + " | "
            + "SNR".rjust(6) + " | "
            + "Src | Dst | Info")
        print("-" * 119)
        client.subscribe(MQTT_TOPIC)
    else:
        print("Verbindung fehlgeschlagen: " + str(rc))


def on_message(client, userdata, msg):
    global duplicate_count
    try:
        data = json.loads(msg.payload.decode("utf-8"))
        raw = data.get("raw", "")
        rssi = data.get("RSSI", "?")
        snr = data.get("SNR", "?")
        ts = data.get("timestamp", "")
        if "T" in str(ts):
            t_short = ts.split("T")[1][:8]
        else:
            t_short = "?"
        pkt_len = int(data.get("len", 0))
        try:
            snr_float = float(snr)
        except (ValueError, TypeError):
            snr_float = -999.0
        payload_hex = extract_payload_hex(raw)
        if payload_hex is None:
            payload_hex = raw
        flush_expired_buffer()
        pkt_info = {
            "raw": raw, "rssi": rssi, "snr": snr,
            "snr_float": snr_float, "ts": ts,
            "t_short": t_short, "pkt_len": pkt_len}
        with buffer_lock:
            if payload_hex in packet_buffer:
                existing = packet_buffer[payload_hex]
                existing_snr = (
                    existing["best"]["snr_float"])
                if snr_float < existing_snr:
                    packet_buffer[payload_hex][
                        "best"] = pkt_info
                    print(
                        "     | " + t_short.rjust(8)
                        + " | DUPLIKAT | TAUSCH  | "
                        + str(pkt_len).rjust(5)
                        + " |          |"
                        + "      | "
                        + str(rssi).rjust(5) + " | "
                        + str(snr).rjust(6)
                        + " | Behalte SNR " + str(snr)
                        + " (statt "
                        + str(existing_snr) + ")")
                else:
                    print(
                        "     | " + t_short.rjust(8)
                        + " | DUPLIKAT | SKIP    | "
                        + str(pkt_len).rjust(5)
                        + " |          |"
                        + "      | "
                        + str(rssi).rjust(5) + " | "
                        + str(snr).rjust(6)
                        + " | Repeater-Echo "
                        + "verworfen (SNR "
                        + str(snr) + ")")
                duplicate_count += 1
            else:
                packet_buffer[payload_hex] = {
                    "buffer_time": _time.time(),
                    "best": pkt_info}
    except json.JSONDecodeError:
        print("Ungueltiges JSON empfangen")
    except Exception as e:
        print("Fehler: " + str(e))


def on_disconnect(client, userdata, flags, rc,
                  properties):
    print("\nVerbindung getrennt (" + str(rc) + ")")


def signal_handler(sig, frame):
    with buffer_lock:
        for payload_hex, entry in packet_buffer.items():
            process_packet(entry["best"])
        packet_buffer.clear()
    print("\n\n" + "=" * 119)
    print("Monitor gestoppt: "
        + datetime.now().strftime("%H:%M:%S"))
    if start_time and total_pkts > 0:
        lauf = (
            datetime.now() - start_time).total_seconds()
        t_air = total_air_ms / 1000
        dc = (t_air / lauf) * 100 if lauf > 0 else 0
        print("\nGESAMT-STATISTIK")
        print("=" * 50)
        print("  Laufzeit:       " + str(round(lauf))
            + "s (" + str(round(lauf / 60, 1))
            + " Min)")
        print("  Pakete:         " + str(total_pkts))
        print("  Duplikate:      "
            + str(duplicate_count))
        print("  Airtime total:  "
            + str(round(t_air, 2)) + "s")
        print("  Duty Cycle:     "
            + str(round(dc, 3)) + "%")
        print("  CSV-Zeilen:     "
            + str(csv_rows_written))
        print("  Knoten:         "
            + str(len(known_nodes_by_key)))
        n_collisions = sum(
            1 for h in known_nodes_by_hash
            if len(known_nodes_by_hash[h]) > 1)
        print("  Hash-Kollisionen: "
            + str(n_collisions))
        if duplicate_count > 0:
            gesamt = total_pkts + duplicate_count
            quote = duplicate_count / gesamt * 100
            print("  Dupl.-Quote:    "
                + str(round(quote, 1)) + "%")
        print("\nNACH PAKETTYP")
        print("  " + "Typ".ljust(12) + "Pkts".rjust(6)
            + "Airtime".rjust(10)
            + "Anteil".rjust(7))
        print("  " + "=" * 38)
        for tn, s in sorted(
                type_stats.items(),
                key=lambda x: x[1]["air_ms"],
                reverse=True):
            pct = 0
            if total_air_ms > 0:
                pct = s["air_ms"] / total_air_ms * 100
            print("  " + tn.ljust(12)
                + str(s["count"]).rjust(6)
                + (str(round(s["air_ms"] / 1000, 2))
                   + "s").rjust(10)
                + (str(round(pct, 1)) + "%").rjust(7))
        if sender_stats:
            print("\nTOP SENDER (nach Paketen)")
            print("  " + "Hash".ljust(4)
                + "  " + "Name".ljust(24)
                + "Pkts".rjust(6)
                + "Airtime".rjust(10))
            print("  " + "=" * 50)
            top = sorted(
                sender_stats.items(),
                key=lambda x: x[1]["count"],
                reverse=True)[:20]
            for sh, s in top:
                name = s["name"]
                if name == "?":
                    ln = lookup_name(sh)
                    if ln:
                        name = ln
                print("  " + sh.ljust(4)
                    + "  " + name.ljust(24)
                    + str(s["count"]).rjust(6)
                    + (str(round(
                        s["air_ms"] / 1000, 2))
                       + "s").rjust(10))
        gps_nodes = [
            n for n in known_nodes_by_key.values()
            if n.get("lat") is not None]
        if gps_nodes:
            print("\nKNOTEN MIT GPS ("
                + str(len(gps_nodes)) + ")")
            print("  " + "Name".ljust(24)
                + "Modus".ljust(10)
                + "Lat".rjust(10)
                + "Lon".rjust(10))
            print("  " + "=" * 58)
            for n in sorted(
                    gps_nodes,
                    key=lambda x: x["name"]):
                print("  " + n["name"].ljust(24)
                    + n["mode"].ljust(10)
                    + str(round(
                        n["lat"], 4)).rjust(10)
                    + str(round(
                        n["lon"], 4)).rjust(10))
        if len(packet_log) > 10:
            wdc = get_window_dc()
            if wdc < DC_WARNING:
                st = "OK"
            elif wdc < DC_LIMIT:
                st = "WARNUNG"
            else:
                st = "KRITISCH"
            print("\nFenster-DC ("
                + str(DC_WINDOW_SEC) + "s): "
                + str(round(wdc, 3)) + "% ["
                + st + "]")
    close_csv_file()
    print("\nMonitor beendet.Auf Wiedersehen!")
    sys.exit(0)


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


def main():
    print("\nMeshCore Duty-Cycle-Monitor v0.7.1")
    print("  Gestartet: "
        + datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("  CSV-Ordner: " + LOG_DIR)
    print("  Features: Source/Dest Hash, GPS, "
        + "Region Code")
    print("=" * 119)
    os.makedirs(LOG_DIR, exist_ok=True)
    client = mqtt.Client(
        callback_api_version=(
            mqtt.CallbackAPIVersion.VERSION2),
        client_id=(
            "duty-cycle-monitor-" + str(os.getpid())))
    client.on_connect = on_connect
    client.on_message = on_message
    client.on_disconnect = on_disconnect
    client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
    schedule_midnight_rotation()
    client.loop_forever()


if __name__ == "__main__":
    main()
