# Mintray
Mintray 是一款适用于 macOS 和 Linux 的代理客户端。为了你的隐私与自由而生。无任何依赖，仅提供 TUI 界面。_(即使在没有桌面环境的裸机服务器上，通过 SSH 也能正常运行)_
## 演示
**Linux** _(CachyOS，使用 Console 作为终端)_
![Linux 1.1 Demo](demo/Showcase_Linux_5STBL-2.png.png)

**macOS** _(Tahoe，使用 Warp 作为终端)_
![macOS 1.1 Demo](demo/Showcase_macOS_5STBL-2.png.png)
## 为什么选择 Mintray
- **无依赖。** _(在裸机 Linux 和 macOS 上无需任何依赖即可实现全部功能)_
- **在 macOS 和 Linux 上使用方式完全一致** _(无需为每个平台单独学习不同应用)_
- **TUN 模式。** _(除本地流量外，其余所有流量都会通过代理路由)_
- **轻量级** _(没有沉重的后台服务，仅依靠 Python3 运行)_
- **设计简洁** _(简洁、直观的终端界面，人人都能上手)_
## 获取 MintRay
- [**GitHub Releases**](https://github.com/dev4ones-space/Mintray/releases) _(始终保持最新，优先获取渠道)_
## 其他一些说明
1. UDP 可能无法正常工作——这是 Xray 核心的问题 _(我们对此无能为力，无法修复，因此任何使用 UDP 的功能都可能无法正常工作，例如 WhatsApp 通话，或一般性的 WebRTC)_
2. 不支持 Windows。_(1. 没有内置标准库——没有 curses 模块（而这正是主界面所必需的）。2. 限制过于严格（例如没有真正的 root 实现）。3. 网络兼容性问题——需要为整个网络栈单独实现一套方案（而 macOS 与 Linux 之间在安全层面基本是兼容的）)_
3. 在部分 Linux 上可能无法运行——经过充分测试，确认该应用在 Linux 和 macOS 上都能完整运行，但 Linux 的发行版家族太多，我们无法针对每一个都进行优化。以下是已确认可正常运行的系统列表：
- macOS _(Ventura 及更高版本 100% 兼容，系统更新从不涉及可能导致问题的部分)_
- CachyOS 与 Arch _(Linux 发行版；基于 Arch Linux 的发行版大概率也能正常运行)_
## 从源码构建
```bash
git clone https://github.com/dev4ones-space/Mintray.git
cd Mintray
```
从以下仓库获取最新版本的二进制文件
[XTLS/Xray-core](https://github.com/XTLS/Xray-core/releases)
[xjasonlyu/tun2socks](https://github.com/xjasonlyu/tun2socks/releases)
```bash
pip install pyinstaller
mkdir bin && cp /path/to/xray /path/to/tun2socks bin/
pyinstaller Mintray.spec
```
## 从源码构建（分步说明）
_(以下所有步骤都要求你的 `$PATH` 中已安装 Python3，可前往 [python.org](https://www.python.org/downloads/) 下载安装程序)_
1. 克隆仓库并进入目录：_(`cd` 命令用于切换你终端当前所在的目录)_
```bash
git clone https://github.com/dev4ones-space/Mintray.git && cd Mintray
```
2. 为你当前的设备获取对应的二进制文件：_(需指定设备架构（x84_64、aarch64/arm）以及操作系统（macOS 为 darwin，Linux 则直接为 linux）)_
- **[XTLS/Xray-core](https://github.com/XTLS/Xray-core/releases)**
- **[xjasonlyu/tun2socks](https://github.com/xjasonlyu/tun2socks/releases)**
3. 安装 Python 的 PyInstaller 模块：_(需要 pip 已安装并能正常工作)_
```bash
pip install pyinstaller
```
或者
```bash
python3 -m pip install pyinstaller
```
_(另外，如果安装因 externally managed 报错而失败，请在命令中加上以下参数：`--break-system-packages`)_
4. 创建 `bin/` 目录并将二进制文件放入其中：_(需根据实际路径对命令稍作修改)_
```bash
mkdir bin && cp /path/to/xray /path/to/tun2socks bin/
```
5. 构建二进制文件：
```
pyinstaller Mintray.spec
```
或者
```
python3 -m PyInstaller Mintray.spec
```
### 完成以上所有步骤后，一个完全可用的可执行文件应会构建在 `dist/Mintray` 中

_(translated by Claude Sonnet 5 High effort)_
