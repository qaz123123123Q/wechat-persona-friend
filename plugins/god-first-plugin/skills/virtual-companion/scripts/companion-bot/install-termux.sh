#!/data/data/com.termux/files/usr/bin/sh
# 旧手机上的一条龙安装脚本。在 Termux 里跑：
#     sh install-termux.sh
set -e

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

echo "== 1/5 检查 Python =="
# 注意：不要用 `pkg update`。Termux 的 pkg 在某些版本里会调用 curl，
# 而 curl 一旦和 openssl 版本对不上就会直接失败，把整条安装链带崩。
# 所以这里只检查、只补装，失败也不拦。
if ! command -v python >/dev/null 2>&1; then
  apt update -y >/dev/null 2>&1 || true
  apt install -y python || { echo "装 python 失败，手动跑：apt install -y python"; exit 1; }
fi
python -V

echo "== 2/5 让系统别杀 Termux =="
termux-wake-lock || echo "（wake-lock 没成功，手动去 设置→应用→Termux→电池 改成无限制）"

echo "== 3/5 装依赖 =="
python -m pip install --upgrade pip >/dev/null 2>&1 || true
python -m pip install -r requirements.txt

echo "== 4/5 铺开记忆文件 =="
mkdir -p "$HOME/.virtual-companion"
if [ -f memory/state.example.md ] && [ ! -f "$HOME/.virtual-companion/state.md" ]; then
  cp memory/state.example.md "$HOME/.virtual-companion/state.md"
  echo "已放下 state.md 模板——记得改成你自己的人格（或从电脑上拷一份过来）"
fi
[ -f "$HOME/.virtual-companion/reminders.md" ] || : > "$HOME/.virtual-companion/reminders.md"
[ -f config.json ] || cp config.example.json config.json

echo "== 5/5 检查 key =="
if [ -f env.sh ]; then
  . ./env.sh
fi
if [ -n "$SILICONFLOW_API_KEY" ]; then
  grep -q SILICONFLOW_API_KEY "$HOME/.bashrc" 2>/dev/null || cat env.sh >> "$HOME/.bashrc"
  grep -q SILICONFLOW_API_KEY "$HOME/.bash_profile" 2>/dev/null || cat env.sh >> "$HOME/.bash_profile"
  echo "已把 API key 写进 ~/.bashrc 与 ~/.bash_profile"
else
  echo
  echo "还差一步：把 API key 加进去，然后重开 Termux："
  echo "    echo 'export SILICONFLOW_API_KEY=sk-你的key' >> ~/.bashrc"
  echo "    echo 'export SILICONFLOW_API_KEY=sk-你的key' >> ~/.bash_profile"
  echo
fi

echo
echo "记忆文件: $HOME/.virtual-companion"
echo "接下来要做两件事："
echo "  1. 编辑 config.json，把 app_id / app_secret / persona_name 填上"
echo "  2. 跑 python bot.py，看到「正在建立长连接」之后再去飞书后台点保存"
