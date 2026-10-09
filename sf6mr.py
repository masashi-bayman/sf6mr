#!/usr/bin/env python3
"""SF6 マスターレート記録ツール（Raspberry Pi 用）

使い方:
  python sf6mr.py run              取得 → SQLite保存 → スプシ追記 → Discord通知
  python sf6mr.py run --dry-run    取得して表示のみ（保存・通知なし）
  python sf6mr.py login [--headed] ログインしてセッションを保存（手動確認用）
  python sf6mr.py parse FILE.html  保存したHTMLの解析テスト
  python sf6mr.py export           グラフ用 JSON を書き出し
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sqlite3
import sys
import time
import traceback
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
JST = timezone(timedelta(hours=9))

PLAY_URL = "https://www.streetfighter.com/6/buckler/ja-jp/profile/{sid}/play"
LOGIN_URL = "https://www.streetfighter.com/6/buckler/ja-jp/auth/loginep?redirect_url=/"
LOGIN_REQUIRED_TEXT = "ご利用いただくにはログインまたは登録が必要です"
NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json"[^>]*>(.*?)</script>', re.S
)


# ---------------------------------------------------------------- 設定

def load_env() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(BASE_DIR / ".env")
    except ImportError:
        pass


def env(key: str, default: str | None = None) -> str | None:
    v = os.environ.get(key)
    return v if v not in (None, "") else default


def path_env(key: str, default: str) -> Path:
    p = Path(env(key, default))
    return p if p.is_absolute() else BASE_DIR / p


# ---------------------------------------------------------------- 解析

class LoginRequired(Exception):
    pass


class ParseError(Exception):
    pass


@dataclass
class CharMR:
    character: str          # 日本語名（例: ダルシム）
    alpha: str              # 例: DHALSIM
    tool_name: str          # 例: dhalsim
    mr: int | None          # 未レート（---MR）は None
    lp: int | None
    mr_ranking: int | None
    league_rank: int | None


@dataclass
class Profile:
    short_id: int
    fighter_id: str
    favorite_character_id: int | None
    chars: list[CharMR]
    char_ids: dict[int, CharMR]


def parse_play_html(html: str) -> Profile:
    m = NEXT_DATA_RE.search(html)
    if not m:
        if LOGIN_REQUIRED_TEXT in html:
            raise LoginRequired("ログインが必要なページでした")
        raise ParseError("__NEXT_DATA__ が見つかりません（ページ構成が変わった可能性）")
    data = json.loads(m.group(1))
    pp = data.get("props", {}).get("pageProps", {})
    login_user = (pp.get("common") or {}).get("loginUser")
    if "play" not in pp or "fighter_banner_info" not in pp:
        if not login_user or LOGIN_REQUIRED_TEXT in html:
            raise LoginRequired("ログインが必要なページでした")
        raise ParseError(f"想定外のページ内容です（keys={list(pp.keys())}）")

    fbi = pp["fighter_banner_info"]
    pi = fbi.get("personal_info") or {}
    chars: list[CharMR] = []
    char_ids: dict[int, CharMR] = {}
    for c in pp["play"].get("character_league_infos") or []:
        li = c.get("league_info") or {}
        mr = li.get("master_rating") or 0
        cm = CharMR(
            character=c.get("character_name", ""),
            alpha=c.get("character_alpha", ""),
            tool_name=c.get("character_tool_name", ""),
            mr=mr if mr > 0 else None,
            lp=li.get("league_point"),
            mr_ranking=(li.get("master_rating_ranking") or None) if mr > 0 else None,
            league_rank=li.get("league_rank"),
        )
        chars.append(cm)
        if c.get("character_id") is not None:
            char_ids[int(c["character_id"])] = cm
    return Profile(
        short_id=int(pi.get("short_id") or 0),
        fighter_id=pi.get("fighter_id", ""),
        favorite_character_id=fbi.get("favorite_character_id"),
        chars=chars,
        char_ids=char_ids,
    )


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").lower()
    return re.sub(r"[\s.・_\-]", "", s)


def pick_character(profile: Profile, wanted: str) -> CharMR:
    """スプシのキャラ名（日本語/英字どちらでも）に一致するキャラを返す。空欄ならよく使うキャラ。"""
    if not wanted.strip():
        cm = profile.char_ids.get(profile.favorite_character_id or -1)
        if cm:
            return cm
        raise ParseError("キャラ名が空欄で、よく使うキャラも特定できませんでした")
    w = _norm(wanted)
    for cm in profile.chars:
        if w in (_norm(cm.character), _norm(cm.alpha), _norm(cm.tool_name)):
            return cm
    names = "、".join(c.character for c in profile.chars if c.character != "ランダム")
    raise ParseError(f"キャラ「{wanted}」が見つかりません（候補: {names}）")


# ---------------------------------------------------------------- 取得（Playwright）

class Browser:
    def __init__(self, headed: bool = False):
        from playwright.sync_api import sync_playwright
        self.state_path = path_env("STATE_FILE", "data/session_state.json")
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        launch = {"headless": not headed}
        if env("CHROMIUM_PATH"):
            launch["executable_path"] = env("CHROMIUM_PATH")
        self._browser = self._pw.chromium.launch(**launch)
        ctx_opts = {"locale": "ja-JP", "timezone_id": "Asia/Tokyo"}
        if self.state_path.exists():
            ctx_opts["storage_state"] = str(self.state_path)
        self.ctx = self._browser.new_context(**ctx_opts)
        self.page = self.ctx.new_page()
        self.page.set_default_timeout(60_000)

    def close(self) -> None:
        try:
            self._browser.close()
        finally:
            self._pw.stop()

    def save_state(self) -> None:
        self.ctx.storage_state(path=str(self.state_path))
        os.chmod(self.state_path, 0o600)

    def _dump_debug(self, tag: str) -> Path:
        d = path_env("DEBUG_DIR", "data/debug")
        d.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(JST).strftime("%Y%m%d-%H%M%S")
        base = d / f"{stamp}-{tag}"
        try:
            self.page.screenshot(path=str(base.with_suffix(".png")), full_page=True)
            base.with_suffix(".html").write_text(self.page.content(), encoding="utf-8")
        except Exception:
            pass
        return base

    def login(self) -> None:
        user, pw = env("CAPCOM_ID"), env("CAPCOM_PASSWORD")
        if not user or not pw:
            raise RuntimeError(".env に CAPCOM_ID / CAPCOM_PASSWORD が設定されていません")
        p = self.page
        try:
            p.goto(LOGIN_URL, wait_until="domcontentloaded")
            p.wait_for_url(re.compile(r"cid\.capcom\.com"), timeout=60_000)
            p.wait_for_load_state("networkidle")
            p.locator(
                'input[type="email"], input[name="email"], input[name="username"], input[name="loginId"]'
            ).first.fill(user)
            p.locator('input[type="password"]').first.fill(pw)
            p.locator('button[type="submit"], input[type="submit"]').first.click()
            p.wait_for_url(re.compile(r"streetfighter\.com/6/buckler"), timeout=90_000)
            p.wait_for_load_state("domcontentloaded")
        except Exception as e:
            base = self._dump_debug("login-failed")
            raise RuntimeError(
                f"CAPCOM ID ログインに失敗しました（画面: {base}.png）: {e}"
            ) from e
        self.save_state()

    def fetch_profile(self, sid: str) -> Profile:
        self.page.goto(PLAY_URL.format(sid=sid), wait_until="domcontentloaded")
        html = self.page.content()
        try:
            return parse_play_html(html)
        except ParseError:
            self._dump_debug(f"parse-{sid}")
            raise


# ---------------------------------------------------------------- 対象リスト（スプシ）

@dataclass
class Target:
    sid: str
    name: str
    character: str


def _gsheet():
    import gspread
    cred = path_env("GOOGLE_SERVICE_ACCOUNT_JSON", "service_account.json")
    gc = gspread.service_account(filename=str(cred))
    return gc.open_by_key(env("SHEET_ID"))


def load_targets() -> list[Target]:
    rows: list[dict]
    if env("SHEET_ID"):
        ws = _gsheet().worksheet(env("SHEET_TARGETS_TAB", "対象"))
        rows = ws.get_all_records()
    else:
        import csv
        with open(path_env("TARGETS_CSV", "targets.csv"), encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
    out = []
    for r in rows:
        sid = re.sub(r"\D", "", str(r.get("ID", "")))
        if sid:
            out.append(Target(sid, str(r.get("名前", "")).strip(), str(r.get("キャラ名", "")).strip()))
    return out


def append_sheet(rows: list[list]) -> None:
    if not env("SHEET_ID") or not rows:
        return
    sh = _gsheet()
    tab = env("SHEET_RECORD_TAB", "記録")
    try:
        ws = sh.worksheet(tab)
    except Exception:
        ws = sh.add_worksheet(title=tab, rows=1000, cols=10)
        ws.append_row(["日時", "ID", "名前", "キャラ", "MR", "LP", "MR順位", "前回比"])
    ws.append_rows(rows, value_input_option="USER_ENTERED")


# ---------------------------------------------------------------- SQLite

SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  fetched_at  TEXT NOT NULL,      -- JST ISO8601
  short_id    TEXT NOT NULL,
  name        TEXT,
  fighter_id  TEXT,
  character   TEXT NOT NULL,      -- 英字（DHALSIM など）
  character_ja TEXT,
  mr          INTEGER,            -- 未レートは NULL
  lp          INTEGER,
  mr_ranking  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_records ON records(short_id, character, fetched_at);
"""


def db() -> sqlite3.Connection:
    p = path_env("DB_PATH", "data/sf6mr.sqlite3")
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def previous_mr(con, sid: str, char: str, before: str, older_than: str | None = None):
    sql = "SELECT mr FROM records WHERE short_id=? AND character=? AND fetched_at<? AND mr IS NOT NULL"
    args = [sid, char, before]
    if older_than:
        sql += " AND fetched_at<=?"
        args.append(older_than)
    row = con.execute(sql + " ORDER BY fetched_at DESC LIMIT 1", args).fetchone()
    return row["mr"] if row else None


# ---------------------------------------------------------------- Discord

def discord(content: str) -> None:
    url = env("DISCORD_WEBHOOK_URL")
    if not url:
        print("[discord] (未設定)\n" + content)
        return
    import requests
    for chunk in [content[i:i + 1900] for i in range(0, len(content), 1900)]:
        r = requests.post(url, json={"content": chunk}, timeout=20)
        if r.status_code >= 300:
            print(f"[discord] 送信失敗 {r.status_code}: {r.text[:200]}", file=sys.stderr)


def fmt_diff(d: int | None) -> str:
    if d is None:
        return "—"
    return f"+{d}" if d > 0 else ("±0" if d == 0 else str(d))


# ---------------------------------------------------------------- 実行

def cmd_run(dry_run: bool = False) -> int:
    now = datetime.now(JST)
    now_s = now.isoformat(timespec="seconds")
    day_ago = (now - timedelta(hours=23)).isoformat(timespec="seconds")
    errors: list[str] = []
    lines: list[str] = []
    sheet_rows: list[list] = []

    try:
        targets = load_targets()
    except Exception as e:
        discord(f"⚠️ **SF6 MR記録**: 対象リストを読めませんでした\n```{e}```")
        raise
    if not targets:
        discord("⚠️ **SF6 MR記録**: 対象リストが空です")
        return 1

    con = None if dry_run else db()
    br = Browser()
    try:
        logged_in_this_run = False
        for i, t in enumerate(targets):
            if i:
                time.sleep(random.uniform(4, 8))  # サーバーへの負荷を抑える
            label = t.name or t.sid
            try:
                try:
                    prof = br.fetch_profile(t.sid)
                except LoginRequired:
                    if logged_in_this_run:
                        raise
                    print("ログインが必要 → CAPCOM ID でログインします")
                    br.login()
                    logged_in_this_run = True
                    time.sleep(3)
                    prof = br.fetch_profile(t.sid)
                cm = pick_character(prof, t.character)
                name = t.name or prof.fighter_id
                prev = prev24 = None
                if con is not None:
                    prev = previous_mr(con, t.sid, cm.alpha, now_s)
                    prev24 = previous_mr(con, t.sid, cm.alpha, now_s, day_ago)
                d_prev = cm.mr - prev if (cm.mr is not None and prev is not None) else None
                d_24 = cm.mr - prev24 if (cm.mr is not None and prev24 is not None) else None
                if cm.mr is not None:
                    mr_txt = f"{cm.mr:,} MR"
                elif cm.lp is not None and cm.lp >= 0:
                    mr_txt = f"MR未確定（{cm.lp:,} LP）"
                else:
                    mr_txt = "未プレイ"
                lines.append(
                    f"**{name}** / {cm.character}：{mr_txt}"
                    + (f"（前回比 {fmt_diff(d_prev)}・24h {fmt_diff(d_24)}）" if cm.mr is not None else "")
                )
                if con is not None:
                    con.execute(
                        "INSERT INTO records(fetched_at,short_id,name,fighter_id,character,character_ja,mr,lp,mr_ranking)"
                        " VALUES(?,?,?,?,?,?,?,?,?)",
                        (now_s, t.sid, name, prof.fighter_id, cm.alpha, cm.character,
                         cm.mr, cm.lp, cm.mr_ranking),
                    )
                    con.commit()
                sheet_rows.append([
                    now.strftime("%Y/%m/%d %H:%M"), t.sid, name, cm.character,
                    cm.mr if cm.mr is not None else "", cm.lp if (cm.lp or 0) >= 0 else "",
                    cm.mr_ranking or "", d_prev if d_prev is not None else "",
                ])
            except Exception as e:
                errors.append(f"{label}: {e}")
                traceback.print_exc()
    finally:
        br.close()

    if dry_run:
        print("\n".join(lines + [f"ERROR {e}" for e in errors]))
        return 1 if errors else 0

    try:
        append_sheet(sheet_rows)
    except Exception as e:
        errors.append(f"スプレッドシート追記: {e}")
    try:
        cmd_export(quiet=True)
    except Exception as e:
        errors.append(f"グラフ用JSON書き出し: {e}")

    if lines:
        discord(f"📊 **SF6 MR記録** {now:%m/%d %H:%M}\n" + "\n".join(lines))
    if errors:
        hint = ""
        if any("ログイン" in e for e in errors):
            hint = "\n→ 手動ログインが必要かもしれません（README の「ログインがうまくいかないとき」参照）"
        discord("⚠️ **SF6 MR記録 エラー**\n" + "\n".join(f"- {e}" for e in errors) + hint)
    return 1 if errors else 0


def cmd_login(headed: bool) -> int:
    br = Browser(headed=headed)
    try:
        br.login()
        prof = br.fetch_profile(env("MY_ID", "4050953661"))
        print(f"ログインOK: {prof.fighter_id}（セッションを {br.state_path} に保存）")
    finally:
        br.close()
    return 0


def cmd_parse(file: str) -> int:
    prof = parse_play_html(Path(file).read_text(encoding="utf-8"))
    print(f"{prof.fighter_id} ({prof.short_id})  よく使うキャラID={prof.favorite_character_id}")
    for c in prof.chars:
        if c.mr is not None:
            print(f"  {c.character:10s} {c.alpha:10s} {c.mr:>5} MR  LP {c.lp:,}  順位 {c.mr_ranking:,}")
    return 0


def cmd_export(quiet: bool = False) -> int:
    out = path_env("EXPORT_JSON", "data/mr.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    con = db()
    series: dict[str, dict] = {}
    for r in con.execute(
        "SELECT fetched_at,short_id,name,character_ja,mr FROM records WHERE mr IS NOT NULL ORDER BY fetched_at"
    ):
        key = f"{r['short_id']}:{r['character_ja']}"
        s = series.setdefault(key, {"label": f"{r['name']}（{r['character_ja']}）", "points": []})
        s["label"] = f"{r['name']}（{r['character_ja']}）"
        s["points"].append([r["fetched_at"], r["mr"]])
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps({"updated": datetime.now(JST).isoformat(timespec="seconds"),
                               "series": list(series.values())}, ensure_ascii=False), encoding="utf-8")
    tmp.replace(out)
    if not quiet:
        print(f"書き出し: {out}")
    return 0


def main() -> int:
    load_env()
    ap = argparse.ArgumentParser(description="SF6 MR 記録")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run"); r.add_argument("--dry-run", action="store_true")
    lg = sub.add_parser("login"); lg.add_argument("--headed", action="store_true")
    pa = sub.add_parser("parse"); pa.add_argument("file")
    sub.add_parser("export")
    a = ap.parse_args()
    if a.cmd == "run":
        return cmd_run(a.dry_run)
    if a.cmd == "login":
        return cmd_login(a.headed)
    if a.cmd == "parse":
        return cmd_parse(a.file)
    return cmd_export()


if __name__ == "__main__":
    sys.exit(main())
