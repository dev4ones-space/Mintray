# Mintray

Mintray 是一款适用于 macOS 和 Linux 的代理客户端。为了您的隐私与自由。

无任何依赖，仅提供 TUI 界面。项目开源，如有任何问题可通过 Issues 页面或邮件反馈。

- [English language/Английский язык/英语](index.md) _(仅随每次发布更新)_
- [Russian language/Русский язык/俄语](indexRussian.md) _(仅随每次发布更新)_

## 支持的协议
- **Vless** _(完整支持所有传输方式和加密方式)_，由 Xray 软件包提供 (`bin/xray`)
- **Socks5**
- **Trojan**，由 Xray 软件包提供 (`bin/xray`)
- **Shadowsocks**
- **Hysteria2**，由 Hysteria2 软件包提供 (`bin/hysteria2`)
-----------
## 展示
**Linux** _(CachyOS，终端为 Console)_
![Linux Showcase](demo/Showcase_Linux.png)

**macOS** _(Tahoe，终端为 Warp)_
![macOS Showcase](demo/Showcase_macOS.png)
-----------
## 为什么选择 Mintray
- **无依赖** _(在裸机 Linux 和 macOS 上无需任何依赖即可通过二进制文件完整运行全部功能)_
- **在 macOS 和 Linux 上使用方式一致** _(无需为不同平台学习不同的应用)_
- **TUN 模式** _(将除本地流量外的所有流量通过代理转发)_
- **轻量** _(只需要 Python3，任何机器都能顺畅运行)_
- **设计简洁** _(简洁清晰的终端界面，人人都能上手)_
- **开源** _(大多数客户端是闭源的，而我们在这里做到 100% 透明)_
-----------
## 获取 Mintray _(已发布的二进制文件与仓库)_
- [**Mintray 仓库**](https://yzyworks.com/git/Mintray/) _(优先推荐，包含源代码及其他内容)_
- [**GitHub**](https://github.com/dev4ones-space/Mintray/releases) _(次选，仅提供 Releases 与 README)_
-----------
## 使用方法
### Mintray 支持通过订阅提供方添加连接 _(服务器)_
- **如何向 Mintray 添加订阅**：
  ```bash
  mintray --add-sub [https URL] 
  ```
  _(添加后 Mintray 即可正常工作)_
  
#### 注意！请使用 `--help` 查看更多参数信息，其中一些参数可能正好能解决您的问题
-----------
## 其他说明
1. Vless 上的 UDP 可能无法正常工作——这是 Xray 内核本身的问题 _(我们对此无能为力，因此任何依赖 UDP 的功能都可能无法使用，例如 WhatsApp 通话，或者 WebRTC 相关功能)_
2. 不支持 Windows。_(1. 没有内置的标准库支持——没有 curses（这是必需的主界面）。2. 系统限制过于严格（例如没有真正的 root 实现）。3. 网络兼容性问题——需要为整个网络栈重新实现一套方案（而 macOS 与 Linux 之间基本可以安全地保持兼容）)_
3. 在某些 Linux 上可能无法运行——虽然经过充分测试，确认可以在 Linux 和 macOS 上完整运行所有功能，但由于 Linux 发行版家族众多，我们无法针对每一个发行版进行优化。以下是已确认可正常运行的操作系统列表：
- macOS _(与 Ventura 及更高版本 100% 兼容，更新从不涉及可能导致问题的部分)_
- CachyOS 与 Arch _(Linux 发行版。基于 Arch Linux 的发行版可以正常运行，Debian 及其他发行版可能无法运行)_
--------
## 从源代码构建

```
git clone https://yzyworks.com/git/Mintray/.git
cd Mintray
```
请在以下仓库中获取最新版本的二进制文件

[XTLS/Xray-core](https://github.com/XTLS/Xray-core/releases)

[xjasonlyu/tun2socks](https://github.com/xjasonlyu/tun2socks/releases)

**[apernet/hysteria](https://github.com/apernet/hysteria/releases)**
```
pip install pyinstaller
mkdir bin && cp /path/to/xray /path/to/tun2socks bin/
pyinstaller Mintray.spec
```

## 从源代码构建（分步说明）
_(以下所有步骤均要求您的 `$PATH` 中已安装 Python3，可从 [python.org](https://www.python.org/downloads/) 下载安装程序)_
1. 克隆仓库并进入该目录：_(`cd` 会切换终端当前所在的目录)_
```
git clone https://yzyworks.com/git/Mintray/.git && cd Mintray
```
2. 为您当前的设备收集二进制文件：_(需注意设备架构（x84_64、aarch64/arm）以及操作系统（macOS 对应 darwin，Linux 直接写 linux）等规格信息)_
- **[XTLS/Xray-core](https://github.com/XTLS/Xray-core/releases)**
- **[xjasonlyu/tun2socks](https://github.com/xjasonlyu/tun2socks/releases)**
- **[apernet/hysteria](https://github.com/apernet/hysteria/releases)**
3. 安装 Python 的 PyInstaller 模块：_(需要 pip 已安装并可正常使用)_
```
pip install pyinstaller
```
或
```
python3 -m pip install pyinstaller
```
_(如果安装因 externally managed 错误而失败，请在命令后加上参数：`--break-system-packages`)_

4. 创建 `bin/` 目录并将二进制文件放入其中：_(需要稍微调整命令中的路径)_
```
mkdir bin && cp /path/to/xray /path/to/tun2socks bin/
```
5. 构建二进制文件：
```
pyinstaller Mintray.spec
```
或
```
python3 -m PyInstaller Mintray.spec
```

### 完成以上全部步骤后，完整可用的可执行文件将生成于 `dist/Mintray`
------
## 用户须知
#### 我们 YZYWORKS 认为，隐私是一项人权，每个人都理应享有。
#### 我们是隐私的坚定支持者，绝不会记录您通过本客户端所做的任何事情。
#### [获取免费的 Vless 代理，守护您的隐私](mailto:proxy@yzyworks.com)。_(无日志政策，完整的隐私政策请见[此处](https://yzyworks.com/mdr?source=yzyproxy/PrivacyPolicy.md))_ 我们提供每月 512GB 流量、不限速的免费套餐；如有需要，您也可以订购提供同样服务但不限流量的付费套餐。
#### 这不是广告，这只是我们对自家服务的推荐，该服务对所有人免费开放 _(我们不因您来自哪里或属于哪个国家而区别对待——面向所有人)_
