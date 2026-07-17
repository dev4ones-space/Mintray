a = Analysis(['Mintray.py'], pathex=[], binaries=[('bin/xray', 'bin'), ('bin/tun2socks', 'bin'), ('bin/hysteria', 'bin')], datas=[], hiddenimports=[], hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='Mintray', debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=True, disable_windowed_traceback=False, argv_emulation=False, target_arch=None, codesign_identity=None, entitlements_file=None)
