import re, io, os, pathlib, datetime as dt, urllib.parse, subprocess, sys, textwrap, glob
import requests
from bs4 import BeautifulSoup
import matplotlib.pyplot as plt
from matplotlib import rcParams
from matplotlib.patches import FancyBboxPatch
from PIL import Image  # 画像結合

# ===== フォント設定（GenEiMGothic2-Bold を最優先）=====
def ensure_custom_font():
    """
    リポ直下 fonts/ にある ttf/otf を Matplotlib に登録。
    GenEiMGothic2-Bold.ttf があればその“ファミリ名”を最優先で使う。
    無ければ Noto にフォールバック。
    """
    from matplotlib import font_manager

    preferred_family = None
    try:
        target = "fonts/GenEiMGothic2-Bold.ttf"
        if os.path.isfile(target):
            font_manager.fontManager.addfont(target)
            preferred_family = font_manager.FontProperties(fname=target).get_name()
            print(f"Loaded preferred font: {preferred_family} ({target})")

        # 他のフォントも登録（あれば）
        for p in glob.glob("fonts/**/*.[ot]tf", recursive=True) + glob.glob("fonts/*.[ot]tf"):
            if os.path.abspath(p) != os.path.abspath(target):
                try:
                    font_manager.fontManager.addfont(p)
                except Exception:
                    pass
    except Exception as e:
        print("Font load warning:", e, file=sys.stderr)

    if preferred_family:
        rcParams["font.sans-serif"] = [
            preferred_family,
            "GenEiMGothic2", "GenEiMGothic2 Bold", "GenEiMGothic2-Bold",
            "Noto Sans CJK JP", "Noto Sans CJK JP Regular", "DejaVu Sans",
        ]
    else:
        rcParams["font.sans-serif"] = [
            "GenEiMGothic2", "GenEiMGothic2 Bold", "GenEiMGothic2-Bold",
            "Noto Sans CJK JP", "Noto Sans CJK JP Regular", "DejaVu Sans",
        ]
    rcParams["font.family"] = "sans-serif"
    rcParams["axes.unicode_minus"] = False

ensure_custom_font()
# ================================================

VOTE_URL   = "https://sugushinu-anime.jp/vote/"
TOP_N      = int(os.getenv("TOP_N", "5"))        # Top5
RUN_LABEL  = os.getenv("RUN_LABEL", "")         # "AM" / "PM"（手動実行は空）
PUBLIC_DIR = pathlib.Path("public")

CAMPAIGN_PERIOD = "投票期間：9月19日（金）～10月3日（金）"
# この日時“より後”は投稿停止（= 当回は投稿する）
STOP_AT_JST = dt.datetime(2025, 10, 2, 20, 0, 0, tzinfo=dt.timezone(dt.timedelta(hours=9)))

TITLE_PREFIXES = ["吸血鬼すぐ死ぬ", "吸血鬼すぐ死ぬ２"]  # 1期 / 2期 見出し


def fetch_html(url: str) -> str:
    r = requests.get(url, timeout=20, headers={"User-Agent":"Mozilla/5.0"})
    r.raise_for_status()
    return r.text


def parse_votes_by_season(html: str):
    """期ごとに『タイトル』 数字を抽出 → {"S1":[(title, vote),...], "S2":[...]}"""
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


def pick_top(items, n=5):
    return sorted(items, key=lambda x: (-x[1], x[0]))[:n]


def _wrap(s: str, width: int = 18, max_lines: int = 3) -> str:
    """タイトルを最大3行まで折り返し（4行目以降は…）"""
    lines = textwrap.wrap(s, width=width)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip() + "…"
    return "\n".join(lines)


def _draw_rounded_bars(ax, bars, color, radius=8):
    """矩形バーを透明化→丸角パッチで上書き"""
    for rect in bars:
        x, y = rect.get_x(), rect.get_y()
        w, h = rect.get_width(), rect.get_height()
        rect.set_alpha(0.0)
        rr = min(radius, h * 20)
        patch = FancyBboxPatch((x, y), w, h,
                               boxstyle=f"round,pad=0,rounding_size={rr}",
                               linewidth=0, facecolor=color, edgecolor=color, zorder=3)
        ax.add_patch(patch)


def nice_ceiling(x: int) -> int:
    """1-2-5 の“きりの良い”刻みに丸めて繰り上げ（0は1に）"""
    if x <= 0:
        return 1
    import math
    exp = int(math.floor(math.log10(x)))
    base = x / (10 ** exp)
    if base <= 1:
        nice = 1
    elif base <= 2:
        nice = 2
    elif base <= 5:
        nice = 5
    else:
        nice = 10
    return int(nice * (10 ** exp))


def render_image(top_items, caption, bar_color=None, xlim_max: int | None = None, left_pad: float = 0.36):
    """
    丸角横棒グラフ。各バー右に“投票数”。タイトルは改行で折り返し。
    xlim_max: x軸の上限（S1/S2で統一するため外から渡す）
    """
    titles = [f"{i+1}. {_wrap(t[0])}" for i, t in enumerate(top_items)]
    votes  = [int(t[1]) for t in top_items]
    y = list(range(len(titles)))[::-1]

    fig, ax = plt.subplots(figsize=(10, 7), dpi=220)
    bars = ax.barh(y, votes, color="none")
    _draw_rounded_bars(ax, bars, bar_color or "tab:blue", radius=8)

    ax.set_yticks(y)
    ax.set_yticklabels(titles, fontsize=11)
    # yticklabel を左揃え＆少し左へ
    for lbl in ax.get_yticklabels():
        lbl.set_ha("left")
        xx, yy = lbl.get_position()
        lbl.set_position((xx - 0.02, yy))

    ax.set_xlabel("投票数", fontsize=11)
    ax.set_title(caption, fontsize=14)
    ax.xaxis.grid(True, linestyle=":", alpha=0.3)

    # === x軸スケールを“きりの良い値”まで繰り上げ、右余白は桁数で可変 ===
    vmax = (max(votes) if votes else 0)
    raw_xmax = xlim_max if xlim_max is not None else vmax
    xmax_nice = nice_ceiling(raw_xmax)
    digits = len(f"{xmax_nice:,}")
    right_margin = 0.12 + 0.02 * max(0, digits - 3)  # 12% + 桁数で増加
    ax.set_xlim(0, xmax_nice * (1.0 + right_margin))

    # === 値ラベル（右端クランプ & 端に寄ったら内側白字） ===
    x_right = ax.get_xlim()[1]
    pad = xmax_nice * 0.02 if xmax_nice > 0 else 0.02
    for rect, v in zip(bars, votes):
        bar_right = rect.get_width()
        label_x = min(bar_right + pad, x_right - pad)
        ha = "left"
        color = None
        weight = "normal"

        # 右端の92%以上に迫るバーは内側描画（白太字）
        if x_right > 0 and (bar_right / x_right) > 0.92:
            label_x = bar_right - pad
            ha = "right"
            color = "white"
            weight = "bold"

        ax.text(label_x,
                rect.get_y() + rect.get_height() / 2,
                f"{v:,}",
                va="center", ha=ha, fontsize=11,
                color=color if color else plt.rcParams["text.color"],
                fontweight=weight, zorder=4)

    # 折り返し分の左余白
    plt.subplots_adjust(left=left_pad)
    # はみ出し防止
    plt.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=220, bbox_inches="tight", pad_inches=0.1)
    plt.close(fig)
    buf.seek(0)
    return buf


def stitch_vertical(img1_bytes: io.BytesIO, img2_bytes: io.BytesIO) -> io.BytesIO:
    img1 = Image.open(img1_bytes).convert("RGBA")
    img2 = Image.open(img2_bytes).convert("RGBA")
    w = max(img1.width, img2.width)
    h = img1.height + img2.height
    canvas = Image.new("RGBA", (w, h), (255, 255, 255, 0))
    canvas.paste(img1, (0, 0))
    canvas.paste(img2, (0, img1.height))
    out = io.BytesIO()
    canvas.save(out, format="PNG")
    out.seek(0)
    return out


def git_commit(filepath: pathlib.Path, msg: str):
    subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
    subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"], check=True)
    subprocess.run(["git", "add", str(filepath)], check=True)
    subprocess.run(["git", "commit", "-m", msg], check=True)
    subprocess.run(["git", "push"], check=True)


def post_ifttt(text: str, img_url: str):
    key = os.getenv("IFTTT_KEY")
    event = os.getenv("IFTTT_EVENT")
    if not (key and event):
        print("IFTTT_KEY/IFTTT_EVENT 未設定なので送信スキップ", file=sys.stderr)
        return False
    url = f"https://maker.ifttt.com/trigger/{event}/with/key/{key}"
    r = requests.post(url, json={"value1": text, "value2": img_url}, timeout=30)
    print("IFTTT status:", r.status_code, r.text[:200])
    return r.ok


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

    # S1/S2でx軸スケールを合わせる（はみ出しに強い“きりの良い上限”を共有）
    vmax_all = 0
    if top_s1: vmax_all = max(vmax_all, max(v for _, v in top_s1))
    if top_s2: vmax_all = max(vmax_all, max(v for _, v in top_s2))
    vmax_all = nice_ceiling(vmax_all)

    cap_s1 = f"吸死（1期） 上位{len(top_s1)}（{stamp_full} JST）{label_ja}"
    cap_s2 = f"吸死２（2期） 上位{len(top_s2)}（{stamp_full} JST）{label_ja}"

    left_pad = 0.36
    img1 = render_image(top_s1, cap_s1, bar_color='tab:orange', xlim_max=vmax_all, left_pad=left_pad)
    img2 = render_image(top_s2, cap_s2, bar_color='#7e57c2',   xlim_max=vmax_all, left_pad=left_pad)
    img  = stitch_vertical(img1, img2) if (top_s1 and top_s2) else (img1 or img2)

    PUBLIC_DIR.mkdir(exist_ok=True)
    fname = f"ranking_S1S2Top{TOP_N}_{stamp_day}_{RUN_LABEL or 'RUN'}.png"
    out   = PUBLIC_DIR / fname
    with open(out, "wb") as f:
        f.write(img.read())

    repo = os.getenv("GITHUB_REPOSITORY")
    ref  = os.getenv("GITHUB_REF_NAME", "main")
    img_url = f"https://raw.githubusercontent.com/{repo}/{ref}/public/{urllib.parse.quote(fname)}"

    git_commit(out, f"Add {fname}")

    body = (
        f"🗳️エピソード投票中間結果発表（{month_day} {time_label}）🗳️\n"
        f"{CAMPAIGN_PERIOD}\n"
        f"投票はこちらから（1日1回）→ https://sugushinu-anime.jp/vote/\n\n"
        f"#吸血鬼すぐ死ぬ\n#吸血鬼すぐ死ぬ２\n#応援上映エッヒョッヒョ"
    )

    post_ifttt(body, img_url)
    print(f"IFTTT_TEXT::{body}")
    print(f"IFTTT_IMG::{img_url}")


if __name__ == "__main__":
    main()
