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

- Windows, the keyboard connected **by cable** and switched to USB mode (`258a:010c`).
  Bluetooth does not expose the configuration interface; the 2.4G dongle is untested.
- Close the vendor application while this one is running.

## Running

Download `AULA-F75.exe` from the releases page and start it: a console window opens and the setup page
appears in the browser at `http://127.0.0.1:7575/`. Close the console window to quit.

From source (Python 3.10+, no third-party packages):

```
python f75.py ui
```

`python f75.py -h` lists the command-line commands (`mode`, `set`, `custom`, `save`, `load`, …);
`--lang ru|en` selects the language.

Profiles are stored in the `profiles` folder next to the program.

## Building

```
pip install pyinstaller
pyinstaller --onefile --name AULA-F75 --icon icon.ico --add-data "ui.html;." --add-data "strings.json;." f75.py
```

The GitHub workflow in `.github/workflows/build.yml` does the same on every push and attaches the `.exe`
to a release when a `v*` tag is pushed.

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
