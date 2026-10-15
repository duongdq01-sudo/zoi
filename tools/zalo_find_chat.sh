#!/bin/bash
# zalo_find_chat.sh - do chat_id cua group Zalo tu getUpdates.
#
# CACH DUNG:
#   1. Them bot "Bot it bsh" vao group Zalo can nhan canh bao
#   2. Nhan MOT TIN bat ky trong group do (bot chi thay tin tu luc no vao)
#   3. Chay script nay NGAY sau do
#
# Vi sao phai lam vay: Zalo Bot API khong co getChat/getChats (404), va
# setWebhook thi doi mot URL Zalo goi VAO duoc ma server khong co cong vao.
# getUpdates long-poll ~60s; khong co tin thi tra 408 Request timeout.
set -u
SEC=/u01/app/ggadmin/conf/secrets.ini
BASE=https://bot-api.zapps.me/bot
VONG=${1:-3}

TOKEN=$(awk -F= '/^\[zalo_bot\]/{f=1} f&&/^token/{gsub(/^[ \t]+|[ \t]+$/,"",$2);print $2;exit}' "$SEC")
[ -n "$TOKEN" ] || { echo "[FAIL] khong doc duoc [zalo_bot] token trong $SEC"; exit 1; }
mask() { sed -e "s|$TOKEN|<TOKEN>|g" -e "s|${TOKEN#*:}|<SECRET>|g"; }

echo "=== Bot ==="
curl -s -m 15 "${BASE}${TOKEN}/getMe" | mask | sed 's/^/  /'
echo
echo "=== Doc getUpdates, toi $VONG vong, moi vong cho toi 70 giay ==="
echo "    (neu chua nhan tin nao trong group thi se ra 408 Request timeout)"
OFFSET=0
FOUND=/tmp/zalo_chats.txt
: > "$FOUND"
for i in $(seq 1 "$VONG"); do
  echo "  --- vong $i ---"
  RESP=$(curl -s -m 70 "${BASE}${TOKEN}/getUpdates?offset=${OFFSET}&limit=50" 2>&1)
  printf '    %s\n' "$(printf '%s' "$RESP" | tr -d '\n' | cut -c1-900 | mask)"
  # Lay chat_id + ten chat + update_id bang python de khong phu thuoc jq
  printf '%s' "$RESP" | /u01/app/ggadmin/venv/bin/python3 -c '
import json, sys
try:
    j = json.load(sys.stdin)
except Exception:
    sys.exit(0)
if not j.get("ok"):
    sys.exit(0)
last = 0
seen = {}
for u in j.get("result") or []:
    last = max(last, int(u.get("update_id") or 0))
    msg = u.get("message") or u.get("edited_message") or {}
    chat = msg.get("chat") or {}
    cid = chat.get("id") or chat.get("chat_id")
    if cid:
        seen[str(cid)] = (chat.get("chat_type") or chat.get("type") or "?",
                          chat.get("title") or chat.get("name") or "")
for cid, (typ, title) in seen.items():
    print("CHAT\t%s\t%s\t%s" % (cid, typ, title))
if last:
    print("LASTID\t%d" % last)
' > /tmp/zalo_parse.txt 2>/dev/null
  grep '^CHAT' /tmp/zalo_parse.txt | while IFS=$'\t' read -r _ cid typ title; do
    echo "    >>> TIM THAY chat_id = $cid   loai=$typ   ten=$title"
    echo "$cid" >> "$FOUND"
  done
  L=$(awk -F'\t' '/^LASTID/{print $2}' /tmp/zalo_parse.txt)
  [ -n "${L:-}" ] && OFFSET=$((L + 1))
  if [ -s "$FOUND" ]; then break; fi
done

echo
if [ -s "$FOUND" ]; then
  IDS=$(sort -u "$FOUND" | awk '{printf "%s\"%s\"", (NR>1?", ":""), $1}')
  echo "=== XONG. Dat vao /u01/app/ggadmin/conf/app.toml ==="
  echo "  [notify.zalo_bot]"
  echo "  chat_ids = [$IDS]"
  echo
  echo "  Roi: bash /u01/app/ggadmin/run.sh restart"
else
  echo "=== KHONG tim thay chat_id nao ==="
  echo "  Kiem lai: bot da duoc THEM VAO GROUP chua, va da co ai NHAN TIN"
  echo "  trong group KE TU KHI bot vao chua. Bot khong thay tin cu."
fi
rm -f /tmp/zalo_parse.txt
