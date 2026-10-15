"""Gui canh bao ra ngoai UI.

VI SAO CAN: canh bao chi hien tren UI thi phai co nguoi dang mo trang moi
biet. Replicat abend luc 2 gio sang thi khong ai thay.

THIET KE CAM ROI: moi kenh la mot driver. Hien co:
  log       - ghi vao logs/notify.log. Luon bat, de truy nguyen.
  webhook   - POST JSON toi mot URL bat ky. Dung cho gateway noi bo cua
              BSH neu co (don gian va on dinh nhat).
  zalo_bot  - Zalo Bot Platform (bot-api.zapps.me). Gui duoc vao GROUP.
              Token dang <bot_id>:<secret> lay o https://zalo.me/s/botcreator/

API Zalo Bot - DA DO THAT tren server 2026-10-13, base
https://bot-api.zapps.me/bot<TOKEN>/ :
  getMe           OK   -> {"ok":true,"result":{...,"can_join_groups":true}}
  sendMessage     CO   -> thieu chat_id thi tra error_code 410
  getUpdates      CO   -> long-poll ~60s; khong co tin thi tra 408 Request timeout
                         (tham so timeout=0 bi BO QUA, van cho du 60s)
  setWebhook      CO   -> nhung doi secret_token VA mot URL Zalo goi VAO duoc;
                         server khong co cong vao nao -> KHONG dung duoc
  getChat/getChats/getGroups/getWebhookInfo : 404, khong ton tai
=> Lay chat_id cua group CHI co mot duong: them bot vao group, nhan mot tin
   trong group, roi doc getUpdates. Xem tools/zalo_find_chat.sh

CHONG SPAM - bat buoc, vi khong co no thi cong cu tu bien thanh nguon nhieu:
  - moi alert_event chi gui MOT lan khi MO
  - gui mot lan khi DONG ("da het")
  - CRIT con mo thi nhac lai moi `repeat_minutes`, WARN khong nhac
  - tran cung `max_per_hour`: vuot thi gom lai thanh mot tin tong hop
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("ggadmin.notify")

# Tran do dai tin thu do ADMIN tu go. Mot cho duy nhat: route web va
# Notifier.send_test phai cung mot nguong, khong chep tay hai noi.
MAX_TEST_MSG = 1000

SCHEMA = """
CREATE TABLE IF NOT EXISTS notify_log (
  id         INTEGER PRIMARY KEY,
  ts         INTEGER NOT NULL,
  channel    TEXT NOT NULL,
  kind       TEXT NOT NULL,          -- OPEN | CLOSE | REPEAT | DIGEST | TEST
  alert_id   INTEGER,
  rule_name  TEXT,
  scope_key  TEXT,
  ok         INTEGER NOT NULL,
  detail     TEXT                     -- loi, hoac ma tra ve
);
CREATE INDEX IF NOT EXISTS ix_notify_ts ON notify_log(ts DESC);
CREATE INDEX IF NOT EXISTS ix_notify_alert ON notify_log(alert_id, kind);
"""


@dataclass
class Msg:
    kind: str
    severity: str
    title: str
    body: str
    alert_id: int | None = None
    rule_name: str | None = None
    scope_key: str | None = None

    def as_text(self) -> str:
        icon = {"CRIT": "[NANG]", "WARN": "[CANH BAO]", "INFO": "[TIN]"}.get(
            self.severity, "[TIN]")
        return f"{icon} {self.title}\n{self.body}"

    @staticmethod
    def so(v) -> str:
        """In so gon: 63.48 -> 63.48 ; 32768.0 -> 32,768"""
        try:
            f = float(v)
        except (TypeError, ValueError):
            return str(v)
        return f"{f:,.0f}" if f == int(f) else f"{f:,.2f}"


# --------------------------------------------------------------------- driver
class Channel:
    name = "base"

    def send(self, m: Msg) -> tuple[bool, str]:
        raise NotImplementedError


def mot_dong(s: str) -> str:
    """Gap moi ky tu xuong dong thanh " | ".

    notify.log ghi MOI BAN GHI MOT DONG. Tu khi tin thu cho ADMIN tu nhap
    noi dung, body co the mang ky tu xuong dong -> nguoi gui gia duoc mot
    dong log nhin y nhu that. Gap lai la het chuyen.
    """
    return " | ".join(x.strip() for x in str(s).splitlines() if x.strip())


class LogChannel(Channel):
    name = "log"

    def __init__(self, cfg):
        self._path = cfg.log_dir / "notify.log"

    def send(self, m: Msg) -> tuple[bool, str]:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {m.kind} "
                         f"{m.severity} {m.rule_name or '-'} {m.scope_key or '-'}"
                         f" :: {mot_dong(m.title)} :: {mot_dong(m.body)}\n")
            return True, "ok"
        except OSError as e:
            return False, str(e)


class WebhookChannel(Channel):
    """POST JSON toi URL bat ky.

    Dung khi BSH co gateway noi bo da boc Zalo san - do la cach don gian va
    on dinh nhat vi khong phai quan ly access_token het han o day.
    """
    name = "webhook"

    def __init__(self, cfg, url: str, token: str | None = None,
                 timeout: int = 10):
        self._url = url
        self._token = token
        self._timeout = timeout

    def send(self, m: Msg) -> tuple[bool, str]:
        payload = {
            "kind": m.kind, "severity": m.severity,
            "title": m.title, "body": m.body,
            "rule": m.rule_name, "target": m.scope_key,
            "text": m.as_text(), "source": "ggadmin",
            "host": "oracle-db-ods-ip-33-90",
        }
        req = urllib.request.Request(
            self._url, data=json.dumps(payload, ensure_ascii=False).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        if self._token:
            req.add_header("Authorization", f"Bearer {self._token}")
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as r:
                return (200 <= r.status < 300), f"HTTP {r.status}"
        except urllib.error.HTTPError as e:
            return False, f"HTTP {e.code} {e.read()[:200]!r}"
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"


class ZaloBotChannel(Channel):
    """Zalo Bot Platform - gui vao GROUP.

    Base: https://bot-api.zapps.me/bot<TOKEN>/sendMessage
    Token dang <bot_id>:<secret>, lay tai https://zalo.me/s/botcreator/

    Khong co token refresh, khong co cua so thoi gian - don gian hon OA/ZNS
    nhieu. Doi lai phai co chat_id cua group, ma chi lay duoc bang cach them
    bot vao group roi doc getUpdates (xem tools/zalo_find_chat.sh).
    """
    name = "zalo_bot"
    BASE = "https://bot-api.zapps.me/bot"

    def __init__(self, cfg, token: str, chat_ids: list[str], timeout: int = 15):
        self._token = token
        self._chats = [str(c) for c in chat_ids if str(c).strip()]
        self._timeout = timeout

    def _mask(self, s: str) -> str:
        # Phong khi API doi lai URL co token trong thong bao loi
        return s.replace(self._token, "<TOKEN>").replace(
            self._token.split(":", 1)[-1], "<SECRET>")

    def send(self, m: Msg) -> tuple[bool, str]:
        if not self._chats:
            return False, "chưa cấu hình danh sách nhóm nhận tin"
        url = f"{self.BASE}{self._token}/sendMessage"
        sent, errs = 0, []
        for chat in self._chats:
            payload = {"chat_id": chat, "text": m.as_text()[:3000]}
            req = urllib.request.Request(
                url, data=json.dumps(payload, ensure_ascii=False).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=self._timeout) as r:
                    j = json.loads(r.read().decode())
                if j.get("ok"):
                    sent += 1
                else:
                    errs.append(f"{chat}: {j.get('error_code')} "
                                f"{j.get('description')}")
            except Exception as e:
                errs.append(f"{chat}: {type(e).__name__}: {self._mask(str(e))}")
        if sent and not errs:
            return True, f"gui {sent}/{len(self._chats)} group"
        if sent:
            return True, f"gửi {sent}/{len(self._chats)}; lỗi: {'; '.join(errs)[:200]}"
        return False, "; ".join(errs)[:300]


# ----------------------------------------------------------------- dieu phoi
class Notifier:
    HOST = "oracle-db-ods-ip-33-90 (172.16.33.90)"

    def _body(self, a: dict, extra: str = "") -> str:
        """Than tin canh bao.

        In NGUONG DANG CO HIEU LUC lay tu DB, khong hardcode trong `note`.
        Da gap that: tin noi "duoi 15%" trong khi nguong luc do la 99 va gia
        tri do duoc 63.48 -> tin tu mau thuan, nguoi doc mat tin vao canh bao.
        """
        unit = (a.get("unit") or "").strip()
        dv = f" {unit}" if unit else ""
        dk = ""
        if a.get("op") and a.get("threshold") is not None:
            dk = f"Ngưỡng   : {a['op']} {Msg.so(a['threshold'])}{dv}\n"
        lines = [
            f"Doi tuong: {a['scope_key']}",
            f"Đo được  : {Msg.so(a['peak_val'])}{dv}"
            + (f"  ({a['metric']})" if a.get("metric") else ""),
        ]
        out = "\n".join(lines) + "\n" + dk
        out += f"Luc      : {time.strftime('%H:%M:%S %d/%m/%Y', time.localtime(a['opened_at']))}\n"
        out += f"May      : {self.HOST}\n"
        if extra:
            out += extra + "\n"
        out += f"\n{a['note']}"
        return out

    def __init__(self, cfg, con: sqlite3.Connection):
        self.cfg = cfg
        self.con = con
        con.executescript(SCHEMA)
        n = getattr(cfg, "notify", None) or {}
        self.enabled: bool = bool(n.get("enabled", True))
        self.repeat_minutes: int = int(n.get("repeat_minutes", 60))
        self.max_per_hour: int = int(n.get("max_per_hour", 20))
        self.min_severity: str = n.get("min_severity", "WARN")
        self.channels: list[Channel] = [LogChannel(cfg)]

        wh = n.get("webhook") or {}
        if wh.get("url"):
            tok = cfg.creds.get("notify_webhook")
            self.channels.append(WebhookChannel(
                cfg, wh["url"], tok.password if tok else wh.get("token")))

        zb = n.get("zalo_bot") or {}
        cred = cfg.creds.get("zalo_bot")
        token = cred.password if cred else None
        if token and zb.get("chat_ids"):
            self.channels.append(ZaloBotChannel(cfg, token, zb["chat_ids"]))
        elif token:
            log.warning("có token Zalo nhưng chưa có nhóm nhận tin -> chưa bật kênh này")

    def channel_names(self) -> list[str]:
        return [c.name for c in self.channels]

    # ------------------------------------------------------------- gui
    def _record(self, ch: str, m: Msg, ok: bool, detail: str) -> None:
        self.con.execute(
            "INSERT INTO notify_log(ts,channel,kind,alert_id,rule_name,"
            "scope_key,ok,detail) VALUES (?,?,?,?,?,?,?,?)",
            (int(time.time()), ch, m.kind, m.alert_id, m.rule_name,
             m.scope_key, 1 if ok else 0, detail[:500]))

    def _sent_count_last_hour(self) -> int:
        return int(self.con.execute(
            "SELECT COUNT(*) FROM notify_log WHERE ts > ? AND channel <> 'log'"
            " AND kind <> 'DIGEST'", (int(time.time()) - 3600,)).fetchone()[0])

    def _dispatch(self, m: Msg) -> None:
        for ch in self.channels:
            ok, detail = ch.send(m)
            self._record(ch.name, m, ok, detail)
            if not ok:
                log.warning("kênh %s gửi thất bại: %s", ch.name, detail)

    def send_test(self, who: str, message: str = "") -> list[tuple[str, bool, str]]:
        message = message.strip()
        if len(message) > MAX_TEST_MSG:
            raise ValueError("Nội dung tin thử tối đa 1.000 ký tự.")
        m = Msg("TEST", "INFO", "ggadmin: thu kenh canh bao",
                f"Tin thử do {who} gửi lúc {time.strftime('%H:%M:%S %d/%m/%Y')}.\n"
                + (message or "Nếu nhận được tin này thì kênh đã thông."))
        out = []
        for ch in self.channels:
            ok, detail = ch.send(m)
            self._record(ch.name, m, ok, detail)
            out.append((ch.name, ok, detail))
        return out

    SEV_RANK = {"INFO": 0, "WARN": 1, "CRIT": 2}

    def process(self, open_alerts: list[dict], snap) -> None:
        """Goi sau moi tick. Quyet dinh gui gi, chong spam."""
        if not self.enabled:
            return
        now = int(time.time())
        minrank = self.SEV_RANK.get(self.min_severity, 1)

        # 1. alert MOI mo -> gui mot lan
        for a in open_alerts:
            if self.SEV_RANK.get(a["severity"], 0) < minrank:
                continue
            # Chong trung tren MOI kenh (ke ca log). Truoc day loc
            # channel<>'log' lam khi chi co kenh log thi gui lai moi tick.
            seen = self.con.execute(
                "SELECT 1 FROM notify_log WHERE alert_id=? AND kind='OPEN'"
                " LIMIT 1", (a["id"],)).fetchone()
            if seen:
                continue
            if self._sent_count_last_hour() >= self.max_per_hour:
                self._digest(open_alerts)
                return
            self._dispatch(Msg(
                "OPEN", a["severity"], f"ggadmin: {a['name']}",
                self._body(a), alert_id=a["id"], rule_name=a["name"],
                scope_key=a["scope_key"]))

        # 2. CRIT con mo -> nhac lai dinh ky
        for a in open_alerts:
            if a["severity"] != "CRIT":
                continue
            last = self.con.execute(
                "SELECT MAX(ts) t FROM notify_log WHERE alert_id=?",
                (a["id"],)).fetchone()
            if last and last["t"] and now - last["t"] >= self.repeat_minutes * 60:
                self._dispatch(Msg(
                    "REPEAT", "CRIT", f"VẪN CHƯA XỬ LÝ: {a['name']}",
                    self._body(a, extra=f"Đã kéo dài {int((now - a['opened_at']) / 60)} phút."),
                    alert_id=a["id"], rule_name=a["name"], scope_key=a["scope_key"]))

        # 3. alert vua DONG -> gui mot lan "da het"
        for r in self.con.execute("""
                SELECT e.id, r.name, r.severity, e.scope_key, e.opened_at, e.closed_at
                  FROM alert_event e JOIN alert_rule r ON r.id = e.rule_id
                 WHERE e.closed_at IS NOT NULL AND e.closed_at > ?
                   AND NOT EXISTS (SELECT 1 FROM notify_log n
                                    WHERE n.alert_id = e.id AND n.kind='CLOSE')""",
                (now - 3600,)):
            if self.SEV_RANK.get(r["severity"], 0) < minrank:
                continue
            # chi bao het neu truoc do DA bao mo, tranh gui le "da het"
            had = self.con.execute(
                "SELECT 1 FROM notify_log WHERE alert_id=? AND kind='OPEN'"
                " LIMIT 1", (r["id"],)).fetchone()
            if not had:
                continue
            mins = int((r["closed_at"] - r["opened_at"]) / 60)
            self._dispatch(Msg(
                "CLOSE", "INFO", f"ĐÃ HẾT: {r['name']}",
                f"Đối tượng: {r['scope_key']}\nKéo dài  : {mins} phút\nMáy      : "
                f"{self.HOST}",
                alert_id=r["id"], rule_name=r["name"], scope_key=r["scope_key"]))

    def _digest(self, open_alerts: list[dict]) -> None:
        """Vuot tran -> gom thanh mot tin, de khong bien thanh nguon nhieu."""
        last = self.con.execute(
            "SELECT MAX(ts) t FROM notify_log WHERE kind='DIGEST'").fetchone()
        now = int(time.time())
        if last and last["t"] and now - last["t"] < 900:
            return
        lines = [f"- {a['severity']} {a['name']} / {a['scope_key']}"
                 for a in open_alerts[:15]]
        self._dispatch(Msg(
            "DIGEST", "CRIT",
            f"ggadmin: {len(open_alerts)} cảnh báo đang mở (đã gom tin)",
            "Vượt trần số tin mỗi giờ nên gom lại:\n" + "\n".join(lines)))
