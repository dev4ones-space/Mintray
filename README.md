# Mintray
## Simple TUI Xray client. 
### Supports VLESS with newest technologies like XHTTP, connections/servers bundle _(subsciption)_.
### Works out-of-box on macOS (Intel and M series work) & Linux _(tested only Arch-based distros like CachyOS and bare Arch linux)_ with compiled binaries.
### Has features like: HTTPS GET ping _(sorting by ping too)_, colored interface, low resource, very stable _(works flawlessly with 300+ connections & pings them too)_

> ### Why pick this client?
> ### It works perfectly just by running binary without any dependencies and has no requirements _(except only it only runs on Linux/macOS)_. Also client is user friendly and only requires basic shell skills _(only know how to use arguments & use sudo and execute with it)_

# Build
1. Install PyInstaller for python3 (recommended to use python 3.14 and higher)
2. **Obtain executables for bin/**: [bin/xray](https://github.com/XTLS/Xray-core/releases), [bin/tun2socks](https://github.com/xjasonlyu/tun2socks/releases) 
3. **Build with PyInstaller**: `pyinstaller Mintray.spec` _or_ `python3 -m PyInstaller Mintray.spec`

# Build _(very detailed)_
_(everything here should be done in repo's root)_
1. Download lastest binaries for your device (specifications like x84_64 or aarch64/arm64 & linux or darwin/macOS)
  - [Xray](https://github.com/XTLS/Xray-core/releases)
  - [tun2socks](https://github.com/xjasonlyu/tun2socks/releases)

2. Create folder bin/ _(so in path {repo path}/bin)_ inside repo's root 
3. Unpack binaries you downloaded (those usually come in .zip) in bin/ _({repo path}/bin)_
4. Rename binary/ies into static name/s:
   - tun2socks: from tun2socks-linux-amd64-v3 _(just in example, binray may vary for your device)_ to tun2socks
5. Install pyinstaller: `pip install pyinstaller` or `python3 -m pip install pyinstaller`. _(if pip gives error about externally managed, add this argument at the end of command `--break-system-packages`)_
6. Build: `pyinstaller Mintbeat.spec` or `python3 -m PyInstaller Mintbeat.spec`

_this is unfinished README, any suggestions or edits are welcome in issues or private messaging by [email](mailto:yeezy@yzyworks.com)_
