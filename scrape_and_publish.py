# -*- coding: utf-8 -*-
"""
sugushinu vote → image (Top 5) → commit → IFTTT
- 1期/2期をそれぞれ上位5位で描画
- x軸最大は「各期の最多票 × 1.3」を下二桁切り捨て（100刻みで切り下げ）、下限200
- タイトルは2行まで。以降は「…」
- 1期: 黄→橙、2期: 桃→紫 の横向きグラデ棒
- RUN_LABEL(AM/PM) のときは、IFTTT送信前に 8:00 / 20:00 まで待機して厳密時刻以降に投稿
- public/ に保存 → 画像をコミット＆プッシュ → IFTTTへ送信（value1=本文, value2=画像URL）
"""

import os, re, glob, sys, time, pathlib, urllib.parse, subprocess, textwrap
import datetime as dt
import requests
from bs4 import BeautifulSoup
import matplotlib.pyplot as plt
from matplotlib import rcParams
import numpy as np

# ================= フォント =================
def ensure_custom_font():
    from matplotlib import font_manager
    try:
        pref_path = "fonts/GenEiMGothic2-Bold.ttf"
        pref_name = None
        if os.path.isfile(pref_path):
            font_manager.fontManager.addfont(pref_path)
            pref_name = font_manager.FontProperties(fname=pref_path).get_name()
        for p in glob.glob("fonts/**/*.[ot]tf", recursive=True) + glob.glob("fonts/*.[ot]tf"):
            try:
                if os.path.abspath(p) != os.path.abspath(pref_path):
                    font_manager.fontManager.addfont(p)
            except Exception:
                pass
        rcParams["font.family"] = "sans-serif"
        rcParams["font.sans-serif"] = (
            ["Noto Sans CJK JP", "Noto Sans CJK JP Regular"]
            + ([pref_name] if pref_name else [])
            + ["GenEiMGothic2", "GenEiMGothic2-Bold", "DejaVu Sans"]
        )
        rcParams["axes.unicode_minus"] = False
        rcParams["axes.titlesize"]  = 14
        rcParams["axes.labelsize"]  = 12
        rcParams["xtick.labelsize"] = 11
        rcParams["ytick.labelsize"] = 11
    except Exception as e:
        print("font warn:", e, file=sys.stderr)
ensure_custom_font()

# ================ 定数 ================
VOTE_URL   = "https://sugushinu-anime.jp/vote/"
TOP_N      = int(os.getenv("TOP_N", "5"))      # ★デフォ5
RUN_LABEL  = os.getenv("RUN_LABEL", "")        # AM / PM / ""（手動）
PUBLIC_DIR = pathlib.Path("public")

CAMPAIGN_PERIOD = "投票期間：9月19日（金）～10月3日（金）"
STOP_AT_JST = dt.datetime(2025, 10, 2, 20, 0, 0, tzinfo=dt.timezone(dt.timedelta(hours=9)))

TITLE_PREFIXES = ["吸血鬼すぐ死ぬ", "吸血鬼すぐ死ぬ２"]

# ================ 取得 & パース ================
def fetch_html(url: str) -> str:
    r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    return r.text

def parse_votes_by_season(html: str):
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text("\n", strip=True)

    positions = []
    for p in TITLE_PREFIXES:
        i = text.find(p)
        if i != -1:
            positions.append((i, p))
    positions.sort()
    positions.append((len(text), "END"))

    pat = re.compile(r"『([^』]+)』\s*([0-9]{1,6})")
    out = {"S1": [], "S2": []}
    for i in range(len(positions) - 1):
        start, name = positions[i]
        end, _ = positions[i + 1]
        block = text[start:end]
        items = [(m.group(1).strip(), int(m.group(2))) for m in pat.finditer(block)]
        if name == "吸血鬼すぐ死ぬ":
            out["S1"].extend(items)
        elif name == "吸血鬼すぐ死ぬ２":
            out["S2"].extend(items)
    return out

# ================ ユーティリティ ================
def _wrap(s: str, width: int = 18, max_lines: int = 2) -> str:
    lines = textwrap.wrap(s, width=width)
    lines = lines[:max_lines]
    if len(lines) == max_lines and len(s) > sum(len(x) for x in lines):
        lines[-1] = lines[-1].rstrip() + "…"
    return "\n".join(lines)

def pick_top(items, n=5):
    return sorted(items, key=lambda x: (-x[1], x[0]))[:n]

def jst_now():
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=9)))

def anchor_time_jst(now_jst: dt.datetime, run_label: str) -> dt.datetime:
    tz = dt.timezone(dt.timedelta(hours=9))
    d = now_jst.date()
    if run_label == "AM":
        return dt.datetime(d.year, d.month, d.day, 8, 0, 0, tzinfo=tz)
    elif run_label == "PM":
        return dt.datetime(d.year, d.month, d.day, 20, 0, 0, tzinfo=tz)
    return now_jst

# x軸最大：最多票×1.3 を100刻みで切り下げ（下二桁切り捨て）・最低200
def compute_xlim_130pct_floorhundred(items) -> int:
    if not items:
        return 200
    mv = max(v for _, v in items)
    x = int(mv * 1.3)
    x -= x % 100
    return max(200, x)

# 投稿時刻ガード：目標時刻まで待機（最大15分）
def wait_until(target: dt.datetime, max_wait_seconds: int = 15 * 60):
    now = jst_now()
    if now >= target:
        return
    remaining = (target - now).total_seconds()
    remaining = min(max_wait_seconds, max(0, int(remaining)))
    while remaining > 0:
        sleep_sec = min(20, remaining)
        time.sleep(sleep_sec)
        remaining -= sleep_sec

# ================ グラデ棒 ================
def _hex_to_rgb01(hx: str):
    hx = hx.lstrip('#')
    return (int(hx[0:2],16)/255.0, int(hx[2:4],16)/255.0, int(hx[4:6],16)/255.0)

def _fill_rect_with_gradient(ax, rect, c0_hex: str, c1_hex: str):
    x0, y0 = rect.get_x(), rect.get_y()
    w, h = rect.get_width(), rect.get_height()
    if w <= 0 or h <= 0: return
    x1, y1 = x0+w, y0+h
    c0 = np.array(_hex_to_rgb01(c0_hex))
    c1 = np.array(_hex_to_rgb01(c1_hex))
    cols = 256
    t = np.linspace(0, 1, cols).reshape(1, cols, 1)
    grad = c0 + (c1 - c0) * t
    ax.imshow(grad, extent=[x0,x1,y0,y1], origin='lower',
              aspect='auto', interpolation='bicubic', zorder=0, clip_on=True)

# ================ 描画 ================
def draw_panel(ax, items, caption, grad_from_to, fixed_xlim: int, show_xlabel=False):
    titles = [f"{i+1}. {_wrap(t[0])}" for i, t in enumerate(items)]
    votes  = [int(t[1]) for t in items]
    y = list(range(len(titles)))[::-1]

    bars = ax.barh(y, votes, color='none', edgecolor='none', zorder=1)
    for rect in bars:
        _fill_rect_with_gradient(ax, rect, grad_from_to[0], grad_from_to[1])

    ax.set_xlim(0, fixed_xlim)
    ax.set_xticks(np.arange(0, fixed_xlim+1, 100))
    ax.tick_params(axis='x', colors='black')
    ax.tick_params(axis='y', colors='black')
    ax.set_axisbelow(True)
    ax.xaxis.grid(True, linestyle=":", alpha=0.3, zorder=0)
    if show_xlabel:
        ax.set_xlabel("投票数", color='black')

    ax.set_yticks(y)
    ax.set_yticklabels(titles, color='black')

    ax.set_title(caption, color='black')

    # 上下の余白（端が枠に当たらないように）
    top_pad = 0.6
    bottom_pad = 0.6
    ymin = min(y) - 0.5 - bottom_pad
    ymax = max(y) + 0.5 + top_pad
    ax.set_ylim(ymin, ymax)

    # 票数ラベル（はみ出し防止）
    pad = fixed_xlim * 0.02
    for bar, v in zip(bars, votes):
        x = min(bar.get_width() + pad, fixed_xlim - pad * 0.5)
        ax.text(x, bar.get_y()+bar.get_height()/2, f"{v:,}",
                va="center", ha="left", fontsize=22, color='black', zorder=2)

# ================ メイン ================
def main():
    now_jst = jst_now()
    if now_jst > STOP_AT_JST:
        print(f"STOP: {now_jst} > {STOP_AT_JST} なので投稿スキップ")
        return

    anchor = anchor_time_jst(now_jst, RUN_LABEL)
    stamp_day  = anchor.strftime("%Y-%m-%d")
    month_day  = anchor.strftime("%m/%d")
    time_label = "8:00時点" if RUN_LABEL=="AM" else ("20:00時点" if RUN_LABEL=="PM" else now_jst.strftime("%H:%M時点"))

    html = fetch_html(VOTE_URL)
    by_season = parse_votes_by_season(html)
    if not (by_season["S1"] or by_season["S2"]):
        raise SystemExit("票データが取れませんでした。")

    top_s1 = pick_top(by_season["S1"], TOP_N)
    top_s2 = pick_top(by_season["S2"], TOP_N)

    # ★各期別に「最多×1.3を下二桁切り捨て」
    xlim_s1 = compute_xlim_130pct_floorhundred(top_s1)
    xlim_s2 = compute_xlim_130pct_floorhundred(top_s2)

    cap_s1 = "吸血鬼すぐ死ぬ　上位5位"
    cap_s2 = "吸血鬼すぐ死ぬ２　上位5位"

    try:
        fig, axes = plt.subplots(nrows=2, ncols=1, figsize=(10.2, 10.5), dpi=220,
                                 sharex=False, layout='constrained')
        fig.set_constrained_layout_pads(w_pad=0.4, h_pad=0.10, hspace=0.02, wspace=0.2)
    except TypeError:
        fig, axes = plt.subplots(nrows=2, ncols=1, figsize=(10.2, 10.5), dpi=220, sharex=False)
        fig.tight_layout(rect=(0.05, 0.05, 0.98, 0.98))

    # カラー（指定のグラデ）
    color_s1_left,  color_s1_right  = "#FFFF00", "#FF8A00"  # 黄→橙
    color_s2_left,  color_s2_right  = "#FE2E82", "#4F287D"  # 桃→紫

    draw_panel(axes[0], top_s1, cap_s1, (color_s1_left, color_s1_right), fixed_xlim=xlim_s1, show_xlabel=False)
    axes[0].tick_params(axis='x', labelbottom=True)  # 1期もx軸目盛り表示

    draw_panel(axes[1], top_s2, cap_s2, (color_s2_left, color_s2_right), fixed_xlim=xlim_s2, show_xlabel=True)

    PUBLIC_DIR.mkdir(exist_ok=True)
    fname = f"ranking_S1S2Top{TOP_N}_{stamp_day}_{RUN_LABEL or 'RUN'}.png"
    out   = PUBLIC_DIR / fname
    plt.savefig(out, format="png", dpi=220, bbox_inches="tight", pad_inches=0.15)
    plt.close(fig)

    repo = os.getenv("GITHUB_REPOSITORY")
    ref  = os.getenv("GITHUB_REF_NAME", "main")
    img_url = f"https://raw.githubusercontent.com/{repo}/{ref}/public/{urllib.parse.quote(fname)}"

    # 画像をコミット＆プッシュ
    subprocess.run(["git", "config", "user.name",  "github-actions[bot]"], check=True)
    subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"], check=True)
    subprocess.run(["git", "add", str(out)], check=True)
    subprocess.run(["git", "commit", "-m", f"Add {fname}"], check=True)
    subprocess.run(["git", "push"], check=True)

    # ツイート本文（「中間発表」）
    body = (
        f"🗳️エピソード投票中間発表（{month_day} {time_label}）🗳️\n"
        f"\n{CAMPAIGN_PERIOD}\n"
        f"投票はこちらから（1日1回）→ https://sugushinu-anime.jp/vote/\n\n"
        f"#吸血鬼すぐ死ぬ\n#吸血鬼すぐ死ぬ２\n#応援上映エッヒョッヒョ"
    )

    # ★ アンカー時刻ガード（AM/PMのみ有効）：IFTTTへ送る直前に待機
    if RUN_LABEL in ("AM", "PM"):
        wait_until(anchor, max_wait_seconds=15*60)

    # IFTTTへ送信
    key   = os.getenv("IFTTT_KEY")
    event = os.getenv("IFTTT_EVENT")
    if key and event:
        url = f"https://maker.ifttt.com/trigger/{event}/with/key/{key}"
        r = requests.post(url, json={"value1": body, "value2": img_url}, timeout=30)
        print("IFTTT status:", r.status_code, r.text[:200])
    else:
        print("IFTTT_KEY/IFTTT_EVENT 未設定なので送信スキップ", file=sys.stderr)

    print(f"IFTTT_TEXT::{body}")
    print(f"IFTTT_IMG::{img_url}")

if __name__ == "__main__":
    main()
