import re, io, os, pathlib, datetime as dt, urllib.parse, subprocess, sys, textwrap, glob
import requests
from bs4 import BeautifulSoup
import matplotlib.pyplot as plt
from matplotlib import rcParams
from PIL import Image

# ========== フォント（GenEiMGothic2-Bold があれば最優先） ==========
def ensure_custom_font():
    from matplotlib import font_manager
    preferred = None
    try:
        target = "fonts/GenEiMGothic2-Bold.ttf"
        if os.path.isfile(target):
            font_manager.fontManager.addfont(target)
            preferred = font_manager.FontProperties(fname=target).get_name()
            print(f"Loaded font: {preferred}")
        for p in glob.glob("fonts/**/*.[ot]tf", recursive=True) + glob.glob("fonts/*.[ot]tf"):
            try:
                if os.path.abspath(p) != os.path.abspath(target):
                    font_manager.fontManager.addfont(p)
            except Exception:
                pass
    except Exception as e:
        print("font warn:", e, file=sys.stderr)

    rcParams["font.family"] = "sans-serif"
    rcParams["font.sans-serif"] = ([preferred] if preferred else []) + [
        "GenEiMGothic2", "GenEiMGothic2 Bold", "GenEiMGothic2-Bold",
        "Noto Sans CJK JP", "Noto Sans CJK JP Regular", "DejaVu Sans",
    ]
    rcParams["axes.unicode_minus"] = False

ensure_custom_font()
# ===============================================================

VOTE_URL   = "https://sugushinu-anime.jp/vote/"
TOP_N      = int(os.getenv("TOP_N", "5"))
RUN_LABEL  = os.getenv("RUN_LABEL", "")
PUBLIC_DIR = pathlib.Path("public")

CAMPAIGN_PERIOD = "投票期間：9月19日（金）～10月3日（金）"
STOP_AT_JST = dt.datetime(2025, 10, 2, 20, 0, 0, tzinfo=dt.timezone(dt.timedelta(hours=9)))
TITLE_PREFIXES = ["吸血鬼すぐ死ぬ", "吸血鬼すぐ死ぬ２"]

def fetch_html(url: str) -> str:
    r = requests.get(url, timeout=20, headers={"User-Agent":"Mozilla/5.0"})
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

# ---- ここからグラフ描画（共通） ----
def draw_panel(ax, items, color, caption, fixed_xlim=800, show_xlabel=False):
    titles = [f"{i+1}. {_wrap(t[0])}" for i, t in enumerate(items)]
    votes  = [int(t[1]) for t in items]
    y = list(range(len(titles)))[::-1]

    bars = ax.barh(y, votes, color=color)

    ax.set_yticks(y)
    ax.set_yticklabels(titles, fontsize=11)     # 安定版サイズ
    if show_xlabel:
        ax.set_xlabel("投票数", fontsize=11)
    ax.set_title(caption, fontsize=14)
    ax.xaxis.grid(True, linestyle=":", alpha=0.3)

    ax.set_xlim(0, fixed_xlim)

    # 票数だけ大きめ（22pt）
    pad = fixed_xlim * 0.02
    for bar, v in zip(bars, votes):
        ax.text(bar.get_width() + pad,
                bar.get_y() + bar.get_height() / 2,
                f"{v:,}",
                va="center", ha="left", fontsize=22)

def main():
    # 停止判定：指定時刻“より後”はスキップ（当回は投稿）
    now_jst = dt.datetime.now(dt.timezone(dt.timedelta(hours=9)))
    if now_jst > STOP_AT_JST:
        print(f"STOP: {now_jst} > {STOP_AT_JST} なので投稿スキップ")
        return

    stamp_full = now_jst.strftime("%Y/%m/%d %H:%M")
    stamp_day  = now_jst.strftime("%Y-%m-%d")
    month_day  = now_jst.strftime("%m/%d")
    time_label = "8:00時点" if RUN_LABEL == "AM" else ("20:00時点" if RUN_LABEL == "PM" else now_jst.strftime("%H:%M時点"))
    label_ja   = "（朝の部）" if RUN_LABEL=="AM" else ("（夜の部）" if RUN_LABEL=="PM" else "")

    html = fetch_html(VOTE_URL)
    by_season = parse_votes_by_season(html)
    if not (by_season["S1"] or by_season["S2"]):
        raise SystemExit("票データが取れませんでした。")

    top_s1 = pick_top(by_season["S1"], TOP_N)
    top_s2 = pick_top(by_season["S2"], TOP_N)

    cap_s1 = f"吸死（1期） 上位{len(top_s1)}（{stamp_full} JST）{label_ja}"
    cap_s2 = f"吸死２（2期） 上位{len(top_s2)}（{stamp_full} JST）{label_ja}"

    # ---- 同一キャンバスに2サブプロット、x軸共有で完全に揃える ----
    fixed_xlim = 800
    fig, axes = plt.subplots(nrows=2, ncols=1, figsize=(10, 12), dpi=220, sharex=True)
    fig.subplots_adjust(left=0.33, right=0.98, top=0.96, bottom=0.08, hspace=0.28)

    draw_panel(axes[0], top_s1, 'tab:orange', cap_s1, fixed_xlim=fixed_xlim, show_xlabel=False)
    draw_panel(axes[1], top_s2, '#7e57c2',   cap_s2, fixed_xlim=fixed_xlim, show_xlabel=True)

    PUBLIC_DIR.mkdir(exist_ok=True)
    fname = f"ranking_S1S2Top{TOP_N}_{stamp_day}_{RUN_LABEL or 'RUN'}.png"
    out   = PUBLIC_DIR / fname
    fig.savefig(out, format="png", dpi=220)
    plt.close(fig)

    repo = os.getenv("GITHUB_REPOSITORY")
    ref  = os.getenv("GITHUB_REF_NAME", "main")
    img_url = f"https://raw.githubusercontent.com/{repo}/{ref}/public/{urllib.parse.quote(fname)}"

    # Git commit & push
    subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
    subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"], check=True)
    subprocess.run(["git", "add", str(out)], check=True)
    subprocess.run(["git", "commit", "-m", f"Add {fname}"], check=True)
    subprocess.run(["git", "push"], check=True)

    # 🐦ツイート文面（見出しの直後に空行）
    body = (
        f"🗳️エピソード投票中間結果発表（{month_day} {time_label}）🗳️\n"
        f"\n"
        f"{CAMPAIGN_PERIOD}\n"
        f"投票はこちらから（1日1回）→ https://sugushinu-anime.jp/vote/\n\n"
        f"#吸血鬼すぐ死ぬ\n#吸血鬼すぐ死ぬ２\n#応援上映エッヒョッヒョ"
    )

    # IFTTT 投稿
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
