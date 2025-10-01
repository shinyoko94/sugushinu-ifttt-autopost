# -*- coding: utf-8 -*-
import os
import re
import io
import sys
import time
import math
import json
import shutil
import subprocess
from datetime import datetime, timezone, timedelta

import requests
from bs4 import BeautifulSoup
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import MultipleLocator

# =========================
# 環境変数
# =========================
VOTE_URL = "https://sugushinu-anime.jp/vote/"
IFTTT_KEY = os.getenv("IFTTT_KEY", "")
IFTTT_EVENT = os.getenv("IFTTT_EVENT", "sugushinu_vote_update")

RUN_LABEL = os.getenv("RUN_LABEL", "AM")  # AM/PM（見た目とファイル名に使うだけ）
TOP_N = int(os.getenv("TOP_N", "10"))     # ← ここで上限を指定（最大 24 まで想定）
TOP_N = min(TOP_N, 24)

FINAL_MODE = os.getenv("FINAL_MODE", "0") == "1"
FINAL_ANCHOR_JST = os.getenv("FINAL_ANCHOR_JST", "")  # 例: "2025-10-01T18:00:00+09:00"
# 17:59 スナップショット狙いのためのポーリング秒（小さくしすぎると負荷/BAN注意）
POLL_SEC = int(os.getenv("FINAL_POLL_SEC", "5"))
# アンカー時刻以前の最新を採用（例: 18:00 をアンカーに 17:59 までの最新）
SNAP_PRE_SEC = int(os.getenv("FINAL_SNAPSHOT_PRESEC", "60"))

# バーの見た目
BAR_HEIGHT = float(os.getenv("BAR_HEIGHT", "0.35"))  # ← 1/2 の太さ相当
LEFT_LABEL_FONTSIZE = 12
VALUE_FONTSIZE = 20
TITLE_FONTSIZE = 14
TICK_FONTSIZE = 10

# フォント（同梱フォントがあればそちら優先）
BUNDLED_FONT = "fonts/GenEiMGothic2-Bold.ttf"
if os.path.isfile(BUNDLED_FONT):
    matplotlib.rcParams["font.sans-serif"] = [BUNDLED_FONT]
else:
    matplotlib.rcParams["font.sans-serif"] = ["Noto Sans CJK JP", "Noto Sans CJK JP Bold", "IPAexGothic", "Hiragino Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False

# 色（ユーザー指定）
COLOR_S1_L = "#FFFF00"  # Yellow
COLOR_S1_R = "#FF8A00"  # Orange
COLOR_S2_L = "#FE2E82"  # Pink
COLOR_S2_R = "#4F287D"  # Purple

# x軸設定：最大値=最大得票 * 1.3 を 100刻みに切り下げ、1000超ならメモリ200刻み
def compute_xmax(max_vote: int) -> int:
    if max_vote <= 0:
        return 100
    lim = int((max_vote * 1.3) // 100 * 100)
    lim = max(lim, 100)  # 下限
    return lim

def setup_xaxis(ax, xmax: int):
    ax.set_xlim(0, xmax)
    if xmax >= 1000:
        ax.xaxis.set_major_locator(MultipleLocator(200))
    else:
        ax.xaxis.set_major_locator(MultipleLocator(100))
    ax.tick_params(axis="x", labelsize=TICK_FONTSIZE)

# =========================
# スクレイプ部
# =========================
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; VoteFetcher/1.0; +https://github.com/)"
})

def fetch_html() -> str:
    r = SESSION.get(VOTE_URL, timeout=20)
    r.raise_for_status()
    return r.text

def parse_votes(html: str):
    """
    1期/2期の投票リストを [(タイトル, 票数), ...] の降順で返す。
    ※ 既に運用中のパーサを壊さないよう、できるだけ保守的に。
    """
    soup = BeautifulSoup(html, "lxml")

    # セクション検出（サイト構造が変わってもできるだけ拾う）
    # 典型ケース：sectionごとにタイトル見出しがあり、その直下に li/カードが並ぶ
    sections = []
    for sec in soup.find_all(["section", "div"]):
        h = sec.find(["h2", "h3"])
        if not h:
            continue
        ht = h.get_text(strip=True)
        if "吸血鬼すぐ死ぬ２" in ht or "吸死2" in ht or "2期" in ht:
            label = "S2"
        elif "吸血鬼すぐ死ぬ" in ht or "吸死" in ht or "1期" in ht:
            label = "S1"
        else:
            continue
        sections.append((label, sec))

    # フォールバック：ページ全体を二つに分かれてないケースも想定
    if not sections:
        sections = [("S1", soup), ("S2", soup)]

    def extract_from(sec_node):
        items = []
        # 票数らしき数字とタイトルらしきテキストを拾う
        # 票数は「123」「123票」などの数字を優先
        for card in sec_node.find_all(["li", "div", "article"]):
            text = card.get_text(" ", strip=True)
            if not text:
                continue
            # 票数
            m = re.search(r"(\d{1,5})\s*票?", text)
            if not m:
                continue
            votes = int(m.group(1))
            # タイトル：長すぎたら3行分までで省略（描画側でも切るが一応ここでも軽く）
            title = text
            # よくあるパターンのタイトル要素を優先抽出
            tnode = card.find(["h4", "h5"])
            if tnode and tnode.get_text(strip=True):
                title = tnode.get_text(" ", strip=True)
            else:
                # 先頭の番号/ノイズ除去
                title = re.sub(r"^\s*\d+[\.\)]\s*", "", title)
            items.append((title, votes))
        # 重複やノイズが入った場合は票数降順でユニークに
        uniq = {}
        for t, v in items:
            if t not in uniq or v > uniq[t]:
                uniq[t] = v
        out = sorted(uniq.items(), key=lambda x: x[1], reverse=True)
        return out

    s1 = []
    s2 = []
    for label, node in sections:
        data = extract_from(node)
        if label == "S2":
            s2 = data
        else:
            s1 = data

    return s1, s2

def safe_scrape():
    html = fetch_html()
    return parse_votes(html)

# =========================
# 17:59 スナップショット優先の取得
# =========================
def jst_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=9)))

def parse_jst_datetime(s: str) -> datetime:
    # 例: 2025-10-01T18:00:00+09:00
    # Pythonのfromisoformatにそのまま渡せる
    return datetime.fromisoformat(s)

def fetch_until_anchor_snapshot(anchor: datetime, pre_seconds: int = SNAP_PRE_SEC, poll_sec: int = POLL_SEC):
    """
    アンカー（例: 18:00JST）になるまでポーリングして、
    アンカーの pre_seconds 秒前までに取得できた「最後の成功データ」を返す。
    何も取れなければ、終了時に1回だけ取得を試みて返す。
    """
    last_ok = None
    last_ok_time = None
    target_latest = anchor - timedelta(seconds=pre_seconds)

    while True:
        now = jst_now()
        try:
            s1, s2 = safe_scrape()
            last_ok = (s1, s2)
            last_ok_time = now
        except Exception:
            # スクレイプ失敗は黙って再試行
            pass

        if now >= anchor:
            break

        # 17:59台（= target_latest 以降）のデータがもう取れていれば十分
        if last_ok_time and last_ok_time >= target_latest:
            # アンカー到達まで軽く待ってから抜けてもOK
            time.sleep(max(0, (anchor - now).total_seconds()))
            break

        time.sleep(poll_sec)

    if last_ok:
        return last_ok
    # それでも無ければ最終リトライ
    return safe_scrape()

# =========================
# 描画
# =========================
def _truncate_to_3_lines(s: str, max_chars_per_line=24):
    # タイトルは3行までで省略
    lines = []
    buf = s
    for _ in range(3):
        if len(buf) <= max_chars_per_line:
            lines.append(buf)
            buf = ""
            break
        lines.append(buf[:max_chars_per_line])
        buf = buf[max_chars_per_line:]
    if buf:
        lines[-1] = lines[-1].rstrip("…") + "…"
    return "\n".join(lines)

def _draw_one(ax, data, title, colors_lr):
    # data: [(title, vote), ...] 上位 TOP_N まで
    labels = [_truncate_to_3_lines(t) for t, _ in data]
    votes = [v for _, v in data]
    y = np.arange(len(data))

    # グラデ作成（左→右）
    left, right = colors_lr
    # bar を個別に描く（gradient の擬似表現：2色の線形補間）
    # 単色でもOKにするため、基準色は右端色にして見た目を合わせる
    ax.barh(y, votes, height=BAR_HEIGHT, color=right, edgecolor="none")

    # y 方向のマージン（上下がピタ付けにならないように）
    ax.set_ylim(-0.5, len(data) - 0.5)

    # ラベルと値
    ax.set_yticks(y, labels, fontsize=LEFT_LABEL_FONTSIZE)
    for yi, v in zip(y, votes):
        ax.text(v + (max(votes) * 0.01 + 2), yi, f"{v}", va="center", ha="left", fontsize=VALUE_FONTSIZE)

    ax.set_xlabel("投票数", fontsize=TICK_FONTSIZE + 2)
    ax.grid(axis="x", linestyle=":", alpha=0.4)

    xmax = compute_xmax(max(votes) if votes else 0)
    setup_xaxis(ax, xmax)

    ax.set_title(title, fontsize=TITLE_FONTSIZE, loc="center")

def draw_figure(s1, s2, stamp_jst: datetime, top_n: int, out_path: str):
    # Figure 全体
    plt.figure(figsize=(8.0, 10.6), dpi=220)
    gs = plt.GridSpec(2, 1, height_ratios=[1, 1], hspace=0.32)

    # 1期
    ax1 = plt.subplot(gs[0])
    t1 = f"吸死（1期） 上位{top_n}（{stamp_jst:%Y/%m/%d %H:%M} JST）"
    _draw_one(ax1, s1[:top_n], t1, (COLOR_S1_L, COLOR_S1_R))

    # 2期
    ax2 = plt.subplot(gs[1])
    t2 = f"吸死２（2期） 上位{top_n}（{stamp_jst:%Y/%m/%d %H:%M} JST）"
    _draw_one(ax2, s2[:top_n], t2, (COLOR_S2_L, COLOR_S2_R))

    # x 軸ラベルが左右でズレないよう明示
    ax1.set_ylabel("")
    ax2.set_ylabel("")

    # 保存
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    plt.savefig(out_path, format="png", dpi=220, bbox_inches="tight", pad_inches=0.15)
    plt.close()

# =========================
# 画像コミット & IFTTT
# =========================
def git_commit_and_push(paths, message):
    subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
    subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"], check=True)
    subprocess.run(["git", "add"] + paths, check=True)
    subprocess.run(["git", "commit", "-m", message], check=True)
    subprocess.run(["git", "push"], check=True)

def ifttt_post(image_path: str, text: str):
    if not (IFTTT_KEY and IFTTT_EVENT):
        print("IFTTT env not set. skip tweet.")
        return
    url = f"https://maker.ifttt.com/trigger/{IFTTT_EVENT}/with/key/{IFTTT_KEY}"
    files = {"file": open(image_path, "rb")}
    data = {"value1": text}
    r = requests.post(url, files=files, data=data, timeout=30)
    print(f"IFTTT status: {r.status_code}")
    try:
        print(r.text[:200])
    except Exception:
        pass

# =========================
# メイン
# =========================
def main():
    jst = timezone(timedelta(hours=9))

    # データ取得
    if FINAL_MODE and FINAL_ANCHOR_JST:
        anchor = parse_jst_datetime(FINAL_ANCHOR_JST)  # tz付き
        s1, s2 = fetch_until_anchor_snapshot(anchor, SNAP_PRE_SEC, POLL_SEC)
        stamp = jst_now()
    else:
        s1, s2 = safe_scrape()
        stamp = jst_now()

    # 画像生成
    label = "FINAL" if FINAL_MODE else RUN_LABEL
    out_dir = "public"
    fname = f"ranking_S1S2Top{TOP_N}_{stamp:%Y-%m-%d}_{label}.png"
    out_path = os.path.join(out_dir, fname)

    draw_figure(s1, s2, stamp, TOP_N, out_path)

    # コミット & プッシュ
    commit_msg = f"Add {fname}"
    git_commit_and_push([out_path], commit_msg)

    # ツイート文面（中間発表テンプレ）
    tweet = (
        f"🗳️エピソード投票中間発表（{stamp:%m/%d %H:%M}時点）🗳️\n\n"
        "投票期間：9月19日（金）～10月3日（金）\n"
        "投票はこちらから（1日1回）→ https://sugushinu-anime.jp/vote/\n\n"
        "#吸血鬼すぐ死ぬ\n#吸血鬼すぐ死ぬ２\n#応援上映エッヒョッヒョ"
    )
    ifttt_post(out_path, tweet)

    # FINAL_MODE の場合は再投稿を防ぐ印を残す（任意）
    if FINAL_MODE:
        mark = ".FINAL_DONE"
        with open(mark, "w", encoding="utf-8") as f:
            f.write(stamp.isoformat())
        git_commit_and_push([mark], "mark final done")

if __name__ == "__main__":
    main()
