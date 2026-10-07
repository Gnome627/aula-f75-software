#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""AULA F75 lighting and key-map control over USB (wired mode, 258a:010c). Windows, stdlib only.

Protocol (reverse-engineered from OemDrv.exe, class CDevG5KB):
  HID feature report, 520 bytes, on the vendor collection with FeatureReportByteLength == 520
  [0]=report id 6  [1]=cmd  [2]=index  [3]=0  [4]=chunk count  [5]=chunk no  [6:8]=len LE  [8:]=data
  Reads are cmd | 0x80: send the header with SetFeature, then GetFeature.
  0x82 idx 1  device id, 6 bytes
  0x04/0x84   config, 128 bytes: [0x0a]=effect id, [0x09]=1 in custom mode,
              [0x3a + 2*(id-1)] = brightness 0-9, next byte = speed << 4 | colour index (7 = rainbow)
  0x0a/0x8a   palette, 512 bytes: 7 RGB colours per effect at id * 21
  0x06/0x86   per-key colours, 384 bytes: R, G, B planes of 126 bytes, indexed by matrix position
  0x03/0x83   key map, idx = layer (0 base, 1 Fn), 512 bytes: 4 bytes per matrix position
              00 mods 00 usage = keyboard key, 02 00 hi lo = consumer key, 07/08/0d = firmware functions,
              03 mode count index = play macro (mode 1 = count times, 2 = until the next key press,
              4 = while held)
  0x05/0x85   macros, 4096 bytes in 512-byte chunks: table of {u16 offset, u16 size}, then per macro
              a name (length byte + UTF-16LE) and 4-byte events: [0] = bit 7 release | type << 4 |
              delay bits 16-19, [1:3] = delay ms (applied after the event), [3] = code.
              type 0 = key (HID usage), 1 = modifier (usage e0-e7), 2 = mouse button, 3-5 = mouse motion
"""
import argparse, ctypes, ctypes.wintypes as wt, json, locale, re, sys, threading, time, pathlib

VID, PID, REPORT_ID, REPORT_LEN = 0x258A, 0x010C, 6, 520
DEVICE_ID = bytes.fromhex('0300000000cd')          # Psd from KB.ini
SIGNATURE = b'\x5a\xa5'
CFG_LEN, PALETTE_LEN, KEYS_LEN, KEYMAP_LEN, PLANE = 0x80, 0x200, 384, 0x200, 126
MACRO_LEN, MACRO_NAME_MAX, MAX_DELAY = 0x1000, 30, 0xFFFFF
OFF_CUSTOM, OFF_MODE, OFF_TABLE = 0x09, 0x0A, 0x3A   # config offsets
CUSTOM_MODE, OFF_MODE_ID = 21, 0
DEFAULT_PALETTE = ['ff0000', '00ff00', '0000ff', 'ffff00', 'ff00ff', '00ffff', 'ffffff']
# Built as an .exe (PyInstaller), the page and the texts are packed inside it, while profiles stay next to the .exe
APP_DIR = pathlib.Path(sys.executable if getattr(sys, 'frozen', False) else __file__).resolve().parent
PACKED_DIR = pathlib.Path(getattr(sys, '_MEIPASS', APP_DIR))
PROFILE_DIR = APP_DIR / 'profiles'


def resource(name):
    """A file next to the program wins over the packed copy, so the texts and the page stay editable."""
    return APP_DIR / name if (APP_DIR / name).exists() else PACKED_DIR / name


# Every user-facing text lives in strings.json: {"ru": {key: text}, "en": {...}}
STRINGS = json.loads(resource('strings.json').read_text(encoding='utf-8'))
LANG = 'ru' if (locale.getlocale()[0] or '').lower().startswith(('ru', 'russian')) else 'en'


def tr(key, lang=None, **kw):
    text = STRINGS.get(lang or LANG, STRINGS['ru']).get(key) or STRINGS['ru'].get(key, key)
    return text.format(**kw) if kw else text

# id, name (its title is the string "preset.<name>"), has speed, has colour; from KB.ini LedOptN
# KB.ini also lists 9 Shadow_disappear, 14 Retinue scanning and 18 Rotating storm, but the F75 firmware
# shows nothing for them: the keyboard itself cycles through 16 modes, these 15 plus the custom one.
EFFECTS = [
    (1, 'fixed', 0, 1), (2, 'respire', 1, 1), (3, 'rainbow', 1, 0), (4, 'flash', 1, 1), (5, 'raindrops', 1, 1),
    (6, 'wheel', 1, 1), (7, 'ripples', 1, 1), (8, 'stars', 1, 1), (10, 'snake', 1, 1), (11, 'neon', 1, 1),
    (12, 'reaction', 1, 1), (13, 'sine', 1, 1), (15, 'windmill', 1, 0), (16, 'waterfall', 1, 0), (17, 'blossoming', 1, 0),
]
MODES = {name: i for i, name, *_ in EFFECTS}
INTERACTIVE = {4, 7, 12}   # stay dark until keys are pressed (confirmed on the keyboard)

# Matrix position of every key (KB.ini [KEY], last column); the knob has no LED
MATRIX_INDEX = {
    'Esc': 0, 'F1': 12, 'F2': 18, 'F3': 24, 'F4': 30, 'F5': 36, 'F6': 42, 'F7': 48, 'F8': 54, 'F9': 60, 'F10': 66,
    'F11': 72, 'F12': 78, 'Wheel': 84, '`': 1, '1': 7, '2': 13, '3': 19, '4': 25, '5': 31, '6': 37, '7': 43, '8':
    49, '9': 55, '0': 61, '-': 67, '=': 73, 'Backspace': 79, 'Delete': 85, 'Tab': 2, 'Q': 8, 'W': 14, 'E': 20, 'R':
    26, 'T': 32, 'Y': 38, 'U': 44, 'I': 50, 'O': 56, 'P': 62, '[': 68, ']': 74, '\\': 80, 'PgUp': 86, 'CapsLock': 3,
    'A': 9, 'S': 15, 'D': 21, 'F': 27, 'G': 33, 'H': 39, 'J': 45, 'K': 51, 'L': 57, ';': 63, "'": 69, 'Enter': 81,
    'PgDn': 87, 'LShift': 4, 'Z': 10, 'X': 16, 'C': 22, 'V': 28, 'B': 34, 'N': 40, 'M': 46, ',': 52, '.': 58, '/':
    64, 'RShift': 70, 'Up': 82, 'End': 88, 'LCtrl': 5, 'LWin': 11, 'LAlt': 17, 'Space': 35, 'Fn': 53, 'RCtrl': 59,
    'Left': 77, 'Down': 83, 'Right': 89,
}
# Physical layout in key units: (row offset, [(name, width) or (None, gap)]). KB.ini only has hit boxes of
# the vendor's picture, which are uneven, so the diagram is laid out as a regular 75% board instead.
_ONE = lambda names: [(n, 1) for n in names]
ROWS = [
    (0.00, [('Esc', 1), (None, .5), *_ONE(['F1', 'F2', 'F3', 'F4']), (None, .5), *_ONE(['F5', 'F6', 'F7', 'F8']), (None, .5),
            *_ONE(['F9', 'F10', 'F11', 'F12']), (None, .5), ('Wheel', 1)]),
    (1.25, [*_ONE('`1234567890-='), ('Backspace', 2), ('Delete', 1)]),
    (2.25, [('Tab', 1.5), *_ONE('QWERTYUIOP[]'), ('\\', 1.5), ('PgUp', 1)]),
    (3.25, [('CapsLock', 1.75), *_ONE("ASDFGHJKL;'"), ('Enter', 2.25), ('PgDn', 1)]),
    (4.25, [('LShift', 2.25), *_ONE('ZXCVBNM,./'), ('RShift', 1.75), ('Up', 1), ('End', 1)]),
    (5.25, [('LCtrl', 1.25), ('LWin', 1.25), ('LAlt', 1.25), ('Space', 6.25), ('Fn', 1.25), ('RCtrl', 1.25), (None, .5),
            *_ONE(['Left', 'Down', 'Right'])]),
]
LAYOUT_SIZE = (16, 6.25)   # width and height of the board in key units
LAYOUT = []                # name, matrix index, x, y, width (key units)
for _y, _row in ROWS:
    _x = 0
    for _name, _w in _row:
        if _name:
            LAYOUT.append([_name, MATRIX_INDEX[_name], _x, _y, _w])
        _x += _w
KEYS = {name.lower(): idx for name, idx, *_ in LAYOUT if name != 'Wheel'}   # keys with an LED
MATRIX = {name.lower(): idx for name, idx, *_ in LAYOUT}
# factory key map, layers 0 (base) and 1 (Fn), matrix positions 0..89
DEFAULT_KEYMAP = [
    '00000029' '00000035' '0000002b' '00000039' '00020000' '00010000' '00000000' '0000001e' '00000014' '00000004'
    '0000001d' '00080000' '0000003a' '0000001f' '0000001a' '00000016' '0000001b' '00040000' '0000003b' '00000020'
    '00000008' '00000007' '00000006' '00000000' '0000003c' '00000021' '00000015' '00000009' '00000019' '00000000'
    '0000003d' '00000022' '00000017' '0000000a' '00000005' '0000002c' '0000003e' '00000023' '0000001c' '0000000b'
    '00000011' '00000000' '0000003f' '00000024' '00000018' '0000000d' '00000010' '00000000' '00000040' '00000025'
    '0000000c' '0000000e' '00000036' '0d000000' '00000041' '00000026' '00000012' '0000000f' '00000037' '00100000'
    '00000042' '00000027' '00000013' '00000033' '00000038' '00000000' '00000043' '0000002d' '0000002f' '00000034'
    '00200000' '00000000' '00000044' '0000002e' '00000030' '00000032' '00000064' '00000050' '00000045' '0000002a'
    '00000031' '00000028' '00000052' '00000051' '0700001d' '0000004c' '0000004b' '0000004e' '0000004d' '0000004f',
    '07000004' '07000008' '08020000' '00000000' '08000001' '00000000' '00000000' '07000005' '07000019' '00000000'
    '00000000' '07000001' '02000223' '07000006' '07000018' '00000000' '00000000' '00000000' '0200018a' '07000007'
    '0700001a' '00000000' '00000000' '00000000' '0004002b' '00000000' '00000000' '00000000' '00000000' '00000000'
    '00080008' '00000000' '00000000' '0700000a' '07000011' '00000000' '08030200' '00000000' '00000000' '00000000'
    '00000000' '00000000' '08030100' '00000000' '00000046' '00000000' '00000000' '00000000' '020000b6' '00000000'
    '00000047' '00000000' '00000000' '0d000000' '020000cd' '00000000' '00000048' '00000000' '00000000' '00000000'
    '020000b5' '00000000' '00000000' '00000000' '00000000' '00000000' '020000e2' '00000000' '00000000' '00000000'
    '00000000' '00000000' '020000ea' '00000000' '00000000' '00000000' '00000000' '08040200' '020000e9' '00000000'
    '08000000' '00000000' '08030100' '08030200' '00000000' '00000049' '00000000' '00000000' '0000004a' '08040100',
]


class F75Error(Exception):
    """An error to show to the user: a key of the strings file plus its values."""

    def __init__(self, key, **kw):
        super().__init__(key)
        self.key, self.kw = key, kw

    def text(self, lang=None):
        return tr(self.key, lang, **self.kw)

    def __str__(self):
        return self.text()


hid = ctypes.WinDLL('hid'); sa = ctypes.WinDLL('setupapi'); k32 = ctypes.WinDLL('kernel32', use_last_error=True)
class GUID(ctypes.Structure): _fields_ = [('a', wt.DWORD), ('b', wt.WORD), ('c', wt.WORD), ('d', ctypes.c_ubyte * 8)]
class IFD(ctypes.Structure): _fields_ = [('cb', wt.DWORD), ('g', GUID), ('f', wt.DWORD), ('r', ctypes.c_void_p)]
class ATTR(ctypes.Structure): _fields_ = [('Size', wt.ULONG), ('VID', wt.USHORT), ('PID', wt.USHORT), ('Ver', wt.USHORT)]
class CAPS(ctypes.Structure): _fields_ = [('Usage', wt.USHORT), ('UsagePage', wt.USHORT), ('In', wt.USHORT), ('Out', wt.USHORT), ('Feat', wt.USHORT), ('res', wt.USHORT * 17), ('n', wt.USHORT * 10)]
k32.CreateFileW.restype = ctypes.c_void_p
k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p, wt.DWORD, wt.DWORD, ctypes.c_void_p]
sa.SetupDiGetClassDevsW.restype = ctypes.c_void_p
INVALID = ctypes.c_void_p(-1).value


def find_path():
    g = GUID(); hid.HidD_GetHidGuid(ctypes.byref(g))
    h = ctypes.c_void_p(sa.SetupDiGetClassDevsW(ctypes.byref(g), None, None, 0x12))
    i = 0
    try:
        while True:
            d = IFD(); d.cb = ctypes.sizeof(IFD)
            if not sa.SetupDiEnumDeviceInterfaces(h, None, ctypes.byref(g), i, ctypes.byref(d)):
                return None
            i += 1
            n = wt.DWORD()
            sa.SetupDiGetDeviceInterfaceDetailW(h, ctypes.byref(d), None, 0, ctypes.byref(n), None)
            buf = ctypes.create_string_buffer(n.value)
            ctypes.cast(buf, ctypes.POINTER(wt.DWORD))[0] = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
            sa.SetupDiGetDeviceInterfaceDetailW(h, ctypes.byref(d), buf, n, None, None)
            path = ctypes.wstring_at(ctypes.addressof(buf) + 4)
            f = k32.CreateFileW(path, 0, 3, None, 3, 0, None)
            if f in (None, INVALID):
                continue
            a = ATTR(); a.Size = ctypes.sizeof(ATTR); hid.HidD_GetAttributes(ctypes.c_void_p(f), ctypes.byref(a))
            pp = ctypes.c_void_p(); c = CAPS()
            if hid.HidD_GetPreparsedData(ctypes.c_void_p(f), ctypes.byref(pp)):
                hid.HidP_GetCaps(pp, ctypes.byref(c)); hid.HidD_FreePreparsedData(pp)
            k32.CloseHandle(ctypes.c_void_p(f))
            if (a.VID, a.PID, c.Feat) == (VID, PID, REPORT_LEN):
                return path
    finally:
        sa.SetupDiDestroyDeviceInfoList(h)


class F75:
    def __init__(self):
        path = find_path()
        if not path:
            raise F75Error('err.notFound')
        self.h = k32.CreateFileW(path, 0xC0000000, 3, None, 3, 0, None)
        if self.h in (None, INVALID):
            raise F75Error('err.open')
        if self.read(0x82, 1, 6) != DEVICE_ID:
            self.close()
            raise F75Error('err.wrongDevice')

    def close(self):
        if self.h not in (None, INVALID):
            k32.CloseHandle(ctypes.c_void_p(self.h))
        self.h = None

    def _packet(self, cmd, idx, size, data=b'', chunks=1, chunk=0):
        b = (ctypes.c_ubyte * REPORT_LEN)()
        b[0], b[1], b[2], b[4], b[5], b[6], b[7] = REPORT_ID, cmd, idx, chunks, chunk, size & 0xFF, size >> 8
        b[8:8 + len(data)] = data
        return b

    def _set(self, b):
        for _ in range(3):
            if hid.HidD_SetFeature(ctypes.c_void_p(self.h), b, REPORT_LEN):
                return
            time.sleep(0.07)
        raise F75Error('err.noReply')

    def read(self, cmd, idx, size):
        """Read size bytes; anything over 512 bytes travels in 512-byte chunks."""
        chunks, out = (size + 0x1FF) // 0x200, b''
        for i in range(chunks):
            n = min(0x200, size - i * 0x200)
            self._set(self._packet(cmd, idx, n, chunks=chunks, chunk=i))
            time.sleep(0.03)
            r = (ctypes.c_ubyte * REPORT_LEN)(); r[0] = REPORT_ID
            if not hid.HidD_GetFeature(ctypes.c_void_p(self.h), r, REPORT_LEN):
                raise F75Error('err.noReply')
            out += bytes(r[8:8 + n])
        return out

    def write(self, cmd, idx, data):
        chunks = (len(data) + 0x1FF) // 0x200
        for i in range(chunks):
            part = data[i * 0x200:(i + 1) * 0x200]
            self._set(self._packet(cmd, idx, len(part), part, chunks, i))
            time.sleep(0.03)

    def _read_signed(self, cmd, idx, size):
        d = self.read(cmd, idx, size)
        if d[-2:] != SIGNATURE and d[size - 6:size - 4] != SIGNATURE:
            raise F75Error('err.badReply')
        return bytearray(d)

    def get_cfg(self):
        return self._read_signed(0x84, 0, CFG_LEN)

    def set_cfg(self, cfg):
        assert len(cfg) == CFG_LEN and cfg[-2:] == SIGNATURE
        self.write(0x04, 0, bytes(cfg))

    def get_palette(self):
        return self._read_signed(0x8A, 0, PALETTE_LEN)

    def set_palette(self, pal):
        assert len(pal) == PALETTE_LEN and pal[0x1FA:0x1FC] == SIGNATURE
        self.write(0x0A, 0, bytes(pal))

    def get_key_colors(self):
        return bytearray(self.read(0x86, 0, KEYS_LEN))

    def set_key_colors(self, d):
        assert len(d) == KEYS_LEN
        self.write(0x06, 0, bytes(d))

    def get_macros(self):
        return bytearray(self.read(0x85, 0, MACRO_LEN))

    def set_macros(self, d):
        assert len(d) == MACRO_LEN
        self.write(0x05, 0, bytes(d))

    def get_keymap(self, layer):
        return self._read_signed(0x83, layer, KEYMAP_LEN)

    def set_keymap(self, layer, d):
        assert layer in (0, 1) and len(d) == KEYMAP_LEN and d[-2:] == SIGNATURE
        self.write(0x03, layer, bytes(d))


def parse_rgb(s):
    s = str(s).lstrip('#')
    if not re.fullmatch(r'[0-9a-fA-F]{6}', s):
        raise F75Error('err.color', s=s)
    return bytes.fromhex(s)


def key_index(name, table=KEYS):
    if str(name).lower() not in table:
        raise F75Error('err.key', name=name)
    return table[str(name).lower()]


def slot(effect):
    """Offset of the brightness/speed/colour pair of an effect in the config."""
    if effect not in MODES.values():
        raise F75Error('err.preset', n=effect)
    return OFF_TABLE + 2 * (effect - 1)


def parse_macros(buf):
    """Macro memory -> [{'name', 'events': [{'code', 'down', 'delay'} or {'raw'}]}]."""
    first = int.from_bytes(buf[0:2], 'little')
    if not first or first % 4 or first > len(buf):
        return []
    macros = []
    for i in range(first // 4):
        off, size = int.from_bytes(buf[4 * i:4 * i + 2], 'little'), int.from_bytes(buf[4 * i + 2:4 * i + 4], 'little')
        blob = bytes(buf[off:off + size])
        if not size or off + size > len(buf) or blob[0] + 1 > size:
            break
        events = []
        for o in range(1 + blob[0], size - 3, 4):
            b0, hi, lo, code = blob[o:o + 4]
            if (b0 >> 4) & 7 in (0, 1):
                events.append({'code': code, 'down': not b0 & 0x80, 'delay': (b0 & 15) << 16 | hi << 8 | lo})
            else:   # mouse events and anything else are kept as they are
                events.append({'raw': blob[o:o + 4].hex()})
        macros.append({'name': blob[1:1 + blob[0]].decode('utf-16le', 'replace'), 'events': events})
    return macros


def build_macros(macros):
    blobs = []
    for m in macros:
        name = str(m.get('name', '')).strip()[:MACRO_NAME_MAX] or tr('macro.defaultName')
        raw = name.encode('utf-16le')
        blob = bytearray([len(raw)]) + raw
        for e in m.get('events', []):
            if 'raw' in e:
                if not re.fullmatch(r'[0-9a-fA-F]{8}', str(e['raw'])):
                    raise F75Error('err.macroEvent')
                blob += bytes.fromhex(e['raw'])
                continue
            code, delay = int(e['code']), max(0, min(MAX_DELAY, int(e.get('delay', 0))))
            if not 4 <= code <= 0xE7:
                raise F75Error('err.macroKey')
            kind = 1 if code >= 0xE0 else 0
            blob += bytes([(0 if e.get('down') else 0x80) | kind << 4 | delay >> 16, delay >> 8 & 0xFF, delay & 0xFF, code])
        blobs.append(bytes(blob))
    table = 4 * len(blobs)
    if table + sum(map(len, blobs)) > MACRO_LEN or len(blobs) > 255:
        raise F75Error('err.macroFull')
    buf, off = bytearray(MACRO_LEN), table
    for i, blob in enumerate(blobs):
        buf[4 * i:4 * i + 4] = off.to_bytes(2, 'little') + len(blob).to_bytes(2, 'little')
        buf[off:off + len(blob)] = blob
        off += len(blob)
    return buf, off


class Keyboard:
    """Cached device state; every change is written straight to the keyboard."""

    def __init__(self):
        self.lock = threading.RLock()
        self.dev = None

    def _connect(self):
        if self.dev:
            return
        self.dev = F75()
        try:
            self.cfg = self.dev.get_cfg()
            self.palette = self.dev.get_palette()
            self.colors = self.dev.get_key_colors()
            self.keymap = [self.dev.get_keymap(0), self.dev.get_keymap(1)]
            self.macros = self.dev.get_macros()
        except Exception:
            self.drop()
            raise

    def drop(self):
        if self.dev:
            self.dev.close()
        self.dev = None

    def call(self, fn, *a):
        """Run fn against a connected device; reconnect once if the handle went stale."""
        with self.lock:
            for attempt in (0, 1):
                try:
                    self._connect()
                    return fn(*a)
                except F75Error:
                    self.drop()
                    if attempt:
                        raise

    # --- state -----------------------------------------------------------------
    def _key_color(self, idx):
        return bytes(self.colors[idx + PLANE * p] for p in range(3)).hex()

    def _layer(self, n):
        return {name: self.keymap[n][4 * i:4 * i + 4].hex() for name, i in MATRIX.items()}

    def state(self):
        cfg, effects = self.cfg, []
        for i, name, has_speed, has_color in EFFECTS:
            o = slot(i)
            effects.append({
                'id': i, 'name': name, 'hasSpeed': bool(has_speed), 'hasColor': bool(has_color),
                'interactive': i in INTERACTIVE,
                'brightness': cfg[o], 'speed': cfg[o + 1] >> 4, 'color': cfg[o + 1] & 15,
                'palette': [self.palette[i * 21 + k * 3:i * 21 + k * 3 + 3].hex() for k in range(7)],
            })
        return {
            'connected': True, 'mode': cfg[OFF_MODE], 'customMode': CUSTOM_MODE, 'effects': effects,
            'keys': {name: self._key_color(i) for name, i in KEYS.items()},
            'keymap': [self._layer(0), self._layer(1)],
            'macros': parse_macros(self.macros),
            'macroMemory': {'used': build_macros(parse_macros(self.macros))[1], 'total': MACRO_LEN},
            'defaults': [{name: l[8 * i:8 * i + 8] for name, i in MATRIX.items()} for l in DEFAULT_KEYMAP],
            'layout': [{'name': n.lower(), 'label': n, 'x': x, 'y': y, 'w': w} for n, _, x, y, w in LAYOUT],
            'layoutSize': LAYOUT_SIZE,
        }

    # --- lighting --------------------------------------------------------------
    def set_mode(self, mode):
        if mode not in (OFF_MODE_ID, CUSTOM_MODE, *MODES.values()):
            raise F75Error('err.preset', n=mode)
        self.cfg[OFF_CUSTOM] = int(mode == CUSTOM_MODE)
        self.cfg[OFF_MODE] = mode
        self.dev.set_cfg(self.cfg)

    def set_effect(self, effect, brightness=None, speed=None, color=None):
        o = slot(effect)
        if brightness is not None:
            self.cfg[o] = max(0, min(9, int(brightness)))
        if speed is not None:
            self.cfg[o + 1] = (max(0, min(4, int(speed))) << 4) | (self.cfg[o + 1] & 0x0F)
        if color is not None:
            color = max(0, min(7, int(color)))
            if color < 7 and not any(self.palette[effect * 21:effect * 21 + 21]):
                # effects that shipped without a palette: fill in the stock colours first
                self.palette[effect * 21:effect * 21 + 21] = bytes.fromhex(''.join(DEFAULT_PALETTE))
                self.dev.set_palette(self.palette)
            self.cfg[o + 1] = (self.cfg[o + 1] & 0xF0) | color
        self.dev.set_cfg(self.cfg)

    def set_palette_color(self, effect, index, rgb):
        slot(effect)
        if not 0 <= index < 7:
            raise F75Error('err.sevenColors')
        if not any(self.palette[effect * 21:effect * 21 + 21]):
            self.palette[effect * 21:effect * 21 + 21] = bytes.fromhex(''.join(DEFAULT_PALETTE))
        o = effect * 21 + index * 3
        self.palette[o:o + 3] = parse_rgb(rgb)
        self.dev.set_palette(self.palette)

    def copy_palette(self, effect):
        """Give every effect that has a colour choice the seven colours of this one."""
        slot(effect)
        row = bytes(self.palette[effect * 21:effect * 21 + 21])
        if not any(row):
            row = bytes.fromhex(''.join(DEFAULT_PALETTE))
        for i, _, _, has_color in EFFECTS:
            if has_color:
                self.palette[i * 21:i * 21 + 21] = row
        self.dev.set_palette(self.palette)

    def set_key_colors(self, colors):
        for name, rgb in colors.items():
            i = key_index(name)
            for p, v in enumerate(parse_rgb(rgb)):
                self.colors[i + PLANE * p] = v
        self.dev.set_key_colors(self.colors)

    # --- key map ---------------------------------------------------------------
    def remap(self, layer, key, code):
        if layer not in (0, 1) or not re.fullmatch(r'[0-9a-fA-F]{8}', str(code)):
            raise F75Error('err.assign')
        i = key_index(key, MATRIX)
        self.keymap[layer][4 * i:4 * i + 4] = bytes.fromhex(code)
        self.dev.set_keymap(layer, self.keymap[layer])

    def reset_keymap(self, layer):
        if layer not in (0, 1):
            raise F75Error('err.layer')
        d = bytes.fromhex(DEFAULT_KEYMAP[layer])
        self.keymap[layer][:len(d)] = d
        self.dev.set_keymap(layer, self.keymap[layer])

    # --- macros ----------------------------------------------------------------
    def save_macro(self, index, macro):
        macros = parse_macros(self.macros)
        if index is None:
            macros.append(macro)
        elif 0 <= index < len(macros):
            macros[index] = macro
        else:
            raise F75Error('err.macroGone')
        buf, _ = build_macros(macros)
        self.dev.set_macros(buf); self.macros = buf

    def delete_macro(self, index):
        macros = parse_macros(self.macros)
        if not 0 <= index < len(macros):
            raise F75Error('err.macroGone')
        del macros[index]
        buf, _ = build_macros(macros)
        # keys that played it go back to their factory function; later macros move up by one
        for layer in (0, 1):
            km, default, changed = self.keymap[layer], bytes.fromhex(DEFAULT_KEYMAP[layer]), False
            for i in MATRIX.values():
                if km[4 * i] == 3 and km[4 * i + 3] >= index:
                    if km[4 * i + 3] == index:
                        km[4 * i:4 * i + 4] = default[4 * i:4 * i + 4]
                    else:
                        km[4 * i + 3] -= 1
                    changed = True
            if changed:
                self.dev.set_keymap(layer, km)
        self.dev.set_macros(buf); self.macros = buf

    # --- profiles --------------------------------------------------------------
    def snapshot(self):
        return {'cfg': self.cfg.hex(), 'palette': self.palette.hex(), 'colors': self.colors.hex(),
                'keymap': [k.hex() for k in self.keymap], 'macros': self.macros.hex()}

    def apply(self, p):
        cfg, pal, col = (bytearray.fromhex(p[k]) for k in ('cfg', 'palette', 'colors'))
        keymap = [bytearray.fromhex(k) for k in p['keymap']]
        if (len(cfg), len(pal), len(col)) != (CFG_LEN, PALETTE_LEN, KEYS_LEN) or cfg[-2:] != SIGNATURE \
                or len(keymap) != 2 or any(len(k) != KEYMAP_LEN or k[-2:] != SIGNATURE for k in keymap):
            raise F75Error('err.profileBroken')
        macros = bytearray.fromhex(p.get('macros', '00' * MACRO_LEN))   # older profiles have none
        if len(macros) != MACRO_LEN:
            raise F75Error('err.profileBroken')
        # only the blocks that differ are written
        if macros != self.macros:
            self.dev.set_macros(macros); self.macros = macros
        if pal != self.palette:
            self.dev.set_palette(pal); self.palette = pal
        if col != self.colors:
            self.dev.set_key_colors(col); self.colors = col
        for i in (0, 1):
            if keymap[i] != self.keymap[i]:
                self.dev.set_keymap(i, keymap[i]); self.keymap[i] = keymap[i]
        if cfg != self.cfg:
            self.dev.set_cfg(cfg); self.cfg = cfg


def profile_path(name):
    name = str(name).strip()
    if not re.fullmatch(r'[\w \-]{1,40}', name):
        raise F75Error('err.profileName')
    return PROFILE_DIR / f'{name}.json'


def list_profiles():
    return sorted(f.stem for f in PROFILE_DIR.glob('*.json')) if PROFILE_DIR.exists() else []


def save_profile(kb, name):
    PROFILE_DIR.mkdir(exist_ok=True)
    profile_path(name).write_text(json.dumps(kb.snapshot(), indent=1), encoding='utf-8')


def load_profile(kb, name):
    f = profile_path(name)
    if not f.exists():
        raise F75Error('err.profileMissing', name=name)
    kb.apply(json.loads(f.read_text(encoding='utf-8')))


# --- web UI --------------------------------------------------------------------
def cursor_color():
    """Colour of the screen pixel under the mouse pointer as rrggbb, or None if Windows will not tell."""
    user32, gdi32 = ctypes.WinDLL('user32'), ctypes.WinDLL('gdi32')
    user32.GetDC.restype = ctypes.c_void_p
    gdi32.GetPixel.restype = wt.DWORD
    gdi32.GetPixel.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    pt = wt.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    dc = user32.GetDC(None)
    c = gdi32.GetPixel(dc, pt.x, pt.y)   # 0x00bbggrr
    user32.ReleaseDC(None, dc)
    return None if c == 0xFFFFFFFF else f'{c & 255:02x}{c >> 8 & 255:02x}{c >> 16 & 255:02x}'


def serve(port, open_browser):
    import http.server, webbrowser
    try:   # real pixel coordinates for the screen colour picker on scaled displays
        ctypes.WinDLL('shcore').SetProcessDpiAwareness(2)
    except (OSError, AttributeError):
        pass
    kb = Keyboard()

    # Undo history: snapshots of everything the keyboard stores. Changes that follow one another
    # within a second (a brush stroke, a slider drag) count as one step.
    history = {'undo': [], 'redo': [], 'last': 0.0}
    undoable = {'mode', 'effect', 'palette', 'palette/copy', 'keys', 'remap', 'remap/reset', 'macro/save', 'macro/delete',
                'profiles/load'}

    def remember():
        now = time.monotonic()
        if now - history['last'] > 1.0:
            history['undo'].append(kb.snapshot())
            del history['undo'][:-50]
            history['redo'].clear()
        history['last'] = now

    def travel(src, dst):
        current = kb.snapshot()
        while history[src]:
            snap = history[src].pop()
            if snap != current:   # skip steps that ended up changing nothing
                history[dst].append(current)
                kb.apply(snap)
                break
        history['last'] = 0.0

    def api(path, q, lang):
        if path in undoable:
            kb.call(remember)
        if path in ('undo', 'redo'):
            kb.call(travel, path, 'redo' if path == 'undo' else 'undo')
            return state(lang)
        if path == 'mode':
            kb.call(kb.set_mode, int(q['id']))
        elif path == 'effect':
            kb.call(kb.set_effect, int(q['id']), q.get('brightness'), q.get('speed'), q.get('color'))
        elif path == 'palette':
            kb.call(kb.set_palette_color, int(q['id']), int(q['index']), q['rgb'])
        elif path == 'palette/copy':
            kb.call(kb.copy_palette, int(q['id']))
        elif path == 'keys':
            kb.call(kb.set_key_colors, q['colors'])
        elif path == 'remap':
            kb.call(kb.remap, int(q['layer']), q['key'], q['code'])
        elif path == 'macro/save':
            kb.call(kb.save_macro, None if q.get('index') is None else int(q['index']), q['macro'])
        elif path == 'macro/delete':
            kb.call(kb.delete_macro, int(q['index']))
        elif path == 'remap/reset':
            kb.call(kb.reset_keymap, int(q['layer']))
        elif path == 'profiles/save':
            kb.call(save_profile, kb, q['name'])
        elif path == 'profiles/load':
            kb.call(load_profile, kb, q['name'])
        elif path == 'profiles/delete':
            profile_path(q['name']).unlink(missing_ok=True)
        elif path == 'refresh':
            with kb.lock:
                kb.drop()
        else:
            return None
        return state(lang)

    def state(lang):
        try:
            s = kb.call(kb.state)
        except F75Error as e:
            s = {'connected': False, 'error': e.text(lang)}
        s['profiles'] = list_profiles()
        s['canUndo'], s['canRedo'] = bool(history['undo']), bool(history['redo'])
        return s

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _lang(self):
            lang = self.headers.get('X-Lang')
            return lang if lang in STRINGS else None

        def _local(self):
            # only this machine's own page may talk to the keyboard
            host = self.headers.get('Host', '')
            origin = self.headers.get('Origin')
            ok = host in (f'127.0.0.1:{port}', f'localhost:{port}')
            return ok and (origin is None or origin in (f'http://{host}',))

        def _send(self, code, body, ctype='application/json'):
            self.send_response(code)
            self.send_header('Content-Type', ctype + '; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self._local():
                return self._send(403, b'{}')
            if self.path.split('?')[0] == '/':
                safe = lambda value: json.dumps(value, ensure_ascii=False).replace('</', '<\\/')
                texts = json.loads(resource('strings.json').read_text(encoding='utf-8'))   # re-read: editable without a restart
                page = resource('ui.html').read_text(encoding='utf-8')
                page = page.replace('null/*STRINGS*/', safe(texts), 1).replace('null/*STATE*/', safe(state(None)), 1)
                return self._send(200, page.encode(), 'text/html')
            if self.path == '/api/cursor-color':
                return self._send(200, json.dumps({'rgb': cursor_color()}).encode())
            if self.path == '/api/state':
                return self._send(200, json.dumps(state(self._lang())).encode())
            self._send(404, b'{}')

        def do_POST(self):
            if not self._local() or self.headers.get('Content-Type') != 'application/json':
                return self._send(403, b'{}')
            try:
                q = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or b'{}')
                r = api(self.path.removeprefix('/api/'), q, self._lang())
                if r is None:
                    return self._send(404, b'{}')
                self._send(200, json.dumps(r).encode())
            except F75Error as e:
                self._send(400, json.dumps({'error': e.text(self._lang())}).encode())
            except (KeyError, ValueError, TypeError) as e:
                self._send(400, json.dumps({'error': tr('err.request', self._lang())}).encode())

    url = f'http://127.0.0.1:{port}/'
    try:
        srv = http.server.ThreadingHTTPServer(('127.0.0.1', port), Handler)
    except OSError:
        # most likely a second launch: the page is already being served, just show it
        print(tr('cli.running', url=url))
        if open_browser:
            webbrowser.open(url)
        return
    print(tr('cli.started', url=url))
    if open_browser:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


# --- CLI -----------------------------------------------------------------------
def main():
    global LANG
    if len(sys.argv) == 1:   # started by a double click: open the setup page
        sys.argv.append('ui')
    if '--lang' in sys.argv[1:-1]:   # needed before the parser is built: its help texts are translated too
        LANG = {'ru': 'ru', 'en': 'en'}.get(sys.argv[sys.argv.index('--lang') + 1], LANG)
    p = argparse.ArgumentParser(description=tr('cli.desc'))
    p.add_argument('--lang', choices=('ru', 'en'), help=tr('cli.lang'))
    sub = p.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('ui', help=tr('cli.ui'))
    s.add_argument('--port', type=int, default=7575, help=tr('cli.port'))
    s.add_argument('--no-browser', action='store_true', help=tr('cli.noBrowser'))
    sub.add_parser('info', help=tr('cli.info'))
    sub.add_parser('modes', help=tr('cli.modes'))
    s = sub.add_parser('mode', help=tr('cli.mode')); s.add_argument('mode', help=tr('cli.modeArg'))
    s = sub.add_parser('set', help=tr('cli.set'))
    s.add_argument('-m', '--mode', help=tr('cli.setMode'))
    s.add_argument('-b', '--brightness', type=int, choices=range(10), metavar='0-9', help=tr('cli.brightness'))
    s.add_argument('-s', '--speed', type=int, choices=range(5), metavar='0-4', help=tr('cli.speed'))
    s.add_argument('-c', '--color', type=int, choices=range(8), metavar='0-7', help=tr('cli.color'))
    s.add_argument('--rgb', metavar='RRGGBB', help=tr('cli.rgb'))
    sub.add_parser('keys', help=tr('cli.keys'))
    s = sub.add_parser('custom', help=tr('cli.custom'))
    s.add_argument('--all', metavar='RRGGBB', help=tr('cli.all'))
    s.add_argument('keys', nargs='*', metavar=tr('cli.keyMeta'), help=tr('cli.keyArg'))
    s = sub.add_parser('save', help=tr('cli.save')); s.add_argument('name')
    s = sub.add_parser('load', help=tr('cli.load')); s.add_argument('name')
    sub.add_parser('profiles', help=tr('cli.profiles'))
    a = p.parse_args()

    if a.cmd == 'ui':
        return serve(a.port, not a.no_browser)
    if a.cmd == 'modes':
        for i, name, *_ in EFFECTS:
            print(f"{i:3d}  {name:11s} {tr('preset.' + name)}")
        return
    if a.cmd == 'profiles':
        return print('\n'.join(list_profiles()))

    def mode_id(m):
        return {'custom': CUSTOM_MODE, 'off': OFF_MODE_ID, **MODES}.get(m) if not m.isdigit() else int(m)

    kb = Keyboard()

    def run():
        if a.cmd == 'info':
            st = kb.state()
            for e in st['effects']:
                mark = '>' if e['id'] == st['mode'] else ' '
                color = tr('light.allColors') if e['color'] == 7 else '#' + e['palette'][e['color']]
                line = tr('cli.infoLine', b=e['brightness'], s=e['speed'] + 1, c=color)
                print(f"{mark} {e['id']:2d}  {tr('preset.' + e['name']):20s} {line}")
            if st['mode'] == CUSTOM_MODE:
                print('> ' + tr('cli.customOn'))
            elif st['mode'] == OFF_MODE_ID:
                print('> ' + tr('cli.off'))
        elif a.cmd == 'mode':
            m = mode_id(a.mode)
            if m is None:
                raise F75Error('err.noPreset', name=a.mode)
            kb.set_mode(m)
        elif a.cmd == 'set':
            m = mode_id(a.mode) if a.mode else kb.cfg[OFF_MODE]
            if a.rgb:
                kb.set_palette_color(m, 0, a.rgb)
            kb.set_effect(m, a.brightness, a.speed, 0 if a.rgb else a.color)
        elif a.cmd == 'keys':
            for n, c in kb.state()['keys'].items():
                print(f'{n:10s} {c}')
        elif a.cmd == 'custom':
            colors = {n: a.all for n in KEYS} if a.all else {}
            colors.update(kv.rpartition('=')[::2] for kv in a.keys)
            if colors:
                kb.set_key_colors(colors)
            kb.set_mode(CUSTOM_MODE)
        elif a.cmd == 'save':
            save_profile(kb, a.name)
        elif a.cmd == 'load':
            load_profile(kb, a.name)

    try:
        kb.call(run)
    except F75Error as e:
        sys.exit(str(e))


if __name__ == '__main__':
    main()
