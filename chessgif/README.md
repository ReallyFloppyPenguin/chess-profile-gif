# chessgif

Generates an animated profile GIF for `xXCreativeIonCannonXx` using live data
from the chess.com public API, refreshed every 5 minutes via GitHub Actions.

## How it works

1. [chessgif/generate.py](generate.py) fetches stats + recent games from
   `https://api.chess.com/pub/player/<user>/stats` and the latest monthly archive.
2. Values are injected into [chessgif/template.html](template.html) (placeholders
   like `{{USERNAME}}`, `{{BULLET}}`, `{{GAMES_ROWS}}`).
3. Playwright loads the rendered page (800×1000 viewport), waits for fonts +
   canvases to warm up, and captures 45 PNG frames at ~15 fps.
4. Pillow stitches the frames into `profile.gif`, then `gifsicle -O3 --lossy=80`
   shrinks it.
5. [.github/workflows/profile-gif.yml](../.github/workflows/profile-gif.yml) runs
   the script on a `*/5 * * * *` cron and commits the updated gif back to the repo.

> **Note:** GitHub Actions scheduled workflows have a **5-minute minimum** and
> often run with additional delay on shared runners. For true 1-minute cadence
> you'd need a different host (Cloudflare Workers cron, a small VPS, etc.).

## Using it on chess.com

After the first run succeeds, embed the raw GIF URL in your chess.com bio:

```
https://raw.githubusercontent.com/<your-user>/<your-repo>/main/profile.gif
```

Chess.com strips `<style>` and `<script>` from bios, but it *does* render
images, so a pre-rendered animated GIF is the workaround.

## Running locally

```bash
cd chessgif
pip install -r requirements.txt
python -m playwright install chromium
CHESS_USERNAME=xXCreativeIonCannonXx python generate.py
```

Output is written to `../profile.gif`.

## Customization

- **Username** — set `CHESS_USERNAME` env var (or edit the workflow).
- **Frame count / fps** — tweak `FRAME_COUNT` and `FRAME_INTERVAL_MS` in
  [generate.py](generate.py).
- **Viewport** — change `VIEWPORT` in [generate.py](generate.py). Keep it
  reasonable; chess.com bios are narrow.
- **Design** — edit [template.html](template.html); placeholders are plain
  `{{NAME}}` tokens replaced by `render_template()`.
