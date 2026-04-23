# web/

Next.js 14 dashboard that reads live state from the FastAPI backend at `arb/api/server.py`. Custom dark theme, Framer Motion for the signature re-plan animation, Recharts for the price and SOC charts, SWR for polling. Not deployed anywhere — for local demo recording only.

---

## Quick start

```bash
# Terminal 1: FastAPI backend (from repo root)
source .venv/bin/activate
uvicorn arb.api.server:app --port 8000

# Terminal 2: Next.js frontend
cd web
NEXT_PUBLIC_API_BASE=http://localhost:8000 npm run dev
```

Open `http://localhost:3000`.

---

## Structure

```
app/
  page.tsx              dashboard — SOC, price, rationale, backtest, data quality, spike demo
  replan/page.tsx       standalone animation loop for demo recording
  layout.tsx            shell: fonts, atmosphere background, header
  api/config/route.ts   runtime config shim (exposes FASTAPI_URL to client)
  globals.css           CSS variables, Tailwind base, atmosphere effects

components/
  dashboard/
    SOCPanel.tsx        SOC gauge with numeric readout and trend
    PricePanel.tsx      24h Recharts line chart, import/export prices + action strip
    CurrentStatus.tsx   current action, load, solar, battery power
    RationaleFeed.tsx   last N rationale entries from GET /rationale
    BacktestTable.tsx   7-day results table from GET /backtest/latest
    DataQuality.tsx     sensor freshness pills and audit summary
    SpikeDemoButton.tsx triggers POST /spike-demo, fires a custom DOM event
    ReplanSection.tsx   listens for the spike event, renders ReplanMoment
  replan/
    ReplanMoment.tsx    root animation: baseline vs spiked plan, diff overlay
    PriceTimeline.tsx   side-by-side price bars with spike highlight
    ActionStrip.tsx     per-interval action colour strip, before/after
    SpikeFlash.tsx      flash overlay on spike intervals
    index.ts            barrel export
  ui/
    Card.tsx            surface card with optional title
    Badge.tsx           status pill (ok / warn / error)
    Button.tsx          single-variant button
    Gauge.tsx           SVG arc gauge
    StatPill.tsx        label + value chip
    Skeleton.tsx        loading placeholder
  layout/
    Header.tsx          sticky app header with live indicator

hooks/
  useLiveData.ts        SWR hooks for snapshot, plan, rationale, backtest, audit

lib/
  api.ts                fetch wrappers for all backend endpoints
  design-tokens.ts      JS colour, motion, radius, space constants
  utils.ts              clsx wrapper, number formatters
```

---

## Design tokens

Source of truth: `web/lib/design-tokens.ts` and `web/app/globals.css`. Both must stay in sync — Tailwind reads the CSS variables, components import the TS constants directly for Recharts and Framer Motion.

Key values:

| Token | Value | Use |
|---|---|---|
| `bg` | `#0A0E14` | page background |
| `card` | `#111821` | panel surface |
| `border` | `#1A242F` | dividers |
| `ink` | `#E6EDF3` | primary text |
| `inkDim` | `#8B949E` | secondary text |
| `cyan` | `#00E5FF` | SOC, live indicator |
| `amber` | `#FFB020` | price chart, spike |
| `rose` | `#FF3B5C` | discharge, negative |
| `violet` | `#8B5CF6` | action strip |

Motion eases (Framer Motion):
- `out`: `[0.16, 1, 0.3, 1]` — most transitions
- `inOut`: `[0.65, 0, 0.35, 1]` — cross-fades

---

## Env

| Variable | Default | Notes |
|---|---|---|
| `NEXT_PUBLIC_API_BASE` | `http://localhost:8000` | Set to point at the FastAPI host. |
| `FASTAPI_URL` | — | Server-side override (takes precedence over `NEXT_PUBLIC_API_BASE` via the config route). |

---

## Build

```bash
npm run build
```

Produces a `.next` directory. `next start` serves it. There's no static export config — the `/api/config` route needs a Node runtime.

---

## Notes

- The `/replan` page tries `POST /spike-demo` with a 1.2s timeout on load, then falls back to hardcoded demo data. So it works without the backend running.
- The `SpikeDemoButton` fires a `spike-demo-result` custom DOM event that `ReplanSection` listens for. No shared state store.
- SWR poll intervals: snapshot every 30s, rationale every 60s, backtest every 3600s (matches server-side TTL).
- CORS in `server.py` allows `http://localhost:3000` only. If you change the frontend port, update the `allow_origins` list there.
