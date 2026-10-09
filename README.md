# AULA F75 control

An unofficial replacement for the vendor software of the AULA F75 keyboard: lighting presets, per-key
colours (including "from a picture"), key remapping, macros and profiles, with a local web interface in
Russian and English.

Неофициальная замена фирменной программе клавиатуры AULA F75: пресеты подсветки, цвет каждой клавиши
(в том числе «из картинки»), переназначение клавиш, макросы и профили. Интерфейс — локальная веб-страница
на русском и английском.

> Not affiliated with AULA. The protocol was worked out by studying the vendor application and the
> keyboard itself. It writes to the keyboard's memory — use it at your own risk.

## Requirements

- The keyboard connected **by cable** and switched to USB mode (`258a:010c`).
  Bluetooth does not expose the configuration interface; the 2.4G dongle is untested.
- Windows 10 or 11, or Linux. **The Linux version has only been run against an emulated keyboard**
  (a USB/IP device with the same ids), not a real one yet — reports are welcome.
- On Windows, close the vendor application while this one is running.

## Running

Windows downloads on the releases page:

- `AULA-F75-setup.exe` — installer. Installs for the current user without administrator rights, adds a
  Start menu entry and an uninstaller. Profiles are kept in `%APPDATA%\AULA F75\profiles`.
- `AULA-F75-portable.exe` — a single file that needs no installation. Profiles are kept in a `profiles`
  folder next to it.

Either way the program opens in its own window. It needs the Microsoft Edge WebView2 runtime, which
Windows 11 and up-to-date Windows 10 already have.

### Linux

Unpack `aula-f75-linux-x86_64.tar.gz` from the releases page, let your user access the keyboard, and start
the program — it serves the setup page and opens it in the browser:

```
sudo cp 60-aula-f75.rules /etc/udev/rules.d/
sudo udevadm control --reload
# replug the cable, then:
./aula-f75
```

Picking a colour from the screen works only in Chromium-based browsers there.

### From source

Python 3.10+:

```
pip install -r requirements.txt
python f75.py
```

`pywebview` is the only dependency and only the window needs it. `python f75.py ui` shows the same page
in the browser instead and works without any packages.

`python f75.py -h` lists the command-line commands (`mode`, `set`, `custom`, `save`, `load`, …);
`--lang ru|en` selects the language. The `.exe` accepts the same commands when started from a terminal.

Run from source, profiles are stored in the `profiles` folder next to the program.

## Building

Windows:

```
pip install pyinstaller -r requirements.txt
pyinstaller --onefile --noconsole --name AULA-F75 --icon icon.ico --add-data "ui.html;." --add-data "strings.json;." f75.py
```

Linux:

```
pip install pyinstaller
pyinstaller --onefile --name aula-f75 --add-data "ui.html:." --add-data "strings.json:." f75.py
```

The GitHub workflow in `.github/workflows/build.yml` does both on every push, also builds the Windows
installer from `installer.iss` with Inno Setup, and attaches everything to a release when a `v*` tag is pushed.

## Files

| File | What it is |
| --- | --- |
| `f75.py` | the program: HID protocol, local web server, command line; protocol notes are in its docstring |
| `ui.html` | the interface |
| `strings.json` | every text, in Russian and English; a copy next to the `.exe` overrides the built-in one |

## Macros and fair play

In online games, macros that press or repeat keys for you are usually against the rules. Use them for
work and single-player games.

## License

GPL-3.0-or-later, see [LICENSE](LICENSE).
