# MeshCore Paket-Protokoll Referenz

Dokumentiert am 14./15. April 2026 anhand von:
- MeshCore Analyzer (Paket Byte Breakdown)
- meshcore-decoder (github.com/michaelhart/meshcore-decoder)
- Eigene Experimente mit dem BRN Observer

## Grundstruktur eines MeshCore-Pakets

[Header 1B] [Transport 4B*] [PathLen 1B] [PathData] [Payload]


*Transport-Header nur bei T_FLOOD (rtype=0) und T_DIRECT (rtype=3)

## Header Byte (Byte 0)

Bit 7-6: Version (immer 00)
Bit 5-2: Payload Type (0-15)
Bit 1-0: Route Type (0-3)


### Route Types

| Code | Name | Transport-Header? |
|---|---|---|
| 00 | T_FLOOD | Ja (Bytes 1-4) |
| 01 | FLOOD | Nein |
| 10 | DIRECT | Nein |
| 11 | T_DIRECT | Ja (Bytes 1-4) |

### Transport-Header (nur T_FLOOD / T_DIRECT)

Byte 1-2: Region/Scope Code (NICHT Absender!)
Byte 3-4: Return Region Code


### Payload Types

| Code | Name | Beschreibung |
|---|---|---|
| 0 | REQ | Request (verschluesselt) |
| 1 | RESPONSE | Antwort (verschluesselt) |
| 2 | TXT_MSG | Textnachricht (verschluesselt) |
| 3 | ACK | Bestaetigung |
| 4 | ADVERT | Knoten-Ankuendigung |
| 5 | GRP_TXT | Gruppennachricht |
| 6 | GRP_DATA | Gruppendaten |
| 7 | ANON_REQ | Anonymer Request |
| 8 | PATH | Pfad-Information |
| 9 | TRACE | Netzwerk-Trace mit SNR |
| 10 | MULTIPART | Mehrteilige Nachricht |
| 11 | CONTROL | Steuerpaket |
| 15 | RAW_CUSTOM | Benutzerdefiniert |

## Path Length Byte

Bit 7-6: Hash Size (Bytes pro Hop)
00 = 1 Byte/Hop
01 = 2 Bytes/Hop
10 = 3 Bytes/Hop
11 = Spezial (ganzes Byte = Hop Count)
Bit 5-0: Hop Count


## Payload-Strukturen

### ADVERT (Type 4)

Vollstaendige Knoten-Information mit Public Key, GPS und Name.

Byte 0-31: Public Key (Ed25519, 32 Bytes)
Byte 32-35: Timestamp (Unix, 4 Bytes)
Byte 36-99: Signature (Ed25519, 64 Bytes)
Byte 100: App Flags
Byte 101-104: Latitude (float32, falls Flag gesetzt)
Byte 105-108: Longitude (float32, falls Flag gesetzt)
Byte 109+: Node Name (ASCII)


#### App Flags (Byte 100)

Bit 0-3: Rolle
0001 (1) = Chat
0010 (2) = Repeater
0011 (3) = Room
0100 (4) = Sensor
Bit 4: Hat GPS-Position (1=ja)
Bit 7: Hat Name (1=ja)


#### Source Hash Ableitung

Das 1. Byte des Public Key dient als Source Hash
in TXT_MSG, RESPONSE, REQ und ACK Paketen.

Beispiel:
  Public Key: CF 84 42 4F ... -> Source Hash = CF

### TXT_MSG (Type 2)

Byte 0: Destination Hash (1. Byte des Empfaenger Public Key)
Byte 1: Source Hash (1. Byte des Absender Public Key)
Byte 2-3: Cipher MAC
Byte 4+: Ciphertext (verschluesselt)


### RESPONSE (Type 1)

Gleiche Struktur wie TXT_MSG:
Byte 0: Destination Hash
Byte 1: Source Hash
Byte 2-3: Cipher MAC
Byte 4+: Ciphertext


### REQ (Type 0)

Gleiche Struktur wie TXT_MSG:
Byte 0: Destination Hash
Byte 1: Source Hash
Byte 2-3: Cipher MAC
Byte 4+: Ciphertext


### ACK (Type 3)

Kuerzeste Variante (kein Ciphertext):
Byte 0: Destination Hash
Byte 1: Source Hash
Byte 2-3: Cipher MAC


### ANON_REQ (Type 7)

Enthaelt den VOLLEN Public Key des Absenders:
Byte 0: Destination Hash (1 Byte)
Byte 1-32: Sender Public Key (Ed25519, 32 Bytes!)
Byte 33-34: Cipher MAC
Byte 35+: Ciphertext


### GRP_TXT (Type 5)

Gruppennachricht auf einem Kanal. Kein Source Hash!
Byte 0: Channel Hash (1. Byte von SHA256 des Channel Secret)
Byte 1-2: Cipher MAC
Byte 3+: Ciphertext (AES-ECB verschluesselt)


Entschluesselung:
1. Channel Hash nachschlagen -> Channel Secret
2. HMAC-SHA256 mit Secret ueber Ciphertext pruefen (MAC)
3. AES-ECB Entschluesselung mit Secret
4. Ergebnis: Timestamp (4B) + Flags (1B) + Nachricht

### PATH (Type 8)

Routing-Informationen, keine Source/Dest Hashes:
Byte 0-19: Raw Path Data (20 Bytes)


### TRACE (Type 9)

Netzwerk-Trace mit SNR-Messungen pro Hop:
Byte 0-3: Trace Tag (eindeutige ID)
Byte 4-7: Auth Code
Byte 8: Flags
Byte 9+: Path Hashes (1 Byte pro Knoten)


SNR-Daten stehen im Path-Bereich des Headers (nicht Payload).
Jedes Byte = SNR-Wert: Wert/4 = dB (z.B. 0x31 = 12.25 dB)

## Uebersicht: Source/Dest pro Pakettyp

| Pakettyp   | Source Info          | Dest Info    |
|------------|---------------------|--------------|
| ADVERT     | Full Key+Name+GPS   | -            |
| ANON_REQ   | Full Key (32B)      | Hash (1B)    |
| TXT_MSG    | Hash (1B)           | Hash (1B)    |
| RESPONSE   | Hash (1B)           | Hash (1B)    |
| REQ        | Hash (1B)           | Hash (1B)    |
| ACK        | Hash (1B)           | Hash (1B)    |
| GRP_TXT    | Kein Source          | -            |
| PATH       | Kein Source          | -            |
| TRACE      | Kein Source          | -            |

## Lookup-Strategie

1. ADVERT empfangen -> Public Key[0] als Source Hash speichern
   Zusaetzlich: Name, Modus, GPS
2. ANON_REQ empfangen -> Public Key[0] als Source Hash speichern
3. Bei TXT_MSG/RESPONSE/REQ/ACK -> Payload[1] = Source Hash
   -> Nachschlagen in Lookup-Tabelle
4. Kollisionen moeglich (1 Byte = 256 Werte, 274+ Knoten)

## Referenzen

- MeshCore Analyzer: meshcore.net/analyzer
- meshcore-decoder: github.com/michaelhart/meshcore-decoder
- MeshCore Firmware: github.com/ripplebiz/MeshCore
