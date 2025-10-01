#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import io
import time
import json
import math
import base64
import datetime as dt
from typing import List, Tuple, Dict, Any

import requests
from bs4 import BeautifulSoup
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.ticker import MultipleLocator

# ===================== 基本設定 =====================

JST = dt.timezone(dt.timedelta(hours=9))
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; sugushinu-vote-bot/1.0; +https://github.com/)"
})

VOTE_URL = "https://sugushinu-anime.jp/vote/"

# IFTTT: Secrets（GitHub Actions の env/Secrets から）
IFTTT_KEY = os.environ.get("IFTTT_KEY", "")
IFTTT_EVENT = os.environ.get("IFTTT_EVENT", "")     # 例: "sugushinu_vote_update"

# FINAL モード（18:00 アンカー運用に使う時に 1）
FINAL_MODE = os.environ.get("FINAL_MODE", "0") == "1"
# アンカー時刻を手動で上書きしたい時（JST ISO8601）。例: "2025-10-01T18:00:00+09:00"
FINAL_ANCHOR_JST = os.environ.get("FINAL_ANCHOR_JST", "")
# アンカー待ちのポーリング（秒）
FINAL_POLL_SEC = int(os.environ.get("FINAL_POLL_SEC", "5"))
# アンカーの少し前に“スクレイプ＆画像生成”を済ませるためのリード（秒）
FINAL_SNAPSHOT_PRESEC = int(os.environ.get("FINAL_SNAPSHOT_PRESEC", "60"))

# 画像の出力
PUBLIC_DIR = "public"
os.makedirs(PUBLIC_DIR, exist_ok=True)

# ===================== ユーティリティ =====================

def jst_now() -> dt.datetime:
    return dt.datetime.now(JST)

def today_anchor_18() -> dt.datetime:
    now = jst_now()
    return now.replace(hour=18, minute=0, second=0, microsecond=0)

def parse_anchor_env() -> dt.datetime:
    if FINAL_ANCHOR_JST:
        # 例: "2025-10-01T18:00:00+09:00"
        return dt.datetime.fromisoformat(FINAL_ANCHOR_JST)
    return today_anchor_18()

# ===================== スクレイピング =====================

def fetch_html(url: str) -> str:
    r = SESSION.get(url, timeout=30)
    r.raise_for_status()
    return r.text

def _extract_title_and_votes_lis(container: Any) -> List[Tuple[str, int]]:
    """
    サイトの構造変化に耐えるため、よくあるパターンを総当たりで拾う。
    返り値: [(タイトル, 票数)] 降順ではない（後で並び替え）
    """
    items: List[Tuple[str, int]] = []
    if container is None:
        return items

    # li や div を総当りで見て、タイトル+数字 を拾う
    candidates = container.select("li, div, article")
    pat_num = re.compile(r"(\d{1,4}(?:,\d{3})*)")  # 1,234 も 1234 もOK
    for c in candidates:
        txt = c.get_text(" ", strip=True)
        if not txt:
            continue
        # 票数っぽい数字を探す
        m = pat_num.search(txt)
        if not m:
            continue
        n = int(m.group(1).replace(",", ""))
        # タイトル部分を数字より前で大まかに切り出す
        title = txt
        # よくある見出しワードを間引き
        title = re.sub(r"(投票|票|合計|Total|Votes?)", "", title, flags=re.I)
        # 数字以降を落とす
        title = title.split(m.group(1))[0].strip(" .:：-–—")
        if not title:
            continue
        # 極端に長すぎるゴミはスキップ
        if len(title) > 200:
            continue
        items.append((title, n))
    return items

def scrape_vote() -> Dict[str, List[Tuple[str, int]]]:
    """
    返却: {"s1": [(title, votes), ...], "s2": [...]}  ※順不同
    """
    html = fetch_html(VOTE_URL)
    soup = BeautifulSoup(html, "lxml")

    # セクションを見つける（見出しに「吸血鬼すぐ死ぬ」「吸血鬼すぐ死ぬ２」等）
    sections = soup.find_all(["section", "div", "article"])
    s1_items: List[Tuple[str, int]] = []
    s2_items: List[Tuple[str, int]] = []

    KEY_S1 = re.compile(r"吸血鬼すぐ死ぬ($|[^２2])")
    KEY_S2 = re.compile(r"(吸血鬼すぐ死ぬ\s*2|吸血鬼すぐ死ぬ２)")

    for sec in sections:
        text = sec.get_text(" ", strip=True)
        if not text:
            continue

        # セクション見出しで判定
        is_s1 = bool(KEY_S1.search(text))
        is_s2 = bool(KEY_S2.search(text))

        # セクション直下から候補抽出
        lis = _extract_title_and_votes_lis(sec)
        if not lis:
            continue

        # 片方にしか入ってないのが普通
        if is_s1 and not is_s2:
            s1_items.extend(lis)
        elif is_s2 and not is_s1:
            s2_items.extend(lis)

    # もし両方拾えなかったら、全体から一括拾い→タイトルヒューリスティックで分配
    if not s1_items and not s2_items:
        lis_all = _extract_title_and_votes_lis(soup)
        for t, n in lis_all:
            if KEY_S2.search(t):
                s2_items.append((t, n))
            else:
                s1_items.append((t, n))

    # タイトルのノイズ削り（先頭の連番 "1." など）
    def _clean_title(tt: str) -> str:
        tt = re.sub(r"^\s*\d+\s*[.．、)\]]\s*", "", tt)
        return tt.strip()

    s1_items = [(_clean_title(t), v) for t, v in s1_items]
    s2_items = [(_clean_title(t), v) for t, v in s2_items]

    # 0票などの異常を除外
    s1_items = [(t, v) if v >= 0 else (t, 0) for t, v in s1_items]
    s2_items = [(t, v) if v >= 0 else (t, 0) for t, v in s2_items]

    return {"s1": s1_items, "s2": s2_items}

# ===================== ランキング整形 =====================

def sort_and_top(items: List[Tuple[str, int]], topn: int) -> Tuple[List[str], List[int]]:
    # 票数降順、同点はタイトルで安定ソート
    arr = sorted(items, key=lambda x: (-x[1], x[0]))
    arr = arr[:topn]
    titles = [a[0] for a in arr]
    votes = [a[1] for a in arr]
    return titles, votes

# ===================== 描画（上位10版：バー太さ＆中央タイトルを当時の体裁に戻す） =====================

TOP_N = 10
BAR_HEIGHT = 0.62
TITLE_FONTSIZE = 16
TICK_FONTSIZE = 12
LABEL_FONTSIZE = 14
VALUE_FONTSIZE = 18

C1_FROM, C1_TO = "#FFFF00", "#FF8A00"  # 1期：黄→橙
C2_FROM, C2_TO = "#FE2E82", "#4F287D"  # 2期：桃→紫

def _hex2rgb(h):
    h = h.lstrip('#')
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

def _interp_color(c1, c2, t):
    a = np.array(_hex2rgb(c1)); b = np.array(_hex2rgb(c2))
    rgb = (a + (b - a) * t).astype(int)
    return '#%02X%02X%02X' % tuple(rgb)

def _make_colors(n, c_from, c_to):
    if n <= 1: return [c_to]
    return [_interp_color(c_from, c_to, i/(n-1)) for i in range(n)]

def _xmax_scale(max_votes: int) -> Tuple[int, int]:
    """
    x軸最大 = floor(最多票×1.3 / 100) * 100
    1000以上は目盛200刻み
    """
    raw = max_votes * 1.3
    xmax = max(int(raw // 100) * 100, ((max_votes + 99) // 100) * 100)
    tick = 200 if xmax >= 1000 else 100
    return int(xmax), int(tick)

def _trim_title3lines(s: str, max_lines=3) -> str:
    lines = s.split("\n")
    if len(lines) <= max_lines:
        return s
    return "\n".join(lines[:max_lines]) + "…"

def draw_top10_figure(
    s1_titles: List[str], s1_votes: List[int],
    s2_titles: List[str], s2_votes: List[int],
    jst_now: dt.datetime, out_path: str
):
    n1 = min(TOP_N, len(s1_titles))
    n2 = min(TOP_N, len(s2_titles))
    t1, v1 = s1_titles[:n1], s1_votes[:n1]
    t2, v2 = s2_titles[:n2], s2_votes[:n2]

    xmax1, tick1 = _xmax_scale(max(v1) if v1 else 100)
    xmax2, tick2 = _xmax_scale(max(v2) if v2 else 100)

    fig_h = 13 if TOP_N == 10 else 10
    fig = plt.figure(figsize=(10.5, fig_h), dpi=220)
    gs = fig.add_gridspec(2, 1, height_ratios=[1, 1])

    # 1期
    ax1 = fig.add_subplot(gs[0, 0])
    y1 = np.arange(n1)
    colors1 = _make_colors(n1, C1_FROM, C1_TO)
    ax1.barh(y1, v1, height=BAR_HEIGHT, color=colors1, edgecolor='none')

    ts = jst_now.strftime('%Y/%m/%d %H:%M JST')
    title1 = f"吸血鬼すぐ死ぬ（1期） 上位{n1}（{ts}）"
    ax1.set_title(_trim_title3lines(title1), fontsize=TITLE_FONTSIZE, loc='center')

    ax1.set_xlim(0, xmax1)
    ax1.xaxis.set_major_locator(MultipleLocator(tick1))
    ax1.set_xlabel("投票数", fontsize=LABEL_FONTSIZE)
    ax1.tick_params(axis='both', labelsize=TICK_FONTSIZE)
    ax1.set_ylim(-0.6, n1-0.4)

    left_labels1 = [f"{i+1}. {t}" for i, t in enumerate(t1)]
    ax1.set_yticks(y1, labels=left_labels1)

    for y, val in zip(y1, v1):
        ax1.text(val + xmax1*0.01, y, f"{val}", va="center", ha="left", fontsize=VALUE_FONTSIZE)

    # 2期
    ax2 = fig.add_subplot(gs[1, 0])
    y2 = np.arange(n2)
    colors2 = _make_colors(n2, C2_FROM, C2_TO)
    ax2.barh(y2, v2, height=BAR_HEIGHT, color=colors2, edgecolor='none')

    title2 = f"吸血鬼すぐ死ぬ２（2期） 上位{n2}（{ts}）"
    ax2.set_title(_trim_title3lines(title2), fontsize=TITLE_FONTSIZE, loc='center')

    ax2.set_xlim(0, xmax2)
    ax2.xaxis.set_major_locator(MultipleLocator(tick2))
    ax2.set_xlabel("投票数", fontsize=LABEL_FONTSIZE)
    ax2.tick_params(axis='both', labelsize=TICK_FONTSIZE)
    ax2.set_ylim(-0.6, n2-0.4)

    left_labels2 = [f"{i+1}. {t}" for i, t in enumerate(t2)]
    ax2.set_yticks(y2, labels=left_labels2)

    for y, val in zip(y2, v2):
        ax2.text(val + xmax2*0.01, y, f"{val}", va="center", ha="left", fontsize=VALUE_FONTSIZE)

    # 余白（左広め、段間も少し）
    fig.subplots_adjust(left=0.30, right=0.98, top=0.95, bottom=0.08, hspace=0.36)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    plt.savefig(out_path, format="png", bbox_inches="tight", pad_inches=0.15)
    plt.close(fig)

# ===================== Git & IFTTT =====================

def git_commit_and_push(paths: List[str], message: str):
    # 設定（Actions bot）
    try:
        subprocess_run(["git", "config", "user.name", "github-actions[bot]"])
        subprocess_run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"])
    except Exception:
        pass
    for p in paths:
        subprocess_run(["git", "add", p])
    subprocess_run(["git", "commit", "-m", message], check=False)
    subprocess_run(["git", "push"])

def subprocess_run(cmd, check=True):
    import subprocess
    print("$", " ".join(cmd))
    return subprocess.run(cmd, check=check)

def _raw_url_for(image_path: str) -> str:
    import subprocess
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    repo_full = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" in repo_full:
        owner, repo = repo_full.split("/", 1)
    else:
        owner = "shinyoko94"
        repo = "sugushinu-ifttt-autopost"
    rel = os.path.relpath(image_path).replace("\\", "/")
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{sha}/{rel}"

def _wait_url_ok(url: str, tries=15, interval=2):
    for i in range(tries):
        try:
            r = SESSION.head(url, timeout=10)
            if r.ok:
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False

def ifttt_post(image_path: str, text: str):
    if not (IFTTT_KEY and IFTTT_EVENT):
        print("IFTTT env not set. skip tweet.")
        return

    file_url = _raw_url_for(image_path)
    ok = _wait_url_ok(file_url, tries=15, interval=2)
    print("raw URL ready:", ok, file_url)

    url = f"https://maker.ifttt.com/trigger/{IFTTT_EVENT}/with/key/{IFTTT_KEY}"
    payload = {
        "value1": text,          # 本文
        "file": file_url         # アプレット側の Image URL に {{FileUrl}} を設定している場合
        # もし Image URL を {{Value2}} にしているなら: "value2": file_url
    }
    r = SESSION.post(url, json=payload, timeout=30)
    print("IFTTT status:", r.status_code, r.text[:200] if r.text else "")

# ===================== メイン処理 =====================

def build_tweet_text(now: dt.datetime) -> str:
    # 「中間発表」版（ユーザー要望準拠）
    ts = now.strftime("%m/%d %H:%M")
    lines = [
        f"🗳️エピソード投票中間発表（{ts}時点）🗳️",
        "",
        "投票期間：9月19日（金）～10月3日（金）",
        "投票はこちらから（1日1回）→ https://sugushinu-anime.jp/vote/",
        "",
        "#吸血鬼すぐ死ぬ",
        "#吸血鬼すぐ死ぬ２",
        "#応援上映エッヒョッヒョ",
    ]
    return "\n".join(lines)

def main():
    now = jst_now()

    # FINAL_MODE の時はアンカー運用（17:59 取得→18:00 投稿）
    if FINAL_MODE:
        anchor = parse_anchor_env()
        fetch_time = anchor - dt.timedelta(seconds=FINAL_SNAPSHOT_PRESEC)
        print(f"[FINAL] anchor={anchor}, fetch_time={fetch_time}")

        # 取得タイミングまで待機
        while jst_now() < fetch_time:
            time.sleep(max(1, min(30, FINAL_POLL_SEC)))

        # スクレイプ＆生成
        data = scrape_vote()
        s1_titles, s1_votes = sort_and_top(data["s1"], TOP_N)
        s2_titles, s2_votes = sort_and_top(data["s2"], TOP_N)

        out = os.path.join(PUBLIC_DIR, f"ranking_S1S2Top10_{anchor.date()}_FINAL.png")
        draw_top10_figure(s1_titles, s1_votes, s2_titles, s2_votes, jst_now(), out)

        # コミット・プッシュ
        git_commit_and_push([out], f"Add {os.path.basename(out)}")

        # 投稿時刻まで待機
        while jst_now() < anchor:
            time.sleep(max(1, min(30, FINAL_POLL_SEC)))

        # ツイート
        ifttt_post(out, build_tweet_text(anchor))
        return

    # 通常モード：即スクレイプ＆生成＆投稿
    data = scrape_vote()
    s1_titles, s1_votes = sort_and_top(data["s1"], TOP_N)
    s2_titles, s2_votes = sort_and_top(data["s2"], TOP_N)

    out = os.path.join(PUBLIC_DIR, f"ranking_S1S2Top10_{now.date()}_RUN.png")
    draw_top10_figure(s1_titles, s1_votes, s2_titles, s2_votes, now, out)

    git_commit_and_push([out], f"Add {os.path.basename(out)}")
    ifttt_post(out, build_tweet_text(now))

if __name__ == "__main__":
    main()
