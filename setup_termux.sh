#!/data/data/com.termux/files/usr/bin/bash
set -e

echo "== ZORA Advanced Pocket Trader — Termux setup =="

pkg update -y
pkg install -y python git

pip install --upgrade pip
pip install -r requirements.txt

if [ ! -f .env ]; then
    cp .env.example .env
    echo "Created .env from .env.example — edit it before running the bot."
fi

echo ""
echo "Setup complete. Next steps:"
echo "  python bot.py backtest     # test the strategy on history, no money involved"
echo "  python bot.py learn        # same, but lets the auto-learning brain update"
echo "  python bot.py brain        # see what the brain has learned"
echo "  python bot.py run          # paper-trading loop with live market data"
echo "  python bot.py status       # see paper equity / trade summary"
