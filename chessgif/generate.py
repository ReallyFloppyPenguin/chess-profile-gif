"""
Generate an animated chess.com profile GIF.

1. Fetch live data from the public chess.com API
2. Inject it into template.html
3. Use Playwright to load the page and capture N frames
4. Stitch frames into a looping GIF (and optimize with gifsicle if available)
"""
from __future__ import annotations

import asyncio
import io
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
from PIL import Image
from playwright.async_api import async_playwright

ROOT = Path(__file__).parent
TEMPLATE = ROOT / "template.html"
RENDERED = ROOT / "rendered.html"
OUT_GIF = ROOT.parent / "profile.gif"

USERNAME = os.environ.get("CHESS_USERNAME", "xXCreativeIonCannonXx")
VIEWPORT = (800, 1000)
# Literal real-time recording. The page runs at normal browser speed using
# Math.random()-driven canvases (matching the live HTML exactly). We sample
# every FRAME_INTERVAL_MS of real wall-clock time. There is no seamless
# loop guarantee — the GIF will visibly snap when restarting, because the
# original HTML never repeats either.
FRAME_COUNT = 120
FRAME_INTERVAL_MS = 50   # 20 fps
GIF_FRAME_DURATION = 50  # play at the same rate we captured

UA = "chessgif-bot/1.0 (github actions; contact: profile-gif)"


# ---------- chess.com API ----------

async def fetch_chess_data(username: str) -> dict:
    """Fetch stats + recent games from chess.com public API."""
    headers = {"User-Agent": UA, "Accept": "application/json"}
    async with httpx.AsyncClient(headers=headers, timeout=20) as c:
        stats_r = await c.get(f"https://api.chess.com/pub/player/{username}/stats")
        archives_r = await c.get(f"https://api.chess.com/pub/player/{username}/games/archives")

    stats = stats_r.json() if stats_r.status_code == 200 else {}
    archives = archives_r.json().get("archives", []) if archives_r.status_code == 200 else []

    # Most recent archive month
    games: list[dict] = []
    if archives:
        async with httpx.AsyncClient(headers=headers, timeout=20) as c:
            g = await c.get(archives[-1])
        if g.status_code == 200:
            games = g.json().get("games", [])

    def rating_for(key: str) -> int:
        node = stats.get(key) or {}
        last = node.get("last") or {}
        return int(last.get("rating") or 0)

    def record(key: str) -> tuple[int, int, int]:
        node = stats.get(key) or {}
        rec = node.get("record") or {}
        return int(rec.get("win", 0)), int(rec.get("loss", 0)), int(rec.get("draw", 0))

    bullet = rating_for("chess_bullet")
    blitz = rating_for("chess_blitz")
    rapid = rating_for("chess_rapid")
    daily = rating_for("chess_daily")
    puzzle = rating_for("tactics")

    wins = losses = draws = 0
    for k in ("chess_bullet", "chess_blitz", "chess_rapid", "chess_daily"):
        w, l, d = record(k)
        wins += w
        losses += l
        draws += d

    total = wins + losses + draws
    win_rate = round(100 * wins / total) if total else 0

    # Last 7 games, newest first
    recent = list(reversed(games))[:7]
    rows = []
    for i, g in enumerate(recent, 1):
        white = g.get("white", {})
        black = g.get("black", {})
        is_white = white.get("username", "").lower() == username.lower()
        me = white if is_white else black
        opp = black if is_white else white
        result_code = (me.get("result") or "").lower()
        if result_code == "win":
            cls, label = "win", "WIN ✓"
        elif result_code in ("agreed", "stalemate", "repetition", "insufficient", "50move", "timevsinsufficient"):
            cls, label = "draw", "DRAW ="
        else:
            cls, label = "loss", "LOSS ✗"
        opp_name = (opp.get("username") or "?")[:14]
        time_class = (g.get("time_class") or "?").upper()[:6]
        rows.append(
            f'<tr><td>{i:02d}</td><td>{opp_name}</td><td>{time_class}</td>'
            f'<td class="{cls}">{label}</td></tr>'
        )

    if not rows:
        rows.append('<tr><td colspan="4" style="text-align:center;color:#888">no recent games</td></tr>')

    def pct(r: int, ceiling: int = 2800) -> int:
        return max(5, min(100, round(100 * r / ceiling))) if r else 5

    return {
        "USERNAME": username,
        "WINS": str(wins),
        "LOSSES": str(losses),
        "DRAWS": str(draws),
        "TOTAL": str(total),
        "WIN_RATE": str(win_rate),
        "BULLET": str(bullet or "—"),
        "BLITZ": str(blitz or "—"),
        "RAPID": str(rapid or "—"),
        "DAILY": str(daily or "—"),
        "PUZZLE": str(puzzle or "—"),
        "BULLET_PCT": str(pct(bullet)),
        "BLITZ_PCT": str(pct(blitz)),
        "RAPID_PCT": str(pct(rapid)),
        "DAILY_PCT": str(pct(daily)),
        "PUZZLE_PCT": str(pct(puzzle)),
        "GAMES_ROWS": "\n".join(rows),
        "TIMESTAMP": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
    }


def render_template(data: dict) -> Path:
    html = TEMPLATE.read_text(encoding="utf-8")
    for k, v in data.items():
        html = html.replace("{{" + k + "}}", v)
    RENDERED.write_text(html, encoding="utf-8")
    return RENDERED


# ---------- Playwright capture ----------

async def capture_frames(html_path: Path) -> list[Image.Image]:
    """
    Literal real-time recording.

    Loads the page normally, lets it run at real browser speed (so the
    Math.random()-driven canvases behave exactly like the live HTML), and
    takes a screenshot every FRAME_INTERVAL_MS of real wall-clock time.
    Captures FRAME_COUNT frames total. The GIF plays back at the same
    rate, so playback speed == live page speed.
    """
    frames: list[Image.Image] = []
    total_ms = FRAME_COUNT * FRAME_INTERVAL_MS

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        ctx = await browser.new_context(
            viewport={"width": VIEWPORT[0], "height": VIEWPORT[1]},
            device_scale_factor=1,
        )
        page = await ctx.new_page()
        await page.goto(html_path.absolute().as_uri(), wait_until="networkidle")
        await page.evaluate("document.fonts.ready")
        # Let fonts + canvases warm up before sampling
        await page.wait_for_timeout(800)

        # Strict per-frame schedule against the asyncio event loop clock so
        # screenshot capture latency doesn't accumulate drift.
        loop = asyncio.get_event_loop()
        t0 = loop.time()
        for i in range(FRAME_COUNT):
            target = t0 + (i * FRAME_INTERVAL_MS) / 1000
            now = loop.time()
            if target > now:
                await asyncio.sleep(target - now)
            png = await page.screenshot(type="png", full_page=False)
            frames.append(
                Image.open(io.BytesIO(png)).convert("P", palette=Image.ADAPTIVE, colors=128)
            )

        await browser.close()
    print(f"captured {len(frames)} frames over {total_ms}ms real time")
    return frames


def write_gif(frames: list[Image.Image], out: Path) -> None:
    frames[0].save(
        out,
        save_all=True,
        append_images=frames[1:],
        duration=GIF_FRAME_DURATION,
        loop=0,
        optimize=True,
        disposal=2,
    )
    # Optional gifsicle pass
    if shutil.which("gifsicle"):
        tmp = out.with_suffix(".opt.gif")
        subprocess.run(
            ["gifsicle", "-O3", "--lossy=80", "--colors", "128", str(out), "-o", str(tmp)],
            check=False,
        )
        if tmp.exists():
            tmp.replace(out)
    print(f"wrote {out} ({out.stat().st_size // 1024} KB, {len(frames)} frames)")


async def main() -> None:
    print(f"fetching chess.com data for {USERNAME}...")
    try:
        data = await fetch_chess_data(USERNAME)
    except Exception as e:
        print(f"warning: chess.com fetch failed: {e}", file=sys.stderr)
        data = {
            "USERNAME": USERNAME,
            "WINS": "—", "LOSSES": "—", "DRAWS": "—", "TOTAL": "—", "WIN_RATE": "—",
            "BULLET": "—", "BLITZ": "—", "RAPID": "—", "DAILY": "—", "PUZZLE": "—",
            "BULLET_PCT": "5", "BLITZ_PCT": "5", "RAPID_PCT": "5",
            "DAILY_PCT": "5", "PUZZLE_PCT": "5",
            "GAMES_ROWS": '<tr><td colspan="4" style="text-align:center;color:#888">api unavailable</td></tr>',
            "TIMESTAMP": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        }

    html_path = render_template(data)
    print("capturing frames...")
    frames = await capture_frames(html_path)
    print("writing gif...")
    write_gif(frames, OUT_GIF)


if __name__ == "__main__":
    asyncio.run(main())
