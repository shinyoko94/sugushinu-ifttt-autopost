import re, os, pathlib, datetime as dt, urllib.parse, subprocess, sys, textwrap, glob, time
import requests
from bs4 import BeautifulSoup
import matplotlib.pyplot as plt
from matplotlib import rcParams
import numpy as np

# ================= フォント設定 =================
def ensure_custom_font():
    from matplotlib import font_manager
    preferred = None
    try:
        target = "fonts/GenEiMGothic2-Bold.ttf"
        if os.path.isfile(target):
            font_manager.fontManager.addfont(target)
            preferred = font_manager.FontProperties(fname=target).get_name()
        for p in glob.glob("fonts/**/*.[ot]tf", recursive=True) + glob.glob("fonts/*.[ot]tf"):
            try:
                if os.path.abspath(p) != os.path.abspath(target):
                    font_manager.fontManager.addfont(p)
            except Exception:
                pass
    except Exception as e:
        print("font warn:", e, file=sys.stderr)

    rcParams["font.family"] = "sans-serif"
    rcParams["font.sans-serif"] = [
        "Noto Sans CJK JP", "Noto Sans CJK JP Regular"
    ] + ([preferred] if preferred else []) + [
        "GenEiMGothic2", "GenEiMGothic2-Bold", "DejaVu Sans",
    ]
    rcParams["axes.unicode_minus"] = False
    rcParams["axes.titlesize"] = 14
    rcParams["axes.labelsize"] = 11
    rcParams["xtick.labelsize"] = 11
    rcParams["ytick.labelsize"] = 12

ensure_custom_font()
# =================================================

VOTE_URL   = "https://sugushinu-anime.jp/vote/"
TOP_N      = int(os.getenv("TOP_N", "5"))
RUN_LABEL  = os.getenv("RUN_LABEL", "")
PUBLIC_DIR = pathlib.Path("public")

CAMPAIGN_PERIOD = "投票期間：9月19日（金）～10月3日（金）"
STOP_AT_JST = dt.datetime(2025, 10, 2, 20, 0, 0, tzinfo=dt.timezone(dt.timedelta(hours=9)))
TITLE_PREFIXES = ["吸血鬼すぐ死ぬ", "吸血鬼すぐ死ぬ２"]

def fetch_html(url: str) -> str:
    r = requests.get(url, timeout=30, headers={"User-Agent":"Mozilla/5.0"})
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

# タイトルは最大2行（以降は…）
def _wrap(s: str, width: int = 18, max_lines: int = 2) -> str:
    lines = textwrap.wrap(s, width=width)
    lines = lines[:max_lines]
    if len(lines) == max_lines and len(s) > sum(len(x) for x in lines):
        lines[-1] = lines[-1].rstrip() + "…"
    return "\n".join(lines)

def pick_top(items, n=5):
    return sorted(items, key=lambda x: (-x[1], x[0]))[:n]

# 表示用アンカー（AM=08:00 / PM=20:00）
def anchor_time_jst(now_jst: dt.datetime, run_label: str) -> dt.datetime:
    tz = dt.timezone(dt.timedelta(hours=9))
    d = now_jst.date()
    if run_label == "AM":
        return dt.datetime(d.year, d.month, d.day, 8, 0, 0, tzinfo=tz)
    elif run_label == "PM":
        return dt.datetime(d.year, d.month, d.day, 20, 0, 0, tzinfo=tz)
    return now_jst

# x軸最大：最多+200 → 下2桁00（最低 200）
def compute_xlim_hundred(top_s1, top_s2) -> int:
    max_vote = 0
    for items in (top_s1, top_s2):
        if items:
            mv = max(v for _, v in items)
            if mv > max_vote:
                max_vote = mv
    limit = ((max_vote + 200) // 100) * 100  # 530→730→700
    return max(200, limit)

# --------- グラデ用 ----------
def _hex_to_rgb01(hx: str):
    hx = hx.lstrip('#')
    return (int(hx[0:2],16)/255.0, int(hx[2:4],16)/255.0, int(hx[4:6],16)/255.0)

def _fill_rect_with_gradient(ax, rect, c0_hex: str, c1_hex: str):
    x0, y0 = rect.get_x(), rect.get_y()
    w, h = rect.get_width(), rect.get_height()
    if w <= 0 or h <= 0: return
    x1, y1 = x0 + w, y0 + h
    c0 = np.array(_hex_to_rgb01(c0_hex))
    c1 = np.array(_hex_to_rgb01(c1_hex))
    cols = 256
    t = np.linspace(0, 1, cols).reshape(1, cols, 1)
    grad = c0 + (c1 - c0) * t
    # zorder=0 で軸・目盛りより背面へ
    ax.imshow(grad, extent=[x0, x1, y0, y1], origin='lower',
              aspect='auto', interpolation='bicubic', zorder=0, clip_on=True)

# --------- 描画 ----------
def draw_panel(ax, items, caption, grad_from_to: tuple[str,str], fixed_xlim: int, show_xlabel=False):
    titles = [f"{i+1}. {_wrap(t[0])}" for i, t in enumerate(items)]
    votes  = [int(t[1]) for t in items]
    y = list(range(len(titles)))[::-1]

    # 透明バー（枠なし）→上からグラデ
    bars = ax.barh(y, votes, color='none', edgecolor='none', zorder=1)
    for rect in bars:
        _fill_rect_with_gradient(ax, rect, grad_from_to[0], grad_from_to[1])

    # 軸まわり
    ax.set_yticks(y)
    ax.set_yticklabels(titles, color='black')
    ax.set_title(caption, color='black')
    ax.set_xlim(0, fixed_xlim)

    # x目盛りを明示 & 黒で固定（消え対策）
    xticks = np.arange(0, fixed_xlim + 1, 100)
    ax.set_xticks(xticks)
    ax.tick_params(axis='x', colors='black')
    ax.tick_params(axis='y', colors='black')

    ax.set_axisbelow(True)                  # グリッドを背面へ
    ax.xaxis.grid(True, linestyle=":", alpha=0.3, zorder=0)
    if show_xlabel:
        ax.set_xlabel("投票数", color='black')

    # 上下に少し余白（タイトル/ラベル食われ防止）
    ax.margins(y=0.10)

    # 票数だけ大きく & 右端クランプ
    pad = fixed_xlim * 0.02
    for bar, v in zip(bars, votes):
        x = min(bar.get_width() + pad, fixed_xlim - pad * 0.5)
        ax.text(x, bar.get_y() + bar.get_height()/2, f"{v:,}",
                va="center", ha="left", fontsize=22, color='black', zorder=2)

def main():
    now_jst = dt.datetime.now(dt.timezone(dt.timedelta(hours=9)))
    if now_jst > STOP_AT_JST:
        print(f"STOP: {now_jst} > {STOP_AT_JST} なので投稿スキップ")
        return

    anchor = anchor_time_jst(now_jst, RUN_LABEL)
    stamp_full = anchor.strftime("%Y/%m/%d %H:%M")
    stamp_day  = anchor.strftime("%Y-%m-%d")
    month_day  = anchor.strftime("%m/%d")
    time_label = "8:00時点" if RUN_LABEL=="AM" else ("20:00時点" if RUN_LABEL=="PM" else now_jst.strftime("%H:%M時点"))
    label_ja   = "（朝の部）" if RUN_LABEL=="AM" else ("（夜の部）" if RUN_LABEL=="PM" else "")

    html = fetch_html(VOTE_URL)
    by_season = parse_votes_by_season(html)
    if not (by_season["S1"] or by_season["S2"]):
        raise SystemExit("票データが取れませんでした。")

    top_s1 = pick_top(by_season["S1"], TOP_N)
    top_s2 = pick_top(by_season["S2"], TOP_N)
    fixed_xlim = compute_xlim_hundred(top_s1, top_s2)

    cap_s1 = f"吸死（1期） 上位{len(top_s1)}（{stamp_full} JST）{label_ja}"
    cap_s2 = f"吸死２（2期） 上位{len(top_s2)}（{stamp_full} JST）{label_ja}"

    # 自動レイアウト（新）＋フォールバック
    try:
        fig, axes = plt.subplots(nrows=2, ncols=1, figsize=(10.5, 12), dpi=220,
                                 sharex=True, layout='constrained')
    except TypeError:
        fig, axes = plt.subplots(nrows=2, ncols=1, figsize=(10.5, 12), dpi=220, sharex=True)
        fig.tight_layout(rect=(0.06, 0.06, 0.98, 0.98))
    # パネル間の間隔
    try:
        fig.set_constrained_layout_pads(w_pad=0.5, h_pad=0.6, hspace=0.25, wspace=0.2)
    except Exception:
        pass

    # 1期：黄色→オレンジ / 2期：ピンク→紫
    draw_panel(axes[0], top_s1, cap_s1, grad_from_to=("#ffeb3b", "#fb8c00"), fixed_xlim=fixed_xlim, show_xlabel=False)
    draw_panel(axes[1], top_s2, cap_s2, grad_from_to=("#f48fb1", "#7e57c2"),   fixed_xlim=fixed_xlim, show_xlabel=True)

    PUBLIC_DIR.mkdir(exist_ok=True)
    fname = f"ranking_S1S2Top{TOP_N}_{stamp_day}_{RUN_LABEL or 'RUN'}.png"
    out   = PUBLIC_DIR / fname
    # tight で確実に全部入れる
    fig.savefig(out, format="png", dpi=220, bbox_inches="tight", pad_inches=0.2)
    plt.close(fig)

    repo = os.getenv("GITHUB_REPOSITORY")
    ref  = os.getenv("GITHUB_REF_NAME", "main")
    img_url = f"https://raw.githubusercontent.com/{repo}/{ref}/public/{urllib.parse.quote(fname)}"

    subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
    subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"], check=True)
    subprocess.run(["git", "add", str(out)], check=True)
    subprocess.run(["git", "commit", "-m", f"Add {fname}"], check=True)
    subprocess.run(["git", "push"], check=True)

    body = (
        f"🗳️エピソード投票中間結果発表（{month_day} {time_label}）🗳️\n"
        f"\n"
        f"{CAMPAIGN_PERIOD}\n"
        f"投票はこちらから（1日1回）→ https://sugushinu-anime.jp/vote/\n\n"
        f"#吸血鬼すぐ死ぬ\n#吸血鬼すぐ死ぬ２\n#応援上映エッヒョッヒョ"
    )

    time.sleep(3)
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
